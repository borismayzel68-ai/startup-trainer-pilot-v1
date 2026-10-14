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


def make_app(env=ENV):
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

    sg.init_staging(app, env)
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


class YandexCloudDeployTests(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(ROOT / "scripts"))

    def test_render_blueprint_removed(self):
        self.assertFalse((ROOT / "render.yaml").exists())

    def test_current_migrations_additive_only(self):
        import migration_guard
        self.assertEqual(migration_guard.destructive_migrations(), [])
        self.assertEqual(migration_guard.main(), 0)

    def test_guard_detects_destructive_sql(self):
        import migration_guard as g
        for sql in ("DROP TABLE x;", "truncate t;", "DELETE FROM t WHERE a=1;", "UPDATE t SET a=1;",
                    "ALTER TABLE t ALTER COLUMN a TYPE text;", "ALTER TABLE t RENAME TO u;",
                    "ALTER TABLE t DROP COLUMN a;"):
            self.assertTrue(g.is_destructive(sql), sql)
        for sql in ("CREATE TABLE IF NOT EXISTS t (a int REFERENCES u(id) ON DELETE CASCADE);",
                    "INSERT INTO t VALUES (1) ON CONFLICT (a) DO UPDATE SET b=1;",
                    "-- DROP TABLE x;\nALTER TABLE t ADD COLUMN c int;"):
            self.assertFalse(g.is_destructive(sql), sql)

    def test_migrate_job_requires_snapshot_for_destructive(self):
        import migrate_job
        with mock.patch.object(migrate_job.migration_guard, "destructive_migrations", return_value=["003_x.sql"]), \
                mock.patch.object(migrate_job.hq018_migrate, "main") as m:
            code, body = migrate_job.run({})
            self.assertEqual((code, body["error"]), (409, "snapshot_required"))
            m.assert_not_called()
            code, body = migrate_job.run({"snapshot_id": "b1"})
            self.assertEqual(code, 200)
            m.assert_called_once()

    def test_migrate_job_reports_failure(self):
        import migrate_job
        with mock.patch.object(migrate_job.migration_guard, "destructive_migrations", return_value=[]), \
                mock.patch.object(migrate_job.hq018_migrate, "main", side_effect=RuntimeError("boom")):
            self.assertEqual(migrate_job.run({})[0], 500)

    def test_ci_tests_job_has_no_cloud_secrets_and_no_deploy(self):
        text = (ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
        for bad in ("secrets.", "YC_", "yc ", "LOCKBOX", "migrate", "deploy", "RENDER"):
            self.assertNotIn(bad, text)
        self.assertIn("postgres:16", text)

    def test_deploy_scripts_have_no_secret_values_and_migrate_outside_web(self):
        deploy = (ROOT / "deploy" / "yc" / "deploy.sh").read_text(encoding="utf-8")
        self.assertIn("migration_guard.py", deploy)
        self.assertIn("cluster backup", deploy)
        self.assertLess(deploy.index("cluster backup"), deploy.index("/run"))
        self.assertLess(deploy.index("/run"), deploy.index("--container-name \"$WEB\""))
        web = (ROOT / "deploy" / "yc" / "web-entrypoint.sh").read_text(encoding="utf-8")
        code = "\n".join(l for l in web.splitlines() if not l.startswith("#"))
        self.assertNotIn("migrate", code)
        self.assertNotIn("seed", code)
        for f in (ROOT / "deploy" / "yc").iterdir():
            t = f.read_text(encoding="utf-8")
            for key in ("SESSION_SECRET", "GOOGLE_CLIENT_SECRET", "AUTH_ALLOWLIST"):
                self.assertNotRegex(t, key + r"=[^,\s\"$]")

    def test_env_example_names_only(self):
        for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
            for key in ("GOOGLE_CLIENT_SECRET", "SESSION_SECRET", "AUTH_ALLOWLIST", "DATABASE_URL"):
                if line.startswith(key + "="):
                    self.assertEqual(line.split("=", 1)[1].split("#")[0].strip(), "")


if __name__ == "__main__":
    unittest.main()
