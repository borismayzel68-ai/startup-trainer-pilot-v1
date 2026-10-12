"""HQ-042 (P0): из E1 нельзя попасть в E3, минуя E2; основатель получает полный пакет. Только синтетика, LLM — mock."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import hq016_cases as h  # noqa: E402
import test_s1_intake as base  # noqa: E402
from founder_package import build_package, validate_package  # noqa: E402
from s1_intake.models import Category  # noqa: E402
from s1_intake.scoring import apply_fact_signoffs, enforce_rules  # noqa: E402

LABEL = "ИИ-оценка, не инвестиционная рекомендация"
SLOT = "traction.traction_metrics"


def one_claim(level, sources):
    fx = base.cls_fixture()
    c = fx.claims[0]
    c.slot, c.evidence_level, c.sources, c.conflicts_with = SLOT, level, sources, []
    c.category = Category.FACT if level >= 3 else Category.FOUNDER_CLAIM
    fx.claims = [c]
    return fx


def enforce(level, sources, available):
    out = enforce_rules(one_claim(level, sources), base.M, base.CHECKLIST, available)
    return out.claims[0]


class E1ToE3GateTests(unittest.TestCase):
    def test_model_claiming_e3_from_non_evidence_sources_is_capped_below_e3(self):
        caps = base.M["source_level_cap"]
        for src in ("form", "deck", "model"):
            c = enforce(3, [src], {"form", "deck", "model"})
            self.assertEqual(c.evidence_level, caps[src], src)
            self.assertLess(c.evidence_level, 3, src)
            self.assertNotIn(c.category, (Category.FACT, Category.FACT_CANDIDATE), src)

    def test_deck_claim_e1_cannot_jump_to_e3_even_with_signoff(self):
        c = enforce(3, ["deck"], {"form", "deck"})
        self.assertEqual(c.evidence_level, 1)
        signoffs = {SLOT: {"role": "analyst", "at": "2026-10-12"}}
        out = apply_fact_signoffs(one_claim(1, ["deck"]), signoffs, 3)
        self.assertNotEqual(out.claims[0].category, Category.FACT)
        self.assertIsNone(out.claims[0].verified_by)

    def test_e3_reachable_only_with_third_party_document_source(self):
        # E2 (модель с формулами) до E3 не поднимается; E3 — только источник evidence, и то максимум кандидат в факт
        self.assertEqual(enforce(3, ["model"], {"form", "model"}).evidence_level, 2)
        self.assertEqual(enforce(3, ["deck", "model"], {"form", "deck", "model"}).evidence_level, 2)
        self.assertEqual(enforce(3, ["evidence"], {"form", "evidence"}).category, Category.FACT_CANDIDATE)

    def test_dialogue_answer_without_file_stays_below_e3(self):
        self.assertLess(int(base.M["dialogue_questions_s1"]["text_answer_level"]), 3)

    def test_founder_package_blocks_e3_and_fact(self):
        s = {"id": "s-042", "founder_name": "Тест", "description": "10 клиентов", "qa": [], "project_id": "VC-042",
             "project_name": "Синтетика"}
        report = {"facts": ["10 клиентов"], "claims": [], "coverage": {"required": 1, "required_assessed": 1}}
        p = build_package(s, report, "https://example.test", "2026-10-12T10:00:00Z")
        self.assertTrue(validate_package(p))
        self.assertTrue(all(e["evidence_level"] in ("E0", "E1") for e in p["evidence_delta"]))
        for bad in ({"evidence_level": "E3"}, {"status": "fact"}):
            q = dict(p, evidence_delta=[dict(p["evidence_delta"][0], **bad)])
            with self.assertRaises(ValueError):
                validate_package(q)


class FounderFullPackageTests(unittest.TestCase):
    def test_score_three_gaps_risks_and_ai_label(self):
        res, pack, _ = h.run(h.LS["ls_preclinical_drug"], h.ls_classification())
        fp = pack["free_part"]
        self.assertEqual(fp["label"], LABEL)
        self.assertEqual(fp["score"], res.scores.readiness)
        self.assertEqual(len(fp["gaps"]), 3)
        for g in fp["gaps"]:
            self.assertTrue(g["gap"].strip() and g["risk"].strip())
            self.assertIn(g["risk"], fp["html"])
        self.assertIn(LABEL, fp["html"])
        self.assertIn(f"{res.scores.readiness} / 100", fp["html"])


if __name__ == "__main__":
    unittest.main()
