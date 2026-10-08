# Startup Trainer — External Pilot v1

Private founder-facing pilot. Not an investment decision.

## Env
- `OPENAI_API_KEY`
- `OPENAI_MODEL` (default: `gpt-5.6`)
- `INVITE_CODE`
- `ADMIN_KEY`
- `SESSION_DATABASE_URL` (or Render-provided `DATABASE_URL`)
- `SESSION_RETENTION_DAYS` (default: `30`)
- `TRAINER_EVENT_WEBHOOK` (HTTPS receiver for A8 completion events)
- `WEBHOOK_TIMEOUT_SECONDS` (default: `10`)

## Session storage
A7 persists the complete session state, including the input manifest, Q&A,
report, feedback, and event status. Local development and tests can use
`sqlite:///trainer_sessions.db`. Render must use PostgreSQL so sessions survive
service restarts. `DELETE /api/session/<id>` removes the persisted session.

## Completion event
A8 atomically claims a completed session and makes at most one
`TRAINER_COMPLETED` delivery attempt. The `emitting` state is persisted before
the network call and means the delivery outcome may be unknown after a crash.
A8 does not guarantee exactly-once network delivery and does not retry; recovery,
receiver-side deduplication, and dead-letter handling belong to A9. The event
excludes free-text founder answers, evidence quotes, report text, missing-document
names, meeting questions, deck filenames/raw text, API keys, and patient-level
data. Only structural status tokens, counts, safe deck metadata, and the canonical
project/founder identity fields are sent.

## Run
`pip install -r requirements.txt`
`python app.py`

## Render
Build: `pip install -r requirements.txt`
Start: `gunicorn app:app`

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
| `POST /s1/jobs/<id>/approve` | отправить Founder Readiness Report фаундеру |
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
