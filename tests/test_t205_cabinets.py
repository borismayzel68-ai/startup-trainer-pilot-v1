"""T-205 (#114, FEAT-002): 4 кабинета. AC-005, AC-009. Только синтетика (@example.test, VC-99x).

Кабинеты — текст и области из собственных грантов; адресов данных нет, права не выдаются.
"""
import pytest

from test_auth import BASE, appmod, env, make_client, rows  # noqa: F401  (env — фикстура)
from test_t202_role_route_matrix import _menu_links

pytestmark = pytest.mark.auth_enforced

HTML = {"Accept": "text/html"}
FOUNDER_TEXT = "Проверьте, насколько ваш стартап готов"
INVESTOR_H = "Мои проекты"
STAFF_H = "Мои задачи"
OWNER_H = "Ожидают решения"
ALL_H = (INVESTOR_H, STAFF_H, OWNER_H)


def _home(c):
    r = c.get("/", base_url=BASE, headers=HTML)
    assert r.status_code == 200 and r.mimetype == "text/html"
    return r.get_data(as_text=True)


def _cab(c):
    r = c.get("/cabinet", base_url=BASE, headers=HTML)
    assert r.status_code == 200
    return r.get_data(as_text=True)


def test_founder_cabinet_is_trainer(env):
    """AC-005: founder — тренажёр, как раньше; чужих кабинетов нет."""
    c, _ = make_client(env, "t205f@example.test", [("founder", "VC-991")])
    html = _home(c)
    assert FOUNDER_TEXT in html and not any(h in html for h in ALL_H)


def test_investor_cabinet_own_projects_only(env):
    """AC-005, AC-009: «Мои проекты» — только свои области; без сценария основателя; без чужого VC-99x."""
    c, _ = make_client(env, "t205i@example.test", [("investor", "VC-991")])
    html = _home(c)
    assert INVESTOR_H in html and "VC-991" in html
    assert "VC-992" not in html
    assert FOUNDER_TEXT not in html and 'id="start"' not in html
    assert STAFF_H not in html and OWNER_H not in html
    assert "<form" not in html.split("</nav>", 1)[1].replace('<form method="post" action="/auth/logout"', "")


def test_investor_empty_state(env):
    c, _ = make_client(env, "t205ie@example.test", [("investor", "*")])
    html = _home(c)
    assert INVESTOR_H in html and "Пока ничего не передано" in html


def test_investor_foreign_project_is_403_and_audited(env):
    """Прямой адрес чужого проекта: 403 и запись deny в журнале."""
    c, _ = make_client(env, "t205ix@example.test", [("investor", "VC-991")])
    own = c.get("/s1/jobs/VC-991", base_url=BASE, headers=HTML)
    assert own.status_code != 403
    r = c.get("/s1/jobs/VC-992", base_url=BASE, headers=HTML)
    assert r.status_code == 403
    assert rows(env, actor="t205ix@example.test", result="deny", object="/s1/jobs/VC-992"), "нет записи deny в журнале"


def test_fund_staff_cabinet_tasks_no_decisions(env):
    """AC-005: «Мои задачи»; S2 только после S6; NEXT/HOLD/STOP — предложение; ссылок на JSON нет."""
    c, _ = make_client(env, "t205s@example.test", [("fund_staff", "*")])
    html = _home(c)
    assert STAFF_H in html and "после допуска S6" in html and "предложение человеку" in html
    assert FOUNDER_TEXT not in html and INVESTOR_H not in html and OWNER_H not in html
    for bad in ("/s1/jobs", "/s1/tasks", "/s5/today", "/s6/gates", "/portfolio", "/fund/"):
        assert 'href="%s' % bad not in html, bad


def test_owner_cabinet_pending_and_admin_links(env):
    """AC-005: «Ожидают решения» на /cabinet; ссылки на админку открываются; подписи, сделок и денег нет."""
    c, _ = make_client(env, "t205o@example.test", [("owner", "*")])
    html = _cab(c)
    assert OWNER_H in html and "Подпись, сделки и деньги из кабинета не выполняются" in html
    assert _home(c).count(OWNER_H) == 0 and FOUNDER_TEXT in _home(c)
    for href in ("/admin/users", "/admin/sessions", "/cabinet"):
        assert 'href="%s"' % href in html
        assert c.get(href, base_url=BASE, headers=HTML).status_code == 200


def test_scoped_owner_cabinet_has_no_admin_links(env):
    """Owner с грантом на один проект: блок без ссылок на админку (иначе 403)."""
    c, _ = make_client(env, "t205os@example.test", [("owner", "VC-991")])
    html = _cab(c)
    assert OWNER_H in html
    assert 'href="/admin/users"' not in html and 'href="/admin/sessions"' not in html


@pytest.mark.parametrize("grants,expect_cabinets", [
    ([("investor", "VC-991")], [INVESTOR_H]),
    ([("fund_staff", "*")], [STAFF_H]),
    ([("investor", "VC-991"), ("fund_staff", "VC-992")], [INVESTOR_H, STAFF_H]),
])
def test_no_menu_item_leads_to_403(env, grants, expect_cabinets):
    """AC-003/AC-005: ни один пункт меню и ссылка кабинета не ведут на 401/403."""
    c, _ = make_client(env, "t205m%d@example.test" % len(grants) + str(len(expect_cabinets)), grants)
    html = _home(c)
    for h in expect_cabinets:
        assert h in html
    for href in _menu_links(html) + _menu_links(_cab(c)):
        assert c.get(href, base_url=BASE, headers=HTML).status_code not in (401, 403), href


def test_multi_role_does_not_widen_rights(env):
    """Несколько ролей: блоки — только имеющихся ролей; чужая область и роль owner не появляются."""
    c, _ = make_client(env, "t205mr@example.test", [("investor", "VC-991"), ("fund_staff", "VC-992")])
    html = _home(c)
    assert INVESTOR_H in html and STAFF_H in html and OWNER_H not in html
    assert "VC-993" not in html
    assert c.get("/admin/users", base_url=BASE, headers=HTML).status_code == 403
    assert OWNER_H not in _cab(c)


@pytest.mark.parametrize("role", ["expert", "signer", "clinic", "syndicator", "buyer"])
def test_other_roles_keep_cabinet_stub(env, role):
    """Остальные роли — как в #138: без новых блоков, «пока не подключены» на /cabinet."""
    c, _ = make_client(env, "t205x-%s@example.test" % role, [(role, "*")])
    html = _home(c)
    assert not any(h in html for h in ALL_H) and FOUNDER_TEXT not in html
    assert "пока не подключены" in _cab(c)
