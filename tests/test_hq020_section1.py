"""HQ-020: диалог раздела 1 + бесплатная часть S1 + стадия «не подтверждена» (PM 11.10).
Синтетика, LLM — mock, SMTP — подмена (наружу ничего не уходит)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hq016_cases as h  # noqa: E402
import test_s1_intake as base  # noqa: E402
from test_tg1_human_gate import ALICE, Tg1Base  # noqa: E402
from s1_intake import dialogue as d  # noqa: E402
from s1_intake.config import load_methodology  # noqa: E402

M = load_methodology()
UNCERTAIN_FORM = dict(base.SAMPLE_FORM, tranid="8123456:2020", intended_use="синтетический актив",
                      q_traction="нет данных", q_customers="переговоры")
FORBIDDEN = ("PASS", "HOLD", "NEXT")


def uncertain_scores():
    f = dict(h.LS["ls_preclinical_drug"])
    f.pop("ls_stage")
    f["intended_use"] = "синтетический актив"
    res, pack, _ = h.run(f, h.ls_classification())
    return res, pack


class FreePartTests(unittest.TestCase):
    def test_free_part_has_label_score_three_gaps_with_risk(self):
        res, pack = uncertain_scores()
        fp = pack["free_part"]
        self.assertEqual(fp["label"], "ИИ-оценка, не инвестиционная рекомендация")
        self.assertIn("ИИ-оценка, не инвестиционная рекомендация", fp["html"])
        self.assertEqual(fp["score"], res.scores.readiness)
        self.assertEqual(len(fp["gaps"]), 3)
        for g in fp["gaps"]:
            self.assertTrue(g["gap"] and g["risk"])

    def test_free_part_never_has_internal_labels(self):
        for name, form in list(h.NON_LS.items()) + list(h.LS.items()):
            _, pack, _ = h.run(form, h.ls_classification())
            fp = pack["free_part"]
            blob = repr({k: v for k, v in fp.items()})
            for word in FORBIDDEN:
                self.assertNotIn(word, blob, f"{name}: {word}")

    def test_free_part_shows_stage_unconfirmed(self):
        _, pack = uncertain_scores()
        self.assertEqual(pack["free_part"]["stage"], "не подтверждена")
        self.assertIn("не подтверждена", pack["free_part"]["html"])


class DialogueTests(unittest.TestCase):
    def session(self):
        res, _ = uncertain_scores()
        import json
        return d.build_session(json.loads(res.scores.model_dump_json()), M)

    def test_stage_question_first(self):
        s = self.session()
        self.assertEqual(s["queue"]["Р0"][0]["qid"], "stage")
        self.assertEqual(d.first_question(s)["qid"], "stage")
        with self.assertRaises(d.DialogueError):
            d.ask(s, "gate")  # пока нет ответа о стадии — другие вопросы не задаются

    def test_zone_questions_consumed_in_r1(self):
        s = self.session()
        zone_q = {q["qid"] for q in s["queue"]["Р1"] if q["source"] == "zone"}
        self.assertTrue({"З2:hospital_drug", "З3:orphan_paediatric", "З6:biomaterials"} <= zone_q)

    def test_no_questions_on_na_slots(self):
        s = self.session()
        res, _ = uncertain_scores()
        na = {it.key for b in res.scores.blocks for it in b.items if it.applicability == "N_A_BY_STAGE"}
        self.assertTrue(na)
        asked = {q["qid"].split(":", 1)[-1] for q in s["queue"]["Р1"]}
        self.assertFalse(na & asked)

    def test_limits_from_yaml(self):
        s = self.session()
        self.assertEqual((s["limits"]["r0"], s["limits"]["r1"], s["limits"]["r2"]), (7, 12, 5))
        d.answer(s, "stage", "доклиника", "preclinical")
        for i in range(7):
            d.ask(s, f"q{i}")
        with self.assertRaises(d.DialogueError):
            d.ask(s, "q8")  # 8-й вопрос Р0
        d.next_round(s)
        for i in range(12):
            d.ask(s, f"r{i}")
        with self.assertRaises(d.DialogueError):
            d.ask(s, "r13")  # 13-й вопрос Р1
        for i in range(6):
            d.add_r2_item(s, f"doc{i}", "MISSING К1")
        self.assertEqual(len(s["r2_items"]), 5)
        self.assertEqual(s["r2_not_requested"][0]["reason"], "не запрашивалось: лимит досылки")

    def test_rounds_and_exit(self):
        s = self.session()
        d.answer(s, "stage", "доклиника", "preclinical")
        d.next_round(s)
        d.next_round(s)
        self.assertEqual(s["round"], "Р2")
        d.add_r2_item(s, "doc", "MISSING К2")
        with self.assertRaises(d.DialogueError):
            d.record_exit(s, "В1")  # пункт Р2 не закрыт
        d.attach_file(s, "doc", "synthetic.pdf")
        d.record_exit(s, "В1")
        self.assertEqual(s["exit"]["code"], "В1")
        with self.assertRaises(d.DialogueError):
            d.record_exit(self.session(), "X")  # без В1–В4 и без решения человека

    def test_r2_expiry(self):
        s = self.session()
        d.add_r2_item(s, "doc", "MISSING К3")
        d.expire_r2(s)
        self.assertEqual(s["r2_items"][0]["closed_as"], "MISSING/CLAIM")
        self.assertTrue(s["r2_items"][0]["close_reason"])


class StageGateTests(Tg1Base):
    def submit_uncertain(self):
        self.c.post("/s1/webhooks/tilda", data=UNCERTAIN_FORM, headers={"X-Webhook-Key": base.WEBHOOK_KEY})
        jid = self.c.get("/s1/jobs", headers=base.ADMIN).get_json()[0]["id"]
        import time
        for _ in range(100):
            job = self.c.get(f"/s1/jobs/{jid}", headers=base.ADMIN).get_json()
            if job["status"] not in ("received", "processing"):
                return jid, job
            time.sleep(0.05)
        self.fail("timeout")

    def test_uncertain_stage_blocks_human_gate(self):
        jid, job = self.submit_uncertain()
        self.assertEqual(job["status"], "awaiting_founder")  # очередь не продвигает в Human Gate
        r = self.c.post(f"/s1/jobs/{jid}/approve", headers=ALICE)
        self.assertEqual(r.status_code, 409)
        self.assertIn("стадия не подтверждена", r.get_json()["error"])
        self.assertEqual(self.sent, [])
        log = self.c.get(f"/s1/jobs/{jid}/approvals", headers=base.ADMIN).get_json()
        self.assertEqual(log[0]["outcome"], "blocked_stage")
        # rerun очереди тоже не продвигает
        self.c.post(f"/s1/jobs/{jid}/rerun", headers=base.ADMIN)
        _, job = self.wait_done(jid)
        self.assertEqual(job["status"], "awaiting_founder")

    def wait_done(self, jid):
        import time
        for _ in range(100):
            job = self.c.get(f"/s1/jobs/{jid}", headers=base.ADMIN).get_json()
            if job["status"] not in ("received", "processing"):
                return jid, job
            time.sleep(0.05)
        self.fail("timeout")

    def test_free_part_available_without_approve(self):
        jid, _ = self.submit_uncertain()
        fp = self.c.get(f"/s1/jobs/{jid}/free-part", headers=base.ADMIN).get_json()
        self.assertEqual(fp["label"], "ИИ-оценка, не инвестиционная рекомендация")
        self.assertEqual(fp["stage"], "не подтверждена")
        self.assertEqual(self.sent, [])
        dlg = self.c.get(f"/s1/jobs/{jid}/dialogue", headers=base.ADMIN).get_json()
        self.assertEqual(dlg["queue"]["Р0"][0]["qid"], "stage")

    def test_stage_answer_unlocks_gate_then_personal_approve(self):
        jid, _ = self.submit_uncertain()
        self.assertEqual(self.c.post(f"/s1/jobs/{jid}/dialogue/stage", headers=base.ADMIN,
                                     json={"stage": "выдумка"}).status_code, 400)
        r = self.c.post(f"/s1/jobs/{jid}/dialogue/stage", headers=base.ADMIN,
                        json={"stage": "preclinical", "answer": "доклиника (синтетика)"})
        self.assertEqual(r.status_code, 200, r.get_json())
        _, job = self.wait_done(jid)
        self.assertEqual(job["status"], "ready_for_review", job.get("error"))
        self.assertEqual(self.c.post(f"/s1/jobs/{jid}/approve", headers=base.ADMIN).status_code, 403)
        self.assertEqual(self.c.post(f"/s1/jobs/{jid}/approve", headers=ALICE).status_code, 200)
        self.assertEqual(self.c.post(f"/s1/jobs/{jid}/approve", headers=ALICE).status_code, 409)  # no resend
        self.assertEqual(len(self.sent), 1)


if __name__ == "__main__":
    unittest.main()
