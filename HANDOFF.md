# HANDOFF (индекс)

Подробный handoff ведётся по задачам в `docs/handoff/<issue>.md`; здесь — только ссылки. При каждом коммите обновляется файл своей задачи (и эта таблица, если появилась новая задача).

| Issue | Тема | Файл |
|---|---|---|
| #22 | HQ-036 · линия tilda (A9 + S1) | [docs/handoff/22.md](docs/handoff/22.md) |
| #33 | Чистка __pycache__ | [docs/handoff/33.md](docs/handoff/33.md) |
| #36 | P0 #36 · S1 (шкала E, пакет основателю) | [docs/handoff/36.md](docs/handoff/36.md) |
| #52, #54 | MOS-1 Auth+RBAC, MOS-3 импорт — полный лог HANDOFF.md из master 34d43f4 (перенесён без изменений) | [docs/handoff/master-log-2026-10-22.md](docs/handoff/master-log-2026-10-22.md) |
| #53 | MOS-2 · схема dealflow | [docs/handoff/53.md](docs/handoff/53.md) |
| #57 | MOS-6 · staging | [docs/handoff/57.md](docs/handoff/57.md) |
| #59 | HQ-023 · S3 Эксперт (БП-07) | [docs/handoff/59.md](docs/handoff/59.md) |

## Слияние master (15adbca, LSM v1 #75) в ветку MOS-6 (Claude, 23.10, Refs #57)
- Merge master обычным merge-коммитом, без rebase и force-push; конфликт только HANDOFF.md, код не менялся. Разделы LSM v1 из master — ниже без изменений.

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

Остальной лог master (MOS-1, S3, S1, MOS-2, MOS-3 и др., строки 30–172 прежнего HANDOFF.md master) без изменений лежит в `docs/handoff/master-log-2026-10-22.md`.

## Слияние master (#80, #34; 23.10 11:29) в ветку MOS-6 (Claude, 23.10, Refs #57)
- Merge master обычным merge-коммитом, без rebase и force-push; конфликт только HANDOFF.md. Хвост лога master (в т. ч. правки HQ-035 из #34) — в docs/handoff/master-log-2026-10-22.md, записи обеих сторон сохранены.

## Правки по ревью PR #82 (Claude, 23.10, Refs #57)
- PUBLIC_BASE_URL обязателен на staging, ограничен цикл ожидания бэкапа, тест bash -n, CLAUDE.md = master; подробности — docs/handoff/57.md.
