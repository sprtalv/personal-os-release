"""Behavior tests for local record integrity and recoverable writes."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / ".agents/skills/personal-os/scripts/records.py"
SPEC = importlib.util.spec_from_file_location("records", SCRIPT)
records = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(records)


def base_record(record_id="event-one", **overrides):
    meta = {
        "schema_version": 1, "id": record_id, "document_type": "record",
        "recorded_at": "2000-01-01T00:00:00+08:00", "updated_at": "2000-01-01T00:00:00+08:00",
        "kind": "experience", "author": "user", "basis": "user_statement",
        "domains": ["study_research"], "status": "active",
        "source": {"ref": "message:one", "message_at": "2026-10-09T21:00:00+08:00", "excerpt": "昨天阅读结束较晚。"},
        "time": {"event": "2026-10-08", "expressed": "2026-10-09T21:00:00+08:00", "target": None, "precision": "day", "note": None},
        "related": [],
    }
    meta.update(overrides)
    return meta


def change(meta, body="## 内容\n阅读结束较晚，笔记未完成。", path=None, expected=None):
    if path is None:
        prefixes = {"record": "data/records/2026-10", "summary": "data/current", "case": "data/cases", "followup": "data/followups", "report": "reports"}
        path = f"{prefixes[meta['document_type']]}/{meta['id']}.md"
    return {"path": path, "metadata": deepcopy(meta), "body": body, "expected_sha256": expected}


def summary(record_ids):
    return {"schema_version": 1, "id": "current-one", "document_type": "summary", "kind": "current", "record_refs": record_ids}


class RecordsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def apply(self, changes, op="test-op"):
        return records.apply_bundle(self.root, {"changes": changes}, op)

    def existing(self, relative):
        path = self.root / relative
        meta, body = records.read_document(path)
        return change(meta, body, relative, hashlib.sha256(path.read_bytes()).hexdigest())

    def test_real_save_clock_and_preserved_recorded_time(self):
        before = datetime.now(timezone.utc)
        item = change(base_record())
        receipt = self.apply([item])
        after = datetime.now(timezone.utc)
        meta, _ = records.read_document(self.root / item["path"])
        self.assertLessEqual(before, datetime.fromisoformat(meta["recorded_at"]))
        self.assertLessEqual(datetime.fromisoformat(meta["recorded_at"]), after)
        self.assertTrue(meta["recorded_at"].endswith("+08:00"))
        self.assertEqual(meta["time"]["event"], "2026-10-08")
        self.assertEqual(receipt["status"], "local_saved")
        self.assertFalse(receipt["remote_verified"])
        update = self.existing(item["path"])
        update["metadata"]["recorded_at"] = "1999-01-01T00:00:00+08:00"
        self.apply([update], "update-op")
        newer, _ = records.read_document(self.root / item["path"])
        self.assertEqual(newer["recorded_at"], meta["recorded_at"])
        self.assertGreaterEqual(newer["updated_at"], meta["updated_at"])

    def test_idempotent_replay_and_input_mismatch(self):
        item = change(base_record())
        original = self.apply([item])
        content = (self.root / item["path"]).read_bytes()
        again = self.apply([item])
        self.assertTrue(again["idempotent_replay"])
        self.assertEqual(original["files"], again["files"])
        self.assertEqual(content, (self.root / item["path"]).read_bytes())
        self.assertEqual(records.get_operation_bundle(self.root, "test-op"), {"changes": [item]})
        self.assertEqual(records.get_operation_receipt(self.root, "test-op"), original)
        item["body"] += "新增内容"
        with self.assertRaisesRegex(ValueError, "different input"):
            self.apply([item])

    def test_cas_rejects_missing_or_stale_expected_hash(self):
        item = change(base_record())
        self.apply([item])
        with self.assertRaisesRegex(ValueError, "expected_sha256 mismatch"):
            self.apply([item], "overwrite")
        update = self.existing(item["path"])
        (self.root / item["path"]).write_text((self.root / item["path"]).read_text() + "\n用户后来补充。\n")
        with self.assertRaisesRegex(ValueError, "expected_sha256 mismatch"):
            self.apply([update], "stale-update")

    def test_dangling_reference_blocks_all_personal_writes(self):
        first = change(base_record())
        bad = change(base_record("event-two", related=["missing-record"]))
        with self.assertRaisesRegex(ValueError, "Dangling"):
            self.apply([first, bad])
        self.assertFalse((self.root / "data").exists())
        self.assertFalse((self.root / "operations/changes/test-op.json").exists())

    def test_global_duplicate_id_and_wrong_directory(self):
        first = change(base_record())
        duplicate = change(base_record(), path="data/records/2026-09/event-one.md")
        with self.assertRaisesRegex(ValueError, "Duplicate global id"):
            self.apply([first, duplicate])
        with self.assertRaisesRegex(ValueError, "does not match directory"):
            self.apply([change(base_record(), path="data/cases/event-one.md")])

    def test_correction_requires_current_summary_to_change_in_same_bundle(self):
        item = change(base_record())
        current = change(summary(["event-one"]))
        self.apply([item, current])
        corrected = self.existing(item["path"])
        corrected["metadata"]["status"] = "corrected"
        with self.assertRaisesRegex(ValueError, "invalidated record"):
            self.apply([corrected], "bad-correction")
        replacement = change(base_record("event-two", kind="correction", related=["event-one"]))
        refreshed = self.existing(current["path"])
        refreshed["metadata"]["record_refs"] = ["event-two"]
        self.apply([corrected, replacement, refreshed], "good-correction")
        self.assertEqual(records.validate_root(self.root), [])

    def test_pending_hypothesis_cannot_be_in_current_summary(self):
        hypothesis = base_record("hypothesis-one", kind="hypothesis", author="ai", basis="ai_inference", user_endorsement="pending", evidence_state="untested")
        with self.assertRaisesRegex(ValueError, "unaccepted hypothesis"):
            self.apply([change(hypothesis), change(summary([hypothesis["id"]]))])
        hypothesis["user_endorsement"] = "accepted"
        self.apply([change(hypothesis), change(summary([hypothesis["id"]]))])
        saved, _ = records.read_document(self.root / "data/records/2026-10/hypothesis-one.md")
        self.assertEqual(saved["evidence_state"], "untested")

    def test_case_link_types_and_authors(self):
        case = {"schema_version": 1, "id": "case-one", "document_type": "case", "kind": "decision", "status": "open", "record_refs": ["event-one"], "user_hypothesis_refs": [], "ai_hypothesis_refs": [], "outcome_refs": ["event-one"]}
        with self.assertRaisesRegex(ValueError, "outcome_refs must reference outcome"):
            self.apply([change(base_record()), change(case)])
        case["outcome_refs"] = []
        case["ai_hypothesis_refs"] = ["event-one"]
        with self.assertRaisesRegex(ValueError, "ai hypotheses"):
            self.apply([change(base_record()), change(case)])

    def test_unsafe_paths_and_symlink_targets(self):
        for path in ("../outside.md", "/tmp/outside.md", "data/records/2026-10/../event-one.md", ".agents/skills/personal-os/SKILL.md", "data\\records\\2026-10\\event-one.md"):
            with self.subTest(path=path):
                with self.assertRaises(ValueError):
                    self.apply([change(base_record(), path=path)])
        outside = self.root / "outside"
        outside.mkdir()
        (self.root / "data").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "Symlink"):
            self.apply([change(base_record())])
        self.assertEqual(list(outside.iterdir()), [])

    def test_symlink_operation_journal_and_index_are_rejected(self):
        outside = self.root / "outside"
        outside.mkdir()
        (self.root / "operations").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "Symlink"):
            self.apply([change(base_record())])
        (self.root / "operations").unlink()
        (self.root / "generated").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "Symlink"):
            records.build_index(self.root)

    def test_crash_recovery_completes_only_intended_content(self):
        first, second = change(base_record()), change(base_record("event-two"))
        original_atomic_write = records._atomic_write
        def fail_second(path, data):
            if path.name == "event-two.md":
                raise OSError("simulated disk outage")
            return original_atomic_write(path, data)
        with patch.object(records, "_atomic_write", side_effect=fail_second):
            with self.assertRaisesRegex(OSError, "disk outage"):
                self.apply([first, second])
        self.assertTrue((self.root / first["path"]).exists())
        self.assertFalse((self.root / second["path"]).exists())
        self.assertIsNone(records.get_operation_receipt(self.root, "test-op"))
        self.apply([first, second])
        self.assertEqual(records.validate_root(self.root), [])
        self.assertIsNotNone(records.get_operation_receipt(self.root, "test-op"))

    def test_crash_recovery_refuses_unrelated_edits(self):
        first, second = change(base_record()), change(base_record("event-two"))
        original_atomic_write = records._atomic_write
        def fail_second(path, data):
            if path.name == "event-two.md":
                raise OSError("simulated crash")
            return original_atomic_write(path, data)
        with patch.object(records, "_atomic_write", side_effect=fail_second):
            with self.assertRaises(OSError):
                self.apply([first, second])
        path = self.root / first["path"]
        path.write_text(path.read_text() + "\n另一项编辑。\n")
        with self.assertRaisesRegex(ValueError, "unrelated edit"):
            self.apply([first, second])
        self.assertFalse((self.root / second["path"]).exists())

    def test_incomplete_bundle_blocks_new_operations_and_read_models(self):
        first, second = change(base_record()), change(base_record("event-two"))
        original = records._atomic_write
        def fail_second(path, data):
            if path.name == "event-two.md":
                raise OSError("simulated crash")
            return original(path, data)
        with patch.object(records, "_atomic_write", side_effect=fail_second):
            with self.assertRaises(OSError):
                self.apply([first, second])
        update = self.existing(first["path"])
        update["body"] += "不能让新操作接着修改半个包。"
        with self.assertRaisesRegex(ValueError, "unfinished operation"):
            self.apply([update], "new-op")
        self.assertTrue(any("unfinished operation" in e for e in records.validate_root(self.root)))
        for read_model in (lambda: records.search(self.root, "阅读"), lambda: records.build_index(self.root)):
            with self.assertRaisesRegex(ValueError, "unfinished operation"):
                read_model()
        self.apply([first, second])
        self.assertEqual(records.validate_root(self.root), [])

    def test_keyword_search_and_historical_filter(self):
        old = change(base_record("old-event", status="superseded"), "## 内容\n阅读影响精力。")
        new = change(base_record("new-event"), "## 内容\n阅读并不是精力变化的唯一因素。")
        self.apply([old, new])
        result = records.search(self.root, "阅读")
        self.assertEqual([r["id"] for r in result["results"]], ["new-event"])
        self.assertEqual(records.search(self.root, "阅读", True)["count"], 2)
        self.assertEqual(records.search(self.root, "不存在 精力")["count"], 1)
        self.assertEqual(records.search(self.root, "毫无匹配")["count"], 0)
        self.assertEqual(result["search_method"], "keyword")
        index = records.build_index(self.root)
        self.assertEqual(index["count"], 2)
        self.assertEqual(len(json.loads(Path(index["path"]).read_text())["documents"]), 2)

    def test_unknown_source_and_time_precision(self):
        meta = base_record(source={"ref": None, "message_at": None, "excerpt": "本人原话"})
        self.assertTrue(any("Unknown source" in e for e in records.validate_document(meta, "正文")))
        self.assertEqual(records.validate_document(meta, "## 信息边界\n来源不可用，仅保留本人原话。"), [])
        meta["time"]["event"] = "2026-10-08T12:34:00+08:00"
        self.assertTrue(any("precision" in e for e in records.validate_document(meta, "来源不可用")))

    def test_duplicate_yaml_keys_are_rejected(self):
        path = self.root / "bad.md"
        path.write_text("---\nid: one\nid: two\n---\n内容\n")
        with self.assertRaisesRegex(ValueError, "Duplicate YAML"):
            records.read_document(path)

    def test_malformed_enum_types_return_validation_errors(self):
        for field in ("document_type", "kind", "author", "basis", "status"):
            with self.subTest(field=field):
                self.assertTrue(records.validate_document(base_record(**{field: []}), "正文"))
        meta = base_record()
        meta["time"]["precision"] = []
        self.assertTrue(records.validate_document(meta, "正文"))


if __name__ == "__main__":
    unittest.main()
