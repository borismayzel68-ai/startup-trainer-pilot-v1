"""HQ-020 · решения PM 11.10 (рабочие значения): блоки 1 и 8 Pack, устный ответ ≤ hypothesis,
срок досылки 14 дней + возобновление, страница диалога основателя по ссылке со сроком, TTL по умолчанию 14 дней,
вопрос З2 для ЛП в пределах лимита Р1. Синтетика, LLM — mock, писем нет."""
import dataclasses
import json
import sys
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hq016_cases as h  # noqa: E402
import test_s1_intake as base  # noqa: E402
from test_tg1_human_gate import Tg1Base  # noqa: E402
from s1_intake import dialogue as d  # noqa: E402
from s1_intake.config import Settings, load_methodology  # noqa: E402

M = load_methodology()
W = M["working"]


def sess(form):
    res, pack, _ = h.run(form, h.ls_classification())
    return d.build_session(json.loads(res.scores.model_dump_json()), M, None, form.get("product_class", "")), res, pack


class WorkingValuesTests(unittest.TestCase):
    def test_values_in_config_marked_working(self):
        self.assertEqual(W["status"], "рабочее")
        self.assertEqual(W["r2_deadline_days"], 14)
        self.assertEqual((W["limits"]["r0"], W["limits"]["r1"], W["limits"]["r2"]), (7, 12, 5))

    def test_block1_uses_general_yaml_thresholds(self):
        _, res, pack = sess(h.LS["ls_preclinical_drug"])
        b1 = pack["pack_blocks"][0]
        self.assertEqual(b1["value"], W["pack_block1"]["map"][res.scores.verdict])
        self.assertIn("рабочие", b1["basis"])

    def test_block8_30_60_90_not_specified(self):
        _, _, pack = sess(h.LS["ls_preclinical_drug"])
        b8 = pack["pack_blocks"][7]
        self.assertTrue(b8["steps"])
        self.assertTrue({s["horizon"] for s in b8["steps"]} <= {"30 дней", "60 дней", "90 дней"})
        for s in b8["steps"]:
            self.assertEqual((s["owner"], s["procedure_rf"]), ("не указано", "не указано"))

    def test_spoken_answer_never_above_hypothesis(self):
        for lvl in (2, 3, 4):
            c = h._claim(f"C4{lvl}", "product_tech.k2_regulatory_path", lvl, ["evidence", "deck"], "устно (синтетика)")
            c["source_ref"] = "dlg_Q1.К2"
            res, _, _ = h.run(h.LS["ls_preclinical_drug"], h.ls_classification([c]))
            got = next(x for x in res.classification.claims if x.id == f"C4{lvl}")
            self.assertLessEqual(got.evidence_level, 1, lvl)  # hypothesis = E1
            self.assertNotEqual(got.category.value, "FACT")

    def test_drug_hospital_question_within_r1_limit(self):
        s, _, _ = sess(h.LS["ls_preclinical_drug"])
        ids = [q["qid"] for q in s["queue"]["Р1"]]
        self.assertIn("Q1.Z2", ids)
        self.assertLessEqual(len(ids), W["limits"]["r1"])
        s_md, _, _ = sess(h.LS["ls_md_prototype"])
        self.assertNotIn("Q1.Z2", [q["qid"] for q in s_md["queue"]["Р1"]])

    def test_r2_deadline_expire_and_resume(self):
        s, _, _ = sess(h.LS["ls_preclinical_drug"])
        d.add_r2_item(s, "Q1.К2", "MISSING")
        due = datetime.fromisoformat(s["r2_items"][-1]["due"])
        self.assertAlmostEqual((due - datetime.now(timezone.utc)).days, 13, delta=1)
        self.assertEqual(d.expire_overdue(s), [])  # срок не прошёл
        later = datetime.now(timezone.utc) + timedelta(days=15)
        self.assertEqual(d.expire_overdue(s, later), ["Q1.К2"])
        self.assertEqual(s["r2_items"][-1]["state"], "истёк")
        d.resume_r2(s, "Q1.К2", later)
        it = s["r2_items"][-1]
        self.assertEqual(it["state"], "дошлю позже")
        self.assertGreater(datetime.fromisoformat(it["due"]), later)
        with self.assertRaises(d.DialogueError):
            d.resume_r2(s, "Q1.К2")  # не истёк — возобновлять нечего

    def test_ttl_default_14_days_no_secret_default(self):
        s = Settings()
        self.assertEqual(s.free_link_ttl_s, 14 * 86400)
        self.assertEqual(s.free_link_secret, "")


class FounderDialoguePageTests(Tg1Base):
    def setUp(self):
        super().setUp()
        self.state.settings = dataclasses.replace(self.s, free_link_secret="f" * 32)

    def test_page_by_time_limited_link_and_answer(self):
        jid = self.ready_job()
        link = self.c.post(f"/s1/jobs/{jid}/dialogue-link", headers=base.ADMIN).get_json()
        self.assertGreater(link["expires"], time.time() + 13 * 86400)
        page = self.c.get(link["path"])  # без admin-токена
        self.assertEqual(page.status_code, 200)
        qs = page.get_json()["questions"]
        self.assertTrue(qs)
        self.assertEqual(self.c.get(link["path"][:-1] + ("0" if link["path"][-1] != "0" else "1")).status_code, 403)
        r = self.c.post(link["path"], json={"qid": qs[0]["qid"], "answer": "синтетика"})
        self.assertEqual(r.status_code, 200, r.get_json())
        for _ in range(200):
            if self.state.store.get(jid)["status"] not in ("received", "processing"):
                break
            time.sleep(0.05)
        self.assertIn(f"dlg_{qs[0]['qid']}", [a["field"] for a in self.state.store.get(jid)["data"]["submission"]["answers"]])
        self.assertEqual(self.sent, [])  # никаких писем

    def test_free_link_issued_without_ttl_env(self):
        jid = self.ready_job()
        self.assertEqual(self.c.post(f"/s1/jobs/{jid}/free-link", headers=base.ADMIN).status_code, 200)

    def test_expire_resume_endpoints(self):
        jid = self.ready_job()
        data = self.state.store.get(jid)["data"]
        s = data["dialogue"]
        d.add_r2_item(s, "Q1.К7", "MISSING")
        s["r2_items"][-1]["due"] = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        self.state.store.update(jid, data_patch={"dialogue": s})
        r = self.c.post(f"/s1/jobs/{jid}/dialogue/r2/expire", headers=base.ADMIN).get_json()
        self.assertIn("Q1.К7", r["expired"])
        self.assertEqual(self.c.post(f"/s1/jobs/{jid}/dialogue/r2/resume", headers=base.ADMIN,
                                     json={"item": "Q1.К7"}).status_code, 200)


if __name__ == "__main__":
    unittest.main()
