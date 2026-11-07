-- Откат 007_access_invites (FEAT-001 T-002). Применять вручную, только после снимка БД:
-- приглашения и отметки блокировки будут удалены. Доступ по AUTH_ALLOWLIST (env) продолжит работать.
-- Идемпотентно. Папка down/ не читается hq018_migrate.py (glob только db/migrations/*.sql).
DROP INDEX IF EXISTS access_invites_email_idx;
DROP TABLE IF EXISTS access_invites;
ALTER TABLE users DROP COLUMN IF EXISTS blocked_at;
