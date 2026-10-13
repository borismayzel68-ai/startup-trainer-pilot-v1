"""MOS-6 · pre-deploy (Д-79 + поправка 13.10): миграции с защитой от потери данных.
Выполняется Render'ом как preDeployCommand внутри сервиса; CI к базе Render не подключается.
Миграция, которая меняет/удаляет данные (DROP, TRUNCATE, DELETE, UPDATE, ALTER ... DROP/TYPE/RENAME),
выкладывается только при точке восстановления:
  - PITR_CONFIRMED=1 (владелец проверил PITR на тарифе БД и выставил переменную), либо
  - успешная резервная копия: таблицы public копируются в схему pre_mig_<UTC> и сверяются по числу строк.
Нет ни того, ни другого — деплой прерывается, миграции не применяются."""
import os
import pathlib
import re
import subprocess
import sys
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
MIGRATIONS = ROOT / "db" / "migrations"

_DESTRUCTIVE = re.compile(
    r"\bDROP\b|\bTRUNCATE\b|\bDO\s+UPDATE\b|\bRENAME\b"
    r"|^\s*(DELETE|UPDATE)\b"
    r"|^\s*ALTER\s+TABLE\b.*\b(ALTER\s+COLUMN|TYPE)\b",
    re.I | re.S,
)


def is_destructive(sql: str) -> bool:
    """True, если хотя бы одна команда меняет или удаляет данные/схему. ON DELETE CASCADE не считается."""
    sql = re.sub(r"--[^\n]*", "", sql)
    sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.S)
    return any(_DESTRUCTIVE.search(stmt) for stmt in sql.split(";"))


def destructive_migrations(directory: pathlib.Path = MIGRATIONS) -> list[str]:
    return [f.name for f in sorted(directory.glob("*.sql")) if is_destructive(f.read_text(encoding="utf-8"))]


def snapshot(url: str) -> str:
    """Резервная копия внутри той же БД: схема pre_mig_<UTC> с копиями таблиц public. Ошибка/расхождение → исключение."""
    import psycopg
    from psycopg import sql

    schema = "pre_mig_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    with psycopg.connect(url) as conn:
        tables = [r[0] for r in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")]
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        for t in tables:
            conn.execute(sql.SQL("CREATE TABLE {}.{} AS TABLE public.{}").format(
                sql.Identifier(schema), sql.Identifier(t), sql.Identifier(t)))
            src = conn.execute(sql.SQL("SELECT count(*) FROM public.{}").format(sql.Identifier(t))).fetchone()[0]
            dst = conn.execute(sql.SQL("SELECT count(*) FROM {}.{}").format(
                sql.Identifier(schema), sql.Identifier(t))).fetchone()[0]
            if src != dst:
                raise RuntimeError(f"резервная копия {t}: {src} != {dst}")
        conn.commit()
    return schema


def main() -> None:
    url = os.environ.get("SESSION_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url or not url.startswith(("postgres://", "postgresql://")):
        sys.exit("нужен DATABASE_URL=postgresql://...")
    risky = destructive_migrations()
    if risky:
        if os.environ.get("PITR_CONFIRMED") == "1":
            print("PITR подтверждён владельцем; миграции с изменением данных:", ", ".join(risky))
        else:
            try:
                print("резервная копия перед миграцией: схема", snapshot(url))
            except Exception as e:  # без точки восстановления такая миграция не выкладывается
                sys.exit(f"деплой прерван: миграции {', '.join(risky)} меняют данные, а резервная копия не сделана ({e})")
    sys.exit(subprocess.call([sys.executable, str(ROOT / "scripts" / "hq018_migrate.py")]))


if __name__ == "__main__":
    main()
