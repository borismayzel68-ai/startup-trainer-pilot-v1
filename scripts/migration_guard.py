"""MOS-6 · Yandex Cloud: находит миграции, меняющие или удаляющие данные (нужна точка восстановления БД).
Статическая проверка файлов db/migrations, к БД не подключается.
Код выхода: 0 — только добавляющие миграции; 10 — есть деструктивные (перед миграцией нужен снимок БД)."""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
MIGRATIONS = ROOT / "db" / "migrations"
EXIT_DESTRUCTIVE = 10

_DESTRUCTIVE = re.compile(
    r"\bDROP\b|\bTRUNCATE\b|\bDELETE\s+FROM\b|\bUPDATE\b"
    r"|\bALTER\s+TABLE\b[^;]*\b(?:ALTER\s+COLUMN\b[^;]*\bTYPE|RENAME|SET\s+NOT\s+NULL)\b",
    re.I | re.S)
_ON_CONFLICT_UPDATE = re.compile(r"\bDO\s+UPDATE\b", re.I)


def _strip_comments(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    return re.sub(r"--[^\n]*", " ", sql)


def is_destructive(sql: str) -> bool:
    """ON CONFLICT ... DO UPDATE и ON DELETE CASCADE деструктивными не считаются."""
    return bool(_DESTRUCTIVE.search(_ON_CONFLICT_UPDATE.sub(" ", _strip_comments(sql))))


def destructive_migrations(directory: pathlib.Path = MIGRATIONS) -> list:
    return [f.name for f in sorted(directory.glob("*.sql")) if is_destructive(f.read_text(encoding="utf-8"))]


def main() -> int:
    bad = destructive_migrations()
    if bad:
        print("DESTRUCTIVE: " + ", ".join(bad))
        return EXIT_DESTRUCTIVE
    print("additive-only")
    return 0


if __name__ == "__main__":
    sys.exit(main())
