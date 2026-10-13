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

## MOS-2 схема dealflow (Claude, Refs #53)
- Ветка `claude/issue-53-20261013-0822` → master. Добавлено: `db/migrations/003_dealflow.sql` (startups, deal_status_history append-only триггером, portfolio_companies, cabinets, documents с drive_file_id), `dealflow/status.py` (enum, TRANSITIONS, change_status, map_zone, map_core), `docs/STATUS_MAPPING.md`, `tests/test_mos2_dealflow.py`.
- Тесты: 224 passed (Postgres-тесты MOS-2 выполнены, не skipped). Лог — в описании PR.
- Требует решения менеджера/владельца: допущения маппинга CORE (CONDITIONAL+MONITOR→ON_HOLD, +REQUEST_EVIDENCE→DD, без действия→ACTIVE) и таблица переходов; прочие зоны плана миграции в репо отсутствуют → флаг.
- Не сделано: привязка доступа к кабинету через role_grants.scope (только схема), noindex/allowlist не трогались.
