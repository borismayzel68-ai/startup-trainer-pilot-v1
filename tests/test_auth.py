"""MOS-1 (#52): Google OAuth (мок, без сети), allowlist, RBAC роль×маршрут, scope кейса, отзыв гранта, аудит, CSRF."""
import os
import re
import time
import urllib.parse

import pytest

os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("INVITE_CODE", "PILOT-S1-EXT-001")
os.environ.setdefault("SESSION_DATABASE_URL", "sqlite:///:memory:")
import app as appmod  # noqa: E402
import auth  # noqa: E402
from auth_store import ROLE_LABELS, AuthStore  # noqa: E402

pytestmark = pytest.mark.auth_enforced

BASE = "https://localhost"  # cookie Secure не уходит по http
CLIENT_ID = "synthetic-client-id.apps.example.test"
PUBLIC_ENDPOINTS = {
    "health", "robots", "auth.login", "auth.callback", "auth.invite_login",
    "pdn_consent.page", "pdn_consent.submit", "pdn_consent.revoke",
}
EXTERNAL_OK_STATUSES = {401, 403, 404, 503}  # собственная проверка маршрута (Bearer / ключ / подпись ссылки)

ALL_ROLES = sorted(auth.ROLES)


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "synthetic-secret")
    monkeypatch.setenv("AUTH_ALLOWLIST", "boris@example.test:owner, arthur@example.test:investor:*, "
                                         "staff@example.test:fund_staff:CASE-A")
    appmod.SESSIONS.clear()
    fresh = AuthStore("sqlite:///:memory:")
    old = appmod.app.extensions["auth_store"]
    appmod.app.extensions["auth_store"] = fresh
    appmod.app.testing = True
    yield fresh
    appmod.app.extensions["auth_store"] = old


def make_client(store, email, grants):
    """Клиент с готовой сессией (как после входа). grants: [(role, scope)]."""
    os.environ["AUTH_ALLOWLIST"] += ", %s:%s" % (email, grants[0][0] if grants else "owner")  # откатывает monkeypatch в env
    user = store.upsert_user(email, "Synthetic " + email)
    for role, scope in grants:
        store.ensure_grant(user["id"], role, scope)
    c = appmod.app.test_client()
    with c.session_transaction() as s:
        s["uid"] = user["id"]
        s["csrf"] = "csrf-" + email
    c.csrf = "csrf-" + email
    return c, user


def start_case(owner_client, project_id):
    data = {"invite_code": "PILOT-S1-EXT-001", "project_id": project_id, "project_name": "Synth", "founder_name": "F",
            "founder_email": "f@example.test", "jurisdiction": "RF", "mode": "B", "consent": "true",
            "intended_use": "storage and visualization of laboratory history", "product_class": "WELLNESS_INFORMATION"}
    r = owner_client.post("/api/start", data=data, base_url=BASE, headers={"X-CSRF-Token": owner_client.csrf})
    assert r.status_code == 200, r.get_json()
    return r.get_json()["id"]


def rows(store, **where):
    return [r for r in store.audit_rows(1000) if all(r[k] == v for k, v in where.items())]


# ------------------------------------------------------------- OAuth (мок)
def grant_pdn(c, email="pdn-oauth@example.test"):
    """Как после отмеченного чекбокса на /consent: строка журнала и id в сессии."""
    row = appmod.app.extensions["pdn_store"].record(email, "v0-draft", "127.0.0.1", "investor_login")
    with c.session_transaction() as s:
        s["pdn_consent_id"] = row["id"]
    return row


def oauth_login(monkeypatch, c, email, verified=True, nonce_ok=True, aud=CLIENT_ID, headers=None):
    grant_pdn(c, email)
    r = c.get("/auth/login", base_url=BASE)
    assert r.status_code == 302
    q = urllib.parse.parse_qs(urllib.parse.urlparse(r.headers["Location"]).query)
    assert q["client_id"] == [CLIENT_ID] and q["response_type"] == ["code"] and "openid" in q["scope"][0]
    claims = {"iss": "https://accounts.google.com", "aud": aud, "exp": time.time() + 600, "email": email,
              "email_verified": verified, "name": "Synthetic", "nonce": q["nonce"][0] if nonce_ok else "other"}
    monkeypatch.setattr(auth, "exchange_code", lambda code, redirect_uri: claims)
    return c.get("/auth/callback?code=synthetic&state=" + q["state"][0], base_url=BASE, headers=headers or {})


def test_login_allowlisted_creates_user_and_grants(env, monkeypatch):
    c = appmod.app.test_client()
    r = oauth_login(monkeypatch, c, "Boris@Example.test")
    assert r.status_code == 302 and r.headers["Location"].endswith("/")
    user = env.user_by_email("boris@example.test")
    assert env.active_grants(user["id"]) == [("owner", "*")]
    assert c.get("/auth/me", base_url=BASE).get_json()["email"] == "boris@example.test"
    assert rows(env, action="login", result="allow", actor="boris@example.test")


def test_login_with_scope_from_allowlist(env, monkeypatch):
    oauth_login(monkeypatch, appmod.app.test_client(), "staff@example.test")
    assert env.active_grants(env.user_by_email("staff@example.test")["id"]) == [("fund_staff", "CASE-A")]


def test_login_outside_allowlist_403_and_no_user(env, monkeypatch):
    c = appmod.app.test_client()
    r = oauth_login(monkeypatch, c, "stranger@example.test")
    assert r.status_code == 403
    assert env.user_by_email("stranger@example.test") is None
    assert c.get("/auth/me", base_url=BASE).status_code == 401
    assert rows(env, action="login", result="deny", actor="stranger@example.test")


def test_not_in_allowlist_browser_gets_html_page(env, monkeypatch):
    r = oauth_login(monkeypatch, appmod.app.test_client(), "stranger@example.test",
                    headers={"Accept": "text/html,application/xhtml+xml"})
    body = r.get_data(as_text=True)
    assert r.status_code == 403 and r.mimetype == "text/html"
    assert "Доступ по приглашению" in body and "stranger@example.test" in body
    assert "попросите администратора добавить эту почту" in body.lower()
    assert "not_in_allowlist" not in body
    assert env.user_by_email("stranger@example.test") is None


def test_not_in_allowlist_html_escapes_email(env, monkeypatch):
    r = oauth_login(monkeypatch, appmod.app.test_client(), "<b>x</b>@example.test", headers={"Accept": "text/html"})
    body = r.get_data(as_text=True)
    assert r.status_code == 403 and "<b>x</b>" not in body and "&lt;b&gt;" in body


def test_not_in_allowlist_api_keeps_json_403(env, monkeypatch):
    r = oauth_login(monkeypatch, appmod.app.test_client(), "stranger@example.test",
                    headers={"Accept": "application/json"})
    assert r.status_code == 403 and r.get_json() == {"error": "Доступ запрещён", "code": "not_in_allowlist"}


def test_not_in_allowlist_logged_with_masked_email(env, monkeypatch, caplog):
    with caplog.at_level("WARNING", logger="auth"):
        oauth_login(monkeypatch, appmod.app.test_client(), "stranger@example.test")
    text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "auth: not_in_allowlist s***@example.test" in text
    assert "stranger" not in text
    assert auth.mask_email("sergey@example.test") == "s***@example.test" and auth.mask_email("") == "***"


@pytest.mark.parametrize("kw", [{"verified": False}, {"nonce_ok": False}, {"aud": "other-client"}])
def test_login_rejects_bad_id_token(env, monkeypatch, kw):
    r = oauth_login(monkeypatch, appmod.app.test_client(), "boris@example.test", **kw)
    assert r.status_code == 400
    assert env.user_by_email("boris@example.test") is None


def test_callback_rejects_wrong_state(env, monkeypatch):
    c = appmod.app.test_client()
    grant_pdn(c)
    c.get("/auth/login", base_url=BASE)
    monkeypatch.setattr(auth, "exchange_code", lambda *a: pytest.fail("обмен кода не должен вызываться"))
    assert c.get("/auth/callback?code=x&state=wrong", base_url=BASE).status_code == 400


def test_oauth_callback_denied_when_consent_revoked(env, monkeypatch):
    c = appmod.app.test_client()
    grant_pdn(c, "boris@example.test")
    r = c.get("/auth/login", base_url=BASE)
    assert r.status_code == 302
    q = urllib.parse.parse_qs(urllib.parse.urlparse(r.headers["Location"]).query)
    appmod.app.extensions["pdn_store"].revoke("boris@example.test")
    claims = {"iss": "https://accounts.google.com", "aud": CLIENT_ID, "exp": time.time() + 600,
              "email": "boris@example.test", "email_verified": True, "name": "Synthetic", "nonce": q["nonce"][0]}
    monkeypatch.setattr(auth, "exchange_code", lambda code, redirect_uri: claims)
    out = c.get("/auth/callback?code=synthetic&state=" + q["state"][0], base_url=BASE)
    assert out.status_code == 403 and out.get_json()["code"] == "consent_required"
    assert c.get("/auth/me", base_url=BASE).status_code == 401


def test_oauth_callback_denied_when_consent_email_differs(env, monkeypatch):
    c = appmod.app.test_client()
    grant_pdn(c, "other-synth@example.test")
    r = c.get("/auth/login", base_url=BASE)
    q = urllib.parse.parse_qs(urllib.parse.urlparse(r.headers["Location"]).query)
    claims = {"iss": "https://accounts.google.com", "aud": CLIENT_ID, "exp": time.time() + 600,
              "email": "boris@example.test", "email_verified": True, "name": "Synthetic", "nonce": q["nonce"][0]}
    monkeypatch.setattr(auth, "exchange_code", lambda code, redirect_uri: claims)
    out = c.get("/auth/callback?code=synthetic&state=" + q["state"][0], base_url=BASE)
    assert out.status_code == 403 and out.get_json()["code"] == "consent_email_mismatch"
    assert env.user_by_email("boris@example.test") is None
    assert c.get("/auth/me", base_url=BASE).status_code == 401


def test_login_not_configured_503(env, monkeypatch):
    monkeypatch.delenv("GOOGLE_CLIENT_ID")
    c = appmod.app.test_client()
    blocked = c.get("/auth/login", base_url=BASE)
    assert blocked.status_code == 302 and blocked.headers["Location"].endswith("/consent")
    grant_pdn(c)
    assert c.get("/auth/login", base_url=BASE).status_code == 503


def test_allowlist_parser():
    parsed = auth.parse_allowlist("A@x.test:owner; b@x.test:investor:CASE-1,bad,c@x.test:nosuchrole,d@x.test:founder:")
    assert parsed == {"a@x.test": [("owner", "*")], "b@x.test": [("investor", "CASE-1")], "d@x.test": [("founder", "*")]}


def test_logout_audited_and_session_cleared(env):
    c, _ = make_client(env, "boris@example.test", [("owner", "*")])
    r = c.post("/auth/logout", base_url=BASE, headers={"X-CSRF-Token": c.csrf})
    assert r.status_code == 200
    assert c.get("/auth/me", base_url=BASE).status_code == 401
    assert rows(env, action="logout", result="allow")


# ---------------------------------------------- каждый маршрут защищён
def all_rules():
    return [(r.endpoint, m, r) for r in appmod.app.url_map.iter_rules() for m in sorted(r.methods - {"HEAD", "OPTIONS"})]


def test_every_endpoint_has_decorator():
    """Новый маршрут без require_role/public валит тест (и старт приложения)."""
    assert auth.unprotected_endpoints(appmod.app) == []


def fill(rule):
    return re.sub(r"<[^>]+>", "x", rule.rule)


@pytest.mark.parametrize("endpoint,method,rule", all_rules(), ids=lambda v: v if isinstance(v, str) else "")
def test_unauthenticated_request_is_rejected_and_audited(env, endpoint, method, rule):
    if endpoint in PUBLIC_ENDPOINTS:
        pytest.skip("публичный маршрут")
    c = appmod.app.test_client()
    r = c.open(fill(rule), method=method, base_url=BASE, headers={"Accept": "application/json"})
    external = appmod.app.view_functions[endpoint]._rbac.get("external")
    assert r.status_code in (EXTERNAL_OK_STATUSES if external else {401}), (endpoint, r.status_code)
    assert rows(env, result="deny", object=fill(rule)), "отказ не записан в audit_log"
    assert r.headers["X-Robots-Tag"] == "noindex, nofollow"


def test_html_get_redirects_to_login(env):
    r = appmod.app.test_client().get("/", base_url=BASE, headers={"Accept": "text/html"})
    assert r.status_code == 302 and r.headers["Location"].endswith("/auth/login")


@pytest.mark.parametrize("role", ALL_ROLES)
def test_matrix_admin_sessions_owner_only(env, role):
    c, _ = make_client(env, role + "@example.test", [(role, "*")])
    for path in ("/admin/sessions", "/admin/audit"):
        assert c.get(path, base_url=BASE).status_code == (200 if role == "owner" else 403), (role, path)


@pytest.mark.parametrize("role", ALL_ROLES)
def test_matrix_home_any_role(env, role):
    c, _ = make_client(env, role + "@example.test", [(role, "*")])
    r = c.get("/", base_url=BASE)
    assert r.status_code == 200 and b'name="csrf-token"' in r.data and b"noindex" in r.data


def test_admin_key_route_is_404(env):
    assert appmod.app.test_client().get("/admin?key=change-me", base_url=BASE).status_code == 404
    owner, _ = make_client(env, "boris@example.test", [("owner", "*")])
    assert owner.get("/admin?key=change-me", base_url=BASE).status_code == 404
    assert not hasattr(appmod, "ADMIN_KEY")


# ------------------------------------------------------------ scope кейса
@pytest.fixture
def cases(env):
    owner, _ = make_client(env, "boris@example.test", [("owner", "*")])
    return owner, start_case(owner, "VC-1001"), start_case(owner, "VC-1002")


# (роль, scope) → доступ к кейсу VC-1001 / VC-1002
SCOPE_MATRIX = [
    (("owner", "*"), 200, 200),
    (("founder", "VC-1001"), 200, 403),
    (("investor", "VC-1002"), 403, 200),
    (("investor", "*"), 200, 200),
    (("fund_staff", "VC-1001"), 200, 403),
    (("expert", "*"), 403, 403),
    (("clinic", "VC-1001"), 403, 403),
    (("buyer", "*"), 403, 403),
]


@pytest.mark.parametrize("grant,own,other", SCOPE_MATRIX)
def test_case_scope_on_session_read(env, cases, grant, own, other):
    _, sid_a, sid_b = cases
    c, _ = make_client(env, "u-%s@example.test" % grant[0], [grant])
    assert c.get("/api/session/" + sid_a, base_url=BASE).status_code == own
    assert c.get("/api/session/" + sid_b, base_url=BASE).status_code == other
    assert c.get("/api/session/" + sid_a + "/report.json", base_url=BASE).status_code in ((200, 404) if own == 200 else (403,))


def test_founder_cannot_start_foreign_case_or_write_as_investor(env, cases):
    owner, sid_a, _ = cases
    founder, _ = make_client(env, "f@example.test", [("founder", "VC-1001")])
    hdr = {"X-CSRF-Token": founder.csrf}
    r = founder.post("/api/session/" + sid_a + "/feedback", json={"text": "ok"}, base_url=BASE, headers=hdr)
    assert r.status_code == 200
    investor, _ = make_client(env, "i@example.test", [("investor", "*")])
    r = investor.post("/api/session/" + sid_a + "/feedback", json={"text": "x"}, base_url=BASE,
                      headers={"X-CSRF-Token": investor.csrf})
    assert r.status_code == 403
    r = investor.delete("/api/session/" + sid_a, base_url=BASE, headers={"X-CSRF-Token": investor.csrf})
    assert r.status_code == 403


def test_founder_start_only_own_project(env):
    founder, _ = make_client(env, "f@example.test", [("founder", "VC-2001")])
    data = {"invite_code": "PILOT-S1-EXT-001", "project_id": "VC-2002"}
    r = founder.post("/api/start", data=data, base_url=BASE, headers={"X-CSRF-Token": founder.csrf})
    assert r.status_code == 403


def test_revoked_grant_stops_working_immediately(env, cases):
    _, sid_a, _ = cases
    c, user = make_client(env, "i@example.test", [("investor", "VC-1001")])
    assert c.get("/api/session/" + sid_a, base_url=BASE).status_code == 200
    env.revoke(user["id"], "investor", "VC-1001")
    assert c.get("/api/session/" + sid_a, base_url=BASE).status_code == 403
    env.ensure_grant(user["id"], "investor", "VC-1001")  # повторный вход по allowlist не воскрешает отозванный грант
    assert c.get("/api/session/" + sid_a, base_url=BASE).status_code == 403


def test_unknown_case_only_wildcard_grants(env):
    inv, _ = make_client(env, "i@example.test", [("investor", "VC-1001")])
    assert inv.get("/api/session/missing", base_url=BASE).status_code == 403
    owner, _ = make_client(env, "o@example.test", [("owner", "*")])
    assert owner.get("/api/session/missing", base_url=BASE).status_code == 404


# ------------------------------------------------------------------ S1
def test_s1_session_roles(env):
    staff, _ = make_client(env, "s@example.test", [("fund_staff", "*")])
    investor, _ = make_client(env, "i@example.test", [("investor", "*")])
    scoped, _ = make_client(env, "x@example.test", [("investor", "JOB-1")])
    assert staff.get("/s1/jobs", base_url=BASE).status_code == 200
    assert investor.get("/s1/jobs", base_url=BASE).status_code == 200
    assert scoped.get("/s1/jobs", base_url=BASE).status_code in EXTERNAL_OK_STATUSES  # нужен scope '*'
    assert staff.get("/s1/tasks", base_url=BASE).status_code == 200
    r = investor.post("/s1/jobs/x/rerun", base_url=BASE, headers={"X-CSRF-Token": investor.csrf})
    assert r.status_code in EXTERNAL_OK_STATUSES  # investor не имеет write-роли → падает на Bearer-проверке маршрута


def test_s1_bearer_still_works_without_session(env, monkeypatch):
    from s1_intake import main as s1
    state = appmod.app.extensions["s1_intake"]
    import dataclasses
    monkeypatch.setitem(appmod.app.extensions, "s1_intake",
                        dataclasses.replace(state, settings=dataclasses.replace(state.settings, admin_token="a" * 32)))
    r = appmod.app.test_client().get("/s1/jobs", base_url=BASE, headers={"Authorization": "Bearer " + "a" * 32})
    assert r.status_code == 200
    assert rows(env, action="view", result="allow", object="/s1/jobs")


# ------------------------------------------------- CSRF, cookie, аудит
def test_csrf_required_for_post(env, cases):
    _, sid_a, _ = cases
    owner, _ = make_client(env, "o@example.test", [("owner", "*")])
    r = owner.post("/api/session/%s/feedback" % sid_a, json={"text": "x"}, base_url=BASE)
    assert r.status_code == 403 and r.get_json()["code"] == "csrf"
    r = owner.post("/api/session/%s/feedback" % sid_a, json={"text": "x"}, base_url=BASE, headers={"X-CSRF-Token": "bad"})
    assert r.status_code == 403
    r = owner.post("/api/session/%s/feedback" % sid_a, json={"text": "x"}, base_url=BASE,
                   headers={"X-CSRF-Token": owner.csrf})
    assert r.status_code == 200


def test_session_cookie_flags(env, monkeypatch):
    r = oauth_login(monkeypatch, appmod.app.test_client(), "boris@example.test")
    cookie = next(v for k, v in r.headers.items() if k == "Set-Cookie" and v.startswith("session="))
    assert "Secure" in cookie and "HttpOnly" in cookie and "SameSite=Lax" in cookie


def test_audit_records_allow_deny_and_change(env, cases):
    owner, sid_a, _ = cases
    inv, _ = make_client(env, "i@example.test", [("investor", "VC-1002")])
    inv.get("/api/session/" + sid_a, base_url=BASE)
    owner.get("/api/session/" + sid_a, base_url=BASE, environ_overrides={"REMOTE_ADDR": "203.0.113.7"})
    deny = rows(env, actor="i@example.test", result="deny")[0]
    assert deny["action"] == "view" and deny["object"] == "/api/session/" + sid_a and deny["detail"] == "forbidden"
    allow = rows(env, actor="boris@example.test", result="allow", ip="203.0.113.7")[0]
    assert allow["action"] == "view" and "case=VC-1001" in allow["detail"]
    assert rows(env, actor="boris@example.test", action="change", object="/api/start")


def test_robots_and_noindex(env):
    c = appmod.app.test_client()
    r = c.get("/robots.txt", base_url=BASE)
    assert b"Disallow: /" in r.data and r.headers["X-Robots-Tag"] == "noindex, nofollow"
    assert c.get("/health", base_url=BASE).status_code == 200


PG_URL = os.environ.get("HQ018_PG_URL")


@pytest.mark.skipif(not PG_URL, reason="нужен HQ018_PG_URL (Postgres 16)")
def test_postgres_store_and_idempotent_migration():
    store = AuthStore(PG_URL)
    AuthStore(PG_URL)  # повторное применение миграции не падает
    email = "pg-%d@example.test" % time.time_ns()
    user = store.upsert_user(email, "Synthetic PG")
    store.ensure_grant(user["id"], "fund_staff", "CASE-PG")
    store.ensure_grant(user["id"], "fund_staff", "CASE-PG")
    assert store.active_grants(user["id"]) == [("fund_staff", "CASE-PG")]
    store.revoke(user["id"], "fund_staff", "CASE-PG")
    assert store.active_grants(user["id"]) == []
    store.audit(email, "view", "/x", "deny", "203.0.113.9", "pg")
    assert store.audit_rows(1)[0]["actor"] == email
    import psycopg
    with psycopg.connect(PG_URL) as c:
        assert {r[0] for r in c.execute("SELECT code FROM roles")} >= {"owner", "fund_staff"}


def test_no_lp_metrics_in_auth_code():
    for name in ("auth.py", "auth_store.py", "db/migrations/001_roles_access.sql", "docs/ACCESS_MATRIX.md"):
        text = open(os.path.join(os.path.dirname(appmod.__file__), name), encoding="utf-8").read().lower()
        for banned in ("tvpi", "dpi", "rvpi", "management fee", "carry"):
            assert banned not in text, (name, banned)


def test_removed_from_allowlist_logs_out_and_403(env, cases, monkeypatch):
    _, sid_a, _ = cases
    c, _ = make_client(env, "i@example.test", [("investor", "VC-1001")])
    assert c.get("/api/session/" + sid_a, base_url=BASE).status_code == 200
    monkeypatch.setenv("AUTH_ALLOWLIST", "boris@example.test:owner")
    assert c.get("/api/session/" + sid_a, base_url=BASE).status_code == 403
    assert rows(env, actor="i@example.test", detail="removed_from_allowlist")
    with c.session_transaction() as s:
        assert "uid" not in s
    assert c.get("/api/session/" + sid_a, base_url=BASE).status_code == 401


def test_client_ip_ignores_spoofed_forwarded_for(env, cases):
    owner, sid_a, _ = cases
    owner.get("/api/session/" + sid_a, base_url=BASE, headers={"X-Forwarded-For": "6.6.6.6"},
              environ_overrides={"REMOTE_ADDR": "203.0.113.9"})
    assert rows(env, actor="boris@example.test", ip="203.0.113.9")
    assert not rows(env, ip="6.6.6.6")


def test_trusted_proxy_uses_proxyfix():
    from werkzeug.middleware.proxy_fix import ProxyFix
    from flask import Flask
    a = Flask("t")
    a.wsgi_app = ProxyFix(a.wsgi_app, x_for=1)
    a.add_url_rule("/", "ip", lambda: auth.client_ip())
    r = a.test_client().get("/", headers={"X-Forwarded-For": "6.6.6.6, 198.51.100.4"})
    assert r.get_data(as_text=True) == "198.51.100.4"


@pytest.mark.parametrize("env_name", ["staging", "production"])
def test_no_start_without_session_secret(env_name):
    import subprocess
    import sys
    base = {k: v for k, v in os.environ.items() if k not in ("SESSION_SECRET",)}
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    r = subprocess.run([sys.executable, "-c", "import app"], cwd=root, capture_output=True, text=True,
                       env={**base, "APP_ENV": env_name})
    assert r.returncode != 0 and "SESSION_SECRET" in r.stderr
    r = subprocess.run([sys.executable, "-c", "import app"], cwd=root, capture_output=True, text=True,
                       env={**base, "APP_ENV": env_name, "SESSION_SECRET": "synthetic"})
    assert "SESSION_SECRET обязателен" not in r.stderr


def test_audit_limit_cap_and_bad_value(env):
    owner, _ = make_client(env, "o@example.test", [("owner", "*")])
    assert owner.get("/admin/audit?limit=abc", base_url=BASE).status_code == 400
    assert owner.get("/admin/audit?limit=999999", base_url=BASE).status_code == 200
    seen = []
    orig = env.audit_rows
    env.audit_rows = lambda limit=200: seen.append(limit) or orig(limit)
    owner.get("/admin/audit?limit=999999", base_url=BASE)
    assert seen == [500]


# ------------------------------------------------------------ R10 покупатель технологии
def test_buyer_role_exists_with_label(env):
    assert "buyer" in auth.ROLES
    assert ROLE_LABELS["buyer"] == "Покупатель технологии"


def test_buyer_denied_on_working_routes(env, cases):
    """Роль заведена, но ни к одному рабочему маршруту не допущена (deny by default, ТЗ §5.6а)."""
    _, sid_a, _ = cases
    c, _ = make_client(env, "buyer@example.test", [("buyer", "*")])
    for path in ("/s1/jobs", "/s5/today", "/admin/audit", "/api/session/" + sid_a,
                 "/api/session/" + sid_a + "/report.json"):
        assert c.get(path, base_url=BASE).status_code in EXTERNAL_OK_STATUSES - {404}, path
    r = c.post("/api/start", data={"project_id": "VC-1001"}, base_url=BASE, headers={"X-CSRF-Token": c.csrf})
    assert r.status_code == 403
    assert rows(env, actor="buyer@example.test", result="deny")
    me = c.get("/auth/me", base_url=BASE)
    assert me.status_code == 200
