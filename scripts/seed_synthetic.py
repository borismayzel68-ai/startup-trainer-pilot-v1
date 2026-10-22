"""MOS-6 · seed staging: ТОЛЬКО синтетика (example.test). Реальный импорт на staging запрещён до одобрения Бориса.
Отказывается работать, если в users есть не-синтетические записи. Идемпотентно."""
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hq018_seed  # noqa: E402


def assert_synthetic_rows() -> None:
    bad = [e for e, _, _ in hq018_seed.USERS if not e.endswith("@example.test")]
    if bad:
        sys.exit(f"seed содержит несинтетические адреса: {bad}")


def main() -> None:
    assert_synthetic_rows()
    url = os.environ.get("DATABASE_URL") or os.environ.get("SESSION_DATABASE_URL")
    if not url:
        sys.exit("нужен DATABASE_URL")
    os.environ["SESSION_DATABASE_URL"] = url
    import psycopg

    with psycopg.connect(url) as conn:
        real = conn.execute("SELECT count(*) FROM users WHERE NOT is_synthetic").fetchone()[0]
    if real:
        sys.exit(f"в users {real} не-синтетических записей — seed на этой БД запрещён")
    hq018_seed.main()


if __name__ == "__main__":
    main()
