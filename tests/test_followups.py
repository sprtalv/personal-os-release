"""Behavioral checks for result backfill, timing, and retry safety."""

from datetime import datetime
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / ".agents/skills/personal-os/scripts"
sys.path.insert(0, str(SCRIPTS))
import followups
import records


class FollowupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.configure("21:00")
        self.clock = patch.object(followups, "utc_now", return_value=datetime.fromisoformat("2026-10-10T20:00:00+08:00"))
        self.now = self.clock.start()
        self.addCleanup(self.clock.stop)
        self.persist("data/cases/case-one.md", {
            "schema_version": 1, "id": "case-one", "document_type": "case", "kind": "decision",
            "status": "open", "record_refs": [], "user_hypothesis_refs": [],
            "ai_hypothesis_refs": [], "outcome_refs": [],
        }, "先提前停止阅读，观察精力。", "seed-case")

    def configure(self, evening):
        (self.root / "settings.json").write_text(json.dumps({
            "timezone": "Asia/Shanghai", "evening_time": evening, "followup_default_days": 7,
        }), encoding="utf-8")

    def persist(self, path, meta, body, operation):
        return records.apply_bundle(self.root, {"changes": [{
            "path": path, "metadata": meta, "body": body, "expected_sha256": None,
        }]}, operation)

    def outcome(self, identifier="outcome-one"):
        return self.persist(f"data/records/2026-10/{identifier}.md", {
            "schema_version": 1, "id": identifier, "document_type": "record", "kind": "outcome",
            "author": "user", "basis": "user_statement", "domains": ["wellbeing_health"],
            "status": "active", "source": {"ref": "message:test", "message_at": None, "excerpt": "这两天有改善"},
            "time": {"event": None, "expressed": None, "target": None, "precision": "unknown", "note": None},
            "related": [],
        }, "这两天精力有所改善；长期效果仍待观察。", "seed-" + identifier)

    def create(self, identifier="followup-one", **kwargs):
        return followups.create(self.root, identifier, "case-one", "提前停止阅读后精力如何？",
                                "create-" + identifier, **kwargs)

    def meta(self, identifier="followup-one"):
        return records.read_document(self.root / "data/followups" / f"{identifier}.md")[0]

    def snapshot(self):
        return {str(path.relative_to(self.root)): path.read_bytes()
                for path in self.root.rglob("*") if path.is_file()}

    def test_unconfigured_evening_blocks_default_create_and_due(self):
        self.configure(None)
        result = self.create()
        self.assertEqual(result["status"], "blocked")
        self.assertFalse(result["persisted"])
        self.assertFalse((self.root / "data/followups/followup-one.md").exists())
        self.create("explicit-followup", next_check_at="2026-10-17T21:00:00+08:00")
        result = followups.due(self.root, "2026-10-20T22:00:00+08:00")
        self.assertEqual(result["reason"], "evening_time_not_configured")
        self.assertFalse(result["scheduler_created"])

    def test_weekly_default_and_read_only_due(self):
        self.create()
        self.assertEqual(self.meta()["next_check_at"], "2026-10-17T21:00:00+08:00")
        self.assertIsNone(self.meta()["expected_result_at"])
        before = self.snapshot()
        self.assertEqual(followups.due(self.root, "2026-10-17T20:59:00+08:00")["due"], [])
        due = followups.due(self.root, "2026-10-17T21:00:00+08:00")
        self.assertEqual([item["id"] for item in due["due"]], ["followup-one"])
        self.assertEqual(before, self.snapshot())
        self.assertEqual(self.meta()["contact_state"], "not_asked")

    def test_exact_result_after_evening_moves_to_next_evening(self):
        self.create(expected_result_at="2026-10-14T22:00:00+08:00")
        self.assertEqual(self.meta()["next_check_at"], "2026-10-15T21:00:00+08:00")

    def test_due_exposes_known_partial_result_without_closing_long_observation(self):
        self.create()
        self.outcome()
        followups.reply(self.root, "followup-one", "partial", "partial-one", outcome_ids=["outcome-one"])
        followup = self.meta()
        self.assertEqual(followup["status"], "active")
        due = followups.due(self.root, followup["next_check_at"])["due"][0]
        self.assertEqual(due["answer_state"], "partial")
        self.assertEqual(due["known_outcome_refs"], ["outcome-one"])
        self.assertEqual(due["recommendation_refs"], [])

    def test_confirmed_delivery_required_and_no_reply_does_not_mean_no_result(self):
        self.create()
        with self.assertRaises(ValueError):
            followups.mark_asked(self.root, "followup-one", "check-one", "", "asked-empty")
        self.now.return_value = datetime.fromisoformat("2026-10-17T21:10:00+08:00")
        followups.mark_asked(self.root, "followup-one", "check-one", "dot:delivered:one", "asked-one")
        meta = self.meta()
        self.assertEqual(meta["contact_state"], "awaiting_reply")
        self.assertEqual(meta["answer_state"], "unknown")
        self.assertIsNone(meta["last_asked_at"])
        self.assertIsNone(meta["last_answered_at"])
        self.assertEqual(meta["history"][-1]["time_basis"], "processing_time_unknown_event")
        self.assertEqual(meta["next_check_at"], "2026-10-24T21:00:00+08:00")
        due = followups.due(self.root, "2026-10-24T21:00:00+08:00")
        self.assertEqual(due["due"][0]["contact_state"], "awaiting_reply")
        before = self.snapshot()
        result = followups.mark_asked(self.root, "followup-one", "check-one", "dot:delivered:one", "asked-duplicate")
        self.assertEqual(result["status"], "already_recorded")
        self.assertEqual(before, self.snapshot())

    def test_explicit_no_result_resets_week_from_reply(self):
        self.create()
        self.now.return_value = datetime.fromisoformat("2026-10-18T09:00:00+08:00")
        followups.reply(self.root, "followup-one", "no_result", "reply-none")
        meta = self.meta()
        self.assertEqual(meta["contact_state"], "replied")
        self.assertEqual(meta["answer_state"], "no_result")
        self.assertIsNone(meta["last_answered_at"])
        self.assertIsNone(meta["history"][-1]["source_ref"])
        self.assertIn("unknown", meta["history"][-1]["source_limitation"])
        self.assertEqual(meta["next_check_at"], "2026-10-25T21:00:00+08:00")

    def test_delayed_delivery_uses_receipt_time_and_is_already_due(self):
        self.create()
        self.now.return_value = datetime.fromisoformat("2026-10-28T21:05:00+08:00")
        followups.mark_asked(self.root, "followup-one", "receipt-late", "dot:delivery:late", "asked-late",
                             delivered_at="2026-10-18T21:10:00+08:00")
        meta = self.meta()
        self.assertEqual(meta["last_asked_at"], "2026-10-18T21:10:00+08:00")
        self.assertEqual(meta["next_check_at"], "2026-10-25T21:00:00+08:00")
        event = meta["history"][-1]
        self.assertEqual(event["recorded_at"], "2026-10-28T21:05:00+08:00")
        self.assertEqual(event["time_basis"], "source")
        self.assertEqual(event["delivery_ref"], "dot:delivery:late")
        self.assertEqual(len(followups.due(self.root)["due"]), 1)

    def test_delayed_reply_uses_source_time_and_preserves_source(self):
        self.create()
        self.now.return_value = datetime.fromisoformat("2026-10-28T21:05:00+08:00")
        followups.reply(self.root, "followup-one", "no_result", "reply-import",
                        answered_at="2026-10-18T21:15:00+08:00", source_ref="dot:message:old-reply")
        meta = self.meta()
        self.assertEqual(meta["last_answered_at"], "2026-10-18T21:15:00+08:00")
        self.assertEqual(meta["next_check_at"], "2026-10-25T21:00:00+08:00")
        event = meta["history"][-1]
        self.assertEqual(event["recorded_at"], "2026-10-28T21:05:00+08:00")
        self.assertEqual(event["source_ref"], "dot:message:old-reply")
        self.assertEqual(event["time_basis"], "source")
        self.assertEqual(len(followups.due(self.root)["due"]), 1)

    def test_event_times_require_evidence_and_cannot_be_future(self):
        self.create()
        with self.assertRaises(ValueError):
            followups.reply(self.root, "followup-one", "no_result", "reply-no-source",
                            answered_at="2026-10-10T19:00:00+08:00")
        with self.assertRaises(ValueError):
            followups.reply(self.root, "followup-one", "no_result", "reply-future",
                            answered_at="2026-10-11T21:00:00+08:00", source_ref="dot:future")
        with self.assertRaises(ValueError):
            followups.mark_asked(self.root, "followup-one", "check-future", "dot:receipt", "asked-future",
                                 delivered_at="2026-10-11T21:00:00+08:00")

    def test_old_reply_cannot_overwrite_newer_answer(self):
        self.create()
        self.now.return_value = datetime.fromisoformat("2026-10-28T21:05:00+08:00")
        followups.reply(self.root, "followup-one", "no_result", "reply-newer",
                        answered_at="2026-10-25T21:00:00+08:00", source_ref="dot:newer")
        before = self.snapshot()
        result = followups.reply(self.root, "followup-one", "stop", "reply-older",
                                 answered_at="2026-10-18T21:00:00+08:00", source_ref="dot:older")
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(before, self.snapshot())

    def test_old_delivery_cannot_overwrite_newer_reply_contact_state(self):
        self.create()
        self.now.return_value = datetime.fromisoformat("2026-10-28T21:05:00+08:00")
        followups.reply(self.root, "followup-one", "no_result", "reply-newer",
                        answered_at="2026-10-25T21:00:00+08:00", source_ref="dot:newer")
        before = self.snapshot()
        result = followups.mark_asked(self.root, "followup-one", "old-check", "dot:old-delivery", "asked-older",
                                      delivered_at="2026-10-18T21:00:00+08:00")
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(before, self.snapshot())

    def test_old_reply_cannot_clear_a_newer_unanswered_question(self):
        self.create()
        self.now.return_value = datetime.fromisoformat("2026-10-28T21:05:00+08:00")
        followups.mark_asked(self.root, "followup-one", "new-check", "dot:new-delivery", "asked-newer",
                             delivered_at="2026-10-25T21:00:00+08:00")
        before = self.snapshot()
        result = followups.reply(self.root, "followup-one", "no_result", "reply-older-than-check",
                                 answered_at="2026-10-18T21:00:00+08:00", source_ref="dot:older-reply")
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(before, self.snapshot())

    def test_no_result_without_new_time_uses_week_not_old_expectation(self):
        self.create(expected_result_at="2026-11-02")
        self.now.return_value = datetime.fromisoformat("2026-10-18T09:00:00+08:00")
        followups.reply(self.root, "followup-one", "no_result", "reply-none")
        self.assertEqual(self.meta()["next_check_at"], "2026-10-25T21:00:00+08:00")
        self.assertEqual(self.meta()["expected_result_at"], "2026-11-02")

    def test_no_result_after_expected_evening_restarts_weekly_interval(self):
        self.create(expected_result_at="2026-10-18")
        self.now.return_value = datetime.fromisoformat("2026-10-18T21:05:00+08:00")
        followups.reply(self.root, "followup-one", "no_result", "reply-after-evening")
        self.assertEqual(self.meta()["next_check_at"], "2026-10-25T21:00:00+08:00")

    def test_no_result_does_not_repeat_on_same_evening(self):
        self.create(expected_result_at="2026-10-18T20:00:00+08:00")
        self.now.return_value = datetime.fromisoformat("2026-10-18T20:30:00+08:00")
        followups.reply(self.root, "followup-one", "no_result", "reply-before-evening")
        self.assertEqual(self.meta()["next_check_at"], "2026-10-25T21:00:00+08:00")

    def test_no_result_new_time_overrides_weekly_default(self):
        self.create(expected_result_at="2026-11-02")
        self.now.return_value = datetime.fromisoformat("2026-10-18T09:00:00+08:00")
        followups.reply(self.root, "followup-one", "no_result", "reply-new-date",
                        expected_result_at="2026-10-30", source_ref="synthetic:new-date")
        self.assertEqual(self.meta()["next_check_at"], "2026-10-30T21:00:00+08:00")
        followups.reply(self.root, "followup-one", "no_result", "reply-explicit-time",
                        next_check_at="2026-10-29T20:00:00+08:00", source_ref="synthetic:new-time")
        self.assertEqual(self.meta()["next_check_at"], "2026-10-29T20:00:00+08:00")

    def test_partial_and_complete_affect_only_one_question(self):
        self.create()
        self.create("long-term-followup")
        with self.assertRaises(ValueError):
            followups.reply(self.root, "followup-one", "partial", "missing-outcome")
        self.outcome()
        other_before = self.meta("long-term-followup")
        followups.reply(self.root, "followup-one", "partial", "reply-partial", outcome_ids=["outcome-one"])
        self.assertEqual(self.meta()["status"], "active")
        self.assertEqual(self.meta()["outcome_refs"], ["outcome-one"])
        followups.reply(self.root, "followup-one", "complete", "reply-complete", outcome_ids=["outcome-one"])
        self.assertEqual(self.meta()["status"], "closed")
        self.assertIsNone(self.meta()["next_check_at"])
        self.assertEqual(self.meta("long-term-followup"), other_before)

    def test_indefinite_pause_and_dated_resume(self):
        self.create()
        followups.reply(self.root, "followup-one", "pause", "pause-one")
        self.assertEqual(self.meta()["status"], "paused")
        self.assertEqual(followups.due(self.root, "2026-11-01T21:00:00+08:00")["due"], [])
        with self.assertRaises(ValueError):
            followups.mark_asked(self.root, "followup-one", "check-paused", "dot:delivery", "asked-paused")
        followups.reply(self.root, "followup-one", "pause", "pause-until", next_check_at="2026-10-20T21:00:00+08:00")
        self.assertEqual(followups.due(self.root, "2026-10-20T21:00:00+08:00")["due"][0]["status"], "paused")
        self.now.return_value = datetime.fromisoformat("2026-10-20T21:01:00+08:00")
        followups.mark_asked(self.root, "followup-one", "check-resume", "dot:delivery:resume", "asked-resume")
        self.assertEqual(self.meta()["status"], "active")
        self.assertEqual(self.meta()["answer_state"], "unknown")

    def test_sunday_merged_review_uses_2200_and_other_days_2230(self):
        (self.root / "settings.json").write_text(json.dumps({
            "timezone": "Asia/Shanghai", "evening_time": "22:30",
            "weekly_review": {"day": "SU", "time": "22:00", "merge_daily_review": True},
        }))
        self.create(expected_result_at="2026-10-11")
        self.assertEqual(self.meta()["next_check_at"], "2026-10-11T22:00:00+08:00")
        self.assertEqual(followups.due(self.root, "2026-10-11T21:59:00+08:00")["status"], "outside_evening_window")
        self.assertEqual(len(followups.due(self.root, "2026-10-11T22:00:00+08:00")["due"]), 1)
        self.assertEqual(followups.due(self.root, "2026-10-12T22:00:00+08:00")["status"], "outside_evening_window")
        self.create("saturday-late", expected_result_at="2026-10-10T23:00:00+08:00")
        self.assertEqual(self.meta("saturday-late")["next_check_at"], "2026-10-11T22:00:00+08:00")
        self.create("sunday-late", expected_result_at="2026-10-11T23:00:00+08:00")
        self.assertEqual(self.meta("sunday-late")["next_check_at"], "2026-10-12T22:30:00+08:00")
        self.now.return_value = datetime.fromisoformat("2026-10-11T22:05:00+08:00")
        followups.reply(self.root, "followup-one", "no_result", "sunday-reply")
        self.assertEqual(self.meta()["next_check_at"], "2026-10-18T22:00:00+08:00")

    def test_stop_and_unavailable_do_not_invent_success(self):
        self.create()
        followups.reply(self.root, "followup-one", "stop", "stop-one")
        self.assertEqual(self.meta()["status"], "closed")
        self.assertEqual(self.meta()["answer_state"], "unknown")
        self.create("unavailable-followup")
        followups.reply(self.root, "unavailable-followup", "unavailable", "unavailable-one")
        self.assertEqual(self.meta("unavailable-followup")["answer_state"], "unavailable")

    def test_retries_replay_original_operation_without_rescheduling(self):
        first = self.create()
        before = self.snapshot()
        self.now.return_value = datetime.fromisoformat("2026-10-21T22:00:00+08:00")
        replay = self.create()
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(replay["files"], first["files"])
        self.assertEqual(before, self.snapshot())
        with self.assertRaises(ValueError):
            self.create(expected_result_at="2026-12-01")
        followups.reply(self.root, "followup-one", "no_result", "reply-repeat")
        before = self.snapshot()
        self.now.return_value = datetime.fromisoformat("2026-10-25T22:00:00+08:00")
        followups.reply(self.root, "followup-one", "no_result", "reply-repeat")
        self.assertEqual(before, self.snapshot())
        self.assertEqual(self.meta()["next_check_at"], "2026-10-28T21:00:00+08:00")

    def test_due_refuses_invalid_evidence_and_is_still_read_only(self):
        self.create()
        path = self.root / "data/followups/followup-one.md"
        meta, body = records.read_document(path)
        meta["case_id"] = "missing-case"
        path.write_text(records.render_document(meta, body), encoding="utf-8")
        before = self.snapshot()
        result = followups.due(self.root, "2026-10-17T21:00:00+08:00")
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(result["due"], [])
        self.assertEqual(before, self.snapshot())


if __name__ == "__main__":
    unittest.main()
