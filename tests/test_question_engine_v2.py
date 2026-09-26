import unittest

from question_engine_v2 import (
    QUICK_TRIAGE_MAX_TURNS,
    coverage_complete,
    decide_progress,
    ensure_coverage,
    explicit_no_data,
    next_question,
    record_answer,
    seed_source_coverage,
)

def make_session(modules=None, turn=0, phase="quick_triage"):
    return {
        "selected_modules": modules or ["problem_unmet_need", "market"],
        "coverage": {},
        "qa": [],
        "turn": turn,
        "phase": phase,
    }

def mark_all(session, status="fact"):
    for row in ensure_coverage(session).values():
        row["assessed"] = True
        row["status"] = status
        row["evidence_origin"] = "test"

class AdaptiveQuestionEngineTests(unittest.TestCase):
    def test_question_has_required_metadata_and_selected_module(self):
        s = make_session(["market"])
        q = next_question(s)
        self.assertEqual(q["module"], "market")
        self.assertIn(q["slot"], {"beachhead", "market_method"})
        self.assertTrue(q["question"])
        self.assertTrue(q["why"])
        self.assertEqual(q["kind"], "required")
        self.assertEqual(q["source_refs"], ["S1V2", "Router"])
        self.assertIn("jurisdiction_guard", q)
        self.assertTrue(
            q["jurisdiction_guard"]["foreign_route_requires_source_ref"]
        )

    def test_precovered_slot_is_skipped(self):
        s = make_session(["problem_unmet_need", "market"])
        accepted = seed_source_coverage(
            s,
            [
                {"module":"problem_unmet_need","slot":"target_problem","status":"fact","quote":"боль врача"},
                {"module":"market","slot":"beachhead","status":"fact","quote":"узкий сегмент"},
            ],
            "Боль врача подтверждена. Наш узкий сегмент — частные клиники.",
        )
        self.assertEqual(accepted, 2)
        self.assertEqual(next_question(s)["slot"], "pain_evidence")
    def test_non_selected_module_cannot_be_seeded_or_asked(self):
        s = make_session(["market"])
        accepted = seed_source_coverage(
            s,
            [{"module":"clinical","slot":"clinical_plan","status":"fact","quote":"protocol"}],
            "protocol",
        )
        self.assertEqual(accepted, 0)
        self.assertEqual(next_question(s)["module"], "market")

    def test_repeated_question_is_not_asked_again(self):
        s = make_session(["market"])
        first = next_question(s)
        s["qa"].append({"question":first["question"]})
        second = next_question(s)
        self.assertNotEqual(first["question"], second["question"])
        self.assertEqual(second["slot"], "market_method")

    def test_no_data_assesses_missing_without_loop(self):
        s = make_session(["market"])
        first = next_question(s)
        self.assertTrue(explicit_no_data("нет данных"))
        record_answer(
            s, first, "нет данных",
            {"status":"missing","quote":"нет данных","contradiction":""},
        )
        row = s["coverage"]["market.beachhead"]
        self.assertTrue(row["assessed"])
        self.assertEqual(row["status"], "missing")
        self.assertNotEqual(next_question(s)["slot"], "beachhead")

    def test_explicit_na_requires_reason_and_counts_as_assessed(self):
        s = make_session(["market"])
        first = next_question(s)
        record_answer(
            s, first, "не применимо: рынок регулируется единым тарифом",
            {"status":"weak","quote":"","contradiction":""},
        )
        row = s["coverage"]["market.beachhead"]
        self.assertEqual(row["applicability"], "not_applicable")
        self.assertTrue(row["na_reason"])
        self.assertEqual(row["status"], "missing")

    def test_question_thirteen_enters_full_readiness(self):
        s = make_session(["problem_unmet_need","market"], turn=QUICK_TRIAGE_MAX_TURNS)
        decision = decide_progress(s)
        self.assertFalse(decision["done"])
        self.assertEqual(s["phase"], "full_readiness")
        self.assertIsNotNone(next_question(s))
    def test_founder_stop_has_canonical_reason(self):
        s = make_session(turn=13, phase="full_readiness")
        decision = decide_progress(s, founder_stop=True)
        self.assertEqual(decision, {"done":True,"stop_reason":"founder_stop"})

    def test_full_coverage_stops_with_canonical_reason(self):
        s = make_session(turn=12)
        mark_all(s)
        self.assertTrue(coverage_complete(s))
        decision = decide_progress(s)
        self.assertTrue(decision["done"])
        self.assertEqual(decision["stop_reason"], "required_coverage_complete")

    def test_weak_followup_precedes_completion(self):
        s = make_session(["market"], turn=8)
        mark_all(s)
        row = s["coverage"]["market.beachhead"]
        row["status"] = "weak"
        self.assertTrue(coverage_complete(s))
        decision = decide_progress(s)
        self.assertFalse(decision["done"])
        q = next_question(s)
        self.assertEqual(q["kind"], "follow_up")
        self.assertEqual(q["slot"], "beachhead")
        record_answer(
            s, q, "договор пилота",
            {"status":"fact","quote":"договор пилота","contradiction":""},
        )
        decision = decide_progress(s)
        self.assertTrue(decision["done"])
        self.assertEqual(decision["stop_reason"], "required_coverage_complete")

    def test_only_existing_evidence_statuses_are_used(self):
        s = make_session(["market"])
        q = next_question(s)
        record_answer(
            s, q, "ответ",
            {"status":"unsupported-new-status","quote":"ответ","contradiction":""},
        )
        self.assertEqual(s["coverage"]["market.beachhead"]["status"], "weak")

    def test_marginal_value_low_when_no_nonrepeated_question_exists(self):
        s = make_session(["market"], phase="full_readiness", turn=13)
        for _ in range(2):
            q = next_question(s)
            s["qa"].append({"question":q["question"]})
        decision = decide_progress(s)
        self.assertTrue(decision["done"])
        self.assertEqual(decision["stop_reason"], "marginal_value_low")

if __name__ == "__main__":
    unittest.main()
