"""HQ-016 v1.1: «не применимо по стадии ≠ 0» для LS. Тесты T1–T8 (синтетические кейсы, LLM — mock)."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hq016_cases as h  # noqa: E402

IT_SLOTS = ("traction.traction_metrics", "traction.retention", "business_model.unit_economics")
K_KEYS = ("ls_milestones_product.k1_clinical_stage", "ls_milestones_risks.k2_regulatory_path",
          "ls_milestones_risks.k3_ip", "ls_milestones_product.k4_science_poc",
          "ls_milestones_product.k5_manufacturing", "ls_milestones_market.k6_reimbursement")
FROZEN_WEIGHTS = {"team": 0.15, "problem_market": 0.15, "product": 0.15, "traction": 0.20,
                  "business_model": 0.10, "finance_round": 0.15, "risks_legal": 0.10}
FROZEN_THRESHOLDS = {"critical_item_weight": 2.0, "pass_min_substantive_claims": 3, "next_min": 45,
                     "max_critical_gaps_for_next": 1}
# «До HQ-016» (базовый коммит 0f72b0a, тот же синтетический вход): readiness 10 для всех LS-кейсов
BEFORE_LS = {"readiness": 10, "team": 0.55, "problem_market": 0.071, "finance_round": 0.062}


def items(res):
    return {it.key: it for b in res.scores.blocks for it in b.items}


def manual_readiness(res, m):
    w = {b["key"]: b["weight"] for b in m["blocks"]}
    excl = set((res.scores.ls or {}).get("excluded_blocks", []))
    blocks = [b for b in res.scores.blocks if b.key not in excl]
    return int(round(100 * sum(w[b.key] * b.score for b in blocks) / sum(w[b.key] for b in blocks)))


class T1PreRegistration(unittest.TestCase):
    def test_drug_preclinical_it_slots_na_not_zero(self):
        res, pack, m = h.run(h.LS["ls_preclinical_drug"], h.ls_classification())
        it, ls = items(res), res.scores.ls
        for k in IT_SLOTS:
            self.assertEqual(it[k].applicability, "N_A_BY_STAGE", k)
            self.assertTrue(it[k].na_reason)
            self.assertIn("доклиника", it[k].na_reason)
            self.assertNotIn(k, res.scores.critical_gaps)
        self.assertTrue(ls["applied"])
        self.assertLess(ls["applicable_weight"], 1.0)
        self.assertAlmostEqual(ls["applicable_weight"], 0.80)  # выпал блок traction (0.20); 0.76 — для 8 измерений
        self.assertEqual(ls["excluded_blocks"], ["traction"])
        self.assertEqual(res.scores.readiness, manual_readiness(res, m))
        self.assertGreater(res.scores.readiness, BEFORE_LS["readiness"])
        # К1–К6 в чек-листе и оценены по общей шкале E0–E4
        self.assertEqual(sorted(x["key"] for x in ls["milestones"]), sorted(K_KEYS))
        self.assertEqual([x["k"] for x in ls["milestones"]], ["К1", "К2", "К3", "К4", "К5", "К6"])
        scored = {x["key"]: x for x in ls["milestones"]}
        self.assertEqual(scored["ls_milestones_product.k4_science_poc"]["applicability"], "SCORED")
        self.assertEqual(scored["ls_milestones_product.k5_manufacturing"]["applicability"], "MISSING")
        self.assertRegex(ls["summary_line"], r"\d+ из \d+ применимых слотов · не применимо по стадии: ")
        self.assertIn(ls["summary_line"], pack["founder_report_html"])

    def test_medical_device_prototype_slot_level_na(self):
        res, _, m = h.run(h.LS["ls_md_prototype"], h.ls_classification())
        it, ls = items(res), res.scores.ls
        for k in IT_SLOTS:
            self.assertEqual(it[k].applicability, "N_A_BY_STAGE")
        # у МИ в блоке traction остаются pilots_workflow.* → блок не выпадает, N/A — на уровне слота
        self.assertEqual(ls["excluded_blocks"], [])
        traction = next(b for b in res.scores.blocks if b.key == "traction")
        self.assertTrue(any(i.applicability != "N_A_BY_STAGE" for i in traction.items))
        self.assertEqual(res.scores.readiness, manual_readiness(res, m))
        self.assertEqual(ls["slots_applicable"], sum(1 for i in it.values() if i.applicability in ("SCORED", "MISSING")))


class T2PostRegistration(unittest.TestCase):
    def test_registered_scored_normally(self):
        res, _, _ = h.run(h.LS["ls_registered"], h.ls_classification())
        it = items(res)
        self.assertFalse(res.scores.ls["applied"])
        for k in IT_SLOTS:
            self.assertIn(it[k].applicability, ("SCORED", "MISSING"))
        self.assertFalse(any(k in it for k in K_KEYS))
        plain = dict(h.LS["ls_registered"])
        plain.pop("ls_stage")
        base, _, _ = h.run(plain, h.ls_classification())
        self.assertEqual(res.scores.readiness, base.scores.readiness)
        self.assertEqual([(b.key, b.score) for b in res.scores.blocks], [(b.key, b.score) for b in base.scores.blocks])
        self.assertEqual(res.scores.verdict, base.scores.verdict)


class T3NonLsGolden(unittest.TestCase):
    def test_byte_identical_to_pre_hq016(self):
        for name, form in h.NON_LS.items():
            with self.subTest(name):
                got = json.dumps(h.snapshot(form), ensure_ascii=False, indent=1, sort_keys=True)
                want = (Path(__file__).resolve().parent / "golden" / f"hq016_{name}.json").read_text(encoding="utf-8")
                self.assertEqual(got, want)

    def test_non_ls_with_stage_field_unchanged(self):
        form = {**h.NON_LS["it_wellness"], "ls_stage": "preclinical"}
        got = h.snapshot(form)
        want = json.loads((Path(__file__).resolve().parent / "golden" / "hq016_it_wellness.json").read_text(encoding="utf-8"))
        self.assertEqual(got["scores"], want["scores"])
        self.assertEqual(got["founder_report_html"], want["founder_report_html"])
        self.assertNotIn("Life Science", got["founder_report_html"])


class T4ClaimedRevenue(unittest.TestCase):
    def test_claimed_revenue_scored_low_not_na(self):
        deck_claim = dict(h.REVENUE_CLAIM, id="C11", sources=["deck"], evidence_level=3, category="FACT",
                          slot="business_model.unit_economics")
        res, _, _ = h.run(h.LS["ls_preclinical_drug"], h.ls_classification([h.REVENUE_CLAIM, deck_claim]))
        it = items(res)
        self.assertFalse(res.scores.ls["applied"])
        self.assertIn("заявил выручку", res.scores.ls["reason"])
        self.assertFalse(any(i.applicability == "N_A_BY_STAGE" for i in it.values()))
        self.assertEqual(it["traction.traction_metrics"].applicability, "SCORED")
        self.assertEqual(it["traction.traction_metrics"].best_level, 0)          # анкета → E0, без цитаты 0
        self.assertEqual(it["traction.traction_metrics"].status.value, "FOUNDER_CLAIM")
        self.assertEqual(it["business_model.unit_economics"].best_level, 1)      # дек → не выше E1
        self.assertEqual(it["business_model.unit_economics"].status.value, "FOUNDER_CLAIM")  # E3 не ставится


class T5NoGate(unittest.TestCase):
    def test_no_gate_no_na_missing_zero(self):
        res, _, _ = h.run(h.LS["ls_no_gate"], h.ls_classification())
        it = items(res)
        self.assertFalse(res.scores.ls["applied"])
        self.assertIn("гейт", res.scores.ls["reason"])
        for k in ("traction.traction_metrics", "business_model.unit_economics"):
            self.assertEqual(it[k].applicability, "MISSING")
            self.assertIsNone(it[k].best_level)
        self.assertFalse(any(k in it for k in K_KEYS))
        self.assertEqual(res.scores.readiness, BEFORE_LS["readiness"])


class T6Barriers(unittest.TestCase):
    def test_na_visible_barriers_and_one_artifact(self):
        res, pack, _ = h.run(h.LS["ls_preclinical_drug"], h.ls_classification())
        html, ls = pack["founder_report_html"], res.scores.ls
        for n in ls["na_slots"]:
            for field in ("label", "na_reason", "future_milestone", "risk"):
                self.assertIn(n[field], html)
        self.assertEqual(len(ls["na_slots"]), 3)
        self.assertTrue(2 <= len(ls["barriers"]) <= 5)
        self.assertEqual(html.count("Один следующий артефакт (К7)"), 1)
        self.assertIn(ls["next_artifact"]["artifact"], html)
        for bad in ("100 клиентов", "MRR", "pre-orders"):
            self.assertNotIn(bad, ls["next_artifact"]["artifact"])
        for leak in ("PASS", "HOLD", "NEXT"):
            self.assertNotIn(leak, html)

    def test_barriers_at_least_two_when_all_k_strong(self):
        cls = h.ls_classification()
        for c in cls["claims"]:
            if c["slot"].startswith("ls_milestones"):
                c.update(sources=["evidence"], evidence_level=3, category="FACT")
        cls["claims"].append(h._claim("C12", "ls_milestones_product.k5_manufacturing", 3, ["evidence"], "GMP CDMO"))
        res, _, _ = h.run(h.LS["ls_preclinical_drug"], cls)
        self.assertEqual(len(res.scores.ls["barriers"]), 2)


class T7Regression(unittest.TestCase):
    def test_frozen_weights_thresholds_and_verdict_scale(self):
        _, _, m = h.run(h.NON_LS["lab_service"])
        self.assertEqual({b["key"]: b["weight"] for b in m["blocks"]}, FROZEN_WEIGHTS)
        self.assertAlmostEqual(sum(b["weight"] for b in m["blocks"]), 1.0)
        self.assertEqual(m["scoring"], FROZEN_THRESHOLDS)
        for form in list(h.LS.values()) + list(h.NON_LS.values()):
            res, _, _ = h.run(form, h.ls_classification() if form in h.LS.values() else None)
            self.assertIn(res.scores.verdict, ("PASS", "HOLD", "NEXT"))

    def test_applicable_blocks_score_as_frozen(self):
        res, _, _ = h.run(h.LS["ls_preclinical_drug"], h.ls_classification())
        got = {b.key: b.score for b in res.scores.blocks}
        for k in ("team", "problem_market", "finance_round"):  # блоки без N/A и без К-слотов
            self.assertEqual(got[k], BEFORE_LS[k])
        self.assertEqual({b.key: b.weight for b in res.scores.blocks}, FROZEN_WEIGHTS)


class T8RoleConfig(unittest.TestCase):
    def test_s1_has_sk05_sk06_only_s1_touched(self):
        _, _, m = h.run(h.NON_LS["lab_service"])
        rc = m["role_config"]
        self.assertEqual(set(rc), {"S1"})  # S2/S3 здесь не задаются и не меняются
        skills = rc["S1"]["skills"]
        for sk in ("SK-01", "SK-02", "SK-03", "SK-04", "SK-07", "SK-08", "SK-11", "SK-05", "SK-06"):
            self.assertIn(sk, skills)
        self.assertEqual(rc["S1"]["skill_notes"]["SK-05"]["criteria"], ["К1", "К4"])
        self.assertEqual(rc["S1"]["skill_notes"]["SK-06"]["criteria"], ["К2"])


if __name__ == "__main__":
    unittest.main()
