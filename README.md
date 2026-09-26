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

## Render
Build: `pip install -r requirements.txt`
Start: `gunicorn app:app`
Worker: `python delivery_worker.py`
