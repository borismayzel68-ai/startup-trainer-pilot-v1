"""FEAT-002: /cabinet — области доступа по ролям; ссылки только из shell_menu.MENU. Только синтетика."""
import re

import pytest

from auth_store import ROLE_LABELS
from shell_menu import menu_for
from test_auth import BASE, appmod, env, make_client  # noqa: F401  (env — фикстура)

pytestmark = pytest.mark.auth_enforced
HTML = {"Accept": "text/html"}
NAV = re.compile(r'<nav aria-label="Доступные[^>]*>.*?</nav>', re.S)


def _page(c):
    r = c.get("/cabinet", base_url=BASE, headers=HTML)
    assert r.status_code == 200
    assert r.headers["Cache-Control"] == "no-store"
    return r.get_data(as_text=True)


def _links(page):
    main = page.split("<main>", 1)[1]
    return re.findall(r'href="([^"]*)"', "".join(NAV.findall(main)))


def test_cabinet_requires_login(env):
    assert appmod.app.test_client().get("/cabinet", base_url=BASE).status_code == 401


@pytest.mark.parametrize("role", sorted(ROLE_LABELS))
def test_cabinet_links_equal_menu_and_open_as_html(env, role):
    c, _ = make_client(env, "cab-%s@example.test" % role, [(role, "*")])
    page = _page(c)
    expected = [i["href"] for i in menu_for([(role, "*")])]
    assert _links(page) == expected, role
    for href in expected:
        r = c.get(href, base_url=BASE, headers=HTML)
        assert r.status_code == 200 and r.mimetype == "text/html", (role, href)
    if not expected:
        assert "пока не подключены" in page


@pytest.mark.parametrize("role", sorted(ROLE_LABELS))
def test_no_json_links(env, role):
    c, _ = make_client(env, "cabj-%s@example.test" % role, [(role, "*")])
    page = _page(c)
    for bad in ("/portfolio", "/s5/today", "/s1/jobs", "/s1/tasks", "/admin/audit"):
        assert 'href="%s"' % bad not in page, (role, bad)


def test_scoped_owner_has_no_admin_links(env):
    c, _ = make_client(env, "cab-so@example.test", [("owner", "VC-SYNTH")])
    page = _page(c)
    assert "/admin/users" not in page and "VC-SYNTH" in page


def test_multi_grant_same_role_single_card(env):
    c, _ = make_client(env, "cab-multi@example.test", [("fund_staff", "VC-991"), ("fund_staff", "VC-992")])
    page = _page(c)
    assert page.count("<section") == 1
    assert "VC-991" in page and "VC-992" in page


def test_multi_role_one_card_per_role_and_q002_note(env):
    c, _ = make_client(env, "cab-mr@example.test",
                       [("founder", "VC-991"), ("investor", "VC-992"), ("founder", "VC-993")])
    page = _page(c)
    assert page.count("<section") == 2
    assert "Q-002" in page and "нужно решение" in page.lower()


def test_header_has_no_cabinet_link_for_roles_outside_menu(env):
    c, _ = make_client(env, "cab-inv@example.test", [("investor", "*")])
    head = c.get("/", base_url=BASE, headers=HTML).get_data(as_text=True).split("</nav>")[0]
    assert 'href="/cabinet"' not in head
