"""152-ФЗ: страница согласия. Только синтетические email. Сеть не используется."""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("INVITE_CODE", "PILOT-S1-EXT-001")
os.environ.setdefault("SESSION_DATABASE_URL", "sqlite:///:memory:")

import app as appmod  # noqa: E402
import staging_guard as sg  # noqa: E402
from pdn_consent import TEXT_VERSION  # noqa: E402
from pdn_consent.blueprint import TEXT_PATH, render_notice  # noqa: E402
from pdn_consent.store import PdnConsentStore  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
BASE = "https://localhost"
MIGRATION = ROOT / "db" / "migrations" / "006_pdn_consent.sql"
PG_URL = os.environ.get("HQ018_PG_URL", "")


class PdnConsentTests(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in ("CONSENT_PROJECT_NAME", "CONSENT_CONTACT_EMAIL")}
        os.environ.pop("CONSENT_PROJECT_NAME", None)
        os.environ.pop("CONSENT_CONTACT_EMAIL", None)
        self.tmp = tempfile.mkdtemp()
        self.store = PdnConsentStore(f"sqlite:///{self.tmp}/pdn.db")
        self.old = appmod.app.extensions["pdn_store"]
        appmod.app.extensions["pdn_store"] = self.store
        appmod.app.testing = True
        self.c = appmod.app.test_client()

    def tearDown(self):
        appmod.app.extensions["pdn_store"] = self.old
        shutil.rmtree(self.tmp, ignore_errors=True)
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def open_page(self):
        response = self.c.get("/consent", base_url=BASE)
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        match = re.search(r'name="csrf_token" value="([^"]+)"', html)
        self.assertIsNotNone(match)
        return match.group(1), html

    def post_json(self, token, path="/consent", **data):
        return self.c.post(path, data={"csrf_token": token, **data}, base_url=BASE,
                           headers={"Accept": "application/json"})

    def test_page_is_draft_and_checkbox_is_off(self):
        _, html = self.open_page()
        self.assertIn("Черновик — юрист проверит", html)
        self.assertIn("юрист проверит", html)
        self.assertIn("Версия текста: v0-draft", html)
        self.assertIn("Данные хранятся на серверах в России.", html)
        self.assertIn("[Название проекта]", html)
        self.assertIn("[email]", html)
        self.assertNotIn("{project_name}", html)
        self.assertNotIn("{contact_email}", html)
        self.assertNotIn("Artgen", html)
        boxes = [tag for tag in re.findall(r"<input\b[^>]*>", html) if "checkbox" in tag]
        self.assertEqual(boxes, ['<input id="pdnConsent" type="checkbox" name="consent" value="yes" required>'])
        self.assertIn(">Отозвать согласие</button>", html)

    def test_notice_comes_from_file_and_config_placeholders(self):
        raw = TEXT_PATH.read_text(encoding="utf-8")
        self.assertIn("{project_name}", raw)
        self.assertIn("{contact_email}", raw)
        self.assertEqual(TEXT_VERSION, "v0-draft")
        notice = render_notice()
        self.assertIn("[Название проекта]", notice)
        self.assertIn("письмом на [email]", notice)
        os.environ["CONSENT_PROJECT_NAME"] = "SYN-PROJECT"
        os.environ["CONSENT_CONTACT_EMAIL"] = "pdn-contact@example.test"
        swapped = render_notice()
        self.assertIn("SYN-PROJECT", swapped)
        self.assertIn("pdn-contact@example.test", swapped)
        self.assertNotIn("{project_name}", swapped)

    def test_without_checkbox_intake_and_login_are_rejected(self):
        token, _ = self.open_page()
        for flow in ("s1_intake", "investor_login"):
            with self.subTest(flow=flow):
                response = self.c.post(
                    "/consent",
                    data={"csrf_token": token, "email": "synth-no@example.test", "flow": flow, "consent": "false"},
                    base_url=BASE,
                )
                self.assertEqual(response.status_code, 400)
                self.assertFalse(response.headers.get("Location"))
                self.assertIn("юрист проверит", response.get_data(as_text=True))
                self.assertEqual(self.store.for_email("synth-no@example.test"), [])
        missing = self.post_json(token, email="synth-no@example.test", flow="s1_intake")
        self.assertEqual(missing.status_code, 400)
        self.assertEqual(missing.get_json()["code"], "consent_required")

    def test_every_accepted_application_has_versioned_consent(self):
        token, _ = self.open_page()
        emails = [f"synth-consent-{i}@example.test" for i in range(3)]
        for email in emails:
            response = self.post_json(
                token, email=email, consent="yes", flow="s1_intake",
            )
            self.assertEqual(response.status_code, 200, response.get_json())
            body = response.get_json()
            self.assertEqual(body["text_version"], "v0-draft")
            self.assertTrue(body["consented_at"].endswith("Z"))
            self.assertFalse(body["forwarded"])
            self.assertIsNone(body["revoked_at"])
        self.post_json(token, email="synth-rejected@example.test", flow="s1_intake")
        accepted = [self.store.for_email(email)[0] for email in emails]
        self.assertEqual(len(accepted), len(emails))
        self.assertTrue(all(row["text_version"] == "v0-draft" and row["consented_at"].endswith("Z") for row in accepted))
        self.assertEqual(self.store.for_email("synth-rejected@example.test"), [])

    def test_checkbox_records_ip_and_login_does_not_start_oauth(self):
        token, _ = self.open_page()
        blocked = self.c.get("/auth/login", base_url=BASE)
        self.assertEqual(blocked.status_code, 302)
        self.assertTrue(blocked.headers["Location"].endswith("/consent"))
        response = self.c.post(
            "/consent",
            data={"csrf_token": token, "email": "Investor.Synth@example.test", "consent": "yes",
                  "flow": "investor_login"},
            base_url=BASE,
            environ_overrides={"REMOTE_ADDR": "203.0.113.44"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/auth/login"))
        self.assertNotIn("accounts.google.com", response.headers["Location"])
        follow = self.c.get("/auth/login", base_url=BASE)
        self.assertFalse((follow.headers.get("Location") or "").endswith("/consent"))
        row = self.store.for_email("investor.synth@example.test")[0]
        self.assertEqual(row["text_version"], "v0-draft")
        self.assertEqual(row["flow"], "investor_login")
        self.assertEqual(row["ip"], "203.0.113.44")
        self.assertTrue(row["consented_at"].endswith("Z"))
        self.assertIsNone(row["revoked_at"])
        self.post_json(token, path="/consent/revoke", email="investor.synth@example.test")
        self.assertTrue(self.store.for_email("investor.synth@example.test")[0]["revoked_at"].endswith("Z"))
        after = self.c.get("/auth/login", base_url=BASE)
        self.assertEqual(after.status_code, 302)
        self.assertTrue(after.headers["Location"].endswith("/consent"))

    def test_revoke_sets_revoked_at(self):
        token, _ = self.open_page()
        email = "synth-revoke@example.test"
        self.assertEqual(self.post_json(token, email=email, consent="yes", flow="s1_intake").status_code, 200)
        response = self.post_json(token, path="/consent/revoke", email=email)
        self.assertEqual(response.status_code, 200, response.get_json())
        row = self.store.for_email(email)[0]
        self.assertTrue(row["revoked_at"].endswith("Z"))
        self.assertTrue(row["consented_at"].endswith("Z"))
        again = self.post_json(token, path="/consent/revoke", email=email)
        self.assertEqual(again.status_code, 404)
        self.assertEqual(again.get_json()["code"], "consent_not_found")

    def test_revoke_foreign_email_forbidden(self):
        """Проверка стенда 30.10: посетитель другой сессии не может отозвать чужое согласие."""
        token, _ = self.open_page()
        victim = "synth-victim@example.test"
        self.assertEqual(self.post_json(token, email=victim, consent="yes", flow="s1_intake").status_code, 200)
        attacker = appmod.app.test_client()
        page = attacker.get("/consent", base_url=BASE).get_data(as_text=True)
        a_token = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
        r = attacker.post("/consent/revoke", data={"csrf_token": a_token, "email": victim}, base_url=BASE,
                          headers={"Accept": "application/json"})
        self.assertEqual(r.status_code, 403, r.get_json())
        self.assertEqual(r.get_json()["code"], "consent_not_yours")
        self.assertIsNone(self.store.for_email(victim)[0]["revoked_at"])

    def test_migration_006_is_additive_and_unique(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        import migration_guard
        sql = MIGRATION.read_text(encoding="utf-8")
        self.assertFalse(migration_guard.is_destructive(sql))
        self.assertIn("CREATE TABLE IF NOT EXISTS pdn_consents", sql)
        names = sorted(p.name for p in (ROOT / "db" / "migrations").glob("*.sql"))
        self.assertEqual([n for n in names if n.startswith("006")], ["006_pdn_consent.sql"])
        self.assertIn("006", sql)


class StagingConsentOpenTests(unittest.TestCase):
    def test_consent_paths_do_not_require_session(self):
        self.assertTrue(sg.anonymous_open("/consent"))
        self.assertTrue(sg.anonymous_open("/consent/revoke"))
        self.assertFalse(sg.anonymous_open("/consent-other"))
        env = {
            "APP_ENV": "staging",
            "DATABASE_URL": "postgresql://u:p@db.invalid:5432/x",
            "GOOGLE_CLIENT_ID": "synthetic-client-id",
            "GOOGLE_CLIENT_SECRET": "synthetic-client-secret",
            "SESSION_SECRET": "s" * 40,
            "AUTH_ALLOWLIST": "allowed.synth@example.test:owner",
            "PUBLIC_BASE_URL": "https://staging.example.test",
        }
        from flask import Flask

        app = Flask(__name__)
        app.secret_key = "x"

        @app.get("/consent")
        def consent_page():
            return "ok"

        @app.post("/consent/revoke")
        def consent_revoke():
            return "ok"

        @app.get("/")
        def home():
            return "home"

        sg.init_staging(app, env, data_check=lambda url, operators=(): None, user_loader=lambda: None)
        client = app.test_client()
        self.assertEqual(client.get("/consent", base_url=BASE).status_code, 200)
        self.assertEqual(client.post("/consent/revoke", base_url=BASE).status_code, 200)
        denied = client.get("/", base_url=BASE)
        self.assertEqual(denied.status_code, 302)
        self.assertTrue(denied.headers["Location"].endswith("/auth/login"))
        self.assertEqual(client.get("/consent-other", base_url=BASE).status_code, 302)


@unittest.skipUnless(PG_URL.startswith("postgresql://"), "HQ018_PG_URL не задан")
class PdnConsentPostgresTests(unittest.TestCase):
    def test_migration_006_applies_and_revoke(self):
        env = dict(os.environ, SESSION_DATABASE_URL=PG_URL)
        for _ in range(2):
            subprocess.run([sys.executable, str(ROOT / "scripts" / "hq018_migrate.py")], env=env, check=True, cwd=ROOT)
        store = PdnConsentStore(PG_URL)
        email = f"pg-{uuid.uuid4().hex[:10]}@example.test"
        row = store.record(email, TEXT_VERSION, "203.0.113.10", "investor_login")
        self.assertEqual(row["text_version"], "v0-draft")
        self.assertTrue(row["consented_at"].endswith("Z"))
        revoked = store.revoke(email)
        self.assertEqual(len(revoked), 1)
        self.assertTrue(revoked[0]["revoked_at"].endswith("Z"))
        import psycopg
        with psycopg.connect(PG_URL) as conn:
            conn.execute("DELETE FROM pdn_consents WHERE user_email = %s", (email,))
            conn.commit()
