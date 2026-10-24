# HANDOFF

Обновлять при каждом коммите.

## MOS-7 — одна точка замены LLM + Yandex AI Studio, issue #68, ветка `claude/issue-68-20261022-0511` → master (Claude, 22.10)
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

## MOS-1 Auth + RBAC (Claude, 13.10, Refs #52)
- Ветка `claude/issue-52-20261013-0822` → PR в master (не мержить без ревью менеджера и без решения владельца; выпуск на живых пользователей утверждает только Борис).
- Сделано: Google OAuth (OIDC) + `AUTH_ALLOWLIST` (`auth.py`), хранилище users/grants/audit (`auth_store.py`), миграция 001 финализирована (+owner, fund_staff, audit_log, идемпотентна), `require_role` на каждом маршруте (старт приложения падает при маршруте без декоратора), CSRF, noindex, `/admin?key=` и `ADMIN_KEY` удалены (404; админ-просмотр `/admin/sessions` и `/admin/audit` — только owner), `docs/ACCESS_MATRIX.md`, `tests/test_auth.py`.
- S1 не сломан: Bearer-токены, ключ вебхука Tilda, подписанные ссылки и `X-Completion-Secret` сохранены как «внешняя проверка» (`external=`); сессия owner/fund_staff/investor проходит по ролям и scope (`job_id`).
- Тесты: `RBAC` выключается для старых тестов флагом `app.config["AUTH_ENFORCED"]` в `tests/conftest.py` (не env); новые тесты помечены `auth_enforced`.
- НЕ проверено: P0 #43 — смержен ли, проверить не удалось (нет доступа к gh); Postgres-ветка `AuthStore` и реальный Google OAuth локально не запускались (Postgres-тест пойдёт в CI; OAuth в тестах замокан).
- Решения для владельца: (1) founder-ссылки S1 и вебхуки остаются на собственных токенах, а не на Google-входе; (2) founder теперь входит через Google — его email нужно добавить в `AUTH_ALLOWLIST` со scope = project_id; (3) старый invite-code по-прежнему нужен в `/api/start`.
- Перед выкладкой: задать в Render `GOOGLE_CLIENT_ID/SECRET`, `SESSION_SECRET`, `AUTH_ALLOWLIST`, `AUTH_REDIRECT_URI`; убрать `ADMIN_KEY`. Секреты я не менял.

## HQ-023 — S3 Эксперт (БП-07), issue #59, ветка `claude/issue-59-20261013-1241` → master (Claude, 13.10)
**Сделано**
- `s3_expert/engine.py` — чистая логика: проверка входа (пустое поле → вернуть пользователю), рамки (LS, один узкий вопрос, границы компетенции, запрет INVEST/PASS/оценки/мед.-юр. вывода → ОТКАЗ + предложение сузить), утверждения со статусами claim / со слов компании / неизвестно / конфликт (статуса «факт» нет), бриф по таблице БП-07, md + json.
- `s3_expert/blueprint.py` — `/s3/briefs` (создать, статус, review-черновик, sign, brief.md/json, sign-screen). Подпись владельца — механизм S1 (`S1_APPROVERS` + журнал `s1_approvals`, outcome `s3_signed`, хеш содержимого). Второго механизма нет. Опционально `S3_OWNERS=имя,имя` сужает круг подписантов.
- G-12: без подписи бриф «ждёт подписи» и отдаётся 403; правка брифа после подписи снимает подпись (хеш). Систему подписать нельзя (admin-токен — 403). G-4: в `s3_expert` нет отправки наружу (тест по коду).
- `s3_expert/cases.py` — S3-SYN-01…10 и приёмка по 6 критериям; `python -m s3_expert.cases <каталог>` пишет md/json по кейсу + summary для загрузки в HQ_Trainer.
- Ядро скоринга и веса S1 не тронуты. В `app.py` добавлена только регистрация blueprint.

**Сломано или не проверено — важно**
- Файлы Drive (кейсы, критерии, БП-07, сценарий S3) исполнителю были НЕДОСТУПНЫ. SYN-01…10 и 6 критериев — вымышленные заглушки по тексту issue. 10/10 на них — самопроверка, не приёмка; нужна сверка с оригиналом и вердикт GPT.
- Правила рамок/конфликтов — эвристики по ключевым словам (словари в engine.py помечены «утвердить PM»).
- Записи S3 лежат в таблице заявок S1 (`s1_readiness_jobs`, `data.s3`) и видны в `/s1/jobs`; отдельной таблицы нет, чтобы не трогать store S1.
- Вживую на Render не запускалось. Артефакты в HQ_Trainer не загружались (нет доступа).

**Тесты:** 224 passed (22 новых: tests/test_hq023_s3_expert.py).

### Ред. 2 (Claude, сессия Tech Director, 13.10 вечер) — кейсы заменены оригиналом
- Причина: ред. 1 работал на выдуманных заглушках; его 10/10 недействителен. Оригинал кейсов (S3_SYNTHETIC_CASES_PASS_v0.1, Drive 1D77jXquQleMwpacdu8nBni9utkzAu7_A) и БП-07 v1.1 прочитаны.
- `s3_expert/cases.py`: S3-SYN-01…10 — вопросы и ловушки дословно из файла; тексты материалов собраны из «Контекста» и «Ловушки» без новых фактов. 6 критериев — из файла (раздел «Критерии PASS»). Порог PM (≥8/10, 0 ловушек «факт без источника», 0 нарушений безопасности, SYN-10 PASS).
- `s3_expert/topics.py` (новый): 9 тем (клиника, регуляторика, IP, PoC, производство, возмещение, рынок, команда, экономика раунда) — чеклист «что проверить» из «Хорошего ответа», обязательные документы (нет документа → ПРОБЕЛ), узкие вопросы для сужения.
- `engine.py` v0.2: противоречие по структурным фактам (один ключ — разные значения в разных источниках); «факт без источника» = категоричное утверждение только со слов компании; отсутствующий обязательный документ → ПРОБЕЛ; ОТКАЗ на широкий вопрос/INVEST предлагает 2–3 узких вопроса. Проверка безопасности не срабатывает на цитаты из материалов.
- Самопрогон: 10/10, ловушек 0, нарушений 0, SYN-10 PASS. Это НЕ приёмка — вердикт GPT.
- Отрицательный контроль (тест): без детектора противоречий — 5/10; без ловушки «факт» — 2 пропуска; без отказа — SYN-10 FAIL. Без проверки документов — 8/10 (порог формально выполняется: допуск PM в 2 кейса).
- Ограничение: извлечение структурных фактов из живого текста в коде нет (в синтетике `facts` заданы). На реальных документах это шаг LLM (s1_intake/llm.py) — отдельная задача после 05.11 или по решению PM.
- Тесты: 225 passed, 5 skipped (Postgres локально), 29 subtests.

---

# Предыдущее

## Задача / issue
HQ-036 — линия A9 (#6 head 83ab1f7 + #11 head 90e2d22) на линии tilda (Refs #22), ветка `chore/hq-036-rebase-a9-on-tilda` от fix/hq-035-fact-ceiling (85d424a, верх линии tilda с HQ-035). PR → s1/tilda-readiness-pack.

## Сделано
- Код ставит максимум FACT_CANDIDATE («заявление с документом»); verified_by от LLM обнуляется.
- FACT — только POST /s1/jobs/<id>/slots/verify (персональный токен, роль из working_values.yaml fact_signoff.signer_roles = [owner, analyst]), пакетом на слот, журнал s1_fact_signoffs (кто, когда, роль, основание).
- Тесты: tests/test_hq035_fact_signoff.py (ловушка, подпись → FACT + журнал, отказы). Баллы не изменились.
- G-13 закрыт владельцем: FACT ставит только роль owner или назначенный им analyst (выданный аккаунт с ролью analyst). signer_roles = [owner, analyst], без имён. Тесты G13SignerRoleTests: чужая роль → 403, слот остаётся FACT_CANDIDATE; owner и analyst подписывают. Ловушку из Issue #21 не меняли.
- Ревью — Claude; merge только после PASS от Claude (не GPT).
- Влит 90e2d22 (содержит 83ab1f7) merge-коммитом, без rebase.
- app.py: импорты объединены (s1 init_s1 + founder_package/iso_z/utc_now из A9; post_completion_json больше не импортируется — A9 доставляет completion через delivery_worker); лимит запроса 20 МБ (A9) и init_s1 (S1) — оба сохранены.
- README.md: раздел S1 сохранён целиком; строка Worker: `python delivery_worker.py` из A9 — рядом с Build/Start.
- Полный прогон: 197 passed, 0 failed, 0 skipped (с тестами A9).

## Дальше
- HQ-022: ветка от этой линии, только merge.

## Сломано или не проверено
- Имена подписантов и их роли — только через окружение S1_APPROVERS / S1_APPROVER_ROLES; в репо не хранятся.

- «CI прогон зелёный» (Issue #22): в репо нет тестового workflow (только claude.yml в PR #8) — пробел для PM.
- delivery_worker вживую (Render worker) не запускался — только тесты.

## Последний коммит
см. git log -1 (Merge master в fix/hq-035-fact-ceiling: HANDOFF.md объединён, signer_roles: [owner, analyst] сохранён)

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
## P0 #36 S1: запрет E1→E3 + полный пакет основателю (Claude, 12.10)
- Ветка `claude/issue-36-20261012-1121`, PR #43; не мержить — ревью Руководителя бизнеса. Номер задачи P0 #36 (HQ-042 — это S5).
- Код: `s1_intake/scoring.enforce_rules` — `available_sources or set(caps)` заменено на проверку `is None`. Пустой набор файлов теперь значит «источников нет»: ссылка на `evidence` удаляется, E3 недостижим без файла. `None` — прежнее «без ограничений».
- Тесты: `tests/test_p0_36_e1_e3_gate.py` (переименован из test_hq042_*): регрессия «нет файлов + E3 по документу → <E3», поведенческие тесты диалога через `evaluate()` вместо проверки константы.
- Правка 12.10: в тестах диалога `res.claims` → `res.classification.claims` (реальная структура EvaluationResult).
- Правка 12.10 (golden): `python tests/hq016_cases.py hq035` — пересчитаны `tests/golden/hq035_{it_wellness,lab_service,no_gate}.json` (без файлов claims на deck/model/evidence теряют источник и падают до E0). `hq016_cases.run` для LS-кейсов с `cls_override` подаёт синтетические deck/evidence-документы (`SYNTH_DOCS`), поэтому ожидания LS-тестов не менялись; T3 сверяет с базовым `hq016_*` только структуру. Полный прогон: 211 passed, 0 failed. Лог — в описании PR.
- Замечание: фикс шире, чем «запрет E3»: без файлов теряют источник и deck/model, т.е. E1/E2 → E0 (readiness non-LS кейсов 7–10 → 3–4). Решение Руководителя: подтвердить, что так и задумано.
- `jobs_kind_check` (db/migrations/002_jobs_queue.sql) этим PR не затронут, не чинилось.

## MOS-1 #52: ребейз на master с #43 (Claude, 14.10)
- Ветка `claude/issue-52-20261014-0838`: коммит MOS-1 перенесён (cherry-pick) с ветки 13.10 на master после мержа #43. Конфликтов нет.
- Pytest локально (без Postgres): 310 passed, 4 skipped, 29 subtests. Лог — в описании PR; Postgres-ветка проверяется в CI.
- Не мержить: мерж делает человек в дневное окно. Порядок критического пути: #52 → #53 → #55, затем #54, #56, #57.
- Не проверено: реальный вход через Google (в тестах замокан); значения `GOOGLE_CLIENT_ID/SECRET`, `SESSION_SECRET`, `AUTH_ALLOWLIST` в Render не задавались.

## S1 · утечка примерного текста гипотез в разбор (Claude, 14.10)
- Ветка `claude/s1-hypothesis-template-leak`, draft PR; Refs #36. Найдено на живой синтетической сессии VC-994: в сценарии A поле «Новая гипотеза» было заполнено примером про «Гемабанк», текст ушёл на сервер и попал в слот business_model.pricing как источник основателя.
- Код: `templates/index.html` — примерный текст в полях гипотез заменён подсказкой (placeholder); `app.py` — гипотеза сохраняется только в своём сценарии (A — текущая, B — новая, C — обе).
- Тест: `tests/test_hypothesis_mode_leak.py`. Полный прогон: 236 passed, 5 skipped, 1 warning, 29 subtests passed in 24.73s
- Вне кода (на сервисе startup-trainer-pilot-v1, 14.10): добавлены GUNICORN_CMD_ARGS=--timeout 180 (финальный отчёт падал по таймауту 30 с) и PUBLIC_BASE_URL. Не сделано: FLASK_SECRET_KEY не задан — после каждого перезапуска сессии в браузере теряют доступ (401); это секрет, задаёт MOS Tech.

## S1 · единая шкала E: ответ анкеты = E1 (Claude, 14.10)
- Ветка `claude/s1-form-e1-scale`, draft PR; Refs #36. Решение Бориса 14.10: «анкета = E1», как в тренажёре (канон CORE: E0 — нет данных, E1 — заявление основателя).
- Код: `s1_intake/methodology.yaml` — `source_level_cap.form: 0 → 1`, описания E0/E1/E2 приведены к канону; `prompts/01_classify.md` — определения E0/E1.
- E3 по-прежнему только с документом третьей стороны (`evidence`), FACT — только подпись человека. Утверждение, чей единственный источник — несуществующий файл, остаётся E0.
- Тесты: `test_phantom_evidence_removed` — C03 после снятия фантомного evidence = E1 (анкета); новый `test_form_answer_is_e1_like_trainer_and_never_e3`. Golden `hq035_*` пересчитаны (`python tests/hq016_cases.py hq035`): готовность it_wellness 3→6, lab_service 3→5, no_gate 4→6. Полный прогон: 235 passed, 0 failed.

## 2026-10-14 · MOS Tech
- Merged master into the #62 branch (MOS-2): HANDOFF.md conflict, both sections kept. No code changes.

## MOS-3 импорт-CLI из CSV (Claude, 14.10, Refs #54)
- Ветка `claude/issue-54-20261014-1648` → master, draft PR. Добавлено: `scripts/import_dealflow.py`, `dealflow/importer.py`, `docs/IMPORT_FORMAT.md`, `tests/test_mos3_import.py`, синтетические CSV в `tests/fixtures/import/`, правила в `.gitignore` (`imports/*.csv|json|md`, `import_report*`).
- Dry-run по умолчанию (БД не трогается, подключение не нужно); `--apply` — одна транзакция, upsert startups/documents, смена статуса с reason=`import`. Дубли, конфликты, ID на проверке и ID без источника статуса не пишутся.
- Тесты: 276 passed (Postgres-тесты выполнены). Лог — в описании PR.
- Допущение (нужно подтвердить): реальных заголовков CSV у меня нет — набор колонок и синонимы в `docs/IMPORT_FORMAT.md` выбраны мной. Книги статуса не дают, ID только из книг остаются в отчёте.
- Не сделано: portfolio_companies и cabinets при импорте не создаются (это #55).

## MOS-3 · merge master в ветку #54 (Claude, 15.10, Refs #54)
- В `claude/issue-54-20261014-1648` влит `origin/master` (merge, не rebase; +6 коммитов: S1 #66/#67 и др.). Конфликтов нет, кода импорта не менял.
- Полный прогон после merge: 279 passed, 1 warning, 29 subtests passed, 0 skipped (Postgres-тесты выполнены). Лог — в описании PR #72.
- Ничего не мержил в master. Ревью и мерж — MOS Tech; ready-to-merge 20.10.2026.

## MOS-1 #52 · синхронизация с master 5dfc808 (Claude, 22.10)
- Поручение Штаба 22.10 (ребейз). Сделан merge master в ветку (не rebase: история ветки уже на PR, force-push запрещён правилами). Конфликты: HANDOFF.md (оставлены обе части), app.py (импорты auth + s3_bp).
- Поведение MOS-1 сохранено (Google OAuth + allowlist, owner/fund_staff, scope, audit_log, CSRF). Новое из master (S1: шкала E, утечка гипотез; S3 `/s3/*`; MOS-2 dealflow) не тронуто.
- Новые маршруты S3 попали под RBAC: `create_brief/get_status/review/issue_json/issue_md` — owner/fund_staff + внешний Bearer; `sign` — owner + персональный Bearer из `S1_APPROVERS` (G-12 не ослаблен); `sign_screen` — только вошедшим.
- Pytest локально (без Postgres): 370 passed, 4 skipped, 29 subtests. Лог — в описании PR.
- Не проверено: реальный Google OAuth, Postgres-ветка (CI).

## 2026-10-16 · MOS Tech · #71
- Д-93 в CLAUDE.md и docs/STATE.md приведён к Д-92/Д-42 по решению PM: мерж — MOS Tech после зелёного CI и PASS не-автора; Claude в master не мержит.

## MOS-3 · ревью Р-4, merge master (Claude, 22.10, Refs #54)
- В `claude/issue-54-20261014-1648` влит `origin/master` (merge). Конфликт только HANDOFF.md — оставлены обе секции.
- По ревью: (1) стартап, созданный не импортом, со статусом, отличным от CSV, не меняется — `ApplyResult.conflicts` + раздел КОНФЛИКТ в отчёте apply; (2) ID с нераспознанной ссылкой блокируется (`bad_link_ids`, VC-005 больше не пишется); (3) тесты: CSV с BOM/CRLF, файл только с заголовком (3 источника), PG-тест на конфликт.
- Тесты: 286 passed, 1 warning, 29 subtests passed, 0 skipped (Postgres выполнен). Лог — в описании PR / комментарии.
- Не мержил, в master не пушил. Реальные заголовки CSV по-прежнему допущение.

## MOS-1 #52 · правки по ревью Р-4 (Claude, 22.10)
- Merge master (63f9000), конфликт только HANDOFF.md.
- (1) `auth.current_user`: на каждом запросе email сессии сверяется с AUTH_ALLOWLIST; нет — сессия сбрасывается, audit `removed_from_allowlist`, ответ 403 (следующий запрос — 401).
- (2) `auth.client_ip` = `request.remote_addr`; за прокси — `ProxyFix` по `TRUSTED_PROXY_COUNT` (по умолчанию 0; на Render задать 1 — это переменная окружения, делает MOS Tech).
- (3) `APP_ENV=staging|production` без `SESSION_SECRET` — `RuntimeError` при импорте приложения.
- (4) `/admin/audit?limit=`: максимум 500, не число — 400.
- Тесты в `tests/test_auth.py`. pytest: 376 passed, 4 skipped, 1 warning, 29 subtests passed in 20.89s.

## MOS-3 · merge master с MOS-1 #61 (Claude, 22.10, Refs #54)
- В `claude/issue-54-20261014-1648` влит `origin/master` (6bcf282, MOS-1) merge-коммитом, без rebase и force-push. Конфликт только HANDOFF.md — оставлены обе части. Код импорта и код MOS-1 не менялись.
- Пересечения таблиц нет: MOS-1 владеет `users`, `roles`, `role_grants`, `audit_log` (001); импорт пишет только `startups`, `deal_status_history`, `documents` (003). Дублирования нет.
- pytest локально: 398 passed, 4 skipped, 1 warning, 29 subtests passed in 29.31s. 4 skipped — Postgres-тесты (БД в этом запуске нет), проверяет CI.
- Не мержил, в master не пушил.
