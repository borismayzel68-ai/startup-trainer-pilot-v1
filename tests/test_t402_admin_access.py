"""T-402 (#114, FEAT-004): доступ к /admin/sessions и /admin/audit — только owner, scope «*». AC-001, AC-006.

Ожидание читается из docs/ACCESS_MATRIX.md (строки таблицы с этими адресами), а не из кода. Не дублирует
test_auth.py::test_matrix_admin_sessions_owner_only (роль со scope «*» → 200/403) и test_t202 (общая матрица):
здесь добавлено — разбор самой матрицы, аноним 401, deny в журнале по каждому адресу, owner с грантом на один
проект (матрица: «только *») и признак «прочие админ-адреса не открыты». Только синтетика.
"""
import pathlib
import re

import pytest

from auth_store import ROLE_LABELS
from test_auth import BASE, appmod, env, make_client, rows  # noqa: F401  (env — фикстура)

pytestmark = pytest.mark.auth_enforced

MATRIX = pathlib.Path(__file__).resolve().parent.parent / "docs" / "ACCESS_MATRIX.md"
PATHS = ("/admin/sessions", "/admin/audit")
ALL = sorted(ROLE_LABELS)


def matrix_row(path):
    """(номер строки, роли, scope) для адреса `path` GET из таблицы матрицы."""
    for n, line in enumerate(MATRIX.read_text(encoding="utf-8").splitlines(), 1):
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 4 and cells[0] == "`%s`" % path and cells[1] == "GET":
            return n, [r.strip() for r in cells[2].split(",")], cells[3]
    raise AssertionError("в ACCESS_MATRIX.md нет строки %s" % path)


@pytest.mark.parametrize("path", PATHS)
def test_matrix_says_owner_only_star(path):
    n, roles, scope = matrix_row(path)
    assert roles == ["owner"] and scope == "только `*`", (path, n, roles, scope)


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("role", ALL)
def test_role_x_admin_path(env, role, path):
    n, roles, _ = matrix_row(path)
    c, _u = make_client(env, "t402-%s@example.test" % role, [(role, "*")])
    r = c.get(path, base_url=BASE)
    if role in roles:
        assert r.status_code == 200, (path, role, "ACCESS_MATRIX.md:%d" % n)
    else:
        assert r.status_code == 403, (path, role, "ACCESS_MATRIX.md:%d" % n)
        assert rows(env, result="deny", actor="t402-%s@example.test" % role, object=path), "нет deny в журнале"


@pytest.mark.parametrize("path", PATHS)
def test_anonymous_gets_401_and_deny(env, path):
    r = appmod.app.test_client().get(path, base_url=BASE)
    assert r.status_code == 401
    assert rows(env, result="deny", object=path), "отказ не записан в журнал"


@pytest.mark.parametrize("path", PATHS)
def test_owner_with_project_scope_is_denied(env, path):
    """Матрица: «только *». Owner с грантом на один проект (VC-991) панель не открывает."""
    c, _u = make_client(env, "t402-scoped@example.test", [("owner", "VC-991")])
    assert c.get(path, base_url=BASE).status_code == 403
    assert rows(env, result="deny", actor="t402-scoped@example.test", object=path)


@pytest.mark.parametrize("role", ALL)
def test_admin_root_paths_not_open_to_non_owner(env, role):
    """Прочие подпути /admin/ не дают роли без owner ничего, кроме 403/404 (без 200)."""
    if role == "owner":
        pytest.skip("owner: проверяется выше")
    c, _u = make_client(env, "t402b-%s@example.test" % role, [(role, "*")])
    for p in ("/admin", "/admin/", "/admin/sessions/", "/admin/audit/"):
        assert c.get(p, base_url=BASE).status_code in (301, 308, 403, 404), (role, p)
