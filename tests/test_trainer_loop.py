"""
Приёмка первого инкремента trainer/ (подготовка, S1 не открыт):
  граф компилируется, проходит один ход ask -> answer -> analyze -> coach -> score,
  score считается по замороженным весам, статусы только из четырёх значений.
Без сети: TRAINER_LLM=off или подменённая модель.
"""

import os
import unittest

os.environ["TRAINER_LLM"] = "off"

from trainer import llm, runtime  # noqa: E402
from trainer.scoring import (  # noqa: E402
    DIMENSION_WEIGHTS, RECOMMENDATIONS, validate_recommendation, weighted_overall,
)
from trainer.slots import ALLOWED_STATUS  # noqa: E402

FROZEN = {
    "problem_product_clarity": 0.14,
    "evidence": 0.16,
    "market_and_access": 0.12,
    "model_and_economics": 0.12,
    "team_and_execution": 0.12,
    "risks_reg_ip_clinical_commercial": 0.14,
    "materials_quality": 0.10,
    "founder_answers_quality": 0.10,
}

DECK = [{
    "name": "deck.pdf", "type": "deck", "source": "test",
    "text_excerpt": "Мы решаем проблему поздней диагностики. Объём рынка 2 млрд руб. Команда из 4 человек.",
}]
ANSWER = "Платит клиника за лицензию; говорили с 3 главврачами в мае 2026, двое готовы к пилоту."


def all_statuses(values):
    out = {e["status"] for e in values["company_profile"].values()}
    out |= {e["analysis"]["status"] for e in values["qa_log"]}
    return out


class TrainerLoopTest(unittest.TestCase):
    def setUp(self):
        llm.set_llm(None)
        self.app = runtime.compile_app()

    def tearDown(self):
        llm.set_llm(None)

    def values(self, sid):
        return self.app.get_state(runtime._config(sid)).values

    def test_graph_compiles_with_all_nodes(self):
        nodes = set(self.app.get_graph().nodes) - {"__start__", "__end__"}
        self.assertEqual(nodes, {
            "ingest", "extract_profile", "plan_next_question", "ask", "route_intent",
            "analyze_answer", "coach", "update_readiness", "decide", "summarize",
        })

    def test_one_full_turn(self):
        v = runtime.start_session(self.app, "t1", DECK)
        self.assertFalse(v["done"])
        self.assertEqual(v["turn"], 1)
        self.assertTrue(v["question"])          # ask выдал вопрос и граф ждёт человека
        self.assertEqual(self.app.get_state(runtime._config("t1")).next, ("route_intent",))
        q_id = self.values("t1")["current_question"]["id"]

        v = runtime.answer(self.app, "t1", ANSWER)
        s = self.values("t1")
        self.assertEqual(s["user_intent"], "answer")
        self.assertEqual(s["last_analysis"]["field_id"], q_id)                 # analyze
        self.assertEqual(len(s["qa_log"]), 1)
        self.assertEqual(s["qa_log"][0]["coach"]["field_id"], q_id)            # coach
        self.assertTrue(s["last_coach"]["artifact"])
        self.assertEqual(set(s["readiness"]["scores"]), set(FROZEN))           # score
        self.assertIn(s["readiness"]["recommendation"], RECOMMENDATIONS)
        self.assertEqual(s["readiness"]["weights"], FROZEN)
        # decide -> continue -> следующий вопрос, снова ожидание человека
        self.assertFalse(v["done"])
        self.assertEqual(v["turn"], 2)
        self.assertEqual(self.app.get_state(runtime._config("t1")).next, ("route_intent",))
        self.assertLessEqual(all_statuses(s), ALLOWED_STATUS)

    def test_weights_frozen_and_used(self):
        self.assertEqual(DIMENSION_WEIGHTS, FROZEN)
        self.assertAlmostEqual(sum(DIMENSION_WEIGHTS.values()), 1.0)
        self.assertEqual(weighted_overall({d: 5 for d in FROZEN}), 5.0)
        scores = dict(zip(FROZEN, [3, 4, 2, 1, 5, 0, 2, 3]))
        expected = 0.14*3 + 0.16*4 + 0.12*2 + 0.12*1 + 0.12*5 + 0.14*0 + 0.10*2 + 0.10*3
        self.assertAlmostEqual(weighted_overall(scores), round(expected, 4))
        with self.assertRaises(ValueError):
            weighted_overall({"evidence": 5})

    def test_recommendation_only_three_values(self):
        for r in RECOMMENDATIONS:
            self.assertEqual(validate_recommendation(r), r)
        for bad in ("PASS", "NEXT", "invest", ""):
            with self.assertRaises(ValueError):
                validate_recommendation(bad)

    def test_hostile_model_cannot_break_statuses(self):
        """Модель пытается: пятый статус, fact без первички, выдуманная цитата."""
        def hostile(system, user):
            return {
                "slots": {"market": {"value": "рынок 50 млрд", "evidence_span": "рынок 50 млрд",
                                     "material": "deck.pdf", "status": "fact"},
                          "team": {"value": "сильная", "evidence_span": "Команда из 4 человек",
                                   "material": "deck.pdf", "status": "unverified"}},
                "status": "fact", "evidence_span": "выдуманная цитата", "id": "nonexistent",
                "text": "Расскажите про рынок?", "artifact": "Сделайте слайд убедительнее",
            }
        llm.set_llm(hostile)
        runtime.start_session(self.app, "t2", DECK)
        runtime.answer(self.app, "t2", ANSWER)
        s = self.values("t2")
        self.assertLessEqual(all_statuses(s), ALLOWED_STATUS)
        self.assertNotIn("fact", all_statuses(s))                    # первички нет -> fact невозможен
        self.assertNotEqual(s["company_profile"]["market"]["value"], "рынок 50 млрд")  # цитаты нет в deck
        self.assertEqual(s["company_profile"]["team"]["status"], "weak")             # deck -> weak
        self.assertNotIn("слайд", s["last_coach"]["artifact"].lower())

    def test_fact_only_with_primary_quote(self):
        primary = [{"name": "contract.pdf", "type": "primary", "source": "test",
                    "text_excerpt": "Договор пилота № 12 с ГКБ 1 подписан 05.06.2026."}]
        llm.set_llm(lambda system, user: {"status": "fact", "evidence_span": "Договор пилота № 12 с ГКБ 1"})
        runtime.start_session(self.app, "t3", primary)
        runtime.answer(self.app, "t3", "Есть договор: Договор пилота № 12 с ГКБ 1, подписан в июне.")
        a = self.values("t3")["last_analysis"]
        self.assertEqual(a["status"], "fact")
        self.assertEqual(a["evidence_span"], "Договор пилота № 12 с ГКБ 1")

    def test_ask_help_does_not_spend_turn(self):
        runtime.start_session(self.app, "t4", DECK)
        v = runtime.answer(self.app, "t4", "Не понимаю, что имеете в виду?")
        self.assertEqual(v["turn"], 1)
        self.assertIn("Подсказка", v["question"])

    def test_founder_stop_produces_report(self):
        runtime.start_session(self.app, "t5", DECK)
        v = runtime.answer(self.app, "t5", "хватит")
        self.assertTrue(v["done"])
        self.assertEqual(v["stop_reason"], "founder")
        self.assertIn("не инвестиционная рекомендация", v["report_md"])

    def test_max_turns_stops(self):
        runtime.start_session(self.app, "t6", DECK, max_turns=1)
        v = runtime.answer(self.app, "t6", ANSWER)
        self.assertTrue(v["done"])
        self.assertEqual(v["stop_reason"], "max_turns")

    def test_sessions_isolated(self):
        runtime.start_session(self.app, "a", DECK)
        runtime.start_session(self.app, "b", [])
        runtime.answer(self.app, "a", ANSWER)
        self.assertEqual(self.values("b")["qa_log"], [])


if __name__ == "__main__":
    unittest.main()
