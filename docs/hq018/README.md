# HQ-018 · E-1 — локальная тестовая среда (docker compose + Postgres)

Только синтетические данные. Без реальных заявок, прод-секретов и оплат. Внешних вызовов LLM нет (S1_LLM_PROVIDER=mock).

## Запуск из чистого checkout
```
docker compose up --build
```
- `db` — postgres:16-alpine, порт 127.0.0.1:5433.
- `app` — при старте: `scripts/hq018_migrate.py` (существующие миграции приложения: `trainer_sessions`,
  `s1_readiness_jobs` + черновики `db/migrations/*.sql`) → `scripts/hq018_seed.py` (синтетика) → gunicorn :10000.
- Проверка вебхука Tilda (имитация):
  `curl -X POST -H "X-Webhook-Key: synthetic-e1-webhook-key" -d "tranid=synthetic-1&project_name=Synth" http://127.0.0.1:10000/s1/webhooks/tilda`
  Повтор того же `tranid` не создаёт вторую запись. `test=test` → 200 без записи.

## Тесты против Postgres
```
cp .env.example .env   # значения — только локальные заглушки
HQ018_PG_URL=postgresql://trainer:local-e1-only@127.0.0.1:5433/trainer_e1 python -m pytest -rA -v
```
`tests/test_hq018_postgres.py` без `HQ018_PG_URL` пропускается (skip), остальные тесты не меняются.

## Черновики миграций (только роли/доступ и очередь)
- `db/migrations/001_roles_access.sql` — `users`, `roles` (6 ролей), `role_grants`. Полей тарифа/подписки нет.
- `db/migrations/002_jobs_queue.sql` — `jobs`: только `tilda_intake` и `final_analysis`, `UNIQUE(kind, dedup_key)`.
