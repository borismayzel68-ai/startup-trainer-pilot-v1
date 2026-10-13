# HANDOFF

Обновлять при каждом коммите.

## Задача / issue
HQ-036 — линия A9 (#6 head 83ab1f7 + #11 head 90e2d22) на линии tilda (Refs #22), ветка `chore/hq-036-rebase-a9-on-tilda` от fix/hq-035-fact-ceiling (85d424a, верх линии tilda с HQ-035). PR → s1/tilda-readiness-pack.

## Сделано
- Влит 90e2d22 (содержит 83ab1f7) merge-коммитом, без rebase.
- app.py: импорты объединены (s1 init_s1 + founder_package/iso_z/utc_now из A9; post_completion_json больше не импортируется — A9 доставляет completion через delivery_worker); лимит запроса 20 МБ (A9) и init_s1 (S1) — оба сохранены.
- README.md: раздел S1 сохранён целиком; строка Worker: `python delivery_worker.py` из A9 — рядом с Build/Start.
- Полный прогон: 197 passed, 0 failed, 0 skipped (с тестами A9).

## Дальше
- HQ-022: ветка от этой линии, только merge.

## Сломано или не проверено
- «CI прогон зелёный» (Issue #22): в репо нет тестового workflow (только claude.yml в PR #8) — пробел для PM.
- delivery_worker вживую (Render worker) не запускался — только тесты.

## Последний коммит
см. git log -1 (Merge A9 (#6+#11) в линию tilda)

## Слияние в master (Claude, 11.10)
- Цепочка #28 (+#9) влита в s1/tilda-readiness-pack, затем master влит сюда для PR #7. Конфликт только в HANDOFF.md (add/add), оставлена версия линии tilda. Код не менялся.
- Тесты локально: 197 passed, 5 skipped (Postgres-тесты без PG).

## Чистка __pycache__ (Claude, 11.10)
- Задача: убрать закоммиченный байткод. Ветка `chore/remove-pycache` → master, отдельный мелкий PR по запросу владельца.
- Сделано: `__pycache__/app.cpython-314.pyc` снят с учёта (`git rm --cached`); в `.gitignore` добавлены `__pycache__/` и `*.py[cod]`. Код не менялся.
- Не проверено: CI — запустится на PR.

## Issue #51 — ГПФ / живое ТЗ / активация (Claude, 12.10)
- Добавлено: docs/system/{LIVING_SPEC,ACCEPTANCE_CHECKLIST,IDEA_PIPELINE,MANAGER_RUNBOOK,DEAL_OUTCOME_NODE}.md, .github/pull_request_template.md, activity_log.py (минимальный журнал событий + события активации), tests/test_activity_log.py.
- Тесты локально: 205 passed. Golden-снимки не менялись.
- Не сделано/открыто: журнал не подключён к маршрутам app.py (кабинетов пока нет); перечень процессов 1–19 в репо отсутствует — нужен от Grok Bot; схема узла «Исход сделки» — черновик.

## Issue #51 — дополнение: дашборды кабинетов (Claude, 12.10)
- В LIVING_SPEC добавлен раздел «Дашборд кабинета» (стартап, частный инвестор, фонд, портфельная компания, владелец #45; площадки и эксперты — позже); пункт 7 в ACCEPTANCE_CHECKLIST и в PR-шаблон.
- Только документы, код не менялся. Дашбордов в коде нет — кабинетов ещё нет.

## Доходность в дашбордах (Claude, 13.10, issue #51)
- Docs-only: LIVING_SPEC (блок «Доходность в дашбордах»), ACCEPTANCE_CHECKLIST п.8, шаблон PR, DEAL_OUTCOME_NODE (поля доходности). Код не менялся.
- Ветка `claude/issue-51-20261013-0657` включает работу веток …-1921 и …-2113 (fast-forward).
- Не сделано: расчёты и дашборды в коде (кабинетов нет); условия фонда — E0 DATA GAP до мандата; перечень процессов 1–19 и владелец узла — открыты.
