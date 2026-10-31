# SDD — разработка от спецификации

Порядок для каждой функции: `spec.md` (что и для кого) → `plan.md` (как) → `tasks.md` (поручения исполнителю, по одной задаче).

- Шаблоны: `templates/spec_template.md`, `templates/plan_template.md`, `templates/tasks_template.md` (переданы техдиректором 31.10.2026).
- Комплект функции кладётся в `docs/sdd/FEAT-NNN/` — `spec.md`, `plan.md`, `tasks.md`.
- Первая функция — FEAT-001 (готов черновик v0.2: `FEAT-001/spec.md`, `plan.md`, `tasks.md`) «Регистрация, роли и личный кабинет» (по ревью стенда 31.10: `docs/reports/STAND_REVIEW_2026-10-31.md`).
- Комплект PR #117 перенесён сюда целиком (#118 — источник истины, #117 заменён): `AGENTS.md`, `README_для_Сергея.md`, `specs/FEAT-001…004-*/`. Две версии FEAT-001 (`FEAT-001/` и `specs/FEAT-001-vhod-i-registraciya/`) обе сохранены, их нужно свести в одну.
- Для Сергея: начать с `README_для_Сергея.md`, правила работы агента — `AGENTS.md`; FEAT-002 (роли и кабинеты), FEAT-003 (форма «Начать», код доступа), FEAT-004 (панель администратора) — в `specs/`.
- Поручение @claude в issue = карточка задачи из `tasks_template.md` §4.
