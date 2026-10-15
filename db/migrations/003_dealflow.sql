-- MOS-2 · Схема dealflow и портфеля: стартапы, история статусов, портфель, кабинеты, документы.
-- Идемпотентно. Только синтетические данные в тестах; LP-метрик (TVPI/DPI/RVPI/fee/carry) нет (Д-69).
DO $$ BEGIN
    CREATE TYPE deal_status AS ENUM
        ('NOT_SCREENED', 'SCREENING', 'ACTIVE', 'DD', 'IC', 'INVESTED', 'PASS', 'ON_HOLD');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

CREATE TABLE IF NOT EXISTS startups (
    id             BIGSERIAL PRIMARY KEY,
    vc_id          TEXT NOT NULL UNIQUE,            -- напр. VC-130 / IM-006
    name           TEXT NOT NULL,
    sector         TEXT,
    stage          TEXT,
    current_status deal_status NOT NULL DEFAULT 'NOT_SCREENED',
    source         TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Append-only: UPDATE/DELETE запрещены триггером.
CREATE TABLE IF NOT EXISTS deal_status_history (
    id          BIGSERIAL PRIMARY KEY,
    startup_id  BIGINT NOT NULL REFERENCES startups(id),
    from_status deal_status,                         -- NULL для первичной записи
    to_status   deal_status NOT NULL,
    reason      TEXT,
    changed_by  BIGINT REFERENCES users(id),
    changed_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS deal_status_history_startup_idx ON deal_status_history (startup_id, changed_at);

CREATE OR REPLACE FUNCTION deal_status_history_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'deal_status_history is append-only';
END $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS deal_status_history_no_change ON deal_status_history;
CREATE TRIGGER deal_status_history_no_change
    BEFORE UPDATE OR DELETE ON deal_status_history
    FOR EACH ROW EXECUTE FUNCTION deal_status_history_append_only();

CREATE TABLE IF NOT EXISTS portfolio_companies (
    id          BIGSERIAL PRIMARY KEY,
    startup_id  BIGINT NOT NULL UNIQUE REFERENCES startups(id),
    invested_at DATE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    -- поля паспорта (уровень компании, ILPA/IPEV-lite) добавит portfolio-issue
);

-- Один кабинет на стартап. Доступ — через role_grants.scope (= vc_id стартапа).
CREATE TABLE IF NOT EXISTS cabinets (
    id            BIGSERIAL PRIMARY KEY,
    startup_id    BIGINT NOT NULL UNIQUE REFERENCES startups(id),
    owner_user_id BIGINT REFERENCES users(id),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Только идентификатор файла на Drive: ни содержимого, ни публичных ссылок.
CREATE TABLE IF NOT EXISTS documents (
    id            BIGSERIAL PRIMARY KEY,
    startup_id    BIGINT NOT NULL REFERENCES startups(id),
    kind          TEXT NOT NULL,
    title         TEXT NOT NULL,
    drive_file_id TEXT NOT NULL CHECK (drive_file_id ~ '^[A-Za-z0-9_-]+$'),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS documents_startup_idx ON documents (startup_id);
