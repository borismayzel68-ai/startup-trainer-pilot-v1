# HANDOFF (индекс)

Подробный handoff ведётся по задачам в `docs/handoff/<issue>.md`; здесь — только ссылки. При каждом коммите обновляется файл своей задачи (и эта таблица, если появилась новая задача).

## Правки STATE.md по ревью FAIL 01.11 (Claude, PR #119, ветка `docs/state-2026-10-31`)
- Только docs/STATE.md: пометки «ПЕРЕНЕСЕНО (#116), новая дата не назначена» у «Внешнего результата цепочки» и у пунктов «05.11 — полный запуск» / «Код S4–S7 к 05.11»; убрана устаревшая фраза про #108; слияние #113 описано одинаково («НЕ ПОДТВЕРЖДЕНО»); у открытой задачи про отзыв согласия — «срок перенесён (#116)». История не переписана.
- Код и секреты не менялись. pytest: 737 passed, 7 skipped.

## Слияние master в #113 31.10 (Claude, ветка `docs/state-2026-10-30`)
- Конфликт docs/STATE.md после #108: два блока «Статус 30.10» (из #108 и из #113) объединены в один раздел без дублей, ничего не удалено. Добавлена пометка: #105–#108 теперь в master (master 315e6c5), кто слил — НЕ ПОДТВЕРЖДЕНО.
- Только docs; код и секреты не менялись. pytest: 737 passed, 7 skipped.

## Проверка стенда и защиты 30.10 (Claude, ветка `docs/stand-check-2026-10-30`)
- Отчёт: `docs/reports/STAND_CHECK_2026-10-30.md`; STATE.md дополнен. Исправления — отдельными draft PR #105 (S3 CSRF, nginx), #106 (отзыв согласия), #107 (ловушки env staging); handoff каждой — `docs/handoff/sec-*.md` в своей ветке.
- Код продукта в этой ветке не менялся.

## Починка staging-compose 29.10 (MOS Tech, ветка `fix/staging-compose-database-url`)
- Найдено при сборке пакета передачи: `docker-compose.staging.yml` (#102) передавал только `SESSION_DATABASE_URL`, а `staging_guard` при `APP_ENV=staging` требует `DATABASE_URL` → `StagingConfigError` при старте. Исправлено: в app заданы оба с одним адресом.
- `tests/test_staging_compose.py`: compose даёт DATABASE_URL = SESSION_DATABASE_URL, seed в compose нет. Код продукта не менялся.
- Не проверено: сам docker compose (Docker на машине нет); `import app` с env из compose проверено вручную.

## Пакет для Сергея 29.10 (MOS Tech, ветка `docs/for-sergey-2026-10-29`)
- Только документация, код не менялся: `docs/for-sergey/` — README.md, 01 (Nocvela для технического человека), 02 (ТЗ-чек-лист приёмки стенда), 03 (стенд: что установить и как). Основа — master 54295a1.
- Секретов и значений переменных нет (только имена); персональных данных нет, кроме имён участников проекта; демо-адреса — синтетические `@example.test`.
- Не проверено: команды из 03 (docker build, compose, nginx) на живой машине не запускались.

## Починка master 27.10 (диспетчер кода, ветка `fix/delivery-worker-t0`)
- tests/test_delivery_worker.py: та же мина, что 26.10 в test_session_store.py — T0 = 2026-09-26 + хранение 30 дней → с 27.10 store.get() → None, 3 теста красные на master 03152fb. T0 теперь от текущего часа. Код не менялся.
- pytest локально: 638 passed, 27 skipped, 0 failed.

## Интеграция к заморозке 29.10 (Штаб, 26.10, ветка `integration/freeze-2910`)
- В одну ветку обычными merge-коммитами влиты PR #82 (MOS-6), #79 (MOS-7), #81 (S2 мандат), #84 (MOS-5), #85 (S5), #86 (S3 плательщик), #83 (STATE.md) — по порядку. Ни один PR не переписан.
- Конфликты (3): app.py ×2 (#79 после #82: проверка PUBLIC_BASE_URL + check_prod_config — оставлены обе; #84 после #81: регистрация s2_bp + fund_bp — оставлены обе), HANDOFF.md ×1 (#81: сохранены обе части). Код ни одной стороны не удалён.
- Ревью второй моделью (Claude Sonnet) 26.10: все 6 PR с кодом — PASS*, блокеров нет; некритичные замечания — в комментариях PR.
- Исправлено в интеграции (CI 26.10 был красный):
  1. tests/test_session_store.py — жёсткая дата 2026-09-26 + хранение 30 дней = записи истекли 26.10, get() → None (4 теста; ломало и master). Даты теперь от сегодняшнего дня.
  2. Стык #82 × #79: MOS-7 запрещает S1_LLM_PROVIDER=mock на staging, а deploy.sh/rollback.sh MOS-6 ставили mock → staging упал бы при старте. deploy.sh/rollback.sh → openai, OPENAI_API_KEY и OPENAI_BASE_URL берутся из Lockbox (без них контейнер не стартует). check_prod_config: на staging OPENAI_BASE_URL обязателен и не api.openai.com (только Yandex AI Studio) — данные не уйдут в США при пустом секрете. Тест test_staging_requires_ru_base_url.
  pytest после правок (S1_LLM_PROVIDER=mock, как в CI): 555 passed, 27 skipped, 0 failed.
- Известное, вне объёма: два файла миграций с номером 004 (004_fund_view, 004_portfolio_passport) — порядок применения детерминирован (sorted), таблицы независимы; переименование в 005 — отдельной задачей.

| Issue | Тема | Файл |
|---|---|---|
| #16 | HQ-031 · S5 светофор по форме HQ-030 (без новой ledger-таблицы) | [docs/handoff/16.md](docs/handoff/16.md) |
| #17 | HQ-032 · S6 входной пакет до term sheet; обход admin/owner в S2 закрыт | [docs/handoff/17.md](docs/handoff/17.md) |
| — | 152-ФЗ · страница согласия v0-draft (`/consent`, миграция 006); 29.10 MOS Tech: merge master, EXPECTED в test_mig005 + 006. НЕ МЕРЖИТЬ без решения Бориса | [docs/handoff/pdn-consent.md](docs/handoff/pdn-consent.md) |
| #22 | HQ-036 · линия tilda (A9 + S1) | [docs/handoff/22.md](docs/handoff/22.md) |
| #33 | Чистка __pycache__ | [docs/handoff/33.md](docs/handoff/33.md) |
| #36 | P0 #36 · S1 (шкала E, пакет основателю) | [docs/handoff/36.md](docs/handoff/36.md) |
| #41 | HQ-042 · S5 сверка с паспортом входа (доработка ролей 26.10) | [docs/handoff/41.md](docs/handoff/41.md) |
| #52, #54 | MOS-1 Auth+RBAC, MOS-3 импорт — полный лог HANDOFF.md из master 34d43f4 (перенесён без изменений) | [docs/handoff/master-log-2026-10-22.md](docs/handoff/master-log-2026-10-22.md) |
| #53 | MOS-2 · схема dealflow | [docs/handoff/53.md](docs/handoff/53.md) |
| #37 | HQ-041 · S6 «Периметр входа» (шлюз NEEDS_EVIDENCE); 26.10: `POST /s2/checks` только через пройденный S6; 27.10: merge master (после #90); 27.10: атомарный захват шлюза (CAS), forward_to_s2 и POST /s2/checks на одном захвате, повтор → 409, pytest 675 passed | [docs/handoff/37.md](docs/handoff/37.md) |
| #93 | Q-316 · кнопка «Сообщить о проблеме» (S1); новая таблица s1_feedback — ревью MOS Tech; pytest 685 passed | [docs/handoff/93.md](docs/handoff/93.md) |
| mig-005 | Миграция 004_fund_view → 005_fund_view, guard дублей номера, откат down/005 (MOS Tech, 29.10, ветка `fix/migration-005-fund-view`) | [docs/handoff/mig-005.md](docs/handoff/mig-005.md) |
| #57 | MOS-6 · staging; 29.10: docker-compose.staging.yml (без демо-пользователей) + пример nginx | [docs/handoff/57.md](docs/handoff/57.md) |
| #59 | HQ-023 · S3 Эксперт (БП-07) | [docs/handoff/59.md](docs/handoff/59.md) |

## Слияние master (15adbca, LSM v1 #75) в ветку MOS-6 (Claude, 23.10, Refs #57)
- Merge master обычным merge-коммитом, без rebase и force-push; конфликт только HANDOFF.md, код не менялся. Разделы LSM v1 из master — ниже без изменений.

## MOS-7 — одна точка замены LLM + Yandex AI Studio, issue #68, ветка `claude/issue-68-20261022-0511` → master (Claude, 22.10)

## Q-067 — правила ревью кода, issue #76, ветка `claude/q067-review-rules` (Claude, 99 Tech Director, 17.10)
**Сделано**
- `REVIEW.md` — 7 классов провала этого репо (PASS без прогона, статус выше доказательства, порог не из канона, молчаливая неоднозначность, внешнее действие, реальные данные, тихий успех), уровни риска, защищённые пути, формат находок, лимит 3 раунда, регрессия из дефектов.
- `CLAUDE.md` — блок «Проверка до PR».
- `.github/pull_request_template.md` — как проверено / уровень риска / как откатить.
- `.github/CODEOWNERS` — черновик защищённых путей.
- Код продукта не менялся.

**Не сделано / нужно решение Бориса**
- Ruleset на master (обязательный CI, ревью владельца кода, сброс одобрения при новом коммите) — настройки GitHub, делает Борис.
- Отдельный аккаунт MOS Tech для CODEOWNERS — E0.
- Регрессионные тесты EDGE-01…09 — после решений по неоднозначностям (HQ-053), в HQ-031.

## HQ-023 — S3 Эксперт (БП-07), issue #59, ветка `claude/issue-59-20261013-1241` → master (Claude, 13.10)
**Сделано**
- `app.py` больше не создаёт свой `OpenAI(...)`: `call_json` идёт через `s1_intake.llm.LLMClient.complete_dict` (тот же `OPENAI_BASE_URL`, `OPENAI_API_KEY`, `S1_LLM_PROVIDER`). Модель тренажёра по-прежнему `OPENAI_MODEL`.
- `LLMClient`: модель из `Settings.openai_model`; `LLM_JSON_FORMAT=json_schema` переключает `text.format` на json_schema из pydantic (на случай, если Yandex не примет json_object).
- S1: модель закреплена (TG-1) — `OPENAI_MODEL`/`S1_OPENAI_MODEL` по-прежнему не действуют; для смены провайдера введена отдельная явная `S1_LLM_MODEL_ID`.
- Тест `tests/test_mos7_llm_env.py`: base_url и модель из env в обоих местах. Тесты: 268 passed.

**Не проверено / решения для MOS Tech**
- Вызова реального Yandex AI Studio не было (нет ключа/сети). Работает ли `json_object` — не подтверждено; fallback `LLM_JSON_FORMAT=json_schema` готов.
- Поведение app.py изменилось: теперь `text.format=json_object` (раньше формат не задавался). Все три промпта содержат слово JSON.
- `S1_LLM_MODEL_ID` ослабляет TG-1 (смена модели без коммита) — нужно решение ревьюера.
- Имена секретов Lockbox: `OPENAI_API_KEY` (ключ Yandex API), `OPENAI_BASE_URL`, `OPENAI_MODEL` (`gpt://<folder_ID>/<model>`), `S1_LLM_MODEL_ID`. Folder_id входит в строку модели → хранить в Lockbox. Значения не трогались.
- Eval P-07 (приёмка) — отдельно, не в этом PR.

**Правки по ревью Р-4 (Claude, 22.10)**
- Замечание 1: добавлен `LLM_JSON_FORMAT=none` — `text.format` не передаётся, JSON достаётся через `_extract_json`; работает и для app.py (`complete_dict`), и для S1. По умолчанию `json_object` (как было). `json_schema` по-прежнему действует только для S1 (у `complete_dict` нет схемы → `json_object`); это зафиксировано в README, `.env.example`, docstring.
- Замечание 2: тест `test_app_base_url_and_model_from_env` откатывает перезагруженный `app` в `finally` (исходные MODEL/client восстановлены). Новые тесты: оба режима (`json_object`/`json_schema` → json_object для complete_dict; `none` → без `text`).
- Замечание 3: docstring `llm.py` обновлён (модель — из env; S1 по умолчанию закреплена, TG-1). Две переменные модели (`OPENAI_MODEL` / `S1_LLM_MODEL_ID`) остаются — решение ревьюера по TG-1 не принято.
- Тесты: 270 passed, 1 warning. Не проверено: реальный вызов Yandex.

**Слияние master (Claude, 22.10)**
- Влит origin/master (34d43f4: MOS-1 вход и роли, MOS-3 импорт) обычным merge. Конфликты: HANDOFF.md (сохранены обе части), app.py (`static_folder=None` из master + `LLMClient` из этого PR; `OpenAI()` не возвращён).
- Заглушки проверки ролей/входа в этом PR не было — переключать нечего; роли идут через `auth.require_role` из MOS-1.
- Тесты после слияния: 404 passed, 4 skipped, 1 warning.
- Слияние master 23.10 (Claude): влит origin/master (15adbca, LSM v1 #75), конфликт только в HANDOFF.md — сохранены оба раздела, код не менялся.
- Слияние master 23.10 (Claude, #80 и #34): влит origin/master (42ce831), конфликт только в HANDOFF.md — сохранены записи обеих сторон; код не менялся.

**Исправление замечаний ревью 23.10 (Claude, 24.10, поручение Штаба-диспетчера)**
- Исправлено: (1) `LLMClient.calls` — `deque(maxlen=50)` (`CALLS_MAXLEN`), тест: 100 вызовов `complete_dict` → 50; (2) `check_prod_config` в `s1_intake/llm.py`, вызов в `app.py` после проверки SESSION_SECRET: `S1_LLM_PROVIDER=mock` при `APP_ENV=staging|production` → ошибка при старте; тест + строка в README; (3) README и `.env.example`: для Yandex до проверки реальным вызовом ставить `LLM_JSON_FORMAT=none` (умолчание `json_object` не менялось); (4) `OPENAI_API_KEY` в тестах — через `setUpModule`/`tearDownModule` (`mock.patch.dict`), не на уровне модуля.
- Осталось: решение ревьюера по двум переменным модели (TG-1); реальный вызов Yandex не проверялся; зелёный CI на коммите.

---
## S2 — шлюз мандата фонда, issue #44, ветка `claude/issue-44-20261022-0512` → master (Claude, 22.10)
**Сделано**
- `s2_mandate/investment_policy.yaml` — единственный источник правил: чек 6–10 млн ₽ (границы включительно), инструменты (convertible_loan = preferred; equity/loan/safe → exception; grant → no_fit; неизвестный → exception), сектора life_sciences/medtech/biotech + инновационность. Всё помечено `status: hypothesis`; путь можно сменить через `S2_POLICY_PATH`.
- `s2_mandate/engine.py` — чистая оценка → FIT / NO_FIT / EXCEPTION (NO_FIT > EXCEPTION > FIT), причины по каждому правилу. Порогов и списков в коде нет (тест по исходнику).
- `s2_mandate/blueprint.py` — `POST /s2/checks` (admin), `GET /s2/checks/<id>`, `POST /s2/checks/<id>/decision` (approve|reject). Human Gate — механизм S1: персональный токен из `S1_APPROVERS`, журнал `s1_approvals` (outcome `s2_exception_approved|rejected`, хеш записи). Admin-токен решение принять не может (403). `cleared_for_meeting` для EXCEPTION = true только после approve человека. В `app.py` — только регистрация blueprint.
- Отправки наружу нет. Данные только синтетические.

**Не решено / проверить**
- Какие инструменты кроме конвертируемого займа — «исключение», а какие «не подходит» (grant → NO_FIT) — моё допущение, нужно решение Бориса/фонда; правится в yaml.
- Чек вне диапазона = NO_FIT (не EXCEPTION). Валюта — только ₽, без пересчёта.
- Подключения к S1/Pack (вызов шлюза из потока заявки) нет — отдельная задача.

**Merge master (22.10, 34d43f4: MOS-1 #61 + MOS-3 #72):** конфликты в app.py и HANDOFF.md разрешены, обе части сохранены. Собственной заглушки ролей в S2 не было (использовался `_admin_ok()` S1, который уже учитывает сессию MOS-1); добавлены политики RBAC в `S1_POLICY`: `create_check`/`get_check` — owner/fund_staff (scope `*`, external=secret), `decide` — owner + персональный Bearer из `S1_APPROVERS` (сессия сама решение не подписывает, как S3 sign).

**Тесты:** после merge master 412 passed, 4 skipped (до merge 275 passed; новые: tests/test_s2_mandate.py — границы 5.9/6/10/10.1, каждый инструмент, сектор, EXCEPTION без подписи).

**Merge master (23.10, 15adbca: LSM v1 #75):** конфликт только в HANDOFF.md, обе части сохранены, код не менялся.

**Merge master (23.10, 42ce831: MOS-4 #80 + HQ-035 #34):** конфликты в app.py и HANDOFF.md, обе части сохранены (регистрация `s2_bp` и `portfolio_bp`), код S2 не менялся.

**Правки по ревью 23.10 (24.10, поручение Штаба-диспетчера, Refs #44)**
- Исправлено: (1) `validate_input` — `instrument`/`sector` только `str`, `check_rub` конечное число (`math.isfinite`); список/объект/NaN/Infinity → 422, не 500. (2) Проверка создаётся сразу в `ready_for_review` (`store.create_or_get(..., status=)`, по умолчанию `received` — поведение S1/S3 не менялось), под `recoverable()` не попадает. (3) Решение по EXCEPTION атомарно: `store.log_decision_once` + UNIQUE-индекс `s1_approvals_s2_once` (job_id, report_sha256) для исходов S2; проигравший гонку → 409. (4) Границы теста целыми `5_900_000` / `10_100_000`. (5) Тест с `auth_enforced`: `create_check` без входа/роли investor → 401/403, `decide` с сессией (fund_staff, owner) без Bearer → 403.
- Остаётся риск (не ломал общий механизм хранения): проверки S2 лежат в `s1_readiness_jobs`, поэтому видны в `/s1/jobs`; к ним применимы `/s1/jobs/<id>/approve`, `/rerun`, retention-purge (удаляет по сроку хранения). Надёжнее — отдельная таблица; отдельная задача.
- Ждёт подтверждения фонда (вопрос к #44): equity/loan/safe → EXCEPTION, grant → NO_FIT, чек вне диапазона → NO_FIT, только ₽.
- Отдельная задача после слияния: подключить шлюз к потоку заявки S1 и сделать проверку «до встречи» обязательной шагом.

## MOS-5 (#56) — вид фонда, сокращённый объём по Д-92 (Claude, 22.10), ветка `claude/issue-56-20261022-0934` → master
**Сделано (только вид фонда owner/fund_staff; вида инвестора/Артура нет)**
- `db/migrations/004_fund_view.sql`: `decision_snapshots`, `ic_decisions` (append-only). Роли и `audit_log` — в 001 (MOS-1), здесь не создаются.
- `fund_view/gates.py` + проверка в `dealflow/status.py:change_status`: переход в DD/IC без шлюзов Thesis / Buyer (кто И когда) / Engagement (FIXED) → `GateBlocked` (HTTP 409 с перечнем). Блок на сервере, в т.ч. при прямом вызове change_status.
- `fund_view/service.py`: Decision Snapshot; FACT только `sign-fact` владельца, любая правка сбрасывает в hypothesis; Engagement фиксирует только owner; решения IC GO/PAUSE/STOP → `ic_decisions` + `deal_status_history` (PAUSE→ON_HOLD, STOP→PASS, GO из ON_HOLD→ACTIVE) + `audit_log`; чек-лист из `docs/standards/ARTUR_STANDARD.md` дословно (заголовки и пункты `- [ ]`), без файла — «стандарт не загружен».
- `fund_view/blueprint.py` (`/fund/startups/<vc_id>/...`): snapshot (json/html), engagement, sign-fact, transition, ic-decision. noindex: `X-Robots-Tag`, meta; robots.txt уже был. Доступ — auth MOS-1: `auth.require_role(("owner","fund_staff"), scope="*")` на всех 7 маршрутах `/fund/*`; investor → 403, без входа → 401.
- Тест `test_hq018_postgres` обновлён: набор ролей теперь включает owner, fund_staff.

**Остаток (не делалось по решению)**
- Вид инвестора (Артур) — вне объёма по Д-92.
- Еженедельный обзор G/Y/R по активным проектам.
- Дашборд фонда #45 (воронка по enum, портфель RAG, next actions).

**Слияние с master и auth MOS-1 (Claude, 22.10, поручение Бориса в PR #84)**
- master влит в ветку обычным merge (без rebase/force-push); конфликт только в HANDOFF.md — сохранены обе части.
- `resolve_actor()` теперь берёт `auth.current_user()` и гранты MOS-1; временный Bearer-доступ по env-токенам удалён из кода и документации.
- `004_fund_view.sql`: убраны создание ролей и собственный `audit_log` — они в 001 (один `audit_log` на систему). `fund_view.service.audit` пишет в таблицу MOS-1 (actor=email, object=`startup:<id>`, detail=JSON). **Побочный эффект:** триггер append-only на `audit_log` из 004 убран (таблица общая, схему определяет MOS-1); `ic_decisions` остаётся append-only. Нужна ли неизменяемость `audit_log` — решение владельца.
- Тесты HTTP — на реальную сессию MOS-1 (маркер `auth_enforced`): без входа 401, investor 403, fund_staff со scope кейса 403.

**Ревью PR #84 (Claude, 23.10):** CHANGES, см. комментарий в PR; в HANDOFF исправлено устаревшее описание миграции 004. pytest: 437 passed, 4 skipped.

**Правка по ревью п.3 (Claude, 23.10):** `_guard` в `fund_view/blueprint.py` ловит `LookupError` (стартап пропал между `find_startup` и `change_status`/`record_decision`) → 404 `not_found` с rollback вместо 500. Тест `test_startup_vanished_after_lookup_gives_404` (transition, ic-decision; поиск подменён, соединение «без стартапа»).

**Остаток после заморозки 29.10:** п.4 ревью — писать в `audit_log` `result='deny'` для отказов шлюза (409) и «только owner» (403); сейчас в журнале только успешные действия.

**Слияние master после #80 (MOS-4) и #34 (Claude, 23.10, поручение Штаба по Р-4)**
- `origin/master` (42ce831) влит обычным merge, без rebase/force-push. Конфликты: `HANDOFF.md` (сохранены обе стороны) и `app.py` (оставлены обе регистрации: `fund_bp` и `portfolio_bp`). Код MOS-5 не менялся.
- В `db/migrations` теперь два файла `004_*` (`004_fund_view.sql`, `004_portfolio_passport.sql`): таблицы не пересекаются, порядок по имени детерминирован; при желании переименовать в 005 — решение MOS Tech.
- pytest целиком: 487 passed, 4 skipped, 1 warning, 33 subtests passed.

**Не проверено / ограничения**
- Текста стандарта Артура в репо нет; содержание не выдумывалось. Разбор markdown — по заголовкам и `- [ ]`; формат реального файла может потребовать правки парсера.
- Шлюзы проверяются в Python (change_status), не триггером БД: прямой SQL UPDATE startups их обойдёт.
- «Участие ASF» = поле asf_engagement=FIXED, ставит owner; семантику уточнить с PM. Квартал решения — строка YYYY-Qn, повторное решение за тот же квартал не запрещено.
- На Render/живых пользователях не запускалось.

**Тесты:** после правки п.3 ревью (23.10): 439 passed, 4 skipped, 1 warning, 29 subtests (+2 теста 404). До неё, после слияния с master и перехода на MOS-1: 437 passed, 4 skipped (публичные маршруты), 29 subtests, весь pytest (в т.ч. 26 в tests/test_mos5_fund_view.py; Postgres-часть исполнялась, не пропущена).

---

# Предыдущее

## HQ-042 — S5 сверка с паспортом входа + триггер частичного выхода, issue #41, ветка `claude/issue-41-20261022-0932` (Claude, 22.10)
**Сделано**
- `portfolio_update/__init__.py` — отдельный модуль (паспорт портфеля MOS-4 влит в master 23.10, модуль к нему пока не подключён): `reconcile` (список расхождений changed / missing_in_update / not_in_passport), `mark_new_round` (флаг проверки частичного выхода, идемпотентно), `ask`/`answer` (модель вопросов и приём ответа, без отправки в мессенджер и без сети), доступ только роли фонда. Точка подключения: собрать `EntryPassport` из полей MOS-4.
- `docs/hq042_reconcile_example.md` — синтетический пример отчёта (сверяется тестом с `render_report`).
- Тесты: `tests/test_hq042_s5_reconcile.py`. Весь прогон (до merge master): 273 passed; актуальный — в строке ниже.

- 22.10 (Штаб): влит master (#61, #72, #75) обычным merge; конфликт был только в HANDOFF.md, сохранены обе части. pytest: 400 passed, 17 skipped.
- 23.10 (Claude, Р-4): влит master после слияния #80 (MOS-4) и #34 обычным merge-коммитом; конфликт только в HANDOFF.md, сохранены записи обеих сторон, код не менялся. pytest: 461 passed, 4 skipped, 33 subtests (Postgres-тесты пропущены локально — нет HQ018_PG_URL).

**Вопросы / NEEDS_EVIDENCE**
- Состав полей паспорта входа в задаче не задан — поля в тестах условные (field_a…). Поля паспорта теперь есть в `portfolio/` и `db/migrations/004_portfolio_passport.sql` (master) — сопоставить отдельной задачей.
- Правила частичного выхода не заданы: флаг несёт `rules=NEEDS_EVIDENCE`, проверки по существу нет.
- Роли: приведены к `portfolio/access.py` 26.10 (см. docs/handoff/41.md).
- Хранение в БД (миграции) не делалось: состояние в памяти; нужна схема после MOS-4.
- Не подключено к app.py (нет HTTP-эндпоинтов); реальной отправки в мессенджер нет.

---

## HQ-062 — S3: плательщик (P-91), фильтр S3 в /s1/jobs, вход на /sign-screen; issue #65, ветка `claude/issue-65-20261022-0930` → master (Claude, 22.10)
- 23.10 (Claude, по поручению Штаба): исправлен блокер п.1 — сессия MOS-1 на sign-screen и в /s1/jobs; п.3 — TODO.
- 23.10 (ревью Claude, PR #86): CHANGES — см. комментарий ревью; в HANDOFF исправлена устаревшая строка про отсутствие auth.py и число тестов.
- 23.10 (Claude, по поручению Штаба, Р-4): влит master (#80 MOS-4 портфель, #34 HQ-035) обычным merge; конфликт только в HANDOFF.md, сохранены записи обеих сторон; код HQ-062 не менялся.
- 22.10 (Штаб): влит master (#61, #72, #75) обычным merge; конфликт был только в HANDOFF.md, сохранены обе части; `s1_intake/main.py` слился автоматически. pytest: 396 passed, 17 skipped.

**Сделано**
- P-91: `POST /s3/briefs` требует `payer` ∈ {`fund`, `brainbox_I`} (точное совпадение, регистр важен). nocvela, rounds и всё прочее → 422, запись не создаётся. Значение хранится в `data.s3.payer`, отдаётся в `GET /s3/briefs/<id>`; в хеш брифа/подпись не входит.
- `/s1/jobs`: админ-токен видит только S1 (S3 отфильтрован); роль S3 (персональный подписант из `S3_OWNERS`, пусто → любой подписант) видит только S3; чужой подписант → 403, без входа → 401. `GET /s1/jobs/<id>` и артефакты для записи S3 → 404 (S3 только через `/s3/*`). Фильтр — `store.list(limit, s3="exclude"|"only"|"all")`.
- `/s3/briefs/<id>/sign-screen`: сессия owner/fund_staff (MOS-1) или Bearer подписанта; без входа 401, admin-Bearer/не-владелец/investor 403.
- Тесты: `Hq062Tests` в tests/test_hq023_s3_expert.py; существующие тесты S3 подправлены под обязательный `payer` и вход на sign-screen. pytest 23.10 (ревью): 409 passed, 4 skipped. После merge master (#80, #34): 462 passed, 4 skipped, 33 subtests.

**MOS-1 (#61) подключён (23.10, блокер ревью п.1 закрыт).** `s1_intake/main.py::_staff_session()` — сессия прошла `require_role` и имеет роль owner/fund_staff. `_s3_viewer()` и `sign_screen` её принимают; Bearer-ветки не менялись. `/s1/jobs`: сессия owner/fund_staff видит S1 + S3 (`s3="all"`), investor по сессии и admin-Bearer — только S1, подписант по Bearer — только S3. `app.py` S1_POLICY: `s3_expert.sign_screen` получил `external="secret"`, иначе при включённом RBAC Bearer-подписант и admin-Bearer получали 401 до view (admin-Bearer теперь 403, как требовалось). Подпись `POST /s3/briefs/<id>/sign` — по-прежнему только персональный Bearer (G-12), сессия её не заменяет. Тесты: `tests/test_hq062_s3_session.py` (5, маркер `auth_enforced`; на коде до правки падают 3 из 5). pytest 23.10: 414 passed, 4 skipped, 29 subtests.

**Не проверено / важно**
- sign-screen по сессии проверен тестами на мок-входе; реальный Google OAuth вручную не прогонялся. Страница подписи открыта по сессии, но сама подпись (POST /sign) требует Bearer — из браузера подписать по сессии нельзя (G-12, решение за владельцем).
- Отдельная роль «S3-эксперт» не заведена (нет модели ролей до #61); сейчас роль = подписант из `S3_OWNERS`.
- TODO (ревью 23.10 п.3, не делалось): `store.list` читает все строки и режет по limit в Python; перенести фильтр S3 в SQL и вернуть `LIMIT` (пометка в `s1_intake/store.py::list`). Для пилота объём мал.
- payer — только ограничение значения (P-91), не платёжная логика и не привязка к договору/каналу S7.
- CI с Postgres 16 здесь не запускался — смотреть прогон PR.

---
## MOS-4 — Портфель (паспорт, светофор S5, Д-68), issue #55, ветка `claude/issue-55-20261022-0511` → master (Claude, 22.10)
**Сделано**
- `db/migrations/004_portfolio_passport.sql`: роли `owner`, `fund_staff`; `portfolio_companies` + `holding_vehicle` (fund | partner_nominee), настраиваемые пороги runway (CHECK 6–18, red < yellow); таблица `portfolio_passport_snapshots` (cost, valuation basis/date, round, ownership %, runway, burn, plan_burn, next milestone что/когда/кто, loan income, дата последнего апдейта инвестору), append-only, UNIQUE (company, дата). LP-колонок нет.
- `portfolio/rag.py` — единственное место правил: runway красный < 9, жёлтый 9–12 (включительно), зелёный > 12; пороги 6–18 (иначе `InvalidThresholds`); burn: |burn−plan|/plan **> 20%** → флаг (в обе стороны, цвет не меняет); апдейт **> 14 дней** → просрочка (14 — ещё в срок; ни разу не было — просрочка). Причины текстом в `explanation()`.
- `portfolio/access.py`, `service.py`, `blueprint.py`: список и карточка (с историей снапшотов и пометкой «деньги фонда, договор на партнёра» для partner_nominee). owner/fund_staff — всё; investor — только по scope (vc_id или `*`); founder/expert/без актора — 403.
- `app.py`: зарегистрирован blueprint `/portfolio`.

**Не сделано / допущения — важно**
- 22.10 (merge master 34d43f4: MOS-1 #61 + MOS-3 #72): заглушка актора убрана. Маршруты `/portfolio` и `/portfolio/<vc_id>` защищены `auth.require_role(owner, fund_staff, investor)`; актор строится из грантов `auth.current_user()` (`blueprint.actor_from_user`): owner/fund_staff со scope `*` — весь портфель, investor — по своим scope (vc_id или `*`), founder/expert и fund_staff с узким scope — 403, без входа — 401. `configure()` теперь задаёт только репозиторий (по умолчанию Postgres по SESSION_DATABASE_URL; без него 503). Страницы HTML нет, только JSON (#49).
- Интерпретации, которые надо подтвердить: отклонение burn считается по модулю (и недорасход тоже флаг); план = 0 или не задан → флаг не ставится; «обновление» = поле `last_investor_update` снапшота.
- Спецификация SPEC v0.2 с Drive исполнителю недоступна; строил по тексту issue и комментариям. Формы ввода/импорта снапшотов нет (реальные данные — импортом вне репо).
- Роли `owner`/`fund_staff` теперь добавляет и 001 (MOS-1); в 004 они остаются с ON CONFLICT DO NOTHING — безопасно.
- Postgres-тесты (миграция 004, append-only) локально пропущены (нет HQ018_PG_URL), пойдут в CI.

**Тесты:** 443 passed, 4 skipped после merge master (HTTP-тесты портфеля — через реальный вход MOS-1).
- 22.10 (merge master 15adbca, LSM v1 #75): конфликт только в HANDOFF.md, сохранены обе части; код не менялся. Прогон: 449 passed, 4 skipped, 29 subtests.

## LSM v1 · ред. 2 — документ не снимает риск (Claude, 22.10) — Q-096, PR #75
- Основание: замечание внешнего рецензента 17.10 («наличие документа не закрывает риск»), ДА Бориса 22.10 14:06 (РБ-071, вариант А).
- Код: `scoring._ls_summary` — отбор барьеров по слабости (нет данных/E0–E2 → E3+ не FACT → FACT), до barriers_max; пункты с документом E3+ остаются с полем `note` «Документ есть; достаточность доказательства проверяет человек»; показ — в каноническом порядке v0.2; К7 — от самого слабого пункта. `founder_report.html` выводит `note`.
- VC-997 после правки: К2 (E1), К1 (E3, пометка), К4 (E3, пометка), К5 (нет данных), К6 (E0); К3 (E3) не вошёл по лимиту 5.
- Тесты: `LSMv1BarrierRanking` 5 (новый `test_document_does_not_close_risk`); отрицательный контроль: на ред. 1 (6cefc2d) падает 1/5. Полный прогон: 260 passed, 9 skipped, 29 subtests.
- Ред. 2b (по приёмке Sonnet 22.10, риск 1): открытые пункты сверх лимита 5 не теряются — `ls.barriers_hidden` + строка в отчёте «Ещё открытые пункты, не вошедшие в список: …» (VC-997: К3). Формулировка К7 уточнена: «от первого по приоритету» (слабые, внутри — v0.2), не «самый слабый по уровню». Тест `test_overflow_items_not_silently_dropped`. Полный прогон: 261 passed, 9 skipped, 29 subtests.

## LSM v1 · ранжирование LS-барьеров (Claude, 17.10) — Q-096
- Ветка `claude/lsm-v1-barrier-ranking`, draft PR. Решение Бориса 16.10 21:02 (Р-1…Р-3): LSM v1 = LS-ветка внутри S1, приёмка на синтетическом VC-997 «ПО как медизделие».
- Дефект (найден прогоном VC-997): `scoring._ls_block` сортировал вехи К1–К6 только по порядку v0.2; вехи с документом E3 занимали места, E0 «кто платит» (К6) отрезался лимитом barriers_max=5.
- Код: `s1_intake/scoring.py` — сначала слабые (нет данных / E0–E2), затем E3+, затем FACT; число барьеров = число слабых (не меньше barriers_min). Веса, балл, методология не менялись.
- Тесты: `tests/test_hq016_ls_stage.py::LSMv1BarrierRanking` (4). Отрицательный контроль: на старом коде падает 1 из 4. Полный прогон: 259 passed, 9 skipped, 29 subtests passed.
- Не проверено: живой LLM (синтез «критичных барьеров» на mock-LLM содержит текст про CE/выручку из заглушки — на реальной модели проверить в QA-проходе 20.10, Q-094). Противоречие «ПО не МИ» ↔ назначение с диагностикой не детектируется кодом (только К7 «консультация по классу»).

Остальной лог master (MOS-1, S3, S1, MOS-2, MOS-3 и др., строки 30–172 прежнего HANDOFF.md master) без изменений лежит в `docs/handoff/master-log-2026-10-22.md`.

## Слияние master (#80, #34; 23.10 11:29) в ветку MOS-6 (Claude, 23.10, Refs #57)
- Merge master обычным merge-коммитом, без rebase и force-push; конфликт только HANDOFF.md. Хвост лога master (в т. ч. правки HQ-035 из #34) — в docs/handoff/master-log-2026-10-22.md, записи обеих сторон сохранены.

## Правки по ревью PR #82 (Claude, 23.10, Refs #57)
- PUBLIC_BASE_URL обязателен на staging, ограничен цикл ожидания бэкапа, тест bash -n, CLAUDE.md = master; подробности — docs/handoff/57.md.
