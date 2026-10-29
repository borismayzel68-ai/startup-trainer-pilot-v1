"""Миграция 004_fund_view → 005_fund_view: (a) чистая БД 001→005; (b) повтор — no-op; (c) БД со старой
004_fund_view; (d) down → up; (e) guard падает на дубле префикса. Только синтетика. Postgres-тесты — на
отдельных временных БД (создаются и удаляются), нужен HQ018_PG_URL с правом CREATE DATABASE (в CI — да)."""
import os
import pathlib
import subprocess
import sys
import uuid

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
MIG = ROOT / "db" / "migrations"
DOWN = MIG / "down" / "005_fund_view.down.sql"
PG_URL = os.environ.get("HQ018_PG_URL", "")
sys.path.insert(0, str(ROOT / "scripts"))
import migration_guard  # noqa: E402

FUND_TABLES = ("decision_snapshots", "ic_decisions")
EXPECTED = ["001_roles_access.sql", "002_jobs_queue.sql", "003_dealflow.sql",
            "004_portfolio_passport.sql", "005_fund_view.sql", "006_pdn_consent.sql"]


# ---------- статические проверки ----------
def test_rename_done_one_file_per_number():
    names = sorted(f.name for f in MIG.glob("*.sql"))
    assert names == EXPECTED
    assert not (MIG / "004_fund_view.sql").exists()
    assert migration_guard.duplicate_prefixes() == {}
    assert migration_guard.main() == 0


def test_no_stale_references_to_old_name():
    hits = []
    for p in [*ROOT.glob("*.py"), *ROOT.glob("*/*.py"), *ROOT.glob("scripts/*"), *ROOT.glob("deploy/**/*"),
              ROOT / "README.md", ROOT / "docs" / "DEPLOY.md"]:
        if p.is_file() and p.name != pathlib.Path(__file__).name and "004_fund_view" in p.read_text(encoding="utf-8", errors="ignore"):
            hits.append(str(p.relative_to(ROOT)))
    assert hits == []


def test_fund_view_does_not_depend_on_portfolio_passport():
    """005 ссылается только на startups (003) и users (001); объектов 004_portfolio_passport не трогает."""
    sql = (MIG / "005_fund_view.sql").read_text(encoding="utf-8")
    for obj in ("portfolio_companies", "portfolio_passport_snapshots", "holding_vehicle"):
        assert obj not in sql
    assert "REFERENCES startups" in sql and "REFERENCES users" in sql


def test_down_file_exists_and_not_applied_by_migrate():
    assert DOWN.exists()
    assert "DROP VIEW IF EXISTS" in DOWN.read_text(encoding="utf-8")
    assert DOWN not in list(MIG.glob("*.sql"))  # hq018_migrate берёт только верхний уровень


# (e)
def test_guard_fails_on_synthetic_duplicate_prefix(tmp_path, monkeypatch):
    for n in ("001_a.sql", "004_x.sql", "004_y.sql", "005_z.sql"):
        (tmp_path / n).write_text("CREATE TABLE IF NOT EXISTS t (a int);", encoding="utf-8")
    dup = migration_guard.duplicate_prefixes(tmp_path)
    assert dup == {4: ["004_x.sql", "004_y.sql"]}
    monkeypatch.setattr(migration_guard, "MIGRATIONS", tmp_path)
    monkeypatch.setattr(migration_guard.duplicate_prefixes, "__defaults__", (tmp_path,))
    assert migration_guard.main() == migration_guard.EXIT_DUPLICATE_PREFIX
    (tmp_path / "004_y.sql").unlink()
    assert migration_guard.duplicate_prefixes(tmp_path) == {}
    (tmp_path / "noprefix.sql").write_text("SELECT 1;", encoding="utf-8")
    assert migration_guard.duplicate_prefixes(tmp_path)  # файл без номера — тоже ошибка


def test_migrate_job_refuses_on_duplicate_prefix(monkeypatch):
    import migrate_job
    monkeypatch.setattr(migrate_job.migration_guard, "duplicate_prefixes",
                        lambda: {4: ["004_x.sql", "004_y.sql"]})
    called = []
    monkeypatch.setattr(migrate_job.hq018_migrate, "main", lambda: called.append(1))
    code, body = migrate_job.run({"snapshot_id": "synthetic-1"})
    assert code == 409 and body["error"] == "duplicate_migration_prefix" and called == []


# ---------- Postgres ----------
pg = pytest.mark.skipif(not PG_URL.startswith("postgresql://"), reason="HQ018_PG_URL не задан")


@pytest.fixture
def fresh_db():
    import psycopg
    name = "mig005_" + uuid.uuid4().hex[:10]
    base = PG_URL.rsplit("/", 1)[0]
    with psycopg.connect(PG_URL, autocommit=True) as c:
        c.execute(f'CREATE DATABASE "{name}"')
    yield f"{base}/{name}"
    with psycopg.connect(PG_URL, autocommit=True) as c:
        c.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _migrate(url):
    env = dict(os.environ, SESSION_DATABASE_URL=url)
    env.pop("DATABASE_URL", None)
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "hq018_migrate.py")], env=env,
                       capture_output=True, text=True, check=True)
    return [ln.split(" ", 1)[1] for ln in r.stdout.splitlines() if ln.startswith("applied ")]


def _schema(url):
    import psycopg
    with psycopg.connect(url) as c:
        return c.execute(
            "SELECT table_name, column_name, data_type FROM information_schema.columns "
            "WHERE table_schema='public' ORDER BY 1,2").fetchall()


def _tables(url):
    import psycopg
    with psycopg.connect(url) as c:
        return {r[0] for r in c.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname='public'").fetchall()}


def _seed_fund_rows(url):
    import psycopg
    with psycopg.connect(url) as c:
        sid = c.execute("INSERT INTO startups (vc_id, name) VALUES ('SYN-MIG005', 'Synthetic Co') "
                        "RETURNING id").fetchone()[0]
        c.execute("INSERT INTO decision_snapshots (startup_id, thesis) VALUES (%s, 'synthetic')", (sid,))
        c.execute("INSERT INTO ic_decisions (startup_id, quarter, decision, reason) "
                  "VALUES (%s, '2026-Q4', 'GO', 'synthetic')", (sid,))


def _count(url, table):
    import psycopg
    with psycopg.connect(url) as c:
        return c.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


@pg
def test_a_clean_db_applies_001_to_005_in_order(fresh_db):
    assert _migrate(fresh_db) == EXPECTED
    assert set(FUND_TABLES) <= _tables(fresh_db)
    assert {"portfolio_companies", "portfolio_passport_snapshots", "startups", "users"} <= _tables(fresh_db)


@pg
def test_b_second_run_is_noop(fresh_db):
    _migrate(fresh_db)
    _seed_fund_rows(fresh_db)
    before = _schema(fresh_db)
    _migrate(fresh_db)
    assert _schema(fresh_db) == before
    assert _count(fresh_db, "ic_decisions") == 1 and _count(fresh_db, "decision_snapshots") == 1


@pg
def test_c_db_with_old_004_fund_view_keeps_working(fresh_db):
    """Staging-сценарий: 001–004 (включая старое 004_fund_view, тот же SQL) применены ранее, есть данные."""
    import psycopg
    old_order = ["001_roles_access.sql", "002_jobs_queue.sql", "003_dealflow.sql"]
    # старый порядок master: sorted() давал 004_fund_view раньше 004_portfolio_passport
    _migrate(fresh_db)  # таблицы приложения (session_store / s1) + схема
    with psycopg.connect(fresh_db) as c:
        c.execute("DROP TRIGGER IF EXISTS ic_decisions_no_change ON ic_decisions; "
                  "DROP TABLE ic_decisions; DROP TABLE decision_snapshots;")
    with psycopg.connect(fresh_db) as c:
        for n in old_order:
            c.execute((MIG / n).read_text(encoding="utf-8"))
        c.execute((MIG / "005_fund_view.sql").read_text(encoding="utf-8"))  # = старое 004_fund_view
        c.execute((MIG / "004_portfolio_passport.sql").read_text(encoding="utf-8"))
    _seed_fund_rows(fresh_db)
    before = _schema(fresh_db)
    assert _migrate(fresh_db) == EXPECTED
    assert _schema(fresh_db) == before
    assert _count(fresh_db, "ic_decisions") == 1 and _count(fresh_db, "decision_snapshots") == 1
    with psycopg.connect(fresh_db) as c, pytest.raises(psycopg.errors.RaiseException):
        c.execute("UPDATE ic_decisions SET reason='x'")  # триггер append-only на месте


@pg
def test_d_down_then_up(fresh_db):
    import psycopg
    _migrate(fresh_db)
    _seed_fund_rows(fresh_db)
    with psycopg.connect(fresh_db) as c:
        c.execute(DOWN.read_text(encoding="utf-8"))
    tables = _tables(fresh_db)
    assert not (set(FUND_TABLES) & tables)
    assert {"startups", "portfolio_companies"} <= tables  # откат 005 не трогает 001–004
    with psycopg.connect(fresh_db) as c:
        c.execute(DOWN.read_text(encoding="utf-8"))  # повторный откат — без ошибок
    assert _migrate(fresh_db) == EXPECTED
    assert set(FUND_TABLES) <= _tables(fresh_db)
    assert _count(fresh_db, "ic_decisions") == 0
