"""MOS-7: base_url и модель берутся из env в обоих местах (app.py и s1_intake). Без сети."""
import importlib
import os
import sys
import unittest
from unittest import mock

from pydantic import BaseModel

from s1_intake import config as cfg
from s1_intake.llm import CALLS_MAXLEN, LLMClient, LLMError, check_prod_config

BASE = "https://llm.api.cloud.yandex.net/v1"
YA_MODEL = "gpt://b1gsynthetic/deepseek-v4-flash"


_env_patch = None


def setUpModule():
    global _env_patch
    _env_patch = mock.patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    _env_patch.start()
    os.environ.setdefault("SESSION_DATABASE_URL", "sqlite:///:memory:")


def tearDownModule():
    _env_patch.stop()


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
        import app as appmod
        saved = dict(vars(appmod))
        try:
            with mock.patch.dict(os.environ, env):
                importlib.reload(appmod)
                got = {}
                appmod.client._client.responses.create = lambda **kw: (got.update(kw), FakeResp())[1]
                out = appmod.call_json("Return JSON only", "x")
                self.assertEqual(str(appmod.client._client.base_url).rstrip("/"), BASE)
                self.assertEqual(appmod.MODEL, YA_MODEL)
        finally:
            # откат: тот же объект модуля в sys.modules, исходные MODEL/client/функции
            vars(appmod).clear()
            vars(appmod).update(saved)
            sys.modules["app"] = appmod
        self.assertEqual(got["model"], YA_MODEL)
        self.assertEqual(out["covered"], [])
        self.assertNotIn("temperature", got)
        self.assertNotEqual(appmod.MODEL, YA_MODEL)

    def test_complete_dict_default_formats_keep_json_object(self):
        for fmt in ("json_object", "json_schema"):
            with mock.patch.dict(os.environ, {"S1_LLM_PROVIDER": "openai", "LLM_JSON_FORMAT": fmt}):
                c, got = captured_client(cfg.Settings())
            self.assertEqual(c.complete_dict("t", "sys", "usr")["a"], 1)
            self.assertEqual(got["text"]["format"], {"type": "json_object"}, fmt)

    def test_format_none_omits_text_format(self):
        class Wrapped:
            output_text = 'Вот ответ: {"a": 1, "covered": []} готово'

        with mock.patch.dict(os.environ, {"S1_LLM_PROVIDER": "openai", "LLM_JSON_FORMAT": "none"}):
            c, got = captured_client(cfg.Settings())
        c._client.responses.create = lambda **kw: (got.update(kw), Wrapped())[1]
        self.assertEqual(c.complete_dict("t", "sys", "usr")["a"], 1)
        self.assertNotIn("text", got)
        got.clear()
        self.assertEqual(c.complete_json("t", "sys", "usr", Out).a, 1)
        self.assertNotIn("text", got)

    def test_json_schema_fallback(self):
        with mock.patch.dict(os.environ, {"S1_LLM_PROVIDER": "openai", "LLM_JSON_FORMAT": "json_schema"}):
            c, got = captured_client(cfg.Settings())
        c.complete_json("t", "sys", "usr", Out)
        self.assertEqual(got["text"]["format"]["type"], "json_schema")
        self.assertIn("a", got["text"]["format"]["schema"]["properties"])

    def test_calls_log_is_bounded(self):
        with mock.patch.dict(os.environ, {"S1_LLM_PROVIDER": "openai"}):
            c, _ = captured_client(cfg.Settings())
        for _ in range(100):
            c.complete_dict("t", "sys", "usr")
        self.assertLessEqual(len(c.calls), CALLS_MAXLEN)
        self.assertEqual(len(c.calls), CALLS_MAXLEN)

    def test_mock_provider_rejected_in_prod(self):
        with mock.patch.dict(os.environ, {"S1_LLM_PROVIDER": "mock"}):
            st = cfg.Settings()
        for env in ("production", "staging", " Production "):
            with self.assertRaises(LLMError):
                check_prod_config(st, env)
        for env in ("", "dev", "test"):
            check_prod_config(st, env)
        with mock.patch.dict(os.environ, {"S1_LLM_PROVIDER": "openai"}):
            check_prod_config(cfg.Settings(), "production")

    def test_staging_requires_ru_base_url(self):
        for base in ("", "https://api.openai.com/v1"):
            with mock.patch.dict(os.environ, {"S1_LLM_PROVIDER": "openai", "OPENAI_BASE_URL": base}):
                with self.assertRaises(LLMError):
                    check_prod_config(cfg.Settings(), "staging")
        with mock.patch.dict(os.environ, {"S1_LLM_PROVIDER": "openai",
                                          "OPENAI_BASE_URL": "https://llm.api.cloud.yandex.net/v1"}):
            check_prod_config(cfg.Settings(), "staging")


if __name__ == "__main__":
    unittest.main()
