"""MOS-6 · закрытый staging: проверка env при старте, обязательный вход из MOS-1 (#61), noindex, только синтетика.

Включается только при APP_ENV=staging — локальная среда, CI и S1 работают как раньше.
Собственного OAuth здесь нет: вход, сессия и RBAC — модуль `auth` из MOS-1 (#61, `auth.current_user()`).
Здесь только страховочный замок: без сессии #61 открыты лишь /health, /robots.txt и /auth/* (их защищает сам #61);
остальное — 401 (API) или редирект на /auth/login. Если модуля `auth` нет, приложение не стартует (fail-closed).
Секреты берутся только из env (Yandex Lockbox), в репозитории их нет."""
import importlib

from flask import jsonify, redirect, request

REQUIRED_ENV = ("DATABASE_URL", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "SESSION_SECRET", "AUTH_ALLOWLIST")
OPEN_PATHS = {"/health", "/robots.txt"}
OPEN_PREFIXES = ("/auth/",)
NOINDEX = "noindex, nofollow, noarchive"
LOGIN_PATH = "/auth/login"
MIN_SESSION_SECRET = 32
_TRUE = {"1", "true", "yes", "on"}


class StagingConfigError(RuntimeError):
    pass


def allowlist_emails(raw: str) -> list:
    """Email'ы из AUTH_ALLOWLIST формата MOS-1 (`email[:role[:scope]]`, разделители , ; перевод строки)."""
    items = (e.split(":", 1)[0].strip().lower() for e in raw.replace(";", ",").replace("\n", ",").split(","))
    return sorted({e for e in items if "@" in e})


def validate_env(env) -> dict:
    missing = [k for k in REQUIRED_ENV if not (env.get(k) or "").strip()]
    if missing:
        raise StagingConfigError(
            "APP_ENV=staging: не заданы обязательные переменные окружения: " + ", ".join(missing)
            + ". Задайте их в Yandex Lockbox (см. .env.example, README «Развёртывание в Yandex Cloud»).")
    cfg = {k: env[k].strip() for k in REQUIRED_ENV}
    if len(cfg["SESSION_SECRET"]) < MIN_SESSION_SECRET:
        raise StagingConfigError(f"SESSION_SECRET короче {MIN_SESSION_SECRET} символов (openssl rand -hex 32).")
    cfg["allowlist"] = allowlist_emails(cfg["AUTH_ALLOWLIST"])
    if not cfg["allowlist"]:
        raise StagingConfigError("AUTH_ALLOWLIST не содержит ни одного email.")
    return cfg


def synthetic_only(env) -> bool:
    """STAGING_SYNTHETIC_ONLY: по умолчанию true (до решения юриста по #68). Любое иное значение — выключено."""
    raw = env.get("STAGING_SYNTHETIC_ONLY")
    return True if raw is None or not raw.strip() else raw.strip().lower() in _TRUE


def check_synthetic_data(database_url: str, operators=()) -> None:
    """Отказ запуска, если в БД есть пользователи без признака `is_synthetic` (признак — колонка, а не домен email).
    Исключение — email'ы из AUTH_ALLOWLIST: это участники пилота, которые входят через MOS-1 (их запись создаёт сам вход).
    Fail-closed: недоступная БД = отказ. sqlite (только локальные тесты) не проверяется;
    пустая БД без таблицы users допустима (миграции ещё не прошли)."""
    if not database_url.startswith(("postgres://", "postgresql://")):
        return
    import psycopg
    from psycopg import errors

    try:
        with psycopg.connect(database_url, connect_timeout=10) as conn:
            real = conn.execute(
                "SELECT count(*) FROM users WHERE NOT is_synthetic AND NOT (lower(email) = ANY(%s))",
                (list(operators),)).fetchone()[0]
    except errors.UndefinedTable:
        return
    except psycopg.Error as exc:
        raise StagingConfigError(f"не удалось проверить БД на синтетические данные: {type(exc).__name__}") from exc
    if real:
        raise StagingConfigError(
            f"STAGING_SYNTHETIC_ONLY: в users найдено записей без is_synthetic (не из AUTH_ALLOWLIST): {real}. "
            "Staging до решения юриста (#68) работает только с синтетикой.")


def mos1_user_loader():
    """Сессия MOS-1 (#61): `auth.current_user()`. Нет модуля — нет входа — нет старта."""
    try:
        return importlib.import_module("auth").current_user
    except (ImportError, AttributeError) as exc:
        raise StagingConfigError(
            "APP_ENV=staging требует вход из MOS-1 (#61: модуль auth с current_user()). "
            "Он не найден — слейте #61 в ветку; собственного входа у staging нет.") from exc


def init_staging(app, env, data_check=check_synthetic_data, user_loader=None) -> bool:
    @app.after_request
    def _noindex(resp):
        resp.headers["X-Robots-Tag"] = NOINDEX
        return resp

    if (env.get("APP_ENV") or "").strip().lower() != "staging":
        return False
    cfg = validate_env(env)
    if not synthetic_only(env):
        raise StagingConfigError(
            "STAGING_SYNTHETIC_ONLY выключен: staging до решения юриста (#68, OpenAI в США) "
            "запускается только с синтетическими данными. Уберите переменную или поставьте true.")
    current_user = user_loader or mos1_user_loader()
    data_check(cfg["DATABASE_URL"], cfg["allowlist"])
    app.secret_key = cfg["SESSION_SECRET"]  # тот же SESSION_SECRET, что читает #61 (подпись cookie сессии)
    app.config.update(SESSION_COOKIE_SECURE=True, SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")
    app.config["STAGING_MODE"] = True

    @app.before_request
    def _require_login():
        path = request.path
        if path in OPEN_PATHS or path.startswith(OPEN_PREFIXES):
            return None
        if current_user():  # сессия MOS-1; роли и scope проверяет RBAC #61
            return None
        if path.startswith(("/api/", "/s1/")):
            return jsonify({"ok": False, "error": "unauthorized"}), 401
        return redirect(LOGIN_PATH)

    return True
