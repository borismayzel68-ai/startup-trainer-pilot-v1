"""T-203 (#114, FEAT-002): стартовая страница и меню по роли. AC-002, AC-003, AC-009. Только синтетика.

Ожидаемое меню записано здесь независимо от shell_menu.py — по ACCESS_MATRIX.md (источник в комментарии строки).
Права тест не меняет; пункты меню должны открываться (не 401/403).
"""
import pytest

from auth_store import ROLE_LABELS
from test_auth import BASE, appmod, env, make_client  # noqa: F401  (env — фикстура)
from test_t202_role_route_matrix import _menu_links

pytestmark = pytest.mark.auth_enforced

HTML = {"Accept": "text/html"}
FOUNDER_TEXT = "Проверьте, насколько ваш стартап готов"
TRAINER = ["/"]                                # ACCESS_MATRIX.md:38 — /api/start: founder, owner
OWNER_ONLY = ["/admin/users", "/admin/sessions"]  # ACCESS_MATRIX.md:45 (sessions); /admin/users — T-003, нет в матрице

# роль -> пункты меню (кроме «Профиль», он у всех: ACCESS_MATRIX.md:36-37, любая роль)
EXPECTED = {r: [] for r in ROLE_LABELS}
EXPECTED["founder"] = TRAINER
EXPECTED["owner"] = TRAINER + OWNER_ONLY


def _items(c):
    r = c.get("/", base_url=BASE, headers=HTML)
    assert r.status_code == 200
    return r, [h for h in _menu_links(r.get_data(as_text=True)) if h != "/profile"]


def test_expected_table_covers_all_roles():
    assert set(EXPECTED) == set(ROLE_LABELS) and len(ROLE_LABELS) == 9


@pytest.mark.parametrize("role", sorted(ROLE_LABELS))
def test_menu_items_per_role(env, role):
    """AC-003: у роли ровно её пункты; каждый открывается."""
    c, _ = make_client(env, "t203-%s@example.test" % role, [(role, "*")])
    r, items = _items(c)
    assert items == EXPECTED[role], role
    for href in items:
        assert c.get(href, base_url=BASE, headers=HTML).status_code not in (401, 403), (role, href)


@pytest.mark.parametrize("role", sorted(ROLE_LABELS))
def test_home_by_role(env, role):
    """AC-002, AC-009: сценарий основателя видят founder и owner; остальные роли — общую страницу с шапкой."""
    c, _ = make_client(env, "t203h-%s@example.test" % role, [(role, "*")])
    html = c.get("/", base_url=BASE, headers=HTML).get_data(as_text=True)
    assert (FOUNDER_TEXT in html) == (role in ("founder", "owner")), role
    assert "Выйти" in html and ROLE_LABELS[role] in html


def test_investor_does_not_see_founder_scenario(env):
    """AC-009 прямо: инвестор — без сценария основателя и без формы запуска."""
    c, _ = make_client(env, "t203inv@example.test", [("investor", "*")])
    html = c.get("/", base_url=BASE, headers=HTML).get_data(as_text=True)
    assert FOUNDER_TEXT not in html and 'id="start"' not in html


def test_multi_role_menu_is_union(env):
    """Q-002 не решён: выбора кабинета нет, меню — объединение разрешённых пунктов."""
    c, _ = make_client(env, "t203multi@example.test", [("investor", "*"), ("founder", "VC-991")])
    _, items = _items(c)
    assert items == TRAINER
    c2, _ = make_client(env, "t203multi2@example.test", [("founder", "VC-991"), ("owner", "*")])
    _, items2 = _items(c2)
    assert items2 == TRAINER + OWNER_ONLY


def test_owner_with_case_scope_gets_no_star_only_items(env):
    """Матрица: «только *» — owner с грантом на один проект не видит админские пункты (иначе 403)."""
    c, _ = make_client(env, "t203scope@example.test", [("owner", "VC-991")])
    _, items = _items(c)
    assert items == TRAINER
    assert c.get("/admin/users", base_url=BASE, headers=HTML).status_code == 403
