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

## HQ-042 P0 S1: запрет E1→E3 + полный пакет основателю (Claude, 12.10)
- Ветка `claude/issue-36-20261012-1121` от master, один PR; не мержить — ревью Руководителя бизнеса.
- Добавлен `tests/test_hq042_e1_e3_gate.py`: потолки по источнику (form/deck/model < E3), подпись не поднимает E1 до FACT, E3 только при источнике `evidence`, `founder_package.validate_package` отклоняет E3/fact; бесплатная часть содержит балл, 3 пробела с риском и метку «ИИ-оценка». Код продукта не менялся, только синтетика.
- НЕ ПРОВЕРЕНО: pytest в этой среде запустить не удалось (команда требует одобрения) — лог pytest не приложен, тесты новые и ни разу не запускались. Смотреть CI на PR.
- Наблюдение для ревью (код не менял): `enforce_rules` при пустом наборе загруженных источников (`available_sources=set()`, falsy) считает доступными все источники, включая `evidence` — без файлов модель может получить E3 по ссылке на «документ». Тесты HQ-016 (golden) на это опираются, поэтому правка — отдельное решение PM.
