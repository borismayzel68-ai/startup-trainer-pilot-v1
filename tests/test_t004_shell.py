"""T-004 (#114, FEAT-001): оболочка кабинета — шапка, профиль, выход, HTML-ошибки. AC-006, AC-009…AC-011. Только синтетика."""
import pytest

from test_auth import BASE, appmod, env, make_client  # noqa: F401  (env — фикстура)

pytestmark = pytest.mark.auth_enforced
HTML = {"Accept": "text/html"}


def page(c, path):
    return c.get(path, base_url=BASE, headers=HTML)


def test_ac009_shell_on_cabinet_pages(env):
    c, _ = make_client(env, "f@example.test", [("founder", "VC-991")])
    for path in ("/", "/profile"):
        r = page(c, path)
        assert r.status_code == 200
        html = r.get_data(as_text=True)
        assert "Nocvela" in html and "Synthetic f@example.test" in html and "Основатель" in html
        assert 'href="/profile"' in html and "Сообщить о проблеме" in html
        assert 'action="/auth/logout"' in html and "Выйти" in html
        assert r.headers["Cache-Control"] == "no-store"


def test_ac010_profile_shows_name_email_roles_consent(env):
    c, _ = make_client(env, "f@example.test", [("founder", "VC-991")])
    appmod.app.extensions["pdn_store"].record("f@example.test", "v-test", "127.0.0.1", "investor_login")
    html = page(c, "/profile").get_data(as_text=True)
    assert "Synthetic f@example.test" in html and "f@example.test" in html
    assert "Основатель" in html and "VC-991" in html
    assert "Действует" in html and "v-test" in html and "Отозвать согласие" in html


def test_ac011_logout_post_goes_to_login_and_back_is_closed(env):
    c, _ = make_client(env, "f@example.test", [("founder", "VC-991")])
    r = c.post("/auth/logout", data={"csrf_token": c.csrf}, base_url=BASE, headers=HTML)
    assert r.status_code == 303 and r.headers["Location"].endswith("/auth/login")
    assert r.headers["Cache-Control"] == "no-store"
    assert page(c, "/").status_code == 302  # без входа `/` → вход
    assert page(c, "/").headers["Location"].endswith("/auth/login")


def test_logout_without_csrf_denied_and_api_json_unchanged(env):
    c, _ = make_client(env, "f@example.test", [("founder", "VC-991")])
    assert c.post("/auth/logout", base_url=BASE, headers=HTML).status_code == 403
    r = c.post("/auth/logout", base_url=BASE, headers={"X-CSRF-Token": c.csrf})
    assert r.status_code == 200 and r.get_json() == {"ok": True}


def test_ac006_revoke_consent_ends_session_and_blocks_login(env):
    c, _ = make_client(env, "f@example.test", [("founder", "VC-991")])
    store = appmod.app.extensions["pdn_store"]
    row = store.record("f@example.test", "v-test", "127.0.0.1", "investor_login")
    with c.session_transaction() as s:
        s["pdn_consent_id"] = row["id"]
    r = c.post("/profile/revoke-consent", data={"csrf_token": c.csrf}, base_url=BASE, headers=HTML)
    assert r.status_code == 302 and r.headers["Location"].endswith("/consent")
    assert store.by_id(row["id"])["revoked_at"]
    assert page(c, "/").headers["Location"].endswith("/auth/login")  # сессия завершена
    assert page(c, "/auth/login").headers["Location"].endswith("/consent")  # вход — только после нового согласия


def test_revoke_touches_only_own_email(env):
    c, _ = make_client(env, "f@example.test", [("founder", "VC-991")])
    store = appmod.app.extensions["pdn_store"]
    other = store.record("other@example.test", "v-test", "127.0.0.1", "investor_login")
    c.post("/profile/revoke-consent", data={"csrf_token": c.csrf}, base_url=BASE, headers=HTML)
    assert not store.by_id(other["id"])["revoked_at"]


def test_revoke_requires_csrf_and_login(env):
    c, _ = make_client(env, "f@example.test", [("founder", "VC-991")])
    assert c.post("/profile/revoke-consent", base_url=BASE, headers=HTML).status_code == 403
    anon = appmod.app.test_client()
    assert anon.post("/profile/revoke-consent", base_url=BASE).status_code == 401


def test_html_404_403_401_in_russian_for_browser_json_for_api(env):
    c, _ = make_client(env, "i@example.test", [("investor", "*")])
    r = page(c, "/net-takoj-stranicy")
    assert r.status_code == 404 and "Страница не найдена" in r.get_data(as_text=True)
    assert r.mimetype == "text/html"
    # 403: инвестор открывает страницу сотрудников (JSON-отказ → HTML для браузера)
    r = page(c, "/admin/audit")
    assert r.status_code == 403 and r.mimetype == "text/html" and "Нет доступа" in r.get_data(as_text=True)
    # API и fetch (Accept не html) остаются JSON
    r = c.get("/api/session/none", base_url=BASE, headers=HTML)
    assert r.mimetype != "text/html" or r.status_code == 404
    r = c.get("/admin/audit", base_url=BASE)
    assert r.status_code == 403 and r.is_json
    anon = appmod.app.test_client()
    r = anon.get("/api/session/none", base_url=BASE, headers=HTML)
    assert r.status_code == 401 and r.is_json
