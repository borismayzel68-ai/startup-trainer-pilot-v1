"""MOS-3: импорт dealflow из CSV. По умолчанию dry-run (БД не затрагивается); запись — только с --apply.
Пример: python scripts/import_dealflow.py --migration-plan a.csv --core-dashboard b.csv --books c.csv [--apply]"""
import argparse
import json
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dealflow.importer import ImportFormatError, analyze, apply, render_markdown  # noqa: E402


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--migration-plan")
    p.add_argument("--core-dashboard")
    p.add_argument("--books")
    p.add_argument("--apply", action="store_true", help="записать в БД (иначе dry-run)")
    p.add_argument("--report", help="путь отчёта (.json или .md), только вне репозитория")
    p.add_argument("--format", choices=("md", "json"), default="md", help="формат stdout")
    p.add_argument("--db-url", help="по умолчанию SESSION_DATABASE_URL / DATABASE_URL")
    a = p.parse_args(argv)
    paths = {"migration_plan": a.migration_plan, "core_dashboard": a.core_dashboard, "books": a.books}
    if not any(paths.values()):
        p.error("нужен хотя бы один CSV")
    if a.report:
        rp = pathlib.Path(a.report).resolve()
        if rp == ROOT or ROOT in rp.parents:
            p.error("--report должен лежать вне репозитория")
    try:
        rep = analyze(paths)
    except (ImportFormatError, OSError) as e:
        print(f"ошибка: {e}", file=sys.stderr)
        return 2
    res = None
    if a.apply:
        url = a.db_url or os.environ.get("SESSION_DATABASE_URL") or os.environ.get("DATABASE_URL")
        if not url or not url.startswith(("postgres://", "postgresql://")):
            print("ошибка: для --apply нужен --db-url или SESSION_DATABASE_URL", file=sys.stderr)
            return 2
        import psycopg
        with psycopg.connect(url) as conn:
            res = apply(conn, rep)
    md = render_markdown(rep, res)
    if a.report:
        text = json.dumps(rep, ensure_ascii=False, indent=2) if a.report.endswith(".json") else md
        pathlib.Path(a.report).write_text(text, encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False, indent=2) if a.format == "json" else md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
