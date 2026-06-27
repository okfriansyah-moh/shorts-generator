ALTER TABLE scenes ADD COLUMN audio_rms_raw REAL;
ALTER TABLE scenes ADD COLUMN scene_activity_raw REAL;
ALTER TABLE scenes ADD COLUMN image_quality_raw REAL;
ALTER TABLE scenes ADD COLUMN image_quality_score REAL;

CREATE TABLE IF NOT EXISTS video_stage_state (
    video_id TEXT NOT NULL REFERENCES videos(video_id),
    stage_name TEXT NOT NULL,
    status TEXT NOT NULL,
    cache_version TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    units_done INTEGER NOT NULL DEFAULT 0,
    units_total INTEGER NOT NULL DEFAULT 0,
    checkpoint_token TEXT,
    payload_json TEXT,
    started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    error_message TEXT,
    PRIMARY KEY (video_id, stage_name)
);

CREATE INDEX IF NOT EXISTS idx_stage_state_video ON video_stage_state(video_id);
CREATE INDEX IF NOT EXISTS idx_stage_state_status ON video_stage_state(status);

CREATE TABLE IF NOT EXISTS transcript_segments (
    video_id TEXT NOT NULL REFERENCES videos(video_id),
    segment_index INTEGER NOT NULL,
    chunk_index INTEGER NOT NULL,
    start_time_ms INTEGER NOT NULL,
    end_time_ms INTEGER NOT NULL,
    text TEXT NOT NULL,
    confidence REAL NOT NULL,
    PRIMARY KEY (video_id, segment_index)
);

CREATE INDEX IF NOT EXISTS idx_transcript_segments_video_start
ON transcript_segments(video_id, start_time_ms);

CREATE TABLE IF NOT EXISTS transcript_words (
    video_id TEXT NOT NULL REFERENCES videos(video_id),
    segment_index INTEGER NOT NULL,
    word_index INTEGER NOT NULL,
    start_time_ms INTEGER NOT NULL,
    end_time_ms INTEGER NOT NULL,
    text TEXT NOT NULL,
    confidence REAL NOT NULL,
    PRIMARY KEY (video_id, segment_index, word_index),
    FOREIGN KEY (video_id, segment_index)
        REFERENCES transcript_segments(video_id, segment_index)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_transcript_words_video_start
ON transcript_words(video_id, start_time_ms);

CREATE TABLE IF NOT EXISTS scene_face_data (
    scene_id TEXT PRIMARY KEY REFERENCES scenes(scene_id),
    video_id TEXT NOT NULL REFERENCES videos(video_id),
    face_visible_ratio REAL NOT NULL,
    sample_count INTEGER NOT NULL,
    avg_x REAL,
    avg_y REAL,
    avg_width REAL,
    avg_height REAL,
    avg_confidence REAL,
    avg_timestamp_ms INTEGER
);

CREATE INDEX IF NOT EXISTS idx_scene_face_data_video
ON scene_face_data(video_id);

CREATE TABLE IF NOT EXISTS scene_face_boxes (
    scene_id TEXT NOT NULL REFERENCES scenes(scene_id),
    box_index INTEGER NOT NULL,
    timestamp_ms INTEGER NOT NULL,
    x REAL NOT NULL,
    y REAL NOT NULL,
    width REAL NOT NULL,
    height REAL NOT NULL,
    confidence REAL NOT NULL,
    PRIMARY KEY (scene_id, box_index)
);

CREATE INDEX IF NOT EXISTS idx_scene_face_boxes_scene_time
ON scene_face_boxes(scene_id, timestamp_ms);

CREATE TABLE IF NOT EXISTS scheduler_locks (
    lock_name TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    acquired_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    heartbeat_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
