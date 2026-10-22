"""MOS-1 (#52): вход через Google OAuth (OIDC) по allowlist, RBAC на каждом маршруте, CSRF, аудит.

Модель доступа — docs/ACCESS_MATRIX.md. Схема БД — db/migrations/001_roles_access.sql.
Тесты мокают exchange_code(): сетевых вызовов нет.
"""
import base64
import hmac
import json
import logging
import os
import secrets
import time
import urllib.parse
from functools import wraps

from flask import Blueprint, current_app, g, jsonify, redirect, request, session

from auth_store import ROLE_LABELS, AuthStore

log = logging.getLogger("auth")

ROLES = frozenset(ROLE_LABELS)
ANY_ROLE = ROLES
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_ISSUERS = ("https://accounts.google.com", "accounts.google.com")
# Роли, которым разрешён доступ к S1-маршрутам, защищённым общим admin-токеном (см. s1_intake.main._admin_ok).
STAFF_ROLES = frozenset({"owner", "fund_staff"})

bp = Blueprint("auth", __name__, url_prefix="/auth")


# ------------------------------------------------------------------ allowlist
def parse_allowlist(text):
    """'a@x.test:owner, b@x.test:investor:CASE-1' → {email: [(role, scope), ...]}. Разделители: , ; перевод строки."""
    result = {}
    for raw in (text or "").replace(";", ",").replace("\n", ",").split(","):
        raw = raw.strip()
        if not raw:
            continue
        parts = [p.strip() for p in raw.split(":", 2)]
        if len(parts) < 2 or "@" not in parts[0] or parts[1] not in ROLES:
            log.warning("AUTH_ALLOWLIST: пропущена некорректная запись")
            continue
        scope = parts[2] if len(parts) == 3 and parts[2] else "*"
        result.setdefault(parts[0].lower(), []).append((parts[1], scope))
    return result


def allowlist():
    return parse_allowlist(os.environ.get("AUTH_ALLOWLIST", ""))


# ---------------------------------------------------------------------- OIDC
def _jwt_claims(id_token):
    payload = id_token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


def exchange_code(code, redirect_uri):
    """Обмен code на claims id_token. id_token получен прямо от token endpoint Google по TLS —
    подпись по OIDC Core 3.1.3.7 не обязательна; iss/aud/exp/nonce проверяются в callback."""
    import httpx
    r = httpx.post(GOOGLE_TOKEN_URL, timeout=10, data={
        "code": code, "client_id": os.environ.get("GOOGLE_CLIENT_ID", ""),
        "client_secret": os.environ.get("GOOGLE_CLIENT_SECRET", ""),
        "redirect_uri": redirect_uri, "grant_type": "authorization_code"})
    r.raise_for_status()
    return _jwt_claims(r.json()["id_token"])


def _redirect_uri():
    return os.environ.get("AUTH_REDIRECT_URI") or request.url_root.rstrip("/") + "/auth/callback"


# -------------------------------------------------------------------- helpers
def store() -> AuthStore:
    return current_app.extensions["auth_store"]


def client_ip():
    """Только request.remote_addr: за доверенным прокси его подменяет ProxyFix (TRUSTED_PROXY_COUNT, см. app.py).
    Заголовок X-Forwarded-For напрямую не читается — клиент мог бы вписать в него любой IP."""
    return request.remote_addr or ""


def audit(action, obj, result, actor=None, detail=None):
    store().audit(actor, action, obj, result, client_ip(), detail)


def current_user():
    if "rbac_user" in g:
        return g.rbac_user
    user = None
    uid = session.get("uid")
    if uid:
        user = store().user_by_id(uid)
        if user and user["email"].lower() not in allowlist():
            # email убран из AUTH_ALLOWLIST — доступ отзывается на ближайшем же запросе
            audit("logout", request.path, "deny", actor=user["email"], detail="removed_from_allowlist")
            session.clear()
            g.rbac_revoked = True
            user = None
        elif user:
            user["grants"] = store().active_grants(uid)  # отзыв гранта действует сразу
    g.rbac_user = user
    return user


def public(fn):
    """Маршрут без проверки роли: только /health, /robots.txt, /auth/login, /auth/callback."""
    fn._rbac = {"public": True}
    return fn


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def _csrf_ok():
    sent = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token") or ""
    return bool(sent) and hmac.compare_digest(sent, session.get("csrf", ""))


def require_role(roles, scope=None, external=None):
    """Декоратор RBAC. scope: None | '*' | ID кейса | callable(view_kwargs) -> ID кейса.
    external: 'secret' | 'link' — маршрут имеет собственную проверку (Bearer/подпись ссылки); если сессии нет
    или она не подходит, запрос передаётся ей, а итог пишется в audit_log по коду ответа."""
    roles = frozenset(roles)
    assert roles <= ROLES, roles - ROLES

    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not current_app.config.get("AUTH_ENFORCED", True):
                return fn(*args, **kwargs)
            return _enforce(fn, roles, scope, external, args, kwargs)
        wrapper._rbac = {"roles": roles, "scope": scope, "external": external}
        return wrapper
    return deco


def _has_external_credential(kind):
    """Есть ли у запроса учётные данные, которые проверит сам маршрут (Bearer, секрет, ключ вебхука, подписанная ссылка).
    Вошедший пользователь без таких данных и без прав получает 403, а не отказ из чужой проверки."""
    if any(request.headers.get(h) for h in ("Authorization", "X-Completion-Secret", "X-Webhook-Key")):
        return True
    return kind == "link" and bool(request.args)


def _deny(user, action, obj, why, status):
    audit(action, obj, "deny", actor=user["email"] if user else None, detail=why)
    if status == 401 and request.method == "GET" and "text/html" in request.headers.get("Accept", ""):
        return redirect("/auth/login")
    return jsonify({"error": "Требуется вход" if status == 401 else "Доступ запрещён", "code": why}), status


def _enforce(fn, roles, scope, external, args, kwargs):
    user = current_user()
    action = "view" if request.method in SAFE_METHODS else "change"
    obj = request.path
    case = scope(kwargs) if callable(scope) else scope
    granted = set()
    if user:
        if scope is None:
            granted = {r for r, _ in user["grants"] if r in roles}
        elif scope == "*":
            granted = {r for r, s in user["grants"] if r in roles and s == "*"}
        else:
            granted = {r for r, s in user["grants"] if r in roles and (s == "*" or (case is not None and s == case))}
    if user and granted:
        if request.method not in SAFE_METHODS and not _csrf_ok():
            return _deny(user, action, obj, "csrf", 403)
        g.rbac_roles = granted
        g.rbac_session_ok = True
        audit(action, obj, "allow", actor=user["email"], detail="case=%s roles=%s" % (case, ",".join(sorted(granted))))
        return fn(*args, **kwargs)
    if g.get("rbac_revoked") and not (external and _has_external_credential(external)):
        g.rbac_delegated = None
        return jsonify({"error": "Доступ запрещён", "code": "removed_from_allowlist"}), 403
    if external and (not user or _has_external_credential(external)):
        g.rbac_delegated = (user["email"] if user else None, action, obj, case)
        return fn(*args, **kwargs)
    if not user:
        return _deny(None, action, obj, "unauthenticated", 401)
    return _deny(user, action, obj, "forbidden", 403)


def apply_policies(app, policies):
    """Оборачивает уже зарегистрированные view (S1-blueprint) в require_role: {endpoint: (roles, scope, external)}."""
    for endpoint, (roles, scope, external) in policies.items():
        view = app.view_functions[endpoint]
        if not hasattr(view, "_rbac"):
            app.view_functions[endpoint] = require_role(roles, scope, external)(view)


def unprotected_endpoints(app):
    return sorted(ep for ep, view in app.view_functions.items() if not hasattr(view, "_rbac"))


def session_ok():
    """True, если запрос прошёл require_role по сессии (а не по Bearer/ссылке)."""
    return bool(g.get("rbac_session_ok"))


# ----------------------------------------------------------------------- routes
@bp.get("/login")
@public
def login():
    client_id = os.environ.get("GOOGLE_CLIENT_ID", "")
    if not client_id or not os.environ.get("GOOGLE_CLIENT_SECRET"):
        return jsonify({"error": "OAuth не настроен", "code": "oauth_not_configured"}), 503
    session["oauth_state"] = secrets.token_urlsafe(24)
    session["oauth_nonce"] = secrets.token_urlsafe(24)
    query = urllib.parse.urlencode({
        "client_id": client_id, "redirect_uri": _redirect_uri(), "response_type": "code",
        "scope": "openid email profile", "state": session["oauth_state"], "nonce": session["oauth_nonce"],
        "prompt": "select_account"})
    return redirect(GOOGLE_AUTH_URL + "?" + query)


@bp.get("/callback")
@public
def callback():
    state, nonce = session.pop("oauth_state", None), session.pop("oauth_nonce", None)
    code = request.args.get("code", "")
    if request.args.get("error") or not code or not state or not hmac.compare_digest(state, request.args.get("state", "")):
        audit("login", "/auth/callback", "deny", detail="oauth_state_or_error")
        return jsonify({"error": "Вход не выполнен", "code": "oauth_failed"}), 400
    try:
        claims = exchange_code(code, _redirect_uri())
    except Exception:  # сеть/Google недоступны — без деталей наружу
        log.exception("OAuth: обмен кода не удался")
        audit("login", "/auth/callback", "deny", detail="token_exchange_failed")
        return jsonify({"error": "Вход не выполнен", "code": "oauth_failed"}), 502
    email = str(claims.get("email", "")).strip().lower()
    valid = (claims.get("iss") in GOOGLE_ISSUERS and claims.get("aud") == os.environ.get("GOOGLE_CLIENT_ID")
             and float(claims.get("exp", 0)) > time.time() and nonce and claims.get("nonce") == nonce
             and claims.get("email_verified") in (True, "true") and email)
    if not valid:
        audit("login", "/auth/callback", "deny", actor=email or None, detail="id_token_invalid")
        return jsonify({"error": "Вход не выполнен", "code": "oauth_failed"}), 400
    entries = allowlist().get(email)
    if not entries:  # вне allowlist: пользователь НЕ создаётся
        audit("login", "/auth/callback", "deny", actor=email, detail="not_in_allowlist")
        return jsonify({"error": "Доступ запрещён", "code": "not_in_allowlist"}), 403
    user = store().upsert_user(email, claims.get("name") or email)
    for role, scope in entries:
        store().ensure_grant(user["id"], role, scope)
    session.clear()  # новая сессия после входа (против фиксации)
    session["uid"] = user["id"]
    session.permanent = True
    csrf_token()
    audit("login", "/auth/callback", "allow", actor=email)
    return redirect("/")


@bp.post("/logout")
@require_role(ANY_ROLE)
def logout():
    audit("logout", "/auth/logout", "allow", actor=current_user()["email"])
    session.clear()
    return jsonify({"ok": True})


@bp.get("/me")
@require_role(ANY_ROLE)
def me():
    u = current_user()
    return jsonify({"email": u["email"], "grants": [{"role": r, "scope": s} for r, s in u["grants"]],
                    "csrf_token": csrf_token()})


# ------------------------------------------------------------------------ init
def init_auth(app, database_url, policies=None):
    """Вызывать ПОСЛЕ регистрации всех маршрутов. Падает при старте, если есть маршрут без require_role/public."""
    app.extensions["auth_store"] = AuthStore(database_url)
    app.config.setdefault("AUTH_ENFORCED", True)
    app.register_blueprint(bp)
    if policies:
        apply_policies(app, policies)
    missing = unprotected_endpoints(app)
    if missing:
        raise RuntimeError("Маршруты без require_role/public: %s" % ", ".join(missing))

    @app.after_request
    def _after(resp):
        resp.headers["X-Robots-Tag"] = "noindex, nofollow"
        deleg = g.get("rbac_delegated")
        if deleg and "auth_store" in app.extensions:
            actor, action, obj, case = deleg
            store().audit(actor, action, obj, "allow" if resp.status_code < 400 else "deny", client_ip(),
                          "delegated case=%s status=%s" % (case, resp.status_code))
        return resp
