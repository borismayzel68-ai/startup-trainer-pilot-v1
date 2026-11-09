"""T-202 (#114, FEAT-002): тест «роль × маршрут» и «роль × пункт меню». AC-003, AC-007. Только синтетика.

Ожидания берутся из docs/ACCESS_MATRIX.md, не из кода. Источник каждой строки — номер строки матрицы (поле SRC).
Строки, которых в матрице нет (профиль, админка T-003), помечены «нет в матрице»: ожидание взято из кода и
записано в PR, матрицу не правим (её обновляет T-008). Код и права тестом не меняются.

«Разрешено» = ответ не 401/403 (дальше работает сама логика маршрута: 200/400/404/503 и т.п.).
«Запрещено» = 403 для вошедшего без роли, 401 без входа (для маршрутов с external — ответ собственной проверки).
"""
import re

import pytest

from auth_store import ROLE_LABELS
from test_auth import BASE, EXTERNAL_OK_STATUSES, appmod, env, make_client, rows  # noqa: F401  (env — фикстура)

pytestmark = pytest.mark.auth_enforced

ALL = sorted(ROLE_LABELS)
HTML = {"Accept": "text/html"}
M = "ACCESS_MATRIX.md:"
NO_MATRIX = "нет в матрице (ожидание из кода)"

# (метод, путь, роли с доступом, external, источник)
ROUTES = [
    ("GET", "/", ALL, False, M + "37"),
    ("GET", "/auth/me", ALL, False, M + "36"),
    ("POST", "/api/start", ["founder", "owner"], False, M + "38"),
    ("GET", "/api/session/synth-sid", ["founder", "investor", "fund_staff", "owner"], True, M + "39"),
    ("POST", "/api/session/synth-sid/answer", ["founder", "owner"], True, M + "41"),
    ("POST", "/api/session/synth-sid/feedback", ["founder", "fund_staff", "owner"], True, M + "42"),
    ("DELETE", "/api/session/synth-sid", ["founder", "owner"], True, M + "43"),
    ("GET", "/admin/sessions", ["owner"], False, M + "45"),
    ("GET", "/admin/audit", ["owner"], False, M + "46"),
    ("GET", "/s1/jobs", ["owner", "fund_staff", "investor"], True, M + "49"),
    ("GET", "/s1/tasks", ["owner", "fund_staff"], True, M + "50"),
    ("GET", "/s6/gates/synth-gate", ["owner", "fund_staff"], False, M + "55"),
    ("GET", "/s5/today", ["owner", "fund_staff", "investor"], False, M + "58"),
    # админка T-003 и профиль T-004 в матрице отсутствуют
    ("GET", "/profile", ALL, False, NO_MATRIX + ": T-004"),
    ("GET", "/admin/users", ["owner"], False, NO_MATRIX + ": T-003 API-004"),
    ("GET", "/admin/data/users", ["owner"], False, NO_MATRIX + ": T-003 API-008"),
]
IDS = ["%s %s [%s]" % (m, p, src) for m, p, _r, _e, src in ROUTES]


def _call(client, method, path, headers=None):
    h = dict(headers or {})
    if method != "GET":
        h["X-CSRF-Token"] = getattr(client, "csrf", "x")
    return client.open(path, method=method, base_url=BASE, headers=h)


def _client(env, role):
    return make_client(env, "t202-%s@example.test" % role, [(role, "*")])[0]


@pytest.mark.parametrize("role", ALL)
@pytest.mark.parametrize("route", ROUTES, ids=IDS)
def test_role_x_route(env, role, route):
    """AC-007: чужой маршрут → 403 и deny в журнале; свой — не 401/403."""
    method, path, roles, _external, src = route
    c = _client(env, role)
    r = _call(c, method, path)
    if role in roles:
        assert r.status_code not in (401, 403), "%s %s для %s: матрица разрешает (%s), код дал %s" % (
            method, path, role, src, r.status_code)
    else:
        assert r.status_code == 403, "%s %s для %s: матрица запрещает (%s), код дал %s" % (
            method, path, role, src, r.status_code)
        assert rows(env, result="deny", actor="t202-%s@example.test" % role, object=path), "нет deny в журнале"


@pytest.mark.parametrize("route", ROUTES, ids=IDS)
def test_anonymous_x_route(env, route):
    """Без входа: 401 (маршруты с external — ответ их собственной проверки, ACCESS_MATRIX.md:23, 60)."""
    method, path, _roles, external, src = route
    r = _call(appmod.app.test_client(), method, path)
    if external:
        assert r.status_code in EXTERNAL_OK_STATUSES, (src, r.status_code)
    else:
        assert r.status_code == 401, "%s %s без входа (%s): %s" % (method, path, src, r.status_code)


def _menu_links(html):
    nav = re.search(r"<nav[^>]*>(.*?)</nav>", html, re.S)
    assert nav, "в шапке нет <nav>"
    return [h for h in re.findall(r'href="([^"]*)"', nav.group(1)) if h.startswith("/")]


@pytest.mark.parametrize("role", ALL)
def test_menu_links_do_not_lead_to_403(env, role):
    """AC-003: все пункты шапки открываются для роли (не 401/403, не редирект на вход)."""
    c = _client(env, role)
    home = c.get("/", base_url=BASE, headers=HTML)
    assert home.status_code == 200, role
    links = _menu_links(home.get_data(as_text=True))
    assert "/profile" in links
    for href in links:
        r = c.get(href, base_url=BASE, headers=HTML)
        assert r.status_code not in (401, 403), "пункт меню %s ведёт на %s для роли %s" % (href, r.status_code, role)
        assert not (r.status_code == 302 and "/auth/login" in r.headers.get("Location", "")), (role, href)


@pytest.mark.parametrize("role", ALL)
def test_users_menu_item_only_for_owner(env, role):
    """AC-003: «Пользователи» (T-003) — только у owner; у остальных пункта нет, прямой адрес — 403."""
    c = _client(env, role)
    links = _menu_links(c.get("/", base_url=BASE, headers=HTML).get_data(as_text=True))
    assert ("/admin/users" in links) == (role == "owner")
    if role != "owner":
        assert c.get("/admin/users", base_url=BASE, headers=HTML).status_code == 403
