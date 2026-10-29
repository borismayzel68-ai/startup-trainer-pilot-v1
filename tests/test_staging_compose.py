"""docker-compose.staging.yml: app должен получить DATABASE_URL (его требует staging_guard) и тот же адрес в SESSION_DATABASE_URL;
seed в compose не запускается. Без Docker, только разбор YAML."""
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parent.parent
COMPOSE = yaml.safe_load((ROOT / "docker-compose.staging.yml").read_text(encoding="utf-8"))


def test_app_has_database_url_for_staging_guard():
    import staging_guard

    env = COMPOSE["services"]["app"]["environment"]
    assert "DATABASE_URL" in staging_guard.REQUIRED_ENV
    assert env["DATABASE_URL"] == env["SESSION_DATABASE_URL"]


def test_no_seed_in_compose():
    cmds = []
    for svc in COMPOSE["services"].values():
        c = svc.get("command") or []
        cmds.append(c if isinstance(c, str) else " ".join(map(str, c)))
        cmds.append(str(svc.get("entrypoint") or ""))
    joined = "\n".join(cmds)
    assert "seed" not in joined
    assert "hq018_migrate.py" in joined
