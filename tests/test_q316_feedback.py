"""Q-316: кнопка «Сообщить о проблеме» (S1). Без внешней сети: вебхук замокан."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httpx
from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import app as appmod  # noqa: E402  (до тестов: conftest отключает RBAC только у уже импортированного app)
from s1_intake import main as s1main  # noqa: E402
from s1_intake.config import Settings  # noqa: E402
from s1_intake.main import init_s1  # noqa: E402

HOOK = "https://hook.invalid/x"


class FeedbackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        s = Settings(database_url=f"sqlite:///{self.tmp}/s1.db", llm_provider="mock")
        self.app = Flask("q316")
        self.app.secret_key = "x"
        self.state = init_s1(self.app, s)
        self.client = self.app.test_client()
        s1main._feedback_hits.clear()
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("FEEDBACK_WEBHOOK_URL", None)

    def post(self, **body):
        return self.client.post("/s1/feedback", json=body)

    def test_saved_with_number_and_context(self):
        r = self.post(text="Кнопка не нажимается", contact="a@b.c", path="/cabinet/x?token=SECRET", session_id="sess1")
        self.assertEqual(r.status_code, 201)
        j = r.get_json()
        self.assertRegex(j["number"], r"^ОС-\d+$")
        self.assertEqual(j["message"], f"Спасибо, получили. Номер обращения {j['number']}")
        row = self.state.store.get_feedback(j["number"])
        self.assertEqual((row["cabinet"], row["text"], row["contact"]), ("S1", "Кнопка не нажимается", "a@b.c"))
        self.assertEqual((row["path"], row["session_id"]), ("/cabinet/x", "sess1"))
        self.assertTrue(row["created_at"].endswith("Z"))
        self.assertTrue(row["build"])

    def test_numbers_increase(self):
        a = self.post(text="один").get_json()["number"]
        b = self.post(text="два").get_json()["number"]
        self.assertEqual(int(b[3:]), int(a[3:]) + 1)

    def test_without_webhook_url_no_error_no_network(self):
        with mock.patch.object(s1main.httpx, "post") as post:
            self.assertEqual(self.post(text="ok").status_code, 201)
        post.assert_not_called()

    def test_webhook_called_with_json(self):
        with mock.patch.dict(os.environ, {"FEEDBACK_WEBHOOK_URL": HOOK}), \
                mock.patch.object(s1main.httpx, "post") as post:
            j = self.post(text="сломалось").get_json()
        post.assert_called_once()
        args, kw = post.call_args
        self.assertEqual(args[0], HOOK)
        self.assertLessEqual(kw["timeout"], 5)
        self.assertEqual((kw["json"]["number"], kw["json"]["cabinet"], kw["json"]["text"]),
                         (j["number"], "S1", "сломалось"))

    def test_webhook_failure_still_accepted(self):
        with mock.patch.dict(os.environ, {"FEEDBACK_WEBHOOK_URL": HOOK}), \
                mock.patch.object(s1main.httpx, "post", side_effect=httpx.ConnectTimeout("t")):
            r = self.post(text="сломалось")
        self.assertEqual(r.status_code, 201)
        self.assertIsNotNone(self.state.store.get_feedback(r.get_json()["number"]))

    def test_empty_and_too_long_rejected(self):
        for body in ({"text": ""}, {"text": "   "}, {}, {"text": "x" * 2001}):
            self.assertEqual(self.client.post("/s1/feedback", json=body).status_code, 400, body)
        self.assertEqual(self.post(text="x" * 2000).status_code, 201)

    def test_rate_limit_per_session(self):
        for _ in range(5):
            self.assertEqual(self.post(text="a").status_code, 201)
        self.assertEqual(self.post(text="a").status_code, 429)
        other = self.app.test_client()
        with other.session_transaction() as s:
            s["csrf"] = "another-session"
        self.assertEqual(other.post("/s1/feedback", json={"text": "a"}).status_code, 201)


class FeedbackPageTests(unittest.TestCase):
    def test_button_and_notice_on_cabinet_page(self):
        import app as appmod
        html = appmod.app.test_client().get("/").get_data(as_text=True)
        self.assertIn("Сообщить о проблеме", html)
        self.assertIn("Не вводите персональные данные пациентов и закрытые сведения", html)

    def test_feedback_route_has_rbac_policy(self):
        import app as appmod
        self.assertTrue(hasattr(appmod.app.view_functions["s1_intake.feedback_submit"], "_rbac"))


if __name__ == "__main__":
    unittest.main()
