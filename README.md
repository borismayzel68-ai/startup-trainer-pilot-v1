# Startup Trainer — External Pilot v1

Private founder-facing pilot. Not an investment decision.

## Env
- `OPENAI_API_KEY`
- `OPENAI_MODEL` (default: `gpt-5.6`)
- `INVITE_CODE`
- `ADMIN_KEY`
- `SESSION_DATABASE_URL` (or Render-provided `DATABASE_URL`)
- `SESSION_RETENTION_DAYS` (default: `30`)
- `TRAINER_EVENT_WEBHOOK` (Make webhook used by the A9 delivery worker)
- `COMPLETION_RECEIVER_SECRET` (shared secret for Make to call the A9 gate)
- `COMPLETION_GATE_LEASE_SECONDS` (default: `300`)
- `DELIVERY_POLL_SECONDS` (default: `2`)
- `DELIVERY_CLAIM_TIMEOUT_SECONDS` (default: `60`)
- `DELIVERY_MAX_ATTEMPTS` (default: `8`)
- `DELIVERY_RETRY_BASE_SECONDS` (default: `5`)
- `DELIVERY_RETRY_MAX_SECONDS` (default: `300`)
- `DELIVERY_PURGE_INTERVAL_SECONDS` (default: `3600`)
- `WEBHOOK_TIMEOUT_SECONDS` (default: `10`)

## Session storage
A7 persists the complete session state, including the input manifest, Q&A,
report, feedback, and event status. Local development and tests can use
`sqlite:///trainer_sessions.db`. Render must use PostgreSQL so sessions survive
service restarts. `DELETE /api/session/<id>` removes the persisted session.

## Completion event
A8 atomically writes a completed session and one sanitized `TRAINER_COMPLETED`
event to the same database transaction. The web request performs no network I/O.
A9's `delivery_worker.py` claims outbox rows across processes, sends them to Make,
recovers stale claims after restart, retries with exponential backoff and the same
deterministic event ID/key, and moves exhausted items to `dead`. The event excludes
free-text founder answers, evidence quotes, report text, missing-document
names, meeting questions, deck filenames/raw text, API keys, and patient-level
data. Only structural status tokens, counts, safe deck metadata, and the canonical
project/founder identity fields are sent.

## Completion receiver
A9 accepts `TRAINER_COMPLETED` at `POST /api/completion-events`. The
`X-Completion-Secret` header is mandatory. `session_id` must be a canonical UUID;
the idempotency key and UUIDv5 event ID are derived from it and must match exactly.
The endpoint rejects bodies over 64 KiB. PostgreSQL or SQLite records one technical
receipt per key. A receipt becomes a duplicate only after Make confirms that every
downstream action completed. An unfinished processing lease expires and can be
acquired by a retry. The receipt stores only identifiers, lease state and
timestamps, not the completion payload or any free text. Delivered/dead outbox
payloads are erased; session deletion and retention remove the session, outbox
row and receipt together.

The real Make path must be ordered as follows:
1. Custom webhook receives the sanitized outbox event.
2. HTTP calls this service's `/api/completion-events` with the original JSON,
   `Idempotency-Key`, and `X-Completion-Secret`.
3. Downstream actions run only when the gate returns `status=accepted`; Make
   keeps the returned `attempt_token`.
4. After the last downstream action succeeds, HTTP calls
   `/api/completion-events/complete` with the original identifiers and the
   `attempt_token`.
5. Every changing downstream action creates or updates its record by the
   deterministic `event_id`; it must never append an uncontrolled duplicate.
6. `status=duplicate` ends the route successfully without downstream actions.
7. Any other gate or completion response is an error, so Make returns an error
   to the worker and the event remains eligible for retry.
8. Make sends a successful webhook response only after step 4 succeeds.

This gives at-least-once transport without silent loss: a failed downstream route
is retried after its lease expires, while a completed route returns `duplicate`.
Downstream actions must also use the deterministic event ID as their idempotency
key. The worker accepts a 2xx Make response only when the same database receipt
is already `completed`; an early default `200 Accepted` remains retryable.
Literal network exactly-once is not claimed. A9 is not
complete and this branch must not be merged or deployed until that Make route and
the Render worker are wired and the end-to-end restart/retry test passes.

## Run
`pip install -r requirements.txt`
`python app.py`

## Запуск процессов
Build: `pip install -r requirements.txt`
Start: `gunicorn app:app`
Worker: `python delivery_worker.py`

## Развёртывание в Yandex Cloud (MOS-6, закрытый staging, ru-central1)
Только синтетические данные. **Выпуск на живых пользователей — только после явного утверждения Бориса.**
Рендер (Render) больше не используется. Всё в регионе РФ: Managed PostgreSQL и Serverless Container в `ru-central1`.

### 0. Секреты и роли (точный список, значений нет)
**Yandex Lockbox** — один секрет `mos-staging` с ключами: `DATABASE_URL`, `SESSION_SECRET`, `GOOGLE_CLIENT_ID`,
`GOOGLE_CLIENT_SECRET`, `AUTH_ALLOWLIST`; опционально `OPENAI_API_KEY`. Не секрет, обычная env ревизии: `AUTH_REDIRECT_URI`.
**GitHub Environment `staging`** (только для deploy-workflow, не для `tests.yml`): секрет `YC_SA_KEY_JSON` (авторизованный ключ
SA деплоя); идентификаторы `YC_FOLDER_ID`, `YC_REGISTRY_ID`, `YC_NETWORK_ID`, `YC_RUNTIME_SA_ID`, `YC_PG_CLUSTER_ID`,
`LOCKBOX_SECRET_ID`, `AUTH_REDIRECT_URI`.

| Сервисный аккаунт | Роли |
|---|---|
| `mos-runtime` (рантайм контейнеров) | `lockbox.payloadViewer` (на секрет `mos-staging`), `container-registry.images.puller` (на реестр) |
| `mos-deploy` (ключ в GitHub) | `serverless-containers.editor`, `serverless.containers.invoker` (вызов контейнера миграций), `container-registry.images.pusher`, `iam.serviceAccounts.user` (на `mos-runtime`), `vpc.user`, `managed-postgresql.editor` (резервная копия), `lockbox.viewer` |

Названия ролей сверить в консоли IAM: проверка на живом облаке не выполнялась.

### 1. Развёртывание, шаг за шагом
1. **Сеть.** Создать VPC и подсеть в `ru-central1-*`, **NAT-шлюз** и таблицу маршрутов (контейнеру в VPC нужен выход к Google OAuth).
2. **Managed PostgreSQL 16** в той же сети, `ru-central1`, **без публичного адреса**, включить защиту от удаления.
   Автоматические резервные копии: `yc managed-postgresql cluster update <кластер> --backup-window-start 02:00 --backup-retain-period-days 7`
   (по умолчанию включены; проверить окно и срок хранения; PITR — в пределах срока хранения).
3. **Container Registry** `mos` и два сервисных аккаунта из п. 0 с ролями.
4. **Lockbox**: создать секрет `mos-staging` с ключами из п. 0 (значения вводить в консоли, не в командной строке/истории shell).
   `DATABASE_URL=postgresql://<user>:<pass>@<rw-host>.mdb.yandexcloud.net:6432/<db>?sslmode=require`;
   `SESSION_SECRET` — `openssl rand -hex 32`; `AUTH_ALLOWLIST` — email участников пилота через запятую.
5. **Google OAuth client** (Web): redirect URI `https://<custom-domain>/auth/callback`. Сам вход — модуль `auth` из MOS-1 (#61); в staging-коде своего OAuth нет. `AUTH_ALLOWLIST` — в формате #61 `email:role[:scope]`.
6. **GitHub**: создать Environment `staging` (обязательный ревьюер = MOS Tech), добавить секрет и идентификаторы из п. 0,
   workflow лежит как предложение `docs/deploy/yc-deploy.yml.proposed` (расширение `.proposed` — он **не активен**; Claude не имеет права менять `.github/workflows`). Переносить и запускать его — решение MOS Tech, после ревью.
7. **Деплой** (ручной запуск workflow или `deploy/yc/deploy.sh` у оператора). Порядок шагов скрипта:
   образ (секреты в ревизию — флагами `--secret environment-variable=…,id=<lockbox>,version-id=…,key=…`, значений в командах нет) → проверка миграций → **резервная копия, если есть миграция, меняющая/удаляющая данные** → миграции (приватный контейнер
   `mos-staging-migrate`, не CI, не старт web) → web-ревизия `mos-staging` → проверка `/health`.
8. **Проверка:** `/health` → 200; `/` без входа → редирект на `/auth/login`; `/api/*`, `/s1/*` → 401; в ответах `X-Robots-Tag: noindex`.
9. **Seed (по желанию, только синтетика):** `python scripts/seed_synthetic.py` из среды с доступом к БД; на несинтетических данных скрипт откажется работать.
   **Реальный импорт на staging запрещён до одобрения Бориса.**
10. **Домен `<custom-domain>`:** Serverless Containers → API Gateway/Certificate Manager: выпустить сертификат Let's Encrypt, `CNAME` у регистратора,
    HTTPS. Обновить `AUTH_REDIRECT_URI` и redirect URI в Google, повторить деплой.

### 2. Безопасность и CI
- Доступ только по сессии MOS-1 (#61: вход Google + `AUTH_ALLOWLIST`, RBAC); `staging_guard` — страховочный замок поверх неё: открыты только `/health`, `/robots.txt`, `/auth/*`. Если модуля `auth` (#61) нет, при `APP_ENV=staging` приложение не стартует. noindex: заголовок, meta, `robots.txt`.
- Только синтетика: `STAGING_SYNTHETIC_ONLY=true` (по умолчанию); при старте БД проверяется на пользователей без флага `is_synthetic` (признак — колонка, не домен email; участники `AUTH_ALLOWLIST`, входящие через #61, исключены). Seed: `python scripts/seed_synthetic.py`.
- `tests.yml` работает только со своим Postgres 16 (service container) и не содержит секретов облака. Деплой — отдельный workflow.
- Миграции не запускаются из CI и при старте web. Контейнер миграций приватный (вызов с IAM-токеном), БД без публичного адреса.

### 3. Снимок перед опасной миграцией
`scripts/migration_guard.py` ищет в `db/migrations` `DROP`, `TRUNCATE`, `DELETE FROM`, `UPDATE`, `ALTER … TYPE/RENAME/SET NOT NULL`
(`ON CONFLICT … DO UPDATE` и `ON DELETE CASCADE` не считаются). Если такие есть, `deploy.sh` делает
`yc managed-postgresql cluster backup`, ждёт статус `DONE`, и только затем вызывает миграции с `snapshot_id`. Без `snapshot_id` контейнер миграций
отвечает 409 и ничего не применяет. Сейчас все миграции только добавляют, шаг не срабатывает.
Вручную: `yc managed-postgresql cluster backup <кластер>`.

### 4. Откат
- **Код:** `deploy/yc/rollback.sh <тег-образа>` (теги: `yc container image list`) создаёт ревизию на прежнем образе.
- **БД:** `yc managed-postgresql cluster restore --backup-id <id> --name mos-staging-restored …` (из копии) либо PITR на момент до миграции
  (`--time`). Новый кластер → поправить `DATABASE_URL` в Lockbox → деплой. Старый кластер не удалять до проверки.
- **Аварийно закрыть доступ:** `yc serverless container deny-unauthenticated-invoke --name mos-staging`.

---

## S1 · БП-01 «Стартап до раунда» — приём заявок с Tilda (`s1_intake/`)

Второй вход в тот же сервис: фаундер заполняет форму на Tilda (без чата с ИИ и без VPN),
сервер собирает **Readiness Pack** — Founder Readiness Report (фаундеру, после одобрения
аналитиком) и Internal Analyst Brief (фонду, вердикт PASS / HOLD / NEXT).

```
Tilda ─POST─▶ /s1/webhooks/tilda ─200 "ok"─▶ фоновый поток:
  parsers.py   скачивание (https + allowlist + лимиты) → PDF (pypdf) / XLSX (openpyxl)
  evaluator.py маршрут routing_v2 → чек-лист из SLOT_CATALOG → LLM: классификация
               FACT / FOUNDER_CLAIM / UNKNOWN / CONFLICT + уровень E0–E4
  scoring.py   ПРАВИЛА КОДА: потолок E по источнику, конфликты, пробелы, readiness, вердикт
  evaluator.py LLM: Марк (Bull) → Анна (Bear, атакует Марка) → синтез (Proof Points, 30/60/90)
  formatter.py Readiness Pack: JSON + HTML (в БД), PDF — при скачивании
```

**LLM извлекает, код решает.** Уровень доказательности, категория, readiness и вердикт
пересчитываются детерминированно. Потолки: анкета E0, дек E1, финмодель E2 (без формул — E1),
документ третьей стороны E3; E4 (аудит, ЕГРЮЛ/ФИПС) ставит только аналитик. Ссылки модели
на несуществующие документы, слоты вне маршрута и выдуманные claim-id отбрасываются.

**Чек-лист = канон тренажёра.** Модули выбирает `routing_v2.route()` по юрисдикции и классу
продукта, слоты берутся из `question_engine_v2.SLOT_CATALOG`; плюс слоты готовности к раунду
(burn/runway, условия раунда, юрлицо). Группировка в 7 блоков Deal Genome, веса, критичные
слоты и пороги — в `s1_intake/methodology.yaml` (черновик, требует утверждения и калибровки).
Промпты — `s1_intake/prompts/*.md`, правятся без кода.

### Форма Tilda
«Имя переменной» полей: `company`, `name`, `email`, `consent` (обязательный чекбокс),
`project_id` (опц., VC-ID), `jurisdiction` (RF/EAEU/EU/US/GCC/MULTI), `target_markets`,
`product_class` (как в тренажёре), `intended_use`, вопросы `q_*` (см. methodology.yaml),
файлы `pitch_deck` (PDF), `fin_model` (XLSX), `evidence_files` (договоры, акты, выписки, LOI).
Webhook: `https://<render-домен>/s1/webhooks/tilda`, API-ключ в заголовке `X-Webhook-Key`.
**Отправить тестовую заявку и проверить домен ссылок на файлы** → `S1_FILE_HOST_ALLOWLIST`.

### API аналитика (`Authorization: Bearer $S1_ADMIN_TOKEN`)
| | |
|---|---|
| `GET /s1/jobs` | список заявок: статус, вердикт, readiness |
| `GET /s1/jobs/<id>` | сводка: вердикт, гейты, критичные пробелы, условие переоценки |
| `GET /s1/jobs/<id>/{analyst-brief,founder-report}.{html,pdf}`, `readiness-pack.json` | артефакты |
| `POST /s1/jobs/<id>/approve` | отправить Founder Readiness Report фаундеру — только персональный токен из `S1_APPROVERS` (общий admin-токен не подходит); подпись пишется в журнал `s1_approvals`; повторная отправка того же отчёта блокируется (409) |
| `GET /s1/jobs/<id>/approvals` | журнал подписей: кто, когда, sha256 отчёта, результат |
| `POST /s1/jobs/<id>/rerun` | пересчитать (после правки промптов/методологии; файлы из кэша) |

Статусы: `received → processing → ready_for_review → sent`; `failed` (с причиной), `rejected` (нет согласия).

### Эксплуатация
- Переменные — `.env.example`, блок S1. Без них тренажёр работает, вебхук отвечает 503.
- Хранение: та же БД (`SESSION_DATABASE_URL`/`DATABASE_URL`), таблица `s1_readiness_jobs`,
  срок `S1_RETENTION_DAYS`. Файловая система Render не используется.
- Несколько gunicorn-воркеров безопасны: заявку забирает атомарный `claim`; заявка, зависшая
  в `processing` дольше 20 минут (воркер умер), дообрабатывается при старте.
- PDF требует системных pango/harfbuzz. Если на Render их нет — `.pdf` отвечает 501,
  HTML-артефакты работают; тогда переходить на Docker-деплой.
- Тесты: `python -m unittest discover -s tests` (S1 — без сети и без OpenAI, mock-LLM).
