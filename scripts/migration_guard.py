"""MOS-6 · Yandex Cloud: находит миграции, меняющие или удаляющие данные (нужна точка восстановления БД).
Статическая проверка файлов db/migrations, к БД не подключается.
Код выхода: 0 — только добавляющие миграции; 10 — есть деструктивные (перед миграцией нужен снимок БД);
20 — дубль числового префикса (два файла с одним номером, напр. 004_a.sql и 004_b.sql) — CI падает."""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
MIGRATIONS = ROOT / "db" / "migrations"
EXIT_DESTRUCTIVE = 10
EXIT_DUPLICATE_PREFIX = 20
_PREFIX = re.compile(r"^(\d+)_")

_DESTRUCTIVE = re.compile(
    r"\bDROP\b|\bTRUNCATE\b|\bDELETE\s+FROM\b|\bUPDATE\b"
    r"|\bALTER\s+TABLE\b[^;]*\b(?:ALTER\s+COLUMN\b[^;]*\bTYPE|RENAME|SET\s+NOT\s+NULL)\b",
    re.I | re.S)
_ON_CONFLICT_UPDATE = re.compile(r"\bDO\s+UPDATE\b", re.I)
# Пересоздание триггера (DROP TRIGGER ...; CREATE TRIGGER ... BEFORE UPDATE OR DELETE ON ...) данные не меняет.
_DROP_TRIGGER = re.compile(r"\bDROP\s+TRIGGER\b[^;]*;", re.I)
_TRIGGER_EVENTS = re.compile(
    r"\b(?:BEFORE|AFTER|INSTEAD\s+OF)\s+(?:INSERT|UPDATE|DELETE|TRUNCATE)(?:\s+OF\s+[\w\s,]+?)?"
    r"(?:\s+OR\s+(?:INSERT|UPDATE|DELETE|TRUNCATE))*\s+ON\b", re.I)


def _strip_comments(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    return re.sub(r"--[^\n]*", " ", sql)


def is_destructive(sql: str) -> bool:
    """ON CONFLICT ... DO UPDATE, ON DELETE CASCADE и DROP TRIGGER / события триггера деструктивными не считаются."""
    text = _strip_comments(sql)
    for rx in (_ON_CONFLICT_UPDATE, _DROP_TRIGGER, _TRIGGER_EVENTS):
        text = rx.sub(" ", text)
    return bool(_DESTRUCTIVE.search(text))


def destructive_migrations(directory: pathlib.Path = MIGRATIONS) -> list:
    return [f.name for f in sorted(directory.glob("*.sql")) if is_destructive(f.read_text(encoding="utf-8"))]


def duplicate_prefixes(directory: pathlib.Path = MIGRATIONS) -> dict:
    """{номер: [файлы]} для номеров, у которых больше одного файла. Файл без числового префикса — тоже ошибка."""
    seen: dict = {}
    for f in sorted(directory.glob("*.sql")):
        m = _PREFIX.match(f.name)
        key = int(m.group(1)) if m else f"без номера: {f.name}"
        seen.setdefault(key, []).append(f.name)
    return {k: v for k, v in seen.items() if len(v) > 1 or isinstance(k, str)}


def main() -> int:
    dup = duplicate_prefixes()
    if dup:
        print("DUPLICATE PREFIX: " + "; ".join(f"{k}: {', '.join(v)}" for k, v in dup.items()))
        return EXIT_DUPLICATE_PREFIX
    bad = destructive_migrations()
    if bad:
        print("DESTRUCTIVE: " + ", ".join(bad))
        return EXIT_DESTRUCTIVE
    print("additive-only")
    return 0


if __name__ == "__main__":
    sys.exit(main())
