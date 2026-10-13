"""HQ-059: блокеры вычитки PM S1 (PR #12: 12.1–12.4, 12.14; PR #13: 13.1, 13.11). Только синтетические данные."""
import copy
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hq016_cases as h  # noqa: E402
from s1_intake import dialogue as d  # noqa: E402
from s1_intake.config import load_methodology  # noqa: E402
from s1_intake.ls_zones import derive_stage  # noqa: E402
from s1_intake.scoring import is_revenue_claim, ls_block1_status, revenue_claim_info  # noqa: E402

M = load_methodology()


def stage_of(text: str, field: str = "q_product"):
    sub = SimpleNamespace(ls_stage=None, intended_use="", answers=[SimpleNamespace(field=field, value=text)])
    return derive_stage(sub, M)


class StageNoFalseHits(unittest.TestCase):
    """12.1–12.3, 12.10: фразы, ложно задающие стадию РУ / Рег / КИ, стадии не задают."""

    def test_phrases_that_must_not_set_stage(self):
        for phrase in ["наши клиенты — клиники", "клиника как клиент", "для клиник", "клинический партнёр",
                       "зарегистрировали ООО", "регистрация юрлица", "регистрация товарного знака",
                       "свидетельство о регистрации программы для ЭВМ", "регистрация пользователей в приложении",
                       "РУ нет", "без РУ", "до РУ", "РУ планируем", "пока не регистрировали",
                       "сайт company.ru", "готовимся к регистрации", "планируем фазу I", "следующий шаг — ТИ"]:
            with self.subTest(phrase):
                r = stage_of(phrase)
                self.assertIsNone(r["stage"], (phrase, r))

    def test_phrases_that_must_set_stage(self):
        for phrase, stage in [("регистрационное удостоверение получено", "registered"), ("РУ № 123", "registered"),
                              ("подали на регистрацию", "registration"),
                              ("на экспертизе в Росздравнадзоре", "registration"),
                              ("идёт клиническое исследование фаза II", "clinical"),
                              ("доклиника завершена, планируем фазу I", "preclinical")]:
            with self.subTest(phrase):
                self.assertEqual(stage_of(phrase)["stage"], stage)

    def test_foreign_registration_is_not_ru(self):
        """PM 13.10 п. 5: CE / FDA — не «РУ есть» для гейта РФ; пометка «зарубежная регистрация»."""
        for phrase in ["CE mark получен", "CE-маркировка получена", "FDA clearance", "510(k) cleared"]:
            with self.subTest(phrase):
                r = stage_of(phrase)
                self.assertIsNone(r["stage"])
                self.assertEqual(r["note"], "зарубежная регистрация")
        r = stage_of("РУ № 123, CE mark получен")
        self.assertEqual((r["stage"], r["note"]), ("registered", "зарубежная регистрация"))

    def test_customers_field_not_used_for_stage(self):
        self.assertIsNone(stage_of("клинические испытания фаза III", field="q_customers")["stage"])

    def test_phase_not_confused(self):
        self.assertEqual(stage_of("клиническое исследование, фаза II")["phase"], "Ф2")
        self.assertEqual(stage_of("клиническое исследование, фаза I")["phase"], "Ф1")


class RevenueClaims(unittest.TestCase):
    """12.4: фразы, ложно снимающие N/A."""

    def test_not_a_revenue_claim(self):
        for phrase in ["выручки нет", "pre-revenue", "до выручки", "продажи планируем после РУ",
                       "наши клиенты — клиники", "рынок 10 млрд", "платящих клиентов нет"]:
            with self.subTest(phrase):
                self.assertFalse(is_revenue_claim(phrase, M))

    def test_grant_is_not_revenue_services_are(self):
        """PM 13.10 п. 4: грант — неразбавляющее финансирование; услуги — выручка «не основной продукт»."""
        for phrase in ["получили грант 5 млн руб.", "грант РНФ и субсидия", "grant from foundation"]:
            with self.subTest(phrase):
                self.assertFalse(is_revenue_claim(phrase, M))
        self.assertTrue(is_revenue_claim("получили грант. выручка 3 млн руб.", M))
        self.assertEqual(revenue_claim_info("оказываем услуги лабораториям", M),
                         (True, "услуги — выручка, не основной продукт"))
        self.assertEqual(revenue_claim_info("продали 5 устройств", M), (True, None))

    def test_revenue_claim(self):
        for phrase in ["выручка 3 млн руб.", "продали 5 устройств", "платящие клиенты: 4", "MRR 100 тыс.",
                       "ARR 1 млн", "получили предзаказы", "договор поставки подписан",
                       "наши клиенты — клиники, выручка 3 млн руб."]:
            with self.subTest(phrase):
                self.assertTrue(is_revenue_claim(phrase, M))

    def test_negated_revenue_keeps_na(self):
        claim = h._claim("C10", "traction.traction_metrics", 0, ["form"], "Выручки нет, pre-revenue (синтетика)")
        res, _, _ = h.run(h.LS["ls_preclinical_drug"], h.ls_classification([claim]))
        self.assertTrue(res.scores.ls["applied"])

    def test_stated_revenue_removes_na(self):
        res, _, _ = h.run(h.LS["ls_preclinical_drug"], h.ls_classification([h.REVENUE_CLAIM]))
        self.assertFalse(res.scores.ls["applied"])


class KSlotCeiling(unittest.TestCase):
    """12.14 / Р-5: К-слот без документа не выше E1; документ компании не выше E2; FACT — не кодом."""
    SLOT = "ls_milestones_risks.k2_regulatory_path"

    def item(self, sources, ref="синтетика"):
        c = h._claim("C20", self.SLOT, 4, sources, "синтетика")
        c["source_ref"] = ref
        cls = h.ls_classification()
        cls["claims"] = [x for x in cls["claims"] if x["slot"] != self.SLOT] + [c]
        res, _, _ = h.run(h.LS["ls_preclinical_drug"], cls)
        return [i for b in res.scores.blocks for i in b.items if i.key == self.SLOT][0]

    def test_ceilings(self):
        self.assertEqual(self.item(["form"]).best_level, 0)
        self.assertEqual(self.item([]).best_level, 0)
        self.assertEqual(self.item(["deck"]).best_level, 1)
        self.assertLessEqual(self.item(["model"]).best_level, 2)
        self.assertNotEqual(self.item(["deck"]).status.value, "FACT")
        self.assertNotEqual(self.item(["evidence"]).status.value, "FACT")  # максимум FACT_CANDIDATE без подписи

    def test_dialogue_answer_without_file_is_e1(self):
        self.assertEqual(self.item(["evidence"], ref="dlg_Q1.К2").best_level, 1)


def ms(k, level=None, status="FOUNDER_CLAIM", app="SCORED"):
    return {"k": k, "level": level, "status": status, "applicability": app}


def ls_with(**lv):
    return {"gate": True, "milestones": [ms(k, v) if v is not None else ms(k, None, "UNKNOWN", "MISSING")
                                         for k, v in lv.items()]}


class Block1Ls(unittest.TestCase):
    """13.1: статус блока 1 для LS — по LS §2.3 от К1–К7, не по баллу 0–100. Уровень E0–E4 → балл К: 0→1, 1→2, 2→3."""
    W = M["working"]

    def st(self, ls, verdict="HOLD"):
        return ls_block1_status(ls, verdict, self.W)["status"]

    def test_no_gate_not_ready(self):
        self.assertEqual(self.st({"gate": False, "milestones": []}), "not_ready")
        self.assertEqual(self.st(None), "not_ready")

    def test_k2_score_1_not_ready(self):
        self.assertEqual(self.st(ls_with(К1=3, К2=0, К3=3, К4=3, К5=2, К6=2)), "not_ready")

    def test_k1_k4_low_not_ready(self):
        self.assertEqual(self.st(ls_with(К1=None, К2=3, К3=3, К4=3, К5=2, К6=2)), "not_ready")
        self.assertEqual(self.st(ls_with(К1=3, К2=3, К3=3, К4=None, К5=2, К6=2)), "not_ready")

    def test_k1_k2_k4_score_2_iterate(self):
        self.assertEqual(self.st(ls_with(К1=1, К2=1, К3=2, К4=1, К5=1, К6=1)), "iterate")

    def test_all_ge_3_ready(self):
        self.assertEqual(self.st(ls_with(К1=2, К2=2, К3=2, К4=2, К5=1, К6=1)), "ready")
        ls = ls_with(К1=2, К2=2, К3=2, К4=2, К5=1)
        ls["milestones"].append(ms("К6", None, "UNKNOWN", "N_A_BY_STAGE"))
        self.assertEqual(self.st(ls), "ready")

    def test_holes_go_to_iterate(self):
        self.assertEqual(self.st(ls_with(К1=2, К2=2, К3=1, К4=2, К5=1, К6=1)), "iterate")   # К3 = 2
        self.assertEqual(self.st(ls_with(К1=2, К2=2, К3=2, К4=2, К5=None, К6=1)), "iterate")  # К5 пробел

    def test_pass_verdict_not_ready_and_no_internal_labels(self):
        r = ls_block1_status(ls_with(К1=3, К2=3, К3=3, К4=3, К5=3, К6=3), "PASS", self.W)
        self.assertEqual(r["status"], "not_ready")
        self.assertNotIn(r["value"], ("PASS", "HOLD", "NEXT"))

    def test_end_to_end_pack(self):
        def block1(form, cls):
            _, pack, _ = h.run(form, cls)
            return pack["pack_blocks"][0]["value"]
        self.assertEqual(block1(h.LS["ls_no_gate"], h.ls_classification()), "NOT READY")
        self.assertEqual(block1(h.LS["ls_preclinical_drug"], h.ls_classification()), "ITERATE")
        cls = h.ls_classification()
        cls["claims"] = [c for c in cls["claims"] if c["slot"] != "ls_milestones_risks.k2_regulatory_path"]
        cls["claims"].append(h._claim("C30", "ls_milestones_risks.k2_regulatory_path", 0, ["deck"], "синтетика"))
        self.assertEqual(block1(h.LS["ls_preclinical_drug"], cls), "NOT READY")

    def test_it_path_keeps_yaml_map(self):
        _, pack, _ = h.run(h.NON_LS["it_wellness"], h.ls_classification())
        self.assertIn(pack["pack_blocks"][0]["value"], self.W["pack_block1"]["map"].values())


class R1Order(unittest.TestCase):
    """13.11: Q1.R и Q1.Z6 сразу после К6; сверх лимита отрезаются Q1.Z2 → Q1.M → Q1.T."""

    def scores(self):
        res, _, _ = h.run(dict(h.LS["ls_preclinical_drug"]), h.ls_classification())
        return json.loads(res.scores.model_dump_json())

    def session(self, m, prev=None):
        return d.build_session(self.scores(), m, prev, "THERAPEUTIC")

    def qids(self, m):
        s = self.session(m)
        return [q["qid"] for q in s["queue"]["Р1"]], [x["qid"] for x in s["r1_not_asked"]]

    def test_order(self):
        q, _ = self.qids(M)
        i = {x: q.index(x) for x in ("Q1.R", "Q1.Z6", "Q1.T", "Q1.M", "Q1.Z2")}
        self.assertEqual(i["Q1.Z6"], i["Q1.R"] + 1)
        self.assertLess(i["Q1.Z6"], i["Q1.T"])
        self.assertLess(i["Q1.T"], i["Q1.M"])
        self.assertLess(i["Q1.M"], i["Q1.Z2"])
        self.assertTrue(all(q.index(x) < i["Q1.R"] for x in q if x.startswith("Q1.К")))

    def test_overflow_cuts_z2_then_m_then_t_never_r_z6(self):
        full, _ = self.qids(M)
        after_z6 = full.index("Q1.Z6") + 1
        expected = {len(full) - 1: ["Q1.Z2"], len(full) - 2: ["Q1.Z2", "Q1.M"], after_z6: None}
        for lim, cut in expected.items():
            m = copy.deepcopy(M)
            m["dialogue_s1"]["limits"]["r1"] = lim
            q, over = self.qids(m)
            self.assertIn("Q1.R", q)
            self.assertIn("Q1.Z6", q)
            if cut:
                self.assertEqual(set(over[:len(cut)]), set(cut))
            else:
                self.assertTrue({"Q1.T", "Q1.M", "Q1.Z2"} <= set(over))

    def test_mandatory_never_cut_even_over_limit(self):
        """PM 13.10 п. 1: К1–К7, Q1.R, Q1.Z6 не вырезаются никогда (лучше 13), необязательные уходят с конца."""
        for lim in (12, 9, 3):
            m = copy.deepcopy(M)
            m["dialogue_s1"]["limits"]["r1"] = lim
            s = self.session(m)
            q = [x["qid"] for x in s["queue"]["Р1"]]
            self.assertTrue({"Q1.R", "Q1.Z6"} <= set(q))
            self.assertTrue(all(x.get("mandatory") for x in s["queue"]["Р1"] if x["source"] in ("slot", "control")
                                and x["qid"] != "Q1.Z2"))
            self.assertTrue({"Q1.T", "Q1.M", "Q1.Z2"}.isdisjoint(q) or lim == 12)
            self.assertGreaterEqual(s["limits"]["r1"], len(q))
            s["round"] = "Р1"
            for x in q:
                d.ask(s, x["qid"] if isinstance(x, dict) else x)  # лимит ask() не режет обязательные

    def test_two_clarifications_do_not_displace_r_and_z6(self):
        s = self.session(M)
        s["round"] = "Р1"
        d.ask(s, "clar1", clarification=True)
        d.ask(s, "clar2", clarification=True)
        s = self.session(M, s)
        self.assertLessEqual(len(s["queue"]["Р1"]) + s["clarifications"], M["dialogue_s1"]["limits"]["r1"])
        while (nq := d.first_question(s)) is not None:
            try:
                d.ask(s, nq["qid"])
            except d.DialogueError:
                break
        self.assertIn("Q1.R", s["asked"]["Р1"])
        self.assertIn("Q1.Z6", s["asked"]["Р1"])


if __name__ == "__main__":
    unittest.main()
