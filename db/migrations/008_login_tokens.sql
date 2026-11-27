-- #114 · запасной вход по одноразовой ссылке (флаг AUTH_INVITE_FALLBACK=1). Идемпотентно.
-- Хранится только SHA-256 токена; сам токен показывается один раз при выдаче. Откат: down/008_login_tokens.down.sql
CREATE TABLE IF NOT EXISTS login_tokens (
    id          BIGSERIAL PRIMARY KEY,
    token_hash  TEXT NOT NULL UNIQUE,
    email       TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at  TIMESTAMPTZ NOT NULL,
    used_at     TIMESTAMPTZ
);
