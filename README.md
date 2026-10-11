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

## Как запустить тесты
Тесты лежат в каталоге `tests/`. Из корня репозитория:
`pip install -r requirements.txt pytest`
`pytest`

## Render
Build: `pip install -r requirements.txt`
Start: `gunicorn app:app`
