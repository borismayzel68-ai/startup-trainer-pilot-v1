"""HQ-020: банк вопросов S1_DIALOG_SCENARIO_v0.1 (Q0.1–Q0.7, Q1.*, Q1.R), гейт и Р1 (A1), Pack §5.2 блоки 1–11,
SHA кода (A14), пересчёт после файла и «текст без файла ≤ 2», решения PM 11.10 (ссылка на бесплатную часть,
вертикаль, подпись в Pack, call_done), регрессии VC-997 / VC-996 на СИНТЕТИЧЕСКИХ данных.
Реальных кейсов нет; LLM — mock; SMTP — подмена."""
import dataclasses
import json
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hq016_cases as h  # noqa: E402
import test_s1_intake as base  # noqa: E402
from test_tg1_human_gate import ALICE, Tg1Base  # noqa: E402
from s1_intake import dialogue as d  # noqa: E402
from s1_intake import main as s1main  # noqa: E402
from s1_intake.config import load_methodology  # noqa: E402

M = load_methodology()
BANK = M["dialogue_questions_s1"]


def session_for(form, extra=()):
    res, pack, _ = h.run(form, h.ls_classification(list(extra)))
    return d.build_session(json.loads(res.scores.model_dump_json()), M, None, form.get("product_class", "")), res, pack


class ScenarioQuestionTests(unittest.TestCase):
    def test_r0_texts_verbatim_and_q07_only_ls(self):
        s, _, _ = session_for(h.LS["ls_preclinical_drug"])
        texts = {q["id"]: q["question"] for q in s["queue"]["Р0"]}
        for q in BANK["r0"]:
            self.assertEqual(texts[q["id"]], q["text"])
        self.assertEqual(texts["Q0.1"], "Одной-двумя фразами: что делает ваш продукт и для кого — пациент, врач, "
                                         "клиника, лаборатория?")
        s_it, _, _ = session_for(h.NON_LS["it_wellness"])
        self.assertNotIn("Q0.7", [q["id"] for q in s_it["queue"]["Р0"]])

    def test_stage_question_is_q04_and_first_when_uncertain(self):
        f = dict(h.LS["ls_preclinical_drug"])
        f.pop("ls_stage")
        f["intended_use"] = "синтетический актив"
        s, _, _ = session_for(f)
        first = s["queue"]["Р0"][0]
        self.assertEqual((first["qid"], first["id"]), ("stage", "Q0.4"))

    def test_a1_no_r1_question_without_gate(self):
        s, _, _ = session_for(h.LS["ls_no_gate"])
        self.assertTrue(s["gate_missing"])
        d.next_round(s)
        with self.assertRaises(d.DialogueError):
            d.ask(s, "Q1.К1")

    def test_r1_ls_order_branch_texts_and_controls(self):
        s, _, _ = session_for(h.LS["ls_preclinical_drug"])
        ids = [q["id"] for q in s["queue"]["Р1"]]
        order = ["Q1.К1", "Q1.К2", "Q1.К4", "Q1.К3", "Q1.К7", "Q1.К6", "Q1.T", "Q1.M"]
        pos = [ids.index(x) for x in order if x in ids]
        self.assertEqual(pos, sorted(pos))
        for must in ("Q1.К1", "Q1.К3", "Q1.R", "Q1.Z6", "Q1.T", "Q1.M"):
            self.assertIn(must, ids)
        q = {x["id"]: x["question"] for x in s["queue"]["Р1"]}
        self.assertEqual(q["Q1.К1"], BANK["r1_ls"]["К1"]["drug"])
        self.assertEqual(q["Q1.R"], "Уточню: есть ли у вас уже выручка, продажи, предзаказы или платящие клиенты?")
        self.assertTrue(next(x for x in s["queue"]["Р1"] if x["id"] == "Q1.T")["proposal"])

    def test_device_branch_text(self):
        s, _, _ = session_for(h.LS["ls_md_prototype"])
        q = {x["id"]: x["question"] for x in s["queue"]["Р1"]}
        self.assertEqual(q["Q1.К1"], BANK["r1_ls"]["К1"]["device"])

    def test_it_path_no_k_questions_no_q1r(self):
        s, _, _ = session_for(h.NON_LS["it_wellness"])
        ids = [q["id"] for q in s["queue"]["Р1"]]
        self.assertFalse([i for i in ids if i.startswith("Q1.К")])
        self.assertNotIn("Q1.R", ids)
        self.assertIn("Q1.IT1", ids)

    def test_r2_candidates_priority_order(self):
        s, res, _ = session_for(h.LS["ls_preclinical_drug"])
        c = d.r2_candidates(json.loads(res.scores.model_dump_json()), M)
        ks = [x["item"] for x in c]
        prio = ["Q1." + k for k in BANK["r2_priority"]]
        self.assertEqual(ks, [k for k in prio if k in ks])


class PackBlocksTests(unittest.TestCase):
    def test_blocks_1_to_11(self):
        _, _, pack = session_for(h.LS["ls_preclinical_drug"])
        blocks = pack["pack_blocks"]
        self.assertEqual([b["n"] for b in blocks], list(range(1, 12)))
        b = {x["n"]: x for x in blocks}
        # PM 11.10: блок 1 — общие пороги yaml («рабочие»), блок 8 — 30/60/90, «не указано»
        self.assertEqual(b[1]["status"], "рабочее")
        self.assertIn(b[1]["value"], M["working"]["pack_block1"]["map"].values())
        self.assertEqual(b[8]["status"], "рабочее")
        self.assertIn("не медицинский совет", b[9]["value"])
        self.assertEqual(len(b[6]["zones"]), 7)
        for c in b[3]["claims"]:
            self.assertTrue(c["class"] and c["source"])
        blob = json.dumps(blocks, ensure_ascii=False)
        for w in ("PASS", "HOLD", "NEXT"):
            self.assertNotIn(f'"{w}"', blob)


class TextAnswerCapTests(unittest.TestCase):
    def test_dialogue_text_without_file_max_e1(self):
        c = h._claim("C30", "product_tech.k1_clinical_stage", 3, ["evidence"], "доклиника завершена (синтетика)")
        c["source_ref"] = "анкета dlg_Q1.К1"
        res, _, _ = h.run(h.LS["ls_preclinical_drug"], h.ls_classification([c]))
        got = next(x for x in res.classification.claims if x.id == "C30")
        self.assertLessEqual(got.evidence_level, 1)
        self.assertNotEqual(got.category.value, "FACT")


class Vc997SyntheticTests(unittest.TestCase):
    """A15 / VC-997 (синтетика): непроверенное утверждение из анкеты, которое модель пометила FACT/E3,
    не поднимается до «подтверждено» — код обрезает до потолка источника."""
    def test_form_claim_marked_fact_by_model_is_capped(self):
        c = h._claim("C31", "product_tech.k3_ip", 3, ["form"], "у нас патент (синтетика)")
        self.assertEqual(c["category"], "FACT")
        res, _, _ = h.run(h.LS["ls_preclinical_drug"], h.ls_classification([c]))
        got = next(x for x in res.classification.claims if x.id == "C31")
        self.assertEqual(got.evidence_level, M["source_level_cap"]["form"])
        self.assertNotEqual(got.category.value, "FACT")


class EndToEndTests(Tg1Base):
    def setUp(self):
        super().setUp()
        self.s = dataclasses.replace(self.s, free_link_secret="f" * 32, free_link_ttl_s=600, code_sha="synthetic-sha")
        self.state.settings = self.s

    def submit(self, form):
        self.c.post("/s1/webhooks/tilda", data=form, headers={"X-Webhook-Key": base.WEBHOOK_KEY})
        jid = next(j["id"] for j in self.c.get("/s1/jobs", headers=base.ADMIN).get_json()
                   if j["project_name"] == form["company"])
        return jid, self.wait(jid)

    def wait(self, jid):
        for _ in range(200):
            job = self.state.store.get(jid)
            if job["status"] not in ("received", "processing"):
                return job
            time.sleep(0.05)
        self.fail("timeout")

    # A14
    def test_code_sha_model_prompt_hash_in_report(self):
        jid = self.ready_job()
        prov = self.state.store.get(jid)["data"]["readiness_pack"]["provenance"]
        self.assertEqual(prov["code_sha"], "synthetic-sha")
        self.assertTrue(prov["model"] and prov["prompt_sha256"])

    # PM (3): подпись в Pack
    def test_pack_block11_signed_by_who_when_hash(self):
        jid = self.ready_job()
        self.assertEqual(self.c.post(f"/s1/jobs/{jid}/approve", headers=ALICE).status_code, 200)
        b11 = next(b for b in self.state.store.get(jid)["data"]["pack_blocks"] if b["n"] == 11)
        log = self.c.get(f"/s1/jobs/{jid}/approvals", headers=base.ADMIN).get_json()[-1]
        self.assertEqual(b11["signed"], {"by": "alice", "at": log["approved_at"], "report_sha256": log["report_sha256"]})

    # PM (4) / A12
    def test_approve_requires_call_done(self):
        self.c.post("/s1/webhooks/tilda", data=base.SAMPLE_FORM, headers={"X-Webhook-Key": base.WEBHOOK_KEY})
        jid = self.c.get("/s1/jobs", headers=base.ADMIN).get_json()[0]["id"]
        self.wait(jid)
        r = self.c.post(f"/s1/jobs/{jid}/approve", headers=ALICE)
        self.assertEqual(r.status_code, 409)
        self.assertIn("call_done", r.get_json()["error"])
        self.assertEqual(self.sent, [])
        self.assertEqual(self.c.post(f"/s1/jobs/{jid}/call", headers=base.ADMIN, json={}).status_code, 400)
        self.c.post(f"/s1/jobs/{jid}/call", headers=base.ADMIN, json={"outcome": "фонд готов к разговору (синтетика)"})
        self.assertEqual(self.c.post(f"/s1/jobs/{jid}/approve", headers=ALICE).status_code, 200)

    # VC-996 (синтетика): после подписи отчёт сформирован, доставка зафиксирована; сбой — видимая ошибка
    def test_vc996_delivery_recorded_and_failure_visible(self):
        jid = self.ready_job()
        self.assertEqual(self.c.post(f"/s1/jobs/{jid}/approve", headers=ALICE).status_code, 200)
        self.assertEqual(self.state.store.get(jid)["status"], "sent")
        self.assertEqual(self.c.get(f"/s1/jobs/{jid}/approvals", headers=base.ADMIN).get_json()[-1]["outcome"], "sent")
        form2 = dict(base.SAMPLE_FORM, tranid="8123456:9960", company="SyntheticFail")
        jid2, _ = self.submit(form2)
        self.c.post(f"/s1/jobs/{jid2}/call", headers=base.ADMIN, json={"outcome": "синтетика"})
        with mock.patch("s1_intake.main.send_email", side_effect=OSError("smtp down")):
            r = self.c.post(f"/s1/jobs/{jid2}/approve", headers=ALICE)
        self.assertEqual(r.status_code, 502)
        job = self.state.store.get(jid2)
        self.assertEqual(job["status"], "failed")
        self.assertIn("send failed", job["error"])
        self.assertEqual(self.c.get(f"/s1/jobs/{jid2}/approvals", headers=base.ADMIN).get_json()[-1]["outcome"],
                         "send_error")

    # пересчёт после файла
    def test_file_to_r2_item_triggers_rescoring(self):
        jid = self.ready_job()
        data = self.state.store.get(jid)["data"]
        n_files = len(data["submission"]["files"])
        s = data["dialogue"]
        if not s["r2_items"]:
            d.add_r2_item(s, "Q1.К2", "MISSING")
            self.state.store.update(jid, data_patch={"dialogue": s})
        item = self.state.store.get(jid)["data"]["dialogue"]["r2_items"][0]["item"]
        r = self.c.post(f"/s1/jobs/{jid}/dialogue/r2/file", headers=base.ADMIN,
                        json={"item": item, "url": f"{base.BASE}/loi-uuid/loi.pdf"})
        self.assertEqual(r.status_code, 200, r.get_json())
        job = self.wait(jid)
        self.assertEqual(job["status"], "ready_for_review", job["error"])
        data = job["data"]
        self.assertEqual(len(data["submission"]["files"]), n_files + 1)
        self.assertEqual(next(x for x in data["dialogue"]["r2_items"] if x["item"] == item)["state"], "закрыт файлом")
        self.assertTrue(data["parsed_docs"])  # документы пересобраны → пересчёт

    def test_text_answer_endpoint_rescoring(self):
        jid = self.ready_job()
        qid = self.state.store.get(jid)["data"]["dialogue"]["queue"]["Р1"][0]["qid"]
        r = self.c.post(f"/s1/jobs/{jid}/dialogue/answer", headers=base.ADMIN, json={"qid": qid, "answer": "синтетика"})
        self.assertEqual(r.status_code, 200, r.get_json())
        job = self.wait(jid)
        self.assertEqual(job["status"], "ready_for_review")
        self.assertIn(f"dlg_{qid}", [a["field"] for a in job["data"]["submission"]["answers"]])

    # PM (1): ограниченная по времени ссылка
    def test_free_link_time_limited(self):
        jid = self.ready_job()
        link = self.c.post(f"/s1/jobs/{jid}/free-link", headers=base.ADMIN).get_json()
        r = self.c.get(link["path"])  # без admin-токена — по подписанной ссылке
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["label"], "ИИ-оценка, не инвестиционная рекомендация")
        self.assertEqual(self.c.get(link["path"][:-1] + ("0" if link["path"][-1] != "0" else "1")).status_code, 403)
        with self.app.app_context():
            past = int(time.time()) - 10
            sig = s1main._free_token(jid, past)
        self.assertEqual(self.c.get(f"/s1/free/{jid}?exp={past}&sig={sig}").status_code, 410)
        self.assertEqual(self.sent, [])

    # PM (2): вертикаль — три пути
    def test_vertical_in_out_borderline(self):
        jid = self.ready_job()  # MEDICAL_DEVICE → in
        self.assertEqual(self.state.store.get(jid)["data"].get("vertical"), None)
        out = {"tranid": "8123456:7001", "formid": "f", "company": "SyntheticSaaS", "name": "Синт", "email": "x@example.test",
               "consent": "yes", "jurisdiction": "РФ", "product_class": "",
               "intended_use": "SaaS для бухгалтерии малого бизнеса"}
        jid_o, job = self.submit(out)
        self.assertEqual(job["status"], "out_of_scope")
        self.assertIsNone(job["verdict"])
        self.assertIsNone(job["readiness"])
        self.assertIn("Спасибо за заявку", job["data"]["vertical"]["message"])
        self.assertNotIn("readiness_pack", job["data"])
        border = dict(out, tranid="8123456:7002", company="SyntheticLab", product_class="LAB_SERVICE",
                      intended_use="лабораторный сервис")
        jid_b, job = self.submit(border)
        self.assertEqual(job["status"], "manual_review")  # не автоматический отказ
        self.assertEqual(self.c.post(f"/s1/jobs/{jid_b}/vertical", headers=base.ADMIN,
                                     json={"decision": "in"}).status_code, 400)  # нужна причина
        self.c.post(f"/s1/jobs/{jid_b}/vertical", headers=base.ADMIN, json={"decision": "in", "reason": "синтетика"})
        job = self.wait(jid_b)
        self.assertIn(job["status"], ("ready_for_review", "awaiting_founder"))


if __name__ == "__main__":
    unittest.main()
