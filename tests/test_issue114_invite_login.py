"""#114: запасной вход по одноразовой ссылке (AUTH_INVITE_FALLBACK). Только синтетика (VC-99x, @example.test)."""
import os
import sqlite3
import sys

import pytest

os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("SESSION_DATABASE_URL", "sqlite:///:memory:")
import app as appmod  # noqa: E402
import auth  # noqa: E402
from auth_store import AuthStore  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import issue_login_token  # noqa: E402

pytestmark = pytest.mark.auth_enforced
BASE = "https://localhost"
OK, STRANGER = "vc991-founder@example.test", "stranger-vc99x@example.test"


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("AUTH_INVITE_FALLBACK", "1")
    monkeypatch.setenv("AUTH_ALLOWLIST", OK + ":founder:VC-991")
    fresh = AuthStore("sqlite:///:memory:")
    old = appmod.app.extensions["auth_store"]
    appmod.app.extensions["auth_store"] = fresh
    appmod.app.testing = True
    yield fresh
    appmod.app.extensions["auth_store"] = old


def consent(c, email):
    row = appmod.app.extensions["pdn_store"].record(email, "v0-draft", "127.0.0.1", "investor_login")
    with c.session_transaction() as s:
        s["pdn_consent_id"] = row["id"]


def test_allowlisted_user_enters_cabinet(env):
    token = env.create_login_token(OK)
    c = appmod.app.test_client()
    consent(c, OK)
    r = c.get("/auth/invite/" + token, base_url=BASE)
    assert r.status_code == 302 and r.headers["Location"].endswith("/")
    me = c.get("/auth/me", base_url=BASE).get_json()
    assert me["email"] == OK and {"role": "founder", "scope": "VC-991"} in me["grants"]
    assert c.get("/", base_url=BASE, headers={"Accept": "text/html"}).status_code == 200


def test_not_in_allowlist_gets_403_page_and_no_user(env):
    token = env.create_login_token(STRANGER)
    c = appmod.app.test_client()
    r = c.get("/auth/invite/" + token, base_url=BASE, headers={"Accept": "text/html"})
    assert r.status_code == 403 and "Доступ по приглашению" in r.get_data(as_text=True)
    assert env.user_by_email(STRANGER) is None
    assert c.get("/auth/me", base_url=BASE).status_code == 401


def test_reuse_rejected(env):
    token = env.create_login_token(OK)
    c = appmod.app.test_client()
    consent(c, OK)
    assert c.get("/auth/invite/" + token, base_url=BASE).status_code == 302
    c2 = appmod.app.test_client()
    consent(c2, OK)
    r = c2.get("/auth/invite/" + token, base_url=BASE)
    assert r.status_code == 403 and r.get_json()["code"] == "invite_token_invalid"
    assert c2.get("/auth/me", base_url=BASE).status_code == 401


def test_expired_and_unknown_rejected(env):
    token = env.create_login_token(OK)
    with env._conn() as conn:
        conn.execute("UPDATE login_tokens SET expires_at = '2000-01-01T00:00:00.000000Z'")
    c = appmod.app.test_client()
    consent(c, OK)
    assert c.get("/auth/invite/" + token, base_url=BASE).status_code == 403
    assert c.get("/auth/invite/no-such-token", base_url=BASE).status_code == 403


def test_flag_off_route_unavailable(env, monkeypatch):
    token = env.create_login_token(OK)
    monkeypatch.delenv("AUTH_INVITE_FALLBACK")
    c = appmod.app.test_client()
    consent(c, OK)
    assert c.get("/auth/invite/" + token, base_url=BASE).status_code == 404
    monkeypatch.setenv("AUTH_INVITE_FALLBACK", "0")
    assert c.get("/auth/invite/" + token, base_url=BASE).status_code == 404
    assert c.get("/auth/me", base_url=BASE).status_code == 401


def test_without_consent_redirects_and_token_survives(env):
    token = env.create_login_token(OK)
    c = appmod.app.test_client()
    r = c.get("/auth/invite/" + token, base_url=BASE)
    assert r.status_code == 302 and r.headers["Location"].endswith("/consent")
    assert env.peek_login_token(token) == OK


def test_consent_email_mismatch_rejected(env):
    token = env.create_login_token(OK)
    c = appmod.app.test_client()
    consent(c, "other-vc992@example.test")
    assert c.get("/auth/invite/" + token, base_url=BASE).status_code == 403
    assert env.peek_login_token(token) == OK


def test_only_hash_stored(env):
    token = env.create_login_token(OK)
    with env._conn() as conn:
        rows = conn.execute("SELECT token_hash, email FROM login_tokens").fetchall()
    assert len(rows) == 1 and token not in rows[0][0] and len(rows[0][0]) == 64


def test_cli_issues_only_for_allowlist(tmp_path, capsys):
    db = "sqlite:///%s" % (tmp_path / "t.db")
    environ = {"SESSION_DATABASE_URL": db, "AUTH_ALLOWLIST": OK + ":founder:VC-991"}
    assert issue_login_token.main([OK, "--base-url", "https://s.example.test"], environ) == 0
    out = capsys.readouterr().out.strip()
    assert out.startswith("https://s.example.test/auth/invite/")
    assert AuthStore(db).peek_login_token(out.rsplit("/", 1)[1]) == OK
    assert issue_login_token.main([STRANGER], environ) == 2
    assert issue_login_token.main([OK, "--ttl-minutes", "0"], environ) == 2


def test_migration_008_files():
    up = open(os.path.join(ROOT, "db", "migrations", "008_login_tokens.sql"), encoding="utf-8").read()
    down = open(os.path.join(ROOT, "db", "migrations", "down", "008_login_tokens.down.sql"), encoding="utf-8").read()
    assert "IF NOT EXISTS login_tokens" in up and "token_hash" in up and "DROP TABLE IF EXISTS login_tokens" in down
    c = sqlite3.connect(":memory:")
    c.executescript(__import__("auth_store").SQLITE_DDL)
    assert c.execute("SELECT count(*) FROM login_tokens").fetchone()[0] == 0
