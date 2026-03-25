CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id TEXT PRIMARY KEY,
    video_id TEXT NOT NULL REFERENCES videos(video_id),
    started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    last_completed_stage TEXT,
    clips_generated INTEGER DEFAULT 0,
    clips_failed INTEGER DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'started',
    error_log TEXT,
    config_snapshot TEXT
);

CREATE INDEX IF NOT EXISTS idx_runs_video ON pipeline_runs(video_id);
CREATE INDEX IF NOT EXISTS idx_runs_status ON pipeline_runs(status);
