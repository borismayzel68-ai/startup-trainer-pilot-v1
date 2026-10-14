"""MOS-6 · закрытый staging: проверка env при старте, вход через Google по allowlist, noindex.

Включается только при APP_ENV=staging — локальная среда, CI и S1 работают как раньше.
Открыты только /health, /robots.txt и /auth/*; всё остальное — 401 (API) или редирект на вход.
Секреты берутся только из env (Yandex Lockbox), в репозитории их нет."""
import hmac
import secrets
from datetime import timedelta
from urllib.parse import urlencode

import httpx
from flask import jsonify, redirect, request, session, url_for

REQUIRED_ENV = ("DATABASE_URL", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "SESSION_SECRET", "AUTH_ALLOWLIST")
OPEN_PATHS = {"/health", "/robots.txt"}
OPEN_PREFIXES = ("/auth/",)
NOINDEX = "noindex, nofollow, noarchive"
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
MIN_SESSION_SECRET = 32


class StagingConfigError(RuntimeError):
    pass


def parse_allowlist(raw: str) -> frozenset:
    return frozenset(e.strip().lower() for e in raw.replace(";", ",").split(",") if e.strip())


def validate_env(env) -> dict:
    missing = [k for k in REQUIRED_ENV if not (env.get(k) or "").strip()]
    if missing:
        raise StagingConfigError(
            "APP_ENV=staging: не заданы обязательные переменные окружения: " + ", ".join(missing)
            + ". Задайте их в Yandex Lockbox (см. .env.example, README «Развёртывание в Yandex Cloud»).")
    cfg = {k: env[k].strip() for k in REQUIRED_ENV}
    if len(cfg["SESSION_SECRET"]) < MIN_SESSION_SECRET:
        raise StagingConfigError(f"SESSION_SECRET короче {MIN_SESSION_SECRET} символов (openssl rand -hex 32).")
    cfg["allowlist"] = parse_allowlist(cfg["AUTH_ALLOWLIST"])
    if not cfg["allowlist"]:
        raise StagingConfigError("AUTH_ALLOWLIST не содержит ни одного email.")
    cfg["redirect_uri"] = (env.get("OAUTH_REDIRECT_URI") or "").strip()
    return cfg


def init_staging(app, env) -> bool:
    @app.after_request
    def _noindex(resp):
        resp.headers["X-Robots-Tag"] = NOINDEX
        return resp

    if (env.get("APP_ENV") or "").strip().lower() != "staging":
        return False
    cfg = validate_env(env)
    app.secret_key = cfg["SESSION_SECRET"]
    app.config.update(SESSION_COOKIE_SECURE=True, SESSION_COOKIE_HTTPONLY=True,
                      SESSION_COOKIE_SAMESITE="Lax", PERMANENT_SESSION_LIFETIME=timedelta(hours=8))
    app.config["STAGING_MODE"] = True

    @app.before_request
    def _require_login():
        path = request.path
        if path in OPEN_PATHS or path.startswith(OPEN_PREFIXES):
            return None
        email = session.get("user_email")
        if email and email in cfg["allowlist"]:  # повторная проверка: allowlist мог сузиться
            return None
        if path.startswith(("/api/", "/s1/")):
            return jsonify({"ok": False, "error": "unauthorized"}), 401
        return redirect(url_for("auth_login"))

    def redirect_uri():
        return cfg["redirect_uri"] or url_for("auth_callback", _external=True)

    @app.get("/auth/login")
    def auth_login():
        state = secrets.token_urlsafe(32)
        session["oauth_state"] = state
        q = urlencode({"client_id": cfg["GOOGLE_CLIENT_ID"], "redirect_uri": redirect_uri(),
                       "response_type": "code", "scope": "openid email", "state": state,
                       "prompt": "select_account"})
        return redirect(f"{GOOGLE_AUTH_URL}?{q}")

    @app.get("/auth/callback")
    def auth_callback():
        expected = session.pop("oauth_state", "")
        got = request.args.get("state", "")
        code = request.args.get("code", "")
        if not expected or not hmac.compare_digest(expected, got) or not code:
            return jsonify({"ok": False, "error": "bad_state"}), 400
        try:
            tok = httpx.post(GOOGLE_TOKEN_URL, timeout=10, data={
                "code": code, "client_id": cfg["GOOGLE_CLIENT_ID"],
                "client_secret": cfg["GOOGLE_CLIENT_SECRET"],
                "redirect_uri": redirect_uri(), "grant_type": "authorization_code"})
            tok.raise_for_status()
            info = httpx.get(GOOGLE_USERINFO_URL, timeout=10, headers={
                "Authorization": "Bearer " + tok.json()["access_token"]})
            info.raise_for_status()
            info = info.json()
        except (httpx.HTTPError, KeyError, ValueError):
            return jsonify({"ok": False, "error": "oauth_failed"}), 502
        email = str(info.get("email", "")).strip().lower()
        if not info.get("email_verified") or email not in cfg["allowlist"]:
            return jsonify({"ok": False, "error": "forbidden"}), 403
        session.clear()
        session.permanent = True
        session["user_email"] = email
        return redirect("/")

    @app.post("/auth/logout")
    def auth_logout():
        session.clear()
        return jsonify({"ok": True})

    return True
