"""TG-1: Human Gate. Нет автоотправки; approve — только персональная подпись с журналом;
повторная отправка того же отчёта заблокирована; LLM — фиксированная модель, temperature 0, хеш промпта.
Данные синтетические, LLM — mock, SMTP — подмена (наружу ничего не уходит)."""
import dataclasses
import hashlib
import json
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_s1_intake as base  # noqa: E402
import s1_mock_llm  # noqa: E402
from pydantic import BaseModel  # noqa: E402
from s1_intake import config as cfg  # noqa: E402
from s1_intake.llm import LLMClient  # noqa: E402
from s1_intake.main import init_s1  # noqa: E402
from flask import Flask  # noqa: E402

ALICE_TOKEN = "alice-synthetic-token-000000000001"
BOB_TOKEN = "bob-synthetic-token-00000000000002"
ALICE = {"Authorization": "Bearer " + ALICE_TOKEN}
BOB = {"Authorization": "Bearer " + BOB_TOKEN}
ROOT = Path(__file__).resolve().parent.parent


class Tg1Base(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()
        self.s = dataclasses.replace(base.settings(self.tmp), approvers={"alice": ALICE_TOKEN, "bob": BOB_TOKEN},
                                     smtp_host="smtp.example.test", smtp_from="s1@example.test")
        self.app = Flask("tg1")
        self.state = init_s1(self.app, self.s, llm_factory=lambda: LLMClient(self.s, s1_mock_llm.responder),
                             http_client=base.mock_http())
        self.c = self.app.test_client()
        self.sent = []
        p = mock.patch("s1_intake.main.send_email", side_effect=lambda s, to, subj, html, att: self.sent.append(to))
        p.start()
        self.addCleanup(p.stop)

    def ready_job(self):
        self.c.post("/s1/webhooks/tilda", data=base.SAMPLE_FORM, headers={"X-Webhook-Key": base.WEBHOOK_KEY})
        jid = self.c.get("/s1/jobs", headers=base.ADMIN).get_json()[0]["id"]
        t = base.S1EndToEndTests.wait
        job = t(self, jid)
        self.assertEqual(job["status"], "ready_for_review", job["error"])
        return jid


class NoAutoSendTests(Tg1Base):
    def test_setting_removed(self):
        self.assertFalse(hasattr(self.s, "auto_send_founder_report"))
        for f in ("s1_intake/config.py", "s1_intake/main.py", ".env.example"):
            self.assertNotIn("S1_AUTO_SEND_FOUNDER_REPORT", (ROOT / f).read_text(encoding="utf-8"), f)

    def test_pipeline_never_sends_to_founder(self):
        with mock.patch.dict("os.environ", {"S1_AUTO_SEND_FOUNDER_REPORT": "true"}):
            jid = self.ready_job()
        self.assertEqual(self.sent, [])
        self.assertEqual(self.state.store.get(jid)["status"], "ready_for_review")


class ApproveSignatureTests(Tg1Base):
    def test_shared_admin_token_cannot_approve(self):
        jid = self.ready_job()
        self.assertEqual(self.c.post(f"/s1/jobs/{jid}/approve", headers=base.ADMIN).status_code, 403)
        self.assertEqual(self.c.post(f"/s1/jobs/{jid}/approve").status_code, 401)
        self.assertEqual(self.sent, [])

    def test_personal_approve_logs_who_when_what(self):
        jid = self.ready_job()
        r = self.c.post(f"/s1/jobs/{jid}/approve", headers=ALICE)
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(r.get_json()["approved_by"], "alice")
        self.assertEqual(len(self.sent), 1)
        log = self.c.get(f"/s1/jobs/{jid}/approvals", headers=base.ADMIN).get_json()
        self.assertEqual(len(log), 1)
        html = self.state.store.get(jid)["data"]["founder_report_html"]
        self.assertEqual(log[0]["approver"], "alice")
        self.assertEqual(log[0]["outcome"], "sent")
        self.assertEqual(log[0]["report_sha256"], hashlib.sha256(html.encode()).hexdigest())
        self.assertRegex(log[0]["approved_at"], r"^\d{4}-\d\d-\d\dT")

    def test_short_tokens_ignored(self):
        with mock.patch.dict("os.environ", {"S1_APPROVERS": "eve:short;carol:" + "c" * 24}):
            self.assertEqual(cfg._env_approvers("S1_APPROVERS"), {"carol": "c" * 24})


class NoDoubleSendTests(Tg1Base):
    def test_second_approve_blocked_and_logged(self):
        jid = self.ready_job()
        self.assertEqual(self.c.post(f"/s1/jobs/{jid}/approve", headers=ALICE).status_code, 200)
        self.assertEqual(self.c.post(f"/s1/jobs/{jid}/approve", headers=BOB).status_code, 409)
        self.assertEqual(len(self.sent), 1)
        outcomes = [(a["approver"], a["outcome"]) for a in self.state.store.approvals(jid)]
        self.assertEqual(outcomes, [("alice", "sent"), ("bob", "blocked_duplicate")])

    def test_rerun_same_report_not_sent_again(self):
        jid = self.ready_job()
        self.c.post(f"/s1/jobs/{jid}/approve", headers=ALICE)
        self.assertEqual(self.c.post(f"/s1/jobs/{jid}/rerun", headers=base.ADMIN).status_code, 200)
        base.S1EndToEndTests.wait(self, jid)
        self.assertEqual(self.c.post(f"/s1/jobs/{jid}/approve", headers=BOB).status_code, 409)
        self.assertEqual(len(self.sent), 1)

    def test_parallel_approves_send_once(self):
        jid = self.ready_job()
        codes = []

        def go(h):
            with self.app.test_client() as c:
                codes.append(c.post(f"/s1/jobs/{jid}/approve", headers=h).status_code)
        ts = [threading.Thread(target=go, args=(h,)) for h in (ALICE, BOB, ALICE, BOB)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(sorted(codes), [200, 409, 409, 409])
        self.assertEqual(len(self.sent), 1)


class _Out(BaseModel):
    ok: bool


class LlmReproducibilityTests(unittest.TestCase):
    def _client(self):
        s = cfg.Settings(llm_provider="mock")
        c = LLMClient(s, lambda stage, user: {"ok": True})
        captured = []

        class FakeResponses:
            def create(self, **kw):
                captured.append(kw)
                return type("R", (), {"output_text": json.dumps({"ok": True})})()
        c.provider, c.model_name = "openai", cfg.LLM_MODEL_ID
        c._client = type("C", (), {"responses": FakeResponses()})()
        return c, captured

    def test_fixed_model_and_temperature_zero(self):
        c, captured = self._client()
        c.complete_json("classify", "sys", "synthetic user", _Out)
        self.assertEqual(captured[0]["model"], cfg.LLM_MODEL_ID)
        self.assertEqual(captured[0]["temperature"], 0)
        self.assertRegex(cfg.LLM_MODEL_ID, r"\d{4}-\d\d-\d\d$")  # снапшот, не алиас

    def test_env_cannot_override_model(self):
        with mock.patch.dict("os.environ", {"S1_OPENAI_MODEL": "other", "OPENAI_MODEL": "other"}):
            self.assertEqual(cfg.Settings().openai_model, cfg.LLM_MODEL_ID)

    def test_prompt_hash_recorded_per_call(self):
        c, _ = self._client()
        c.complete_json("classify", "sys A", "u1", _Out)
        c.complete_json("synthesis", "sys B", "u1", _Out)
        self.assertEqual(len(c.calls), 2)
        for rec in c.calls:
            self.assertEqual(rec["model"], cfg.LLM_MODEL_ID)
            self.assertEqual(rec["temperature"], 0)
            self.assertRegex(rec["prompt_sha256"], r"^[0-9a-f]{64}$")
        self.assertNotEqual(c.calls[0]["prompt_sha256"], c.calls[1]["prompt_sha256"])
        c2, _ = self._client()
        c2.complete_json("classify", "sys A", "u1", _Out)
        self.assertEqual(c2.calls[0]["prompt_sha256"], c.calls[0]["prompt_sha256"])  # детерминирован

    def test_job_stores_llm_calls(self):
        t = Tg1Base("ready_job")
        t.setUp()
        try:
            jid = t.ready_job()
            calls = t.state.store.get(jid)["data"]["llm_calls"]
            self.assertGreaterEqual(len(calls), 4)
            self.assertTrue(all("prompt_sha256" in x and x["temperature"] == 0 for x in calls))
        finally:
            t.doCleanups()


if __name__ == "__main__":
    unittest.main()
