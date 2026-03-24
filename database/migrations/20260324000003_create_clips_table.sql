CREATE TABLE IF NOT EXISTS clips (
    clip_id TEXT PRIMARY KEY,
    video_id TEXT NOT NULL REFERENCES videos(video_id),
    start_time REAL NOT NULL,
    end_time REAL NOT NULL,
    duration REAL NOT NULL,
    composite_score REAL,
    video_path TEXT,
    thumbnail_path TEXT,
    title TEXT,
    description TEXT,
    tags TEXT,
    status TEXT NOT NULL DEFAULT 'generated',
    scheduled_at TIMESTAMP,
    published_at TIMESTAMP,
    youtube_id TEXT,
    error_message TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_clips_video ON clips(video_id);
CREATE INDEX IF NOT EXISTS idx_clips_status ON clips(status);
CREATE INDEX IF NOT EXISTS idx_clips_scheduled ON clips(scheduled_at)
    WHERE status = 'scheduled';
