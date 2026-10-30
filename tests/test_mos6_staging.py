"""MOS-6 · staging: старт с env, понятная ошибка без секрета, замок по сессии MOS-1 (#61), noindex, только синтетика.
Только синтетические значения; сеть не используется (вход #61 подменён заглушкой сессии)."""
import os
import shutil
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
    "AUTH_ALLOWLIST": "Allowed.Synth@example.test:owner, other@example.test:fund_staff:*",
    "PUBLIC_BASE_URL": "https://staging.example.test",
}


def make_app(env=ENV, data_check=lambda url, operators=(): None, user=lambda: None):
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

    sg.init_staging(app, env, data_check=data_check, user_loader=user)
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

    def test_no_own_oauth_in_staging_guard(self):
        src = (ROOT / "staging_guard.py").read_text(encoding="utf-8")
        for bad in ("accounts.google.com", "oauth2.googleapis.com", "httpx", "client_secret", "/auth/callback"):
            self.assertNotIn(bad, src)
        self.assertFalse(hasattr(sg, "GOOGLE_AUTH_URL"))

    def test_session_of_mos1_opens_pages(self):
        user = {"email": "allowed.synth@example.test", "grants": [("owner", "*")]}
        c = make_app(user=lambda: user).test_client()
        self.assertEqual(c.get("/", base_url=self.base).status_code, 200)
        self.assertEqual(c.post("/api/start", base_url=self.base).status_code, 200)

    def test_open_paths_do_not_need_session(self):
        for path in ("/health", "/robots.txt"):
            self.assertEqual(self.get(path).status_code, 200)

    def test_missing_mos1_module_refuses_start(self):
        with mock.patch.dict(sys.modules, {"auth": None}):
            with self.assertRaises(sg.StagingConfigError) as cm:
                sg.mos1_user_loader()
        self.assertIn("#61", str(cm.exception))

    def test_loader_uses_current_user_of_auth_module(self):
        fake = mock.MagicMock()
        with mock.patch.dict(sys.modules, {"auth": fake}):
            self.assertIs(sg.mos1_user_loader(), fake.current_user)

    def test_comment_text_as_value_refused(self):
        """Проверка стенда 30.10: cp .env.example .env.staging без заполнения — compose отдаёт текст комментария
        как значение; SESSION_SECRET длиннее 32 символов и известен из git. Staging не стартует."""
        with self.assertRaises(sg.StagingConfigError) as e:
            make_app(dict(ENV, SESSION_SECRET="# Lockbox; openssl rand -hex 32 (≥32 символов)",
                          S1_ADMIN_TOKEN="# openssl rand -hex 32 (≥24 символа); Authorization: Bearer"))
        self.assertIn("S1_ADMIN_TOKEN, SESSION_SECRET", str(e.exception))

    def test_founder_without_scope_refused(self):
        """Проверка стенда 30.10: email:founder без scope = «*» (все кейсы) — staging не стартует."""
        for raw in ("f.synth@example.test:founder", "f.synth@example.test:founder:", "f.synth@example.test:founder:*"):
            with self.assertRaises(sg.StagingConfigError):
                make_app(dict(ENV, AUTH_ALLOWLIST="allowed.synth@example.test:owner," + raw))
        make_app(dict(ENV, AUTH_ALLOWLIST="allowed.synth@example.test:owner,f.synth@example.test:founder:VC-1001"))
        self.assertEqual(sg.founders_without_scope("a@x.test:owner;b@x.test:founder\nc@x.test:founder:VC-1"), ["b@x.test"])

    def test_allowlist_emails_mos1_format(self):
        self.assertEqual(sg.allowlist_emails("A@x.test:owner, b@x.test:investor:CASE-1;\nc@x.test"),
                         ["a@x.test", "b@x.test", "c@x.test"])


class RealAppTests(unittest.TestCase):
    def run_app(self, extra):
        env = {k: v for k, v in os.environ.items() if k not in sg.REQUIRED_ENV and k not in ("APP_ENV", "PUBLIC_BASE_URL")}
        # MOS-7 (#79): mock-провайдер запрещён при APP_ENV=staging — как в deploy.sh, провайдер openai (ключ синтетический, сети нет).
        env.update({"OPENAI_API_KEY": "test-key", "S1_LLM_PROVIDER": "openai",
                    "OPENAI_BASE_URL": "https://llm.api.cloud.yandex.net/v1", "SESSION_DATABASE_URL": "sqlite:///:memory:"}, **extra)
        code = ("import app; c=app.app.test_client(); r=c.get('/health'); "
                "print(r.status_code, r.headers.get('X-Robots-Tag')); "
                "print(c.get('/', base_url='https://localhost').status_code)")
        return subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True)

    def test_real_app_starts_with_example_env_on_mos1_auth(self):
        # #61 влит: используется настоящий auth.current_user, заглушки нет.
        p = self.run_app(dict(ENV, DATABASE_URL="sqlite:///:memory:"))
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("200 noindex", p.stdout)
        self.assertTrue(p.stdout.strip().endswith("302"))

    def test_real_app_fails_without_secret(self):
        p = self.run_app({k: v for k, v in ENV.items() if k != "GOOGLE_CLIENT_SECRET"})
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("GOOGLE_CLIENT_SECRET", p.stderr)

    def test_real_app_fails_without_public_base_url(self):
        p = self.run_app({k: v for k, v in ENV.items() if k != "PUBLIC_BASE_URL"})
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("PUBLIC_BASE_URL", p.stderr)

    def test_real_app_non_staging_has_noindex(self):
        p = self.run_app({})
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("200 noindex", p.stdout)


class SeedTests(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        import seed_synthetic
        self.seed = seed_synthetic

    def test_seed_marks_by_flag_not_by_email_domain(self):
        src = (ROOT / "scripts" / "seed_synthetic.py").read_text(encoding="utf-8")
        self.assertIn("is_synthetic", src)
        self.assertNotIn("example.test", src.split('"""', 2)[2])  # вне докстринга домен не используется как признак
        self.assertNotIn("endswith", src)

    def _conn(self, rows=None, count=0):
        conn = mock.MagicMock()
        conn.execute.return_value.fetchone.return_value = (count,)
        conn.execute.return_value.fetchall.return_value = rows or []
        return conn

    def test_non_synthetic_count_excludes_operators(self):
        conn = self._conn(count=0)
        self.assertEqual(self.seed.non_synthetic_count(conn, ["op@x.test"]), 0)
        self.assertEqual(conn.execute.call_args[0][1], (["op@x.test"],))

    def test_seed_refuses_on_non_synthetic_rows(self):
        conn = mock.MagicMock()
        conn.__enter__.return_value = conn
        conn.execute.return_value.fetchone.return_value = (3,)
        import psycopg
        with mock.patch.dict(os.environ, {"DATABASE_URL": "postgresql://x"}), \
                mock.patch.object(psycopg, "connect", return_value=conn), \
                mock.patch.object(self.seed.hq018_seed, "main") as seed_main:
            with self.assertRaises(SystemExit):
                self.seed.main()
            seed_main.assert_not_called()

    def test_unflagged_seed_users_detected(self):
        self.assertEqual(self.seed.unflagged_seed_users(self._conn(rows=[("founder.synth@example.test",)])),
                         ["founder.synth@example.test"])


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
        def bad(url, operators=()):
            raise sg.StagingConfigError("в users найдено не-синтетических записей: 1")
        with self.assertRaises(sg.StagingConfigError):
            make_app(ENV, data_check=bad)

    def test_data_check_receives_database_url(self):
        seen = []
        make_app(ENV, data_check=lambda url, ops: seen.append((url, ops)))
        self.assertEqual(seen[-1], (ENV["DATABASE_URL"], ["allowed.synth@example.test", "other@example.test"]))

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
                sg.check_synthetic_data("postgresql://x", [])
        self.assertIn("без is_synthetic", str(cm.exception))

    def test_check_sql_uses_flag_not_email_domain(self):
        with self._fake_pg(count=0) as conn_patch:
            sg.check_synthetic_data("postgresql://x", ["op@x.test"])
        sql, params = conn_patch.return_value.execute.call_args[0]
        self.assertIn("is_synthetic", sql)
        self.assertNotIn("LIKE", sql.upper())
        self.assertNotIn("example", sql + str(params))
        self.assertEqual(params, (["op@x.test"],))

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

class YandexCloudDeployTests(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(ROOT / "scripts"))

    def test_render_blueprint_removed(self):
        self.assertFalse((ROOT / "render.yaml").exists())

    def test_workflow_stays_proposed_and_has_no_hardcoded_ids(self):
        text = (ROOT / "docs/deploy/yc-deploy.yml.proposed").read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch", text)
        self.assertNotIn("render", text.lower())
        self.assertFalse((ROOT / ".github/workflows/yc-deploy.yml").exists())
        self.assertFalse((ROOT / ".github/workflows/deploy-staging.yml").exists())
        self.assertFalse((ROOT / "deploy/yc/deploy-workflow.example.yml").exists())

    def test_dockerfile_does_not_migrate_or_seed_on_start(self):
        cmd = [l for l in (ROOT / "Dockerfile").read_text(encoding="utf-8").splitlines() if l.startswith("CMD")]
        self.assertEqual(len(cmd), 1)
        self.assertNotIn("migrate", cmd[0])
        self.assertNotIn("seed", cmd[0])

    def test_deploy_passes_lockbox_secrets_via_secret_flag(self):
        for name in ("deploy.sh", "rollback.sh"):
            t = (ROOT / "deploy" / "yc" / name).read_text(encoding="utf-8")
            self.assertIn("--secret", t)
            for key in ("DATABASE_URL", "SESSION_SECRET", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "AUTH_ALLOWLIST"):
                self.assertIn(key, t)

    def test_readme_lists_secrets_and_iam_roles_first(self):
        t = (ROOT / "README.md").read_text(encoding="utf-8")
        sec = t[t.index("### 0. Секреты и роли"):t.index("### 1. Развёртывание")]
        for need in ("lockbox.payloadViewer", "container-registry.images.puller", "mos-runtime", "YC_SA_KEY_JSON", "LOCKBOX_SECRET_ID"):
            self.assertIn(need, sec)
        for need in ("### 3. Снимок", "### 4. Откат", "backup-retain-period-days"):
            self.assertIn(need, t)

    def test_current_migrations_additive_only(self):
        import migration_guard
        self.assertEqual(migration_guard.destructive_migrations(), [])
        self.assertEqual(migration_guard.main(), 0)

    def test_guard_detects_destructive_sql(self):
        import migration_guard as g
        for sql in ("DROP TABLE x;", "truncate t;", "DELETE FROM t WHERE a=1;", "UPDATE t SET a=1;",
                    "ALTER TABLE t ALTER COLUMN a TYPE text;", "ALTER TABLE t RENAME TO u;",
                    "ALTER TABLE t DROP COLUMN a;", "DROP TRIGGER tg ON t; DROP TABLE t;",
                    "CREATE TRIGGER tg BEFORE UPDATE ON t FOR EACH ROW EXECUTE FUNCTION f(); UPDATE t SET a=1;"):
            self.assertTrue(g.is_destructive(sql), sql)
        for sql in ("CREATE TABLE IF NOT EXISTS t (a int REFERENCES u(id) ON DELETE CASCADE);",
                    "INSERT INTO t VALUES (1) ON CONFLICT (a) DO UPDATE SET b=1;",
                    "-- DROP TABLE x;\nALTER TABLE t ADD COLUMN c int;",
                    "DROP TRIGGER IF EXISTS tg ON t;\nCREATE TRIGGER tg BEFORE UPDATE OR DELETE ON t "
                    "FOR EACH ROW EXECUTE FUNCTION f();"):
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

    def test_deploy_scripts_pass_public_base_url(self):
        for name in ("deploy.sh", "rollback.sh"):
            t = (ROOT / "deploy" / "yc" / name).read_text(encoding="utf-8")
            self.assertIn("PUBLIC_BASE_URL=$PUBLIC_BASE_URL", t.replace('"', ""), name)
        self.assertRegex((ROOT / ".env.example").read_text(encoding="utf-8"), r"(?m)^PUBLIC_BASE_URL=")

    def test_backup_wait_is_bounded_and_fails_on_error_status(self):
        t = (ROOT / "deploy" / "yc" / "deploy.sh").read_text(encoding="utf-8")
        self.assertIn("seq 40", t)
        self.assertIn("sleep 15", t)
        self.assertNotIn("until [", t)
        self.assertRegex(t, r"(?s)DONE\).*CREATING\).*\*\).*exit 1")

    @unittest.skipUnless(shutil.which("bash"), "bash не найден")
    def test_deploy_scripts_pass_bash_syntax_check(self):
        scripts = sorted((ROOT / "deploy" / "yc").glob("*.sh"))
        self.assertTrue(scripts)
        for f in scripts:
            r = subprocess.run(["bash", "-n", str(f)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, f"{f.name}: {r.stderr}")

    def test_env_example_names_only(self):
        for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
            for key in ("GOOGLE_CLIENT_SECRET", "SESSION_SECRET", "AUTH_ALLOWLIST", "DATABASE_URL"):
                if line.startswith(key + "="):
                    self.assertEqual(line.split("=", 1)[1].split("#")[0].strip(), "")


if __name__ == "__main__":
    unittest.main()
