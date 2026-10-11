"""HQ-018 · E-1: применить миграции к БД из SESSION_DATABASE_URL / DATABASE_URL.
1) существующие миграции приложения (CREATE TABLE IF NOT EXISTS в session_store.py и s1_intake/store.py);
2) черновые SQL-миграции HQ-018 из db/migrations (только для Postgres). Идемпотентно."""
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> None:
    url = os.environ.get("SESSION_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url or not url.startswith(("postgres://", "postgresql://")):
        sys.exit("нужен SESSION_DATABASE_URL=postgresql://...")
    from session_store import DurableSessionStore
    from s1_intake.store import ReadinessJobStore

    DurableSessionStore(url)
    ReadinessJobStore(url)
    import psycopg

    with psycopg.connect(url) as conn:
        for f in sorted((ROOT / "db" / "migrations").glob("*.sql")):
            conn.execute(f.read_text(encoding="utf-8"))
            print("applied", f.name)
        conn.commit()
    print("migrations ok")


if __name__ == "__main__":
    main()
