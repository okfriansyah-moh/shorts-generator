-- Migration: add account_name to clips table
--
-- Scopes every clip to a publishing account (config/accounts/<name>/).
-- Default '' is a neutral sentinel; insert_clip always provides the real
-- account name explicitly — no row should remain as '' in normal operation.
--
-- The index supports the common scheduler query:
--   SELECT * FROM clips WHERE account_name = ? AND status = ?

ALTER TABLE clips ADD COLUMN account_name TEXT NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_clips_account_name
    ON clips (account_name);

-- Composite index for the scheduler's primary lookup pattern:
--   WHERE account_name = ? AND status IN (...)
CREATE INDEX IF NOT EXISTS idx_clips_account_status
    ON clips (account_name, status);
