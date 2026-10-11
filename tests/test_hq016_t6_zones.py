"""HQ-016 T6 (указание PM 11.10): зоны З1–З7 по v0.2 §7, вопросы «не оценено, нужен ответ»,
порядок барьеров v0.2, К7 «рабочее, до пилота», стадия из ответов анкеты, пилот МИ ≤ «заявление».
Синтетические кейсы, LLM — mock."""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hq016_cases as h  # noqa: E402

NEED = "не оценено, нужен ответ"


def form(**kw):
    f = dict(h.LS["ls_preclinical_drug"])
    f.pop("ls_stage")  # поля ls_stage в Tilda до 05.11 нет
    f.update(kw)
    return f


def zones(res):
    return {z["id"]: z for z in res.scores.ls["zones"]}


class ZonesTests(unittest.TestCase):
    def test_drug_preclinical_zones_from_v02(self):
        res, pack, _ = h.run(form(), h.ls_classification())
        z = zones(res)
        self.assertEqual(list(z), ["З1", "З2", "З3", "З4", "З5", "З6", "З7"])
        self.assertEqual(z["З1"]["status"], "будущая веха")
        self.assertEqual(z["З7"]["status"], "будущая веха")
        self.assertEqual(z["З4"]["status"], "сейчас")
        self.assertEqual(z["З5"]["status"], "сейчас")
        self.assertTrue(z["З5"]["critical"])
        for zid in ("З2", "З3", "З6"):  # нет сигналов: ЛП в стационаре, орфанный/детский, биоматериалы
            self.assertEqual(z[zid]["status"], NEED, zid)
            self.assertTrue(z[zid]["question"])
        signals = {q["signal"] for q in res.scores.ls["dialogue_questions"]}
        self.assertEqual(signals, {"hospital_drug", "orphan_paediatric", "biomaterials"})
        html = pack["founder_report_html"]
        for zid in z:
            self.assertIn(zid + " · ", html)
        self.assertIn("НЕ ПОДТВЕРЖДЕНО", html)

    def test_device_prototype_zones(self):
        f = dict(h.LS["ls_md_prototype"])
        f.pop("ls_stage")
        res, _, _ = h.run(f, h.ls_classification())
        z = zones(res)
        self.assertEqual(z["З1"]["status"], "не применимо")
        self.assertEqual(z["З7"]["status"], "не применимо")
        self.assertTrue(z["З1"]["reason"] and z["З7"]["reason"])
        self.assertEqual(z["З2"]["status"], "будущая веха")
        self.assertEqual(z["З4"]["status"], "сейчас")
        self.assertIn("Росздравнадзор", " ".join(z["З4"]["basis"]))
        self.assertEqual(z["З3"]["status"], NEED)
        self.assertEqual(z["З6"]["status"], NEED)

    def test_software_device_manufacturing_not_covered(self):
        res, _, _ = h.run(form(product_class="SOFTWARE_POTENTIAL_MD", intended_use="ПО-МИ, прототип"),
                          h.ls_classification())
        self.assertIn("НЕ ПОДТВЕРЖДЕНО", zones(res)["З4"]["reason"])

    def test_ip_never_not_applicable(self):
        _, _, m = h.run(form(), h.ls_classification())
        z5 = next(z for z in m["ls_zones"] if z["id"] == "З5")
        for rule in z5["rules"].values():
            self.assertNotIn("не_применимо", rule.values())

    def test_zones_do_not_change_score(self):
        res, _, _ = h.run(form(), h.ls_classification())
        empty = {"zones": [], "gate_request": None, "dialogue_questions": []}
        with mock.patch("s1_intake.evaluator.build_zones", return_value=empty):
            res2, _, _ = h.run(form(), h.ls_classification())
        self.assertEqual(res.scores.readiness, res2.scores.readiness)
        self.assertEqual([(b.key, b.score) for b in res.scores.blocks], [(b.key, b.score) for b in res2.scores.blocks])
        self.assertEqual(res.scores.verdict, res2.scores.verdict)

    def test_clinical_phase_unknown_and_known(self):
        res, _, _ = h.run(form(intended_use="ЛП, клинические испытания"), h.ls_classification())
        z = zones(res)
        self.assertEqual(z["З1"]["status"], NEED)
        self.assertIn("drug_phase", {q["signal"] for q in res.scores.ls["dialogue_questions"]})
        self.assertEqual(z["З7"]["status"], "сейчас")  # Ф1–Ф3: сейчас при любой фазе
        res, _, _ = h.run(form(intended_use="ЛП, клинические испытания, фаза 2"), h.ls_classification())
        self.assertEqual(zones(res)["З1"]["status"], "сейчас")

    def test_no_gate_request_instead_of_block(self):
        res, pack, _ = h.run(h.LS["ls_no_gate"], h.ls_classification())
        self.assertEqual(res.scores.ls["zones"], [])
        self.assertIn("входной гейт", res.scores.ls["gate_request"])
        self.assertIn("входной гейт", pack["founder_report_html"])


class StageDerivationTests(unittest.TestCase):
    def test_rule_works_without_ls_stage_field(self):
        with_field, _, _ = h.run(h.LS["ls_preclinical_drug"], h.ls_classification())
        without, _, _ = h.run(form(), h.ls_classification())
        self.assertEqual(without.scores.ls["stage"], "preclinical")
        self.assertFalse(without.scores.ls["stage_uncertain"])
        self.assertEqual(without.scores.readiness, with_field.scores.readiness)
        self.assertTrue(without.scores.ls["applied"])

    def test_uncertain_stage_marked_and_asked(self):
        res, pack, _ = h.run(form(intended_use="синтетический актив"), h.ls_classification())
        ls = res.scores.ls
        self.assertTrue(ls["stage_uncertain"])
        self.assertEqual(ls["stage_label"], "уточнить в диалоге")
        self.assertTrue(ls["applied"])  # правило работает и без поля
        self.assertEqual(ls["dialogue_questions"][0]["signal"], "stage")
        self.assertIn("уточнить в диалоге", pack["founder_report_html"])

    def test_conflicting_stage_words_uncertain(self):
        res, _, _ = h.run(form(intended_use="прототип; доклиника"), h.ls_classification())
        self.assertTrue(res.scores.ls["stage_uncertain"])


class DevicePilotTests(unittest.TestCase):
    def test_device_pilot_before_registration_is_claim(self):
        pilot = h._claim("C20", "pilots_workflow.pilot_design", 3, ["evidence"], "LOI на пилот (синтетика)")
        f = dict(h.LS["ls_md_prototype"])
        f.pop("ls_stage")
        res, _, _ = h.run(f, h.ls_classification([pilot]))
        it = {i.key: i for b in res.scores.blocks for i in b.items}["pilots_workflow.pilot_design"]
        self.assertEqual(it.best_level, 1)
        self.assertEqual(it.status.value, "FOUNDER_CLAIM")
        self.assertTrue(res.scores.ls["applied"])


class BarrierOrderTests(unittest.TestCase):
    def test_v02_order_and_k7_mark(self):
        res, pack, _ = h.run(form(), h.ls_classification())
        ls = res.scores.ls
        order = ["К2", "К1", "К4", "К3", "К5", "К6"]
        ks = [b["k"] for b in ls["barriers"]]
        self.assertEqual(ks, [k for k in order if k in ks])
        self.assertTrue(2 <= len(ks) <= 5)
        self.assertEqual(ls["next_artifact"]["mark"], "рабочее, до пилота")
        self.assertIn("рабочее, до пилота", pack["founder_report_html"])


if __name__ == "__main__":
    unittest.main()
