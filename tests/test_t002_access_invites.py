"""T-002 (#114, FEAT-001): допуск по приглашениям из БД и блокировка. AC-001…AC-004, spec §9. Только синтетика."""
import os
import sqlite3

import pytest

import auth
from auth_store import AuthStore
from test_auth import BASE, appmod, env, make_client, oauth_login, rows  # noqa: F401  (env — фикстура)

pytestmark = pytest.mark.auth_enforced

HTML = {"Accept": "text/html"}
ROOT = os.path.join(os.path.dirname(__file__), "..")


def in_ctx(fn, *a, **kw):
    with appmod.app.test_request_context("/", base_url=BASE):
        return fn(*a, **kw)


# AC-001: есть приглашение (в БД, не в env) → вход, пользователь создан
def test_ac001_invited_email_logs_in_without_env(env, monkeypatch):
    in_ctx(auth.invite_user, "New.Person@Example.test", "investor", actor="boris@example.test")  # регистр → нижний
    c = appmod.app.test_client()
    r = oauth_login(monkeypatch, c, "new.person@example.test")
    assert r.status_code == 302, r.get_data(as_text=True)
    user = env.user_by_email("new.person@example.test")
    assert user and env.active_grants(user["id"]) == [("investor", "*")]
    assert c.get("/auth/me", base_url=BASE).status_code == 200
    assert "new.person@example.test" not in os.environ.get("AUTH_ALLOWLIST", "")


# AC-002: приглашения нет → страница, user не создан, deny в аудите
def test_ac002_no_invite_shows_page_and_no_user(env, monkeypatch):
    r = oauth_login(monkeypatch, appmod.app.test_client(), "nobody@example.test", headers=HTML)
    body = r.get_data(as_text=True)
    assert r.status_code == 403 and "Доступ по приглашению" in body and "nobody@example.test" in body
    assert env.user_by_email("nobody@example.test") is None
    assert rows(env, action="login", result="deny", actor="nobody@example.test", detail="not_in_allowlist")


# AC-003: приглашение действует без перезапуска; отзыв закрывает вход
def test_ac003_invite_then_revoke_without_restart(env, monkeypatch):
    c = appmod.app.test_client()
    assert oauth_login(monkeypatch, c, "late@example.test").status_code == 403
    invite_id = in_ctx(auth.invite_user, "late@example.test", "fund_staff", "CASE-A", "boris@example.test")
    assert oauth_login(monkeypatch, appmod.app.test_client(), "late@example.test").status_code == 302
    assert in_ctx(auth.revoke_invite, invite_id, "boris@example.test") is True
    assert in_ctx(auth.revoke_invite, invite_id, "boris@example.test") is False  # повторно — нет эффекта
    assert oauth_login(monkeypatch, appmod.app.test_client(), "late@example.test").status_code == 403


def test_revoked_invite_cuts_existing_session_on_next_request(env, monkeypatch):
    invite_id = in_ctx(auth.invite_user, "late@example.test", "investor", "*", "boris@example.test")
    c = appmod.app.test_client()
    oauth_login(monkeypatch, c, "late@example.test")
    assert c.get("/auth/me", base_url=BASE).status_code == 200
    in_ctx(auth.revoke_invite, invite_id, "boris@example.test")
    assert c.get("/auth/me", base_url=BASE).status_code == 403


# AC-004: блокировка → следующий запрос «Доступ приостановлен»; разблокировка возвращает доступ
def test_ac004_block_and_unblock(env):
    user_client, user = make_client(env, "worker@example.test", [("investor", "*")])
    assert user_client.get("/auth/me", base_url=BASE).status_code == 200
    assert in_ctx(auth.block_user, user["id"], "boris@example.test") is True
    page = user_client.get("/auth/me", base_url=BASE, headers=HTML)
    assert page.status_code == 403 and page.mimetype == "text/html"
    assert "Доступ приостановлен" in page.get_data(as_text=True) and "user_blocked" not in page.get_data(as_text=True)
    api = user_client.get("/auth/me", base_url=BASE)
    assert api.status_code == 403 and api.get_json()["code"] == "user_blocked"
    assert rows(env, result="deny", actor="worker@example.test", detail="user_blocked")
    in_ctx(auth.unblock_user, user["id"], "boris@example.test")
    assert user_client.get("/auth/me", base_url=BASE).status_code == 200


def test_blocked_user_cannot_log_in_again(env, monkeypatch):
    _, user = make_client(env, "worker@example.test", [("investor", "*")])
    in_ctx(auth.block_user, user["id"], "boris@example.test")
    r = oauth_login(monkeypatch, appmod.app.test_client(), "worker@example.test", headers=HTML)
    assert r.status_code == 403 and "Доступ приостановлен" in r.get_data(as_text=True)


# spec §9 и аудит
def test_two_invites_give_both_roles_and_env_still_works(env, monkeypatch):
    in_ctx(auth.invite_user, "two@example.test", "investor", "*", None)
    in_ctx(auth.invite_user, "two@example.test", "fund_staff", "CASE-A", None)
    with appmod.app.test_request_context("/", base_url=BASE):
        assert sorted(auth.allowlist()["two@example.test"]) == [("fund_staff", "CASE-A"), ("investor", "*")]
        assert "boris@example.test" in auth.allowlist()  # env не потерян
    assert oauth_login(monkeypatch, appmod.app.test_client(), "boris@example.test").status_code == 302


def test_revoking_one_of_two_invites_revokes_only_that_role(env, monkeypatch):
    first = in_ctx(auth.invite_user, "two@example.test", "investor", "*", None)
    in_ctx(auth.invite_user, "two@example.test", "fund_staff", "CASE-A", None)
    oauth_login(monkeypatch, appmod.app.test_client(), "two@example.test")
    uid = env.user_by_email("two@example.test")["id"]
    in_ctx(auth.revoke_invite, first, None)
    assert env.active_grants(uid) == [("fund_staff", "CASE-A")]


def test_founder_invite_requires_project_and_invalid_data_rejected(env):
    with pytest.raises(ValueError):
        in_ctx(auth.invite_user, "f@example.test", "founder", "*", "boris@example.test")
    with pytest.raises(ValueError):
        in_ctx(auth.invite_user, "f@example.test", "nonexistent", "*", None)
    with pytest.raises(ValueError):
        in_ctx(auth.invite_user, "not-an-email", "investor", "*", None)
    assert env.active_invites() == {}
    in_ctx(auth.invite_user, "f@example.test", "founder", "VC-991", "boris@example.test")
    assert env.active_invites("F@example.test") == {"f@example.test": [("founder", "VC-991")]}


def test_duplicate_active_invite_not_duplicated(env):
    a = in_ctx(auth.invite_user, "d@example.test", "investor", "*", None)
    b = in_ctx(auth.invite_user, "d@example.test", "investor", "*", None)
    assert a == b and len(env.active_invites()["d@example.test"]) == 1


def test_cannot_block_self(env):
    _, user = make_client(env, "boris2@example.test", [("owner", "*")])
    with pytest.raises(ValueError):
        in_ctx(auth.block_user, user["id"], "Boris2@example.test")
    assert env.user_by_id(user["id"])["blocked_at"] is None


def test_audit_masks_email_for_invite_and_block(env):
    _, user = make_client(env, "worker@example.test", [("investor", "*")])
    in_ctx(auth.invite_user, "guest@example.test", "investor", "*", "boris@example.test")
    in_ctx(auth.block_user, user["id"], "boris@example.test")
    in_ctx(auth.unblock_user, user["id"], "boris@example.test")
    for action in ("invite", "block", "unblock"):
        found = rows(env, action=action, result="allow")
        assert found, action
        text = " ".join(str(r["detail"]) for r in found)
        assert "g***@example.test" in text or "w***@example.test" in text
        assert "guest" not in text and "worker" not in text


def test_env_allowlist_format_unchanged():
    assert auth.parse_allowlist("a@x.test:owner, B@x.test:investor:CASE-1") == {
        "a@x.test": [("owner", "*")], "b@x.test": [("investor", "CASE-1")]}


# миграция: файл, down и SQLite-DDL
def test_migration_files_and_down():
    up = open(os.path.join(ROOT, "db", "migrations", "007_access_invites.sql"), encoding="utf-8").read()
    down = open(os.path.join(ROOT, "db", "migrations", "down", "007_access_invites.down.sql"), encoding="utf-8").read()
    assert "access_invites" in up and "tenant_id" in up and "blocked_at" in up and "IF NOT EXISTS" in up
    assert "DROP TABLE IF EXISTS access_invites" in down and "DROP COLUMN IF EXISTS blocked_at" in down


def test_sqlite_ddl_has_tenant_and_old_db_gets_blocked_at(tmp_path):
    path = str(tmp_path / "old.db")
    conn = sqlite3.connect(path)  # БД до 007: users без blocked_at
    conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT UNIQUE NOT NULL, "
                 "display_name TEXT NOT NULL, is_synthetic INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL)")
    conn.execute("INSERT INTO users (email, display_name, created_at) VALUES ('o@example.test', 'o', 'x')")
    conn.commit()
    conn.close()
    store = AuthStore("sqlite:///" + path)
    assert store.user_by_email("o@example.test")["blocked_at"] is None
    store.create_invite("n@example.test", "investor")
    conn = sqlite3.connect(path)
    assert conn.execute("SELECT tenant_id FROM access_invites").fetchone() == ("pilot",)
    conn.close()
    AuthStore("sqlite:///" + path)  # повторная инициализация идемпотентна
