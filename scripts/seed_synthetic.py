"""MOS-6 · seed staging: ТОЛЬКО синтетика (is_synthetic=TRUE). Реальный импорт на staging запрещён до одобрения Бориса.
Признак синтетики — колонка is_synthetic, а не домен email. Отказывается работать, если в users есть записи с
is_synthetic=FALSE (кроме операторов из AUTH_ALLOWLIST, входящих через MOS-1). Идемпотентно."""
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hq018_seed  # noqa: E402
from staging_guard import allowlist_emails  # noqa: E402


def non_synthetic_count(conn, operators) -> int:
    return conn.execute("SELECT count(*) FROM users WHERE NOT is_synthetic AND NOT (lower(email) = ANY(%s))",
                        (list(operators),)).fetchone()[0]


def unflagged_seed_users(conn) -> list:
    """Пользователи seed, у которых после записи нет is_synthetic=TRUE (например, email уже занят реальной записью)."""
    emails = [e for e, _, _ in hq018_seed.USERS]
    rows = conn.execute("SELECT email FROM users WHERE email = ANY(%s) AND NOT is_synthetic", (emails,)).fetchall()
    return [r[0] for r in rows]


def main() -> None:
    url = os.environ.get("DATABASE_URL") or os.environ.get("SESSION_DATABASE_URL")
    if not url:
        sys.exit("нужен DATABASE_URL")
    operators = allowlist_emails(os.environ.get("AUTH_ALLOWLIST", ""))
    os.environ["SESSION_DATABASE_URL"] = url
    import psycopg

    with psycopg.connect(url) as conn:
        real = non_synthetic_count(conn, operators)
        if real:
            sys.exit(f"в users {real} записей без is_synthetic — seed на этой БД запрещён")
    hq018_seed.main()
    with psycopg.connect(url) as conn:
        bad = unflagged_seed_users(conn)
        if bad:
            sys.exit(f"после seed записи без is_synthetic: {bad}")


if __name__ == "__main__":
    main()
