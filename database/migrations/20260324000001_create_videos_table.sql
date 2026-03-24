CREATE TABLE IF NOT EXISTS videos (
    video_id TEXT PRIMARY KEY,
    file_path TEXT NOT NULL,
    duration_seconds REAL NOT NULL,
    resolution_width INTEGER NOT NULL,
    resolution_height INTEGER NOT NULL,
    codec_video TEXT,
    codec_audio TEXT,
    file_size_bytes INTEGER NOT NULL,
    ingested_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    status TEXT NOT NULL DEFAULT 'ingested'
);
