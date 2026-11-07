-- FEAT-001 T-002 (#114) · приглашения в БД и блокировка пользователя. Идемпотентно.
-- tenant_id заложен сразу (TD-007); значение по умолчанию — единственный арендатор пилота.
-- Остальные таблицы tenant_id не получают (вне объёма первого выпуска, Q-007).
-- Откат: db/migrations/down/007_access_invites.down.sql
CREATE TABLE IF NOT EXISTS access_invites (
    id          BIGSERIAL PRIMARY KEY,
    tenant_id   TEXT NOT NULL DEFAULT 'pilot',
    email       TEXT NOT NULL,
    role        TEXT NOT NULL REFERENCES roles(code),
    scope       TEXT NOT NULL DEFAULT '*',
    invited_by  TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS access_invites_email_idx ON access_invites (lower(email));
ALTER TABLE users ADD COLUMN IF NOT EXISTS blocked_at TIMESTAMPTZ;
