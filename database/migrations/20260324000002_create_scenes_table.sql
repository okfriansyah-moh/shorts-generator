CREATE TABLE IF NOT EXISTS scenes (
    scene_id TEXT PRIMARY KEY,
    video_id TEXT NOT NULL REFERENCES videos(video_id),
    start_time REAL NOT NULL,
    end_time REAL NOT NULL,
    duration REAL NOT NULL,
    composite_score REAL,
    keyword_score REAL,
    audio_energy_score REAL,
    scene_activity_score REAL,
    face_presence_score REAL,
    sentence_density_score REAL,
    face_visible_ratio REAL,
    transcript_text TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_scenes_video ON scenes(video_id);
CREATE INDEX IF NOT EXISTS idx_scenes_score ON scenes(composite_score);
