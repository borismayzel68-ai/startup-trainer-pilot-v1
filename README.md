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
A8 sends one `TRAINER_COMPLETED` JSON event when a session first becomes
complete. The attempt is recorded before the network call. A8 does not retry a
failed call; retry, receiver-side deduplication, and dead-letter handling belong
to A9. API keys, raw deck text, and patient identifiers are excluded.

## Run
`pip install -r requirements.txt`
`python app.py`

## Render
Build: `pip install -r requirements.txt`
Start: `gunicorn app:app`
