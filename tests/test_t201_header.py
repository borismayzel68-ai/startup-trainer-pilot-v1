"""T-201 (#114, FEAT-002): общая шапка на каждой странице кабинета. FR-001, AC-001, NFR-002, NFR-003. Только синтетика."""
import re

import pytest

from auth_store import ROLE_LABELS
from test_auth import BASE, appmod, env, make_client  # noqa: F401  (env — фикстура)

pytestmark = pytest.mark.auth_enforced
HTML = {"Accept": "text/html"}
PAGES = ["/", "/profile", "/cabinet"]


def _get(c, path):
    return c.get(path, base_url=BASE, headers=HTML)


def _set_name(env, email, name):
    env._run("UPDATE users SET display_name = ? WHERE email = ?", (name, email))


def _header(html):
    m = re.search(r'<header class="shell" id="shell">(.*?)</header>', html, re.S)
    assert m, "нет шапки"
    return m.group(1)


@pytest.mark.parametrize("role", sorted(ROLE_LABELS))
def test_header_on_every_page_for_every_role(env, role):
    """AC-001: имя, роль подписью (не кодом), «Выйти», «Сообщить о проблеме» — на каждой странице кабинета."""
    email = "t201-%s@example.test" % role
    c, _ = make_client(env, email, [(role, "*")])
    for path in PAGES:
        r = _get(c, path)
        assert r.status_code == 200, (role, path)
        html = r.get_data(as_text=True)
        head = _header(html)
        assert "Synthetic " + email in head, (role, path)
        assert ROLE_LABELS[role] in head, (role, path)
        assert 'action="/auth/logout"' in head and "Выйти" in head
        assert "Сообщить о проблеме" in head and 'id="fbLink"' in head


@pytest.mark.parametrize("role", sorted(ROLE_LABELS))
def test_role_shown_as_label_not_code(env, role):
    c, _ = make_client(env, "t201c-%s@example.test" % role, [(role, "*")])
    who = re.search(r'<span class="who">(.*?)</span>', _get(c, "/profile").get_data(as_text=True), re.S).group(1)
    assert ROLE_LABELS[role] in who
    assert ("· " + role) not in who and who.strip().split("·")[-1].strip() != role


def test_only_one_report_entry_point(env):
    """Шаг 2: под сессией одна точка входа «Сообщить о проблеме» (ссылка в шапке), плавающей кнопки нет."""
    c, _ = make_client(env, "t201one@example.test", [("founder", "VC-991")])
    for path in PAGES:
        html = _get(c, path).get_data(as_text=True)
        assert 'id="fbLink"' in html and 'id="fbBtn"' not in html, path
        assert html.count('id="fbBox"') == 1, path


def test_no_session_page_keeps_floating_button(env):
    """Страница без шапки (нет сессии) сохраняет плавающую кнопку: единственная точка входа."""
    c = appmod.app.test_client()
    html = _get(c, "/no-such-page").get_data(as_text=True)
    assert 'id="shell"' not in html and html.count('id="fbBtn"') <= 1


def test_no_name_falls_back_to_email(env):
    """NFR-003: нет имени → почта."""
    email = "t201noname@example.test"
    c, _ = make_client(env, email, [("investor", "*")])
    _set_name(env, email, "")
    for path in PAGES:
        who = re.search(r'<span class="who">(.*?)</span>', _get(c, path).get_data(as_text=True), re.S).group(1)
        assert email in who and ROLE_LABELS["investor"] in who, path


def test_multi_role_header_lists_all_labels(env):
    c, _ = make_client(env, "t201multi@example.test", [("investor", "*"), ("founder", "VC-991")])
    head = _header(_get(c, "/profile").get_data(as_text=True))
    assert ROLE_LABELS["investor"] in head and ROLE_LABELS["founder"] in head


def test_name_is_escaped_xss(env):
    """NFR-002: имя с HTML экранируется во всех страницах."""
    email = "t201xss@example.test"
    c, _ = make_client(env, email, [("founder", "VC-991")])
    evil = '<script>alert(1)</script><img src=x onerror=alert(2)>'
    _set_name(env, email, evil)
    for path in PAGES:
        html = _get(c, path).get_data(as_text=True)
        assert evil not in html and "<img src=x" not in html, path
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in _header(html), path


def test_header_has_no_other_users_data(env):
    c, _ = make_client(env, "t201own@example.test", [("founder", "VC-991")])
    make_client(env, "t201other@example.test", [("investor", "*")])
    assert "t201other@example.test" not in _header(_get(c, "/").get_data(as_text=True))


def test_header_on_admin_page_for_owner(env):
    c, _ = make_client(env, "t201adm@example.test", [("owner", "*")])
    r = _get(c, "/admin/users")
    assert r.status_code == 200
    head = _header(r.get_data(as_text=True))
    assert "Выйти" in head and "Сообщить о проблеме" in head and ROLE_LABELS["owner"] in head
