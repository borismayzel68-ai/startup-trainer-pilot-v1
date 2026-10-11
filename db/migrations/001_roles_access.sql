-- HQ-018 · ЧЕРНОВИК миграции: только роли и доступ. Полей тарифа/подписки НЕТ (нужно явное «да» владельца).
CREATE TABLE IF NOT EXISTS users (
    id           BIGSERIAL PRIMARY KEY,
    email        TEXT UNIQUE NOT NULL,
    display_name TEXT NOT NULL,
    is_synthetic BOOLEAN NOT NULL DEFAULT FALSE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS roles (
    code  TEXT PRIMARY KEY,
    label TEXT NOT NULL
);
INSERT INTO roles (code, label) VALUES
    ('founder',  'Основатель'),
    ('investor', 'Фонд / инвестор'),
    ('expert',   'Эксперт'),
    ('signer',   'Владелец / подписант'),
    ('clinic',   'Клиника'),
    ('syndicator','Синдикатор')
ON CONFLICT (code) DO NOTHING;
CREATE TABLE IF NOT EXISTS role_grants (
    id         BIGSERIAL PRIMARY KEY,
    user_id    BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role_code  TEXT   NOT NULL REFERENCES roles(code),
    scope      TEXT   NOT NULL DEFAULT '*',   -- '*' или ID кейса
    granted_by BIGINT REFERENCES users(id),
    granted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at TIMESTAMPTZ,
    UNIQUE (user_id, role_code, scope)
);
