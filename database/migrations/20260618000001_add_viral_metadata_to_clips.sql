-- Add viral metadata columns to clips table.
-- These are populated by the Cowork Claude agent via scripts/apply_ai_metadata.py.

ALTER TABLE clips ADD COLUMN viral_confidence REAL;
ALTER TABLE clips ADD COLUMN viral_reasoning TEXT;
ALTER TABLE clips ADD COLUMN category TEXT;
ALTER TABLE clips ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0;
