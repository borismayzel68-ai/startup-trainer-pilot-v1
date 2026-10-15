"""MOS-6 · staging: старт с env, понятная ошибка без секрета, закрытые маршруты, noindex, seed синтетики.
Только синтетические значения; сеть замокана (httpx)."""
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from flask import Flask

import staging_guard as sg

ROOT = Path(__file__).resolve().parent.parent
ENV = {
    "APP_ENV": "staging",
    "DATABASE_URL": "postgresql://u:p@db.invalid:5432/x",
    "GOOGLE_CLIENT_ID": "synthetic-client-id",
    "GOOGLE_CLIENT_SECRET": "synthetic-client-secret",
    "SESSION_SECRET": "s" * 40,
    "AUTH_ALLOWLIST": "Allowed.Synth@example.test, other@example.test",
}


def make_app(env=ENV, data_check=lambda url: None):
    app = Flask(__name__)
    app.secret_key = "x"

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.get("/robots.txt")
    def robots():
        return "User-agent: *\nDisallow: /\n"

    @app.get("/")
    def home():
        return "home"

    @app.post("/api/start")
    def api():
        return {"ok": True}

    sg.init_staging(app, env, data_check=data_check)
    return app


class FakeResp:
    def __init__(self, data):
        self._d = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._d


class StagingTests(unittest.TestCase):
    def setUp(self):
        self.c = make_app().test_client()
        self.base = "https://localhost"

    def get(self, path, **kw):
        return self.c.get(path, base_url=self.base, **kw)

    def test_missing_secret_clear_error(self):
        for key in sg.REQUIRED_ENV:
            env = {k: v for k, v in ENV.items() if k != key}
            with self.assertRaises(sg.StagingConfigError) as cm:
                make_app(env)
            self.assertIn(key, str(cm.exception))

    def test_short_session_secret_rejected(self):
        with self.assertRaises(sg.StagingConfigError):
            make_app(dict(ENV, SESSION_SECRET="short"))

    def test_non_staging_does_not_gate(self):
        c = make_app({}).test_client()
        self.assertEqual(c.get("/").status_code, 200)

    def test_anonymous_blocked_except_health_robots(self):
        self.assertEqual(self.get("/health").status_code, 200)
        self.assertIn("Disallow: /", self.get("/robots.txt").get_data(as_text=True))
        r = self.get("/")
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r.headers["Location"].endswith("/auth/login"))
        self.assertEqual(self.c.post("/api/start", base_url=self.base).status_code, 401)
        self.assertEqual(self.get("/nonexistent").status_code, 302)

    def test_noindex_header_everywhere(self):
        for path in ("/health", "/robots.txt", "/", "/auth/login"):
            self.assertIn("noindex", self.get(path).headers["X-Robots-Tag"], path)

    def _login(self, userinfo):
        loc = self.get("/auth/login").headers["Location"]
        self.assertTrue(loc.startswith(sg.GOOGLE_AUTH_URL))
        with self.c.session_transaction(base_url=self.base) as s:
            state = s["oauth_state"]
        with mock.patch.object(sg.httpx, "post", return_value=FakeResp({"access_token": "t"})), \
                mock.patch.object(sg.httpx, "get", return_value=FakeResp(userinfo)):
            return self.get(f"/auth/callback?code=c&state={state}")

    def test_allowlisted_user_gets_in(self):
        r = self._login({"email": "ALLOWED.synth@example.test", "email_verified": True})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.get("/").status_code, 200)

    def test_not_in_allowlist_forbidden(self):
        r = self._login({"email": "stranger@example.test", "email_verified": True})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.get("/").status_code, 302)

    def test_unverified_email_forbidden(self):
        r = self._login({"email": "other@example.test", "email_verified": False})
        self.assertEqual(r.status_code, 403)

    def test_bad_state_rejected(self):
        self.get("/auth/login")
        self.assertEqual(self.get("/auth/callback?code=c&state=wrong").status_code, 400)


class RealAppTests(unittest.TestCase):
    def run_app(self, extra):
        env = {k: v for k, v in os.environ.items() if k not in sg.REQUIRED_ENV and k != "APP_ENV"}
        env.update({"OPENAI_API_KEY": "test-key", "SESSION_DATABASE_URL": "sqlite:///:memory:"}, **extra)
        code = ("import app; c=app.app.test_client(); r=c.get('/health'); "
                "print(r.status_code, r.headers.get('X-Robots-Tag')); "
                "print(c.get('/', base_url='https://localhost').status_code)")
        return subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True)

    def test_real_app_starts_with_example_env(self):
        p = self.run_app(dict(ENV, DATABASE_URL="sqlite:///:memory:"))
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("200 noindex", p.stdout)
        self.assertTrue(p.stdout.strip().endswith("302"))

    def test_real_app_fails_without_secret(self):
        p = self.run_app({k: v for k, v in ENV.items() if k != "GOOGLE_CLIENT_SECRET"})
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("GOOGLE_CLIENT_SECRET", p.stderr)

    def test_real_app_non_staging_has_noindex(self):
        p = self.run_app({})
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("200 noindex", p.stdout)


class SeedTests(unittest.TestCase):
    def test_seed_rows_synthetic(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        import seed_synthetic
        seed_synthetic.assert_synthetic_rows()
        for email, _, _ in seed_synthetic.hq018_seed.USERS:
            self.assertTrue(email.endswith("@example.test"))

    def test_seed_rejects_non_synthetic(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        import seed_synthetic
        with mock.patch.object(seed_synthetic.hq018_seed, "USERS", [("real@gmail.com", "x", "founder")]):
            with self.assertRaises(SystemExit):
                seed_synthetic.assert_synthetic_rows()

    def test_render_yaml_removed(self):
        self.assertFalse((ROOT / "render.yaml").exists())

    def test_deploy_proposal_skips_without_secret_and_has_no_hardcoded_ids(self):
        text = (ROOT / "docs/deploy/yc-deploy.yml.proposed").read_text(encoding="utf-8")
        self.assertIn('-n "$YC_SA_KEY"', text)
        self.assertIn("steps.gate.outputs.enabled == 'true'", text)
        self.assertNotIn("render", text.lower())
        for ident in ("b1glq3r8033s27m72v2u", "b1gl7ikgct8sob9mtm1g"):
            self.assertNotIn(ident, text)
        self.assertFalse((ROOT / ".github/workflows/yc-deploy.yml").exists())


class SyntheticOnlyTests(unittest.TestCase):
    def test_flag_default_true(self):
        self.assertTrue(sg.synthetic_only({}))
        self.assertTrue(sg.synthetic_only({"STAGING_SYNTHETIC_ONLY": ""}))
        self.assertTrue(sg.synthetic_only({"STAGING_SYNTHETIC_ONLY": "TRUE"}))

    def test_flag_off_refuses_start(self):
        for v in ("false", "0", "no", "off", "garbage"):
            with self.assertRaises(sg.StagingConfigError, msg=v) as cm:
                make_app(dict(ENV, STAGING_SYNTHETIC_ONLY=v))
            self.assertIn("STAGING_SYNTHETIC_ONLY", str(cm.exception))

    def test_flag_unset_starts(self):
        make_app(ENV)

    def test_non_staging_ignores_flag(self):
        make_app({"STAGING_SYNTHETIC_ONLY": "false"})

    def test_data_check_failure_refuses_start(self):
        def bad(url):
            raise sg.StagingConfigError("в users найдено не-синтетических записей: 1")
        with self.assertRaises(sg.StagingConfigError):
            make_app(ENV, data_check=bad)

    def test_data_check_receives_database_url(self):
        seen = []
        make_app(ENV, data_check=seen.append)
        self.assertEqual(seen, [ENV["DATABASE_URL"]])

    def _fake_pg(self, count=None, exc=None):
        import psycopg
        conn = mock.MagicMock()
        conn.__enter__.return_value = conn
        if exc:
            conn.execute.side_effect = exc
        else:
            conn.execute.return_value.fetchone.return_value = (count,)
        return mock.patch.object(psycopg, "connect", return_value=conn)

    def test_check_rejects_real_users(self):
        with self._fake_pg(count=2):
            with self.assertRaises(sg.StagingConfigError) as cm:
                sg.check_synthetic_data("postgresql://x")
        self.assertIn("не-синтетических", str(cm.exception))

    def test_check_passes_on_synthetic_only(self):
        with self._fake_pg(count=0):
            sg.check_synthetic_data("postgresql://x")

    def test_check_missing_table_ok(self):
        from psycopg import errors
        with self._fake_pg(exc=errors.UndefinedTable("no users")):
            sg.check_synthetic_data("postgresql://x")

    def test_check_unreachable_db_fails_closed(self):
        import psycopg
        with mock.patch.object(psycopg, "connect", side_effect=psycopg.OperationalError("down")):
            with self.assertRaises(sg.StagingConfigError):
                sg.check_synthetic_data("postgresql://x")

    def test_real_app_refuses_when_flag_off(self):
        p = RealAppTests().run_app(dict(ENV, DATABASE_URL="sqlite:///:memory:", STAGING_SYNTHETIC_ONLY="false"))
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("STAGING_SYNTHETIC_ONLY", p.stderr)


if __name__ == "__main__":
    unittest.main()
