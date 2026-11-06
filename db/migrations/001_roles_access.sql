-- HQ-018 / MOS-1 (#52) · миграция ролей и доступа. Идемпотентна (IF NOT EXISTS / ON CONFLICT).
-- Полей тарифа/подписки НЕТ (нужно явное «да» владельца).
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
    ('syndicator','Синдикатор'),
    ('owner',      'Владелец фонда'),
    ('fund_staff', 'Сотрудник фонда'),
    ('buyer',      'Покупатель технологии')  -- R10: роль без маршрутов (ТЗ «Роли и доступ», §5.6а)
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
CREATE INDEX IF NOT EXISTS role_grants_user_idx ON role_grants (user_id) WHERE revoked_at IS NULL;
-- Журнал доступа: кто, когда, действие, объект, результат (allow/deny), IP.
CREATE TABLE IF NOT EXISTS audit_log (
    id         BIGSERIAL PRIMARY KEY,
    at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    actor      TEXT,                       -- email (NULL для анонимных отказов)
    action     TEXT NOT NULL,              -- login | logout | view | change | ...
    object     TEXT,                       -- маршрут / ID кейса
    result     TEXT NOT NULL CHECK (result IN ('allow', 'deny')),
    ip         TEXT,
    detail     TEXT
);
CREATE INDEX IF NOT EXISTS audit_log_at_idx ON audit_log (at);
