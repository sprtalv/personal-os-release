"""Synthetic checks for evidence, advice, revisions and correction impact."""
import hashlib
from pathlib import Path
import tempfile
import unittest

from test_records import base_record, change, records, summary


def insight(identifier, evidence, **overrides):
    meta = base_record(identifier, kind="insight", author="ai", basis="ai_inference",
                       context="Synthetic short study session", valid_from="2026-10-01",
                       valid_until=None, user_endorsement="pending", evidence_state="untested",
                       evidence_refs=[evidence], counterexample_refs=[], revises=[], unknowns=["Long-term effect unknown"])
    meta.update(overrides)
    return meta


def recommendation(identifier, evidence, **overrides):
    meta = base_record(identifier, kind="recommendation", author="ai", basis="ai_inference",
                       context="Synthetic reading session", expected_result="Start reading one paragraph",
                       evidence_refs=[evidence], counterexample_refs=[], process_refs=[], outcome_refs=[],
                       side_effect_refs=[], revision_refs=[], revises=[], unknowns=["Effect unknown"],
                       adoption_state="unknown", adoption_ref=None)
    meta.update(overrides)
    return meta


class InsightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def apply(self, items, operation):
        return records.apply_bundle(self.root, {"changes": items}, operation)

    def update(self, relative):
        path = self.root / relative
        meta, body = records.read_document(path)
        return change(meta, body, relative, hashlib.sha256(path.read_bytes()).hexdigest())

    def test_old_records_remain_valid_and_insight_needs_evidence(self):
        self.apply([change(base_record("source-one"))], "old-save")
        self.assertEqual(records.validate_root(self.root), [])
        weak = insight("claim-one", "source-one", evidence_refs=[])
        self.assertTrue(any("evidence_refs" in error for error in records.validate_document(weak, "Synthetic claim")))

    def test_revised_claim_requires_old_version_retired_and_current_blocks_bad_claim(self):
        original = change(base_record("source-one"))
        old = change(insight("old-claim", "source-one", user_endorsement="accepted", evidence_state="supported"))
        self.apply([original, old], "seed")
        revised = change(insight("new-claim", "source-one", revises=["old-claim"],
                                 user_endorsement="accepted", evidence_state="mixed"))
        with self.assertRaisesRegex(ValueError, "must be corrected or superseded"):
            self.apply([revised], "premature-revision")
        retirement = self.update(old["path"])
        retirement["metadata"]["status"] = "superseded"
        receipt = self.apply([retirement, revised], "revise")
        self.assertEqual(receipt["status"], "local_saved")
        self.assertEqual(self.apply([retirement, revised], "revise")["idempotent_replay"], True)
        bad = self.update(revised["path"])
        bad["metadata"]["evidence_state"] = "contradicted"
        current = change(summary(["new-claim"]), "Synthetic current summary")
        with self.assertRaisesRegex(ValueError, "contradicted claim"):
            self.apply([bad, current], "wrong-promotion")
        pending = self.update(revised["path"])
        pending["metadata"]["user_endorsement"] = "pending"
        with self.assertRaisesRegex(ValueError, "unaccepted insight"):
            self.apply([pending, current], "unaccepted-promotion")

    def test_contradicted_user_accepted_hypothesis_stays_out_of_current(self):
        source = change(base_record("source-one"))
        hypothesis = change(base_record("hypothesis-one", kind="hypothesis", author="ai", basis="ai_inference",
                                        user_endorsement="accepted", evidence_state="contradicted"))
        self.apply([source, hypothesis], "hypothesis-seed")
        with self.assertRaisesRegex(ValueError, "contradicted claim"):
            self.apply([change(summary(["hypothesis-one"]), "A current profile")], "false-current")
        revision = change(insight("revised-insight", "source-one", revises=["hypothesis-one"],
                                  evidence_state="mixed", user_endorsement="accepted"))
        old = self.update(hypothesis["path"])
        old["metadata"]["status"] = "superseded"
        self.apply([old, revision], "revise-hypothesis")
        self.assertEqual(records.validate_root(self.root), [])

    def test_recommendation_unknown_declined_adopted_and_result_links(self):
        source = change(base_record("source-one"))
        advice = change(recommendation("advice-one", "source-one"))
        self.apply([source, advice], "seed")
        meta, _ = records.read_document(self.root / advice["path"])
        self.assertEqual(meta["adoption_state"], "unknown")
        invalid = self.update(advice["path"])
        invalid["metadata"]["adoption_state"] = "declined"
        with self.assertRaisesRegex(ValueError, "requires adoption_ref"):
            self.apply([invalid], "declined-without-source")
        user_choice = change(base_record("user-choice", kind="decision"), "Synthetic user declined")
        invalid["metadata"]["adoption_ref"] = "user-choice"
        self.apply([user_choice, invalid], "declined")
        outcome = change(base_record("outcome-one", kind="outcome"), "Synthetic short result")
        adopted_choice = change(base_record("adopted-choice", kind="decision"), "Synthetic user chose to try")
        adopted = self.update(advice["path"])
        adopted["metadata"].update(adoption_state="adopted", adoption_ref="adopted-choice", outcome_refs=["outcome-one"],
                                   counterexample_refs=["source-one"], side_effect_refs=["outcome-one"])
        adopted["metadata"]["related"].append("user-choice")
        self.apply([outcome, adopted_choice, adopted], "adopted-with-result")
        self.assertEqual(records.validate_root(self.root), [])
        altered = self.update(advice["path"])
        altered["metadata"]["expected_result"] = "A different promised result"
        with self.assertRaisesRegex(ValueError, "Original recommendation expected_result cannot change"):
            self.apply([altered], "rewrite-expectation")
        self_citing = self.update(advice["path"])
        self_citing["metadata"]["evidence_refs"] = ["advice-one"]
        with self.assertRaisesRegex(ValueError, "Original recommendation evidence_refs cannot change"):
            self.apply([self_citing], "self-citation-update")

    def test_new_claim_cannot_use_ai_advice_as_base_evidence(self):
        source = change(base_record("source-one"))
        advice = change(recommendation("advice-one", "source-one"))
        false_insight = change(insight("claim-one", "advice-one"))
        with self.assertRaisesRegex(ValueError, "must reference base evidence"):
            self.apply([source, advice, false_insight], "ai-citation-loop")

    def test_invalidated_evidence_blocks_active_advice_but_allows_retired_history(self):
        source = change(base_record("source-one"))
        advice = change(recommendation("advice-one", "source-one"))
        self.apply([source, advice], "seed")
        retired_source = self.update(source["path"])
        retired_source["metadata"]["status"] = "corrected"
        with self.assertRaisesRegex(ValueError, "cannot rely on invalidated"):
            self.apply([retired_source], "bad-source-correction")
        retired_advice = self.update(advice["path"])
        retired_advice["metadata"]["status"] = "superseded"
        self.apply([retired_source, retired_advice], "correct-source-and-advice")
        self.assertEqual(records.validate_root(self.root), [])

    def test_case_links_advice_process_result_side_effect_and_revised_insight(self):
        source = change(base_record("source-one"))
        choice = change(base_record("user-choice", kind="decision"), "Synthetic user chose the suggestion")
        outcome = change(base_record("outcome-one", kind="outcome"), "Synthetic partial benefit and cost")
        revised = change(insight("revised-understanding", "outcome-one", evidence_state="mixed",
                                 user_endorsement="accepted", counterexample_refs=["source-one"]))
        advice = change(recommendation("advice-one", "source-one", adoption_state="adopted",
                                       adoption_ref="user-choice", process_refs=["user-choice"],
                                       outcome_refs=["outcome-one"], side_effect_refs=["outcome-one"],
                                       revision_refs=["revised-understanding"]))
        case = change({"schema_version": 1, "id": "case-one", "document_type": "case", "kind": "decision",
                       "status": "observing", "record_refs": ["source-one", "user-choice"],
                       "user_hypothesis_refs": [], "ai_hypothesis_refs": [], "outcome_refs": ["outcome-one"],
                       "insight_refs": ["revised-understanding"], "recommendation_refs": ["advice-one"],
                       "purpose": "Synthetic goal", "investment_boundary": "One short trial",
                       "pause_condition": "Stop if cost rises"}, "Synthetic case with explicit tradeoff")
        self.apply([source, choice, outcome, revised, advice, case], "complete-chain")
        self.assertEqual(records.validate_root(self.root), [])

    def test_correction_reports_case_and_report_to_review(self):
        old = change(base_record("old-fact"))
        new = change(base_record("new-fact", kind="correction"))
        case = change({"schema_version": 1, "id": "case-one", "document_type": "case", "kind": "decision",
                       "status": "observing", "record_refs": ["old-fact"], "user_hypothesis_refs": [],
                       "ai_hypothesis_refs": [], "outcome_refs": []}, "Old fact appeared in the case")
        report = change({"schema_version": 1, "id": "report-one", "document_type": "report", "kind": "periodic",
                         "record_refs": ["old-fact"]}, "Old fact informed this report")
        current = change(summary(["old-fact"]), "Current fact")
        self.apply([old, case, report, current], "seed")
        retired = self.update(old["path"])
        retired["metadata"]["status"] = "corrected"
        with self.assertRaisesRegex(ValueError, "Current summary references invalidated"):
            self.apply([retired, new], "bad-correction")
        updated_current = self.update(current["path"])
        updated_current["metadata"]["record_refs"] = ["new-fact"]
        receipt = self.apply([retired, new, updated_current], "correction")
        review = receipt["review_required"][0]
        paths = {item["path"] for item in review["review_required"]}
        self.assertIn(case["path"], paths)
        self.assertIn(report["path"], paths)
        self.assertEqual(records.impact(self.root, "old-fact")["review_required"], review["review_required"])
        self.assertEqual(records.validate_root(self.root), [])


if __name__ == "__main__":
    unittest.main()
