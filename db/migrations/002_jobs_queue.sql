-- HQ-018 · ЧЕРНОВИК: очередь в Postgres (HQ-020 §6b/§6f). Только 2 вида задач:
-- приём webhook Tilda и финальный анализ. Диалог Р0/Р1/Р2 — синхронно, без очереди.
CREATE TABLE IF NOT EXISTS jobs (
    id           BIGSERIAL PRIMARY KEY,
    kind         TEXT NOT NULL CHECK (kind IN ('tilda_intake', 'final_analysis')),
    dedup_key    TEXT NOT NULL,              -- tranid для tilda_intake, id кейса для final_analysis
    status       TEXT NOT NULL DEFAULT 'queued'
                 CHECK (status IN ('queued', 'processing', 'done', 'failed', 'dead')),
    payload      JSONB NOT NULL,
    attempts     INT  NOT NULL DEFAULT 0,
    locked_at    TIMESTAMPTZ,
    last_error   TEXT,
    result_ref   TEXT,                       -- повтор упавшей задачи не создаёт второй результат
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (kind, dedup_key)
);
CREATE INDEX IF NOT EXISTS jobs_status_idx ON jobs (status, created_at);
