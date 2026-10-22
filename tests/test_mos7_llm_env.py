"""MOS-7: base_url и модель берутся из env в обоих местах (app.py и s1_intake). Без сети."""
import importlib
import os
import unittest
from unittest import mock

os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("SESSION_DATABASE_URL", "sqlite:///:memory:")

from pydantic import BaseModel

from s1_intake import config as cfg
from s1_intake.llm import LLMClient

BASE = "https://llm.api.cloud.yandex.net/v1"
YA_MODEL = "gpt://b1gsynthetic/deepseek-v4-flash"


class Out(BaseModel):
    a: int


class FakeResp:
    output_text = '{"a": 1, "covered": []}'


def captured_client(settings):
    c = LLMClient(settings)
    got = {}

    def create(**kw):
        got.update(kw)
        return FakeResp()

    c._client.responses.create = create
    return c, got


class LLMEnvTests(unittest.TestCase):
    def test_s1_base_url_and_model_from_env(self):
        env = {"S1_LLM_PROVIDER": "openai", "OPENAI_API_KEY": "k", "OPENAI_BASE_URL": BASE,
               "S1_LLM_MODEL_ID": YA_MODEL}
        with mock.patch.dict(os.environ, env):
            c, got = captured_client(cfg.Settings())
        self.assertEqual(str(c._client.base_url).rstrip("/"), BASE)
        c.complete_json("t", "sys", "usr", Out)
        self.assertEqual(got["model"], YA_MODEL)
        self.assertEqual(c.calls[0]["model"], YA_MODEL)

    def test_s1_default_model_still_pinned(self):
        with mock.patch.dict(os.environ, {"OPENAI_MODEL": "x", "S1_OPENAI_MODEL": "x"}):
            self.assertEqual(cfg.Settings().openai_model, cfg.LLM_MODEL_ID)

    def test_app_base_url_and_model_from_env(self):
        env = {"S1_LLM_PROVIDER": "openai", "OPENAI_API_KEY": "k", "OPENAI_BASE_URL": BASE,
               "OPENAI_MODEL": YA_MODEL}
        with mock.patch.dict(os.environ, env):
            import app as appmod
            appmod = importlib.reload(appmod)
            got = {}
            appmod.client._client.responses.create = lambda **kw: (got.update(kw), FakeResp())[1]
            out = appmod.call_json("Return JSON only", "x")
            self.assertEqual(str(appmod.client._client.base_url).rstrip("/"), BASE)
        self.assertEqual(appmod.MODEL, YA_MODEL)
        self.assertEqual(got["model"], YA_MODEL)
        self.assertEqual(out["covered"], [])
        self.assertNotIn("temperature", got)

    def test_json_schema_fallback(self):
        with mock.patch.dict(os.environ, {"S1_LLM_PROVIDER": "openai", "LLM_JSON_FORMAT": "json_schema"}):
            c, got = captured_client(cfg.Settings())
        c.complete_json("t", "sys", "usr", Out)
        self.assertEqual(got["text"]["format"]["type"], "json_schema")
        self.assertIn("a", got["text"]["format"]["schema"]["properties"])


if __name__ == "__main__":
    unittest.main()
