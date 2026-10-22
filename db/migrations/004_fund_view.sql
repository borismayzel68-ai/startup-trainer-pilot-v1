-- MOS-5 (#56) · вид фонда: Decision Snapshot, решения IC. Роли owner/fund_staff, users, role_grants и audit_log — в 001 (MOS-1).
-- Идемпотентно. LP-метрик (TVPI/DPI/RVPI/fee/carry) нет (Д-69).
-- Один snapshot на стартап. fact_* заполняются только подписью владельца (см. fund_view/service.py).
CREATE TABLE IF NOT EXISTS decision_snapshots (
    id               BIGSERIAL PRIMARY KEY,
    startup_id       BIGINT NOT NULL UNIQUE REFERENCES startups(id),
    thesis           TEXT NOT NULL DEFAULT '',
    deal_type        TEXT NOT NULL DEFAULT '',
    buyer            TEXT NOT NULL DEFAULT '',   -- потенциальный покупатель/инвестор
    buyer_when       TEXT NOT NULL DEFAULT '',   -- когда купит (Buyer-first: кто и когда)
    trigger_event    TEXT NOT NULL DEFAULT '',
    asf_engagement   TEXT NOT NULL DEFAULT 'NOT_DECIDED'
                     CHECK (asf_engagement IN ('NOT_DECIDED', 'FIXED', 'DECLINED')),
    engagement_by    BIGINT REFERENCES users(id),
    engagement_at    TIMESTAMPTZ,
    key_risks        TEXT NOT NULL DEFAULT '',
    next_action      TEXT NOT NULL DEFAULT '',
    evidence_status  TEXT NOT NULL DEFAULT 'hypothesis'
                     CHECK (evidence_status IN ('hypothesis', 'fact')),
    fact_signed_by   BIGINT REFERENCES users(id),
    fact_signed_at   TIMESTAMPTZ,
    updated_by       BIGINT REFERENCES users(id),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Append-only история квартальных решений IC.
CREATE TABLE IF NOT EXISTS ic_decisions (
    id         BIGSERIAL PRIMARY KEY,
    startup_id BIGINT NOT NULL REFERENCES startups(id),
    quarter    TEXT NOT NULL CHECK (quarter ~ '^[0-9]{4}-Q[1-4]$'),
    decision   TEXT NOT NULL CHECK (decision IN ('GO', 'PAUSE', 'STOP')),
    reason     TEXT NOT NULL,
    decided_by BIGINT REFERENCES users(id),
    decided_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ic_decisions_startup_idx ON ic_decisions (startup_id, decided_at);

CREATE OR REPLACE FUNCTION fund_view_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
END $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS ic_decisions_no_change ON ic_decisions;
CREATE TRIGGER ic_decisions_no_change BEFORE UPDATE OR DELETE ON ic_decisions
    FOR EACH ROW EXECUTE FUNCTION fund_view_append_only();
