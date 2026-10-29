-- MOS-4 (#55): паспорт портфельной компании + история снапшотов. Идемпотентно.
-- Уровень компании (ILPA/IPEV-lite). Колонок TVPI/DPI/RVPI/fee/carry нет и не будет (Д-69).
INSERT INTO roles (code, label) VALUES
    ('owner',      'Владелец фонда'),
    ('fund_staff', 'Сотрудник фонда')
ON CONFLICT (code) DO NOTHING;

DO $$ BEGIN
    CREATE TYPE holding_vehicle AS ENUM ('fund', 'partner_nominee');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

ALTER TABLE portfolio_companies
    ADD COLUMN IF NOT EXISTS holding_vehicle holding_vehicle NOT NULL DEFAULT 'fund',
    ADD COLUMN IF NOT EXISTS runway_red_below   NUMERIC(4,1) NOT NULL DEFAULT 9  CHECK (runway_red_below   BETWEEN 6 AND 18),
    ADD COLUMN IF NOT EXISTS runway_yellow_upto NUMERIC(4,1) NOT NULL DEFAULT 12 CHECK (runway_yellow_upto BETWEEN 6 AND 18);
DO $$ BEGIN
    ALTER TABLE portfolio_companies ADD CONSTRAINT portfolio_thresholds_order CHECK (runway_red_below < runway_yellow_upto);
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- Снапшот паспорта по дате. Append-only. RAG не хранится — считается модулем portfolio.rag.
CREATE TABLE IF NOT EXISTS portfolio_passport_snapshots (
    id               BIGSERIAL PRIMARY KEY,
    company_id       BIGINT NOT NULL REFERENCES portfolio_companies(id),
    snapshot_date    DATE NOT NULL,
    cost             NUMERIC(14,2),            -- вложено
    valuation_basis  TEXT,                     -- IPEV-lite: метод
    valuation_date   DATE,                     -- IPEV-lite: дата
    round            TEXT,
    ownership_pct    NUMERIC(5,2) CHECK (ownership_pct BETWEEN 0 AND 100),
    runway_months    NUMERIC(5,1) CHECK (runway_months >= 0),
    burn             NUMERIC(14,2) CHECK (burn >= 0),       -- в месяц
    plan_burn        NUMERIC(14,2) CHECK (plan_burn >= 0),
    next_milestone_what TEXT,
    next_milestone_when DATE,
    next_milestone_who  TEXT,
    loan_income      NUMERIC(14,2),
    last_investor_update DATE,
    created_by       BIGINT REFERENCES users(id),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (company_id, snapshot_date)
);
CREATE INDEX IF NOT EXISTS portfolio_snapshots_idx ON portfolio_passport_snapshots (company_id, snapshot_date DESC);

CREATE OR REPLACE FUNCTION portfolio_snapshots_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'portfolio_passport_snapshots is append-only';
END $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS portfolio_snapshots_no_change ON portfolio_passport_snapshots;
CREATE TRIGGER portfolio_snapshots_no_change
    BEFORE UPDATE OR DELETE ON portfolio_passport_snapshots
    FOR EACH ROW EXECUTE FUNCTION portfolio_snapshots_append_only();
