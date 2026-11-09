"""T-003 (#114, FEAT-001): админка пользователей и «Данные». AC-003, AC-004, AC-018. Только синтетика (VC-99x)."""
import pytest

from test_auth import BASE, appmod, env, make_client, oauth_login, rows  # noqa: F401  (env — фикстура)

pytestmark = pytest.mark.auth_enforced
HTML = {"Accept": "text/html"}


def owner(env):
    return make_client(env, "boss@example.test", [("owner", "*")])


def post(c, path, **data):
    return c.post(path, json=data, base_url=BASE, headers={"X-CSRF-Token": c.csrf})


def test_invite_created_201_and_listed(env):
    c, _ = owner(env)
    r = post(c, "/admin/users/invite", email="New@Example.test", role="investor")
    assert r.status_code == 201 and r.get_json()["ok"]
    j = c.get("/admin/users", base_url=BASE).get_json()
    assert [i["email"] for i in j["invites"]] == ["new@example.test"]
    assert any(u["email"] == "boss@example.test" for u in j["users"])


@pytest.mark.parametrize("body", [
    {"email": "bad", "role": "investor"},
    {"email": "a@example.test", "role": "nope"},
    {"email": "a@example.test", "role": "founder"},                 # founder без VC-NNN
    {"email": "a@example.test", "role": "founder", "scope": "*"},
])
def test_invite_invalid_400(env, body):
    c, _ = owner(env)
    assert post(c, "/admin/users/invite", **body).status_code == 400
    assert env.active_invites() == {}


def test_founder_with_project_ok(env):
    c, _ = owner(env)
    assert post(c, "/admin/users/invite", email="f@example.test", role="founder", scope="VC-991").status_code == 201


def test_ac003_invited_logs_in_without_restart_and_revoke(env, monkeypatch):
    c, _ = owner(env)
    iid = post(c, "/admin/users/invite", email="late@example.test", role="investor").get_json()["id"]
    assert oauth_login(monkeypatch, appmod.app.test_client(), "late@example.test").status_code == 302
    assert post(c, "/admin/users/invite", email="other@example.test", role="investor").status_code == 201
    oid = [i["id"] for i in c.get("/admin/users", base_url=BASE).get_json()["invites"] if i["email"] == "other@example.test"][0]
    assert post(c, "/admin/invites/%d/revoke" % oid).status_code == 200
    assert post(c, "/admin/invites/%d/revoke" % oid).status_code == 404
    assert oauth_login(monkeypatch, appmod.app.test_client(), "other@example.test").status_code == 403
    assert iid


def test_ac004_block_then_next_request_suspended_then_unblock(env):
    c, _ = owner(env)
    t, tu = make_client(env, "tester@example.test", [("investor", "*")])
    assert t.get("/auth/me", base_url=BASE).status_code == 200
    assert post(c, "/admin/users/%d/block" % tu["id"]).status_code == 200
    r = t.get("/", base_url=BASE, headers=HTML)
    assert r.status_code == 403 and "Доступ приостановлен" in r.get_data(as_text=True)
    assert post(c, "/admin/users/%d/unblock" % tu["id"]).status_code == 200
    assert t.get("/auth/me", base_url=BASE).status_code == 200
    assert rows(env, action="block", result="allow") and rows(env, action="unblock", result="allow")


def test_block_self_400_and_unknown_404(env):
    c, me = owner(env)
    r = post(c, "/admin/users/%d/block" % me["id"])
    assert r.status_code == 400 and r.get_json()["code"] == "self_block"
    assert post(c, "/admin/users/9999/block").status_code == 404
    assert post(c, "/admin/users/9999/unblock").status_code == 404


def test_last_owner_guard_does_not_trigger_with_two_owners(env):
    c, _ = owner(env)
    o2, u2 = make_client(env, "boss2@example.test", [("owner", "*")])
    assert post(c, "/admin/users/%d/block" % u2["id"]).status_code == 200  # boss остаётся
    assert env.count_active_owners() == 1


def test_last_owner_guard_counts_only_active_owners(env):
    _, me = owner(env)
    _, u2 = make_client(env, "boss2@example.test", [("owner", "*")])
    env.block_user(me["id"])
    assert env.count_active_owners(exclude_user_id=u2["id"]) == 0


def test_html_pages_and_form_errors(env):
    c, _ = owner(env)
    r = c.get("/admin/users", base_url=BASE, headers=HTML)
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and "Пригласить" in html and 'href="/admin/users"' in html and "Выйти" in html
    r = c.post("/admin/users/invite", data={"email": "f@example.test", "role": "founder", "csrf_token": c.csrf},
               base_url=BASE, headers=HTML)
    assert r.status_code == 400 and "номер проекта" in r.get_data(as_text=True)
    r = c.post("/admin/users/invite", data={"email": "ok@example.test", "role": "investor", "csrf_token": c.csrf},
               base_url=BASE, headers=HTML)
    assert r.status_code == 303


def test_filter(env):
    c, _ = owner(env)
    make_client(env, "zed@example.test", [("investor", "*")])
    j = c.get("/admin/users?q=ZED", base_url=BASE).get_json()
    assert [u["email"] for u in j["users"]] == ["zed@example.test"]
    assert c.get("/admin/users?q=%25", base_url=BASE).get_json()["users"] == []  # % не подстановочный знак


def test_ac018_data_users_table_search_readonly(env):
    c, _ = owner(env)
    make_client(env, "zed@example.test", [("investor", "*")])
    r = c.get("/admin/data/users", base_url=BASE, headers=HTML)
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and "boss@example.test" in html and "zed@example.test" in html and "Только чтение" in html
    j = c.get("/admin/data/users?q=zed", base_url=BASE).get_json()
    assert [row[1] for row in j["rows"]] == ["zed@example.test"]
    assert post(c, "/admin/data/users", x=1).status_code == 405


def test_data_whitelist_and_no_secret_columns(env):
    c, _ = owner(env)
    assert c.get("/admin/data/sqlite_master", base_url=BASE).status_code == 404
    assert c.get("/admin/data/users;drop", base_url=BASE).status_code == 404
    from admin_users.blueprint import DATA_TABLES
    for _, cols in DATA_TABLES.values():
        assert not [x for x in cols if any(w in x for w in ("secret", "token", "hash", "password", "ip"))]
    assert c.get("/admin/data/audit_log?q=%27%20OR%201%3D1", base_url=BASE).status_code == 200


@pytest.mark.parametrize("role", ["investor", "fund_staff", "founder"])
def test_non_owner_403_on_every_route(env, role):
    c, u = make_client(env, "x-%s@example.test" % role, [(role, "VC-991" if role == "founder" else "*")])
    for method, path in [("get", "/admin/users"), ("post", "/admin/users/invite"), ("post", "/admin/users/1/block"),
                         ("post", "/admin/users/1/unblock"), ("post", "/admin/invites/1/revoke"),
                         ("get", "/admin/data/users")]:
        r = getattr(c, method)(path, base_url=BASE, headers={"X-CSRF-Token": c.csrf})
        assert r.status_code == 403, (path, r.status_code)
    assert c.get("/admin/users", base_url=BASE, headers=HTML).status_code == 403
    assert "Нет доступа" in c.get("/admin/users", base_url=BASE, headers=HTML).get_data(as_text=True)


def test_anonymous_and_missing_csrf(env):
    anon = appmod.app.test_client()
    assert anon.get("/admin/users", base_url=BASE).status_code == 401
    c, _ = owner(env)
    assert c.post("/admin/users/invite", json={"email": "a@example.test", "role": "investor"},
                  base_url=BASE).status_code == 403


def test_owner_sees_menu_link_others_do_not(env):
    c, _ = owner(env)
    assert 'href="/admin/users"' in c.get("/profile", base_url=BASE, headers=HTML).get_data(as_text=True)
    f, _ = make_client(env, "f@example.test", [("founder", "VC-991")])
    assert 'href="/admin/users"' not in f.get("/profile", base_url=BASE, headers=HTML).get_data(as_text=True)
