"""T-203 (#114, FEAT-002): таблица «роль → пункты меню» шапки. Только чтение, права не выдаёт.

Пункт показывается, если у человека есть грант с подходящей ролью (и scope «*», если матрица требует «только *»).
Источник каждой строки — docs/ACCESS_MATRIX.md; сами права проверяет require_role на маршруте.
Пункты — только существующие адреса, открывающие страницу (не сырой JSON). JSON-адреса матрицы
(/s1/jobs, /s1/tasks, /s5/today, /admin/audit) в меню не включены: см. описание PR.
"""

# (подпись, адрес, роли, только scope «*», источник)
MENU = [
    ("Тренажёр", "/", ("founder", "owner"), False, "ACCESS_MATRIX.md:38 (POST /api/start: founder, owner)"),
    ("Пользователи", "/admin/users", ("owner",), True, "нет в матрице; T-003 API-004 (owner, scope *)"),
    ("Сессии", "/admin/sessions", ("owner",), True, "ACCESS_MATRIX.md:45 (owner, только *)"),
]
TRAINER_ROLES = frozenset(MENU[0][2])


def menu_for(grants):
    """grants: [(role, scope)] -> [{'label', 'href'}] — объединение пунктов по всем ролям пользователя."""
    items = []
    for label, href, roles, star_only, _src in MENU:
        if any(r in roles and (sc == "*" or not star_only) for r, sc in grants):
            items.append({"label": label, "href": href})
    return items


def shows_trainer(grants):
    """Сценарий основателя на «/» — только founder/owner; остальные роли видят общую стартовую страницу."""
    return any(r in TRAINER_ROLES for r, _ in grants)
