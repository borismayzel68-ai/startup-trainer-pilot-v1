# HANDOFF

Обновлять при каждом коммите.

## MOS-1 Auth + RBAC (Claude, 13.10, Refs #52)
- Ветка `claude/issue-52-20261013-0822` → PR в master (не мержить без ревью менеджера и без решения владельца; выпуск на живых пользователей утверждает только Борис).
- Сделано: Google OAuth (OIDC) + `AUTH_ALLOWLIST` (`auth.py`), хранилище users/grants/audit (`auth_store.py`), миграция 001 финализирована (+owner, fund_staff, audit_log, идемпотентна), `require_role` на каждом маршруте (старт приложения падает при маршруте без декоратора), CSRF, noindex, `/admin?key=` и `ADMIN_KEY` удалены (404; админ-просмотр `/admin/sessions` и `/admin/audit` — только owner), `docs/ACCESS_MATRIX.md`, `tests/test_auth.py`.
- S1 не сломан: Bearer-токены, ключ вебхука Tilda, подписанные ссылки и `X-Completion-Secret` сохранены как «внешняя проверка» (`external=`); сессия owner/fund_staff/investor проходит по ролям и scope (`job_id`).
- Тесты: `RBAC` выключается для старых тестов флагом `app.config["AUTH_ENFORCED"]` в `tests/conftest.py` (не env); новые тесты помечены `auth_enforced`.
- НЕ проверено: P0 #43 — смержен ли, проверить не удалось (нет доступа к gh); Postgres-ветка `AuthStore` и реальный Google OAuth локально не запускались (Postgres-тест пойдёт в CI; OAuth в тестах замокан).
- Решения для владельца: (1) founder-ссылки S1 и вебхуки остаются на собственных токенах, а не на Google-входе; (2) founder теперь входит через Google — его email нужно добавить в `AUTH_ALLOWLIST` со scope = project_id; (3) старый invite-code по-прежнему нужен в `/api/start`.
- Перед выкладкой: задать в Render `GOOGLE_CLIENT_ID/SECRET`, `SESSION_SECRET`, `AUTH_ALLOWLIST`, `AUTH_REDIRECT_URI`; убрать `ADMIN_KEY`. Секреты я не менял.

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

## P0 #36 S1: запрет E1→E3 + полный пакет основателю (Claude, 12.10)
- Ветка `claude/issue-36-20261012-1121`, PR #43; не мержить — ревью Руководителя бизнеса. Номер задачи P0 #36 (HQ-042 — это S5).
- Код: `s1_intake/scoring.enforce_rules` — `available_sources or set(caps)` заменено на проверку `is None`. Пустой набор файлов теперь значит «источников нет»: ссылка на `evidence` удаляется, E3 недостижим без файла. `None` — прежнее «без ограничений».
- Тесты: `tests/test_p0_36_e1_e3_gate.py` (переименован из test_hq042_*): регрессия «нет файлов + E3 по документу → <E3», поведенческие тесты диалога через `evaluate()` вместо проверки константы.
- Правка 12.10: в тестах диалога `res.claims` → `res.classification.claims` (реальная структура EvaluationResult).
- Правка 12.10 (golden): `python tests/hq016_cases.py hq035` — пересчитаны `tests/golden/hq035_{it_wellness,lab_service,no_gate}.json` (без файлов claims на deck/model/evidence теряют источник и падают до E0). `hq016_cases.run` для LS-кейсов с `cls_override` подаёт синтетические deck/evidence-документы (`SYNTH_DOCS`), поэтому ожидания LS-тестов не менялись; T3 сверяет с базовым `hq016_*` только структуру. Полный прогон: 211 passed, 0 failed. Лог — в описании PR.
- Замечание: фикс шире, чем «запрет E3»: без файлов теряют источник и deck/model, т.е. E1/E2 → E0 (readiness non-LS кейсов 7–10 → 3–4). Решение Руководителя: подтвердить, что так и задумано.
- `jobs_kind_check` (db/migrations/002_jobs_queue.sql) этим PR не затронут, не чинилось.
