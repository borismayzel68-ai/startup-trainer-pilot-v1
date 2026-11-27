"""#114: выдача одноразовой ссылки входа (запасной вход, AUTH_INVITE_FALLBACK=1). Писем не отправляет.
Только для почты из allowlist (AUTH_ALLOWLIST ∪ действующие приглашения). Токен печатается один раз, в БД — хэш.
Пример: python scripts/issue_login_token.py vc991-founder@example.test --ttl-minutes 60 --base-url https://<ДОМЕН>"""
import argparse
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from auth import parse_allowlist  # noqa: E402
from auth_store import AuthStore  # noqa: E402


def main(argv=None, environ=None):
    environ = os.environ if environ is None else environ
    ap = argparse.ArgumentParser()
    ap.add_argument("email")
    ap.add_argument("--ttl-minutes", type=int, default=60)
    ap.add_argument("--base-url", default="https://<ДОМЕН>")
    a = ap.parse_args(argv)
    url = environ.get("SESSION_DATABASE_URL") or environ.get("DATABASE_URL") or "sqlite:///trainer_sessions.db"
    store = AuthStore(url)
    email = a.email.strip().lower()
    allowed = parse_allowlist(environ.get("AUTH_ALLOWLIST", ""))
    for e, entries in store.active_invites().items():
        allowed.setdefault(e, []).extend(entries)
    if email not in allowed:
        print("Отказ: почта не в списке допущенных (AUTH_ALLOWLIST или приглашения).", file=sys.stderr)
        return 2
    try:
        token = store.create_login_token(email, a.ttl_minutes * 60)
    except ValueError:
        print("Отказ: срок от 1 минуты до 7 суток.", file=sys.stderr)
        return 2
    print("%s/auth/invite/%s" % (a.base_url.rstrip("/"), token))
    print("Ссылка одноразовая, действует %d мин. Не пересылайте её в чаты с превью." % a.ttl_minutes, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
