-- 152-ФЗ · журнал согласия на обработку персональных данных.
-- Черновик текста: версия v0-draft (файл pdn_consent/text_v0-draft.txt). Юрист ещё не проверял.
-- Номер 006. Идёт после 005_fund_view: PR fix/migration-005-fund-view переименовывает
-- 004_fund_view → 005. Если на master уже есть 005, этот файл всё равно 006.
-- От объектов 004_portfolio_passport и 005_fund_view не зависит.
-- Идемпотентно. Колонку revoked_at заполняет приложение; здесь только схема.
CREATE TABLE IF NOT EXISTS pdn_consents (
    id            BIGSERIAL PRIMARY KEY,
    user_email    TEXT NOT NULL,
    text_version  TEXT NOT NULL,
    consented_at  TIMESTAMPTZ NOT NULL,
    ip            TEXT,
    revoked_at    TIMESTAMPTZ,
    flow          TEXT NOT NULL CHECK (flow IN ('s1_intake', 'investor_login'))
);
CREATE INDEX IF NOT EXISTS pdn_consents_email_idx ON pdn_consents (user_email);
