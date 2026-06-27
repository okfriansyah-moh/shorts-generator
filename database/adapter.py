"""Database adapter for Shorts Factory.

Single entry point for all database access. Modules under modules/
MUST NOT import this — only the orchestrator calls the adapter.
All operations accept and return frozen dataclass DTOs where applicable.
All SQL uses portable syntax (ON CONFLICT DO NOTHING, not INSERT OR IGNORE).

Type conversion note:
  SceneSegment DTO uses milliseconds (int) for start_time/end_time.
  The scenes table currently stores these as REAL (seconds).
  This adapter performs the ms→sec and sec→ms conversion internally.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any

from contracts.face import FaceBBox, FaceDetectionResult, SceneFaceData
from contracts.scene import SceneSegment
from contracts.scoring import ScoredScene, ScoredSceneList
from contracts.transcript import Transcript, TranscriptSegment, Word

logger = logging.getLogger(__name__)


class DatabaseAdapter:
    """Facade for all database operations.

    Only the orchestrator instantiates and calls this adapter.
    Modules never touch the database.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    @property
    def connection(self) -> sqlite3.Connection:
        """Access the underlying connection (for testing/orchestrator only)."""
        return self._conn

    # ------------------------------------------------------------------
    # Video operations
    # ------------------------------------------------------------------

    def insert_video(
        self,
        video_id: str,
        file_path: str,
        duration_seconds: float,
        width: int,
        height: int,
        fps: float,
        has_audio: bool,
        file_size_bytes: int,
        codec_video: str | None = None,
        codec_audio: str | None = None,
    ) -> None:
        """Insert a video record. Idempotent via ON CONFLICT DO NOTHING."""
        self._conn.execute(
            """INSERT INTO videos
               (video_id, file_path, duration_seconds, resolution_width,
                resolution_height, file_size_bytes, codec_video, codec_audio)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (video_id) DO NOTHING""",
            (video_id, file_path, duration_seconds, width, height,
             file_size_bytes, codec_video, codec_audio),
        )
        self._conn.commit()

    def get_video(self, video_id: str) -> dict[str, Any] | None:
        """Retrieve a video record by ID."""
        row = self._conn.execute(
            "SELECT * FROM videos WHERE video_id = ?", (video_id,)
        ).fetchone()
        return dict(row) if row else None

    def video_id_exists(self, video_id: str) -> bool:
        """Return True only if this video has been *fully processed* (status='processed').

        A video_id that exists in the DB with any other status (e.g. 'ingested')
        is a partial/interrupted run — it should be resumed, not skipped as a duplicate.

        Used by generation_scheduler to skip re-processing renamed duplicates.
        """
        row = self._conn.execute(
            "SELECT status FROM videos WHERE video_id = ? LIMIT 1", (video_id,)
        ).fetchone()
        if row is None:
            return False
        return row[0] == "processed"

    def get_clip_youtube_id(self, clip_id: str) -> str | None:
        """Return the youtube_id for a clip, or None if not yet published.

        Used by upload_scheduler to guard against duplicate uploads.
        """
        row = self._conn.execute(
            "SELECT youtube_id FROM clips WHERE clip_id = ? LIMIT 1", (clip_id,)
        ).fetchone()
        if row is None:
            return None
        return row["youtube_id"] or None

    # ------------------------------------------------------------------
    # Scene operations (DTO-based, with ms↔sec conversion)
    # ------------------------------------------------------------------

    def insert_scenes(self, scenes: tuple[SceneSegment, ...] | list[SceneSegment]) -> None:
        """Batch insert scene records from SceneSegment DTOs.

        Idempotent via ON CONFLICT DO NOTHING.
        Converts DTO millisecond ints to REAL seconds for DB storage.
        """
        if not scenes:
            return
        try:
            self._conn.execute("BEGIN")
            self._conn.executemany(
                """INSERT INTO scenes
                   (scene_id, video_id, start_time, end_time, duration)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT (scene_id) DO NOTHING""",
                [
                    (
                        s.scene_id,
                        s.video_id,
                        s.start_time / 1000.0,
                        s.end_time / 1000.0,
                        s.duration,
                    )
                    for s in sorted(scenes, key=lambda s: (s.video_id, s.start_time))
                ],
            )
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    def get_scenes_for_video(self, video_id: str) -> list[SceneSegment]:
        """Retrieve all scenes for a video as SceneSegment DTOs.

        Converts DB REAL seconds back to DTO millisecond ints.
        """
        rows = self._conn.execute(
            "SELECT * FROM scenes WHERE video_id = ? ORDER BY start_time ASC",
            (video_id,),
        ).fetchall()
        return [
            SceneSegment(
                scene_id=row["scene_id"],
                video_id=row["video_id"],
                start_time=round(row["start_time"] * 1000),
                end_time=round(row["end_time"] * 1000),
                duration=float(row["duration"]),
            )
            for row in rows
        ]

    def get_scene_rows(self, video_id: str) -> list[dict[str, Any]]:
        """Return raw scene rows for a video, ordered by start_time."""
        rows = self._conn.execute(
            "SELECT * FROM scenes WHERE video_id = ? ORDER BY start_time ASC",
            (video_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def update_scene_metric(self, scene_id: str, column: str, value: float | str | None) -> None:
        """Update a single whitelisted scene column."""
        allowed = {
            "audio_rms_raw",
            "audio_energy_score",
            "scene_activity_raw",
            "scene_activity_score",
            "image_quality_raw",
            "image_quality_score",
            "keyword_score",
            "face_presence_score",
            "sentence_density_score",
            "composite_score",
            "face_visible_ratio",
            "transcript_text",
        }
        if column not in allowed:
            raise ValueError(f"Unsupported scene metric column: {column}")
        self._conn.execute(
            f"UPDATE scenes SET {column} = ? WHERE scene_id = ?",
            (value, scene_id),
        )
        self._conn.commit()

    def bulk_update_scene_metrics(
        self,
        updates: list[tuple[str, dict[str, float | str | None]]],
    ) -> None:
        """Bulk update multiple scene columns for multiple scenes."""
        if not updates:
            return
        allowed = {
            "audio_rms_raw",
            "audio_energy_score",
            "scene_activity_raw",
            "scene_activity_score",
            "image_quality_raw",
            "image_quality_score",
            "keyword_score",
            "face_presence_score",
            "sentence_density_score",
            "composite_score",
            "face_visible_ratio",
            "transcript_text",
        }
        try:
            self._conn.execute("BEGIN")
            for scene_id, columns in updates:
                invalid = set(columns) - allowed
                if invalid:
                    raise ValueError(f"Unsupported scene metric columns: {sorted(invalid)}")
                set_clause = ", ".join(f"{name} = ?" for name in columns)
                params = [columns[name] for name in columns] + [scene_id]
                self._conn.execute(
                    f"UPDATE scenes SET {set_clause} WHERE scene_id = ?",
                    params,
                )
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    def get_scene_metric_map(self, video_id: str, column: str) -> dict[str, Any]:
        """Return a mapping of scene_id -> metric value for a whitelisted column."""
        allowed = {
            "audio_rms_raw",
            "audio_energy_score",
            "scene_activity_raw",
            "scene_activity_score",
            "image_quality_raw",
            "image_quality_score",
            "keyword_score",
            "face_presence_score",
            "sentence_density_score",
            "composite_score",
            "face_visible_ratio",
            "transcript_text",
        }
        if column not in allowed:
            raise ValueError(f"Unsupported scene metric column: {column}")
        rows = self._conn.execute(
            f"SELECT scene_id, {column} AS metric FROM scenes WHERE video_id = ?",
            (video_id,),
        ).fetchall()
        return {row["scene_id"]: row["metric"] for row in rows}

    def get_scene_ids_missing_metric(self, video_id: str, column: str) -> list[str]:
        """Return scene_ids whose given metric column is NULL."""
        allowed = {
            "audio_rms_raw",
            "audio_energy_score",
            "scene_activity_raw",
            "scene_activity_score",
            "image_quality_raw",
            "image_quality_score",
            "keyword_score",
            "face_presence_score",
            "sentence_density_score",
            "composite_score",
            "face_visible_ratio",
            "transcript_text",
        }
        if column not in allowed:
            raise ValueError(f"Unsupported scene metric column: {column}")
        rows = self._conn.execute(
            f"""SELECT scene_id FROM scenes
                WHERE video_id = ? AND {column} IS NULL
                ORDER BY start_time ASC""",
            (video_id,),
        ).fetchall()
        return [row["scene_id"] for row in rows]

    def upsert_stage_state(
        self,
        video_id: str,
        stage_name: str,
        status: str,
        cache_version: str,
        config_hash: str,
        units_done: int = 0,
        units_total: int = 0,
        checkpoint_token: str | None = None,
        payload_json: str | None = None,
        error_message: str | None = None,
    ) -> None:
        """Insert or update persisted stage cache state."""
        completed_at = "CURRENT_TIMESTAMP" if status == "completed" else "NULL"
        self._conn.execute(
            f"""INSERT INTO video_stage_state
                (video_id, stage_name, status, cache_version, config_hash,
                 units_done, units_total, checkpoint_token, payload_json,
                 completed_at, error_message)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, {completed_at}, ?)
                ON CONFLICT(video_id, stage_name) DO UPDATE SET
                  status = excluded.status,
                  cache_version = excluded.cache_version,
                  config_hash = excluded.config_hash,
                  units_done = excluded.units_done,
                  units_total = excluded.units_total,
                  checkpoint_token = excluded.checkpoint_token,
                  payload_json = excluded.payload_json,
                  updated_at = CURRENT_TIMESTAMP,
                  completed_at = CASE
                      WHEN excluded.status = 'completed' THEN CURRENT_TIMESTAMP
                      ELSE NULL
                  END,
                  error_message = excluded.error_message""",
            (
                video_id,
                stage_name,
                status,
                cache_version,
                config_hash,
                units_done,
                units_total,
                checkpoint_token,
                payload_json,
                error_message,
            ),
        )
        self._conn.commit()

    def get_stage_state(self, video_id: str, stage_name: str) -> dict[str, Any] | None:
        """Return persisted state for a specific stage."""
        row = self._conn.execute(
            """SELECT * FROM video_stage_state
               WHERE video_id = ? AND stage_name = ?""",
            (video_id, stage_name),
        ).fetchone()
        return dict(row) if row else None

    def list_stage_states(self, video_id: str) -> dict[str, dict[str, Any]]:
        """Return all persisted stage states keyed by stage_name."""
        rows = self._conn.execute(
            """SELECT * FROM video_stage_state
               WHERE video_id = ?""",
            (video_id,),
        ).fetchall()
        return {row["stage_name"]: dict(row) for row in rows}

    def invalidate_stage_states(self, video_id: str, stages: list[str] | None = None) -> None:
        """Delete persisted stage states for a video."""
        if stages:
            placeholders = ",".join("?" * len(stages))
            self._conn.execute(
                f"""DELETE FROM video_stage_state
                    WHERE video_id = ? AND stage_name IN ({placeholders})""",
                (video_id, *stages),
            )
        else:
            self._conn.execute(
                "DELETE FROM video_stage_state WHERE video_id = ?",
                (video_id,),
            )
        self._conn.commit()

    def upsert_transcript_chunk(
        self,
        video_id: str,
        chunk_index: int,
        segments: tuple[TranscriptSegment, ...] | list[TranscriptSegment],
    ) -> None:
        """Replace a transcript chunk transactionally."""
        try:
            self._conn.execute("BEGIN")
            existing = self._conn.execute(
                """SELECT segment_index FROM transcript_segments
                   WHERE video_id = ? AND chunk_index = ?""",
                (video_id, chunk_index),
            ).fetchall()
            for row in existing:
                self._conn.execute(
                    """DELETE FROM transcript_words
                       WHERE video_id = ? AND segment_index = ?""",
                    (video_id, row["segment_index"]),
                )
            self._conn.execute(
                """DELETE FROM transcript_segments
                   WHERE video_id = ? AND chunk_index = ?""",
                (video_id, chunk_index),
            )
            for local_segment_index, segment in enumerate(segments):
                segment_index = chunk_index * 1_000_000 + local_segment_index
                self._conn.execute(
                    """INSERT INTO transcript_segments
                       (video_id, segment_index, chunk_index, start_time_ms,
                        end_time_ms, text, confidence)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        video_id,
                        segment_index,
                        chunk_index,
                        segment.start_time,
                        segment.end_time,
                        segment.text,
                        segment.confidence,
                    ),
                )
                for local_word_index, word in enumerate(segment.words):
                    self._conn.execute(
                        """INSERT INTO transcript_words
                           (video_id, segment_index, word_index, start_time_ms,
                            end_time_ms, text, confidence)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (
                            video_id,
                            segment_index,
                            local_word_index,
                            word.start_time,
                            word.end_time,
                            word.text,
                            word.confidence,
                        ),
                    )
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    def get_transcript_chunk_indexes(self, video_id: str) -> set[int]:
        """Return chunk indexes already cached for a video transcript."""
        rows = self._conn.execute(
            "SELECT DISTINCT chunk_index FROM transcript_segments WHERE video_id = ?",
            (video_id,),
        ).fetchall()
        return {int(row["chunk_index"]) for row in rows}

    def get_transcript(self, video_id: str) -> Transcript | None:
        """Reconstruct a transcript DTO from persisted rows."""
        segment_rows = self._conn.execute(
            """SELECT * FROM transcript_segments
               WHERE video_id = ?
               ORDER BY start_time_ms ASC, segment_index ASC""",
            (video_id,),
        ).fetchall()
        if not segment_rows:
            return None

        word_rows = self._conn.execute(
            """SELECT * FROM transcript_words
               WHERE video_id = ?
               ORDER BY segment_index ASC, word_index ASC""",
            (video_id,),
        ).fetchall()
        words_by_segment: dict[int, list[Word]] = {}
        for row in word_rows:
            words_by_segment.setdefault(int(row["segment_index"]), []).append(
                Word(
                    text=row["text"],
                    start_time=int(row["start_time_ms"]),
                    end_time=int(row["end_time_ms"]),
                    confidence=float(row["confidence"]),
                )
            )

        segments: list[TranscriptSegment] = []
        total_words = 0
        for row in segment_rows:
            segment_index = int(row["segment_index"])
            words = tuple(words_by_segment.get(segment_index, []))
            total_words += len(words)
            segments.append(
                TranscriptSegment(
                    text=row["text"],
                    start_time=int(row["start_time_ms"]),
                    end_time=int(row["end_time_ms"]),
                    words=words,
                    confidence=float(row["confidence"]),
                )
            )
        return Transcript(
            video_id=video_id,
            segments=tuple(segments),
            total_words=total_words,
            language="en",
        )

    def upsert_face_scene(
        self,
        scene_id: str,
        video_id: str,
        scene_data: SceneFaceData,
    ) -> None:
        """Persist face summary and boxes for a single scene transactionally."""
        avg = scene_data.average_bbox
        try:
            self._conn.execute("BEGIN")
            self._conn.execute(
                """INSERT INTO scene_face_data
                   (scene_id, video_id, face_visible_ratio, sample_count,
                    avg_x, avg_y, avg_width, avg_height, avg_confidence, avg_timestamp_ms)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(scene_id) DO UPDATE SET
                     video_id = excluded.video_id,
                     face_visible_ratio = excluded.face_visible_ratio,
                     sample_count = excluded.sample_count,
                     avg_x = excluded.avg_x,
                     avg_y = excluded.avg_y,
                     avg_width = excluded.avg_width,
                     avg_height = excluded.avg_height,
                     avg_confidence = excluded.avg_confidence,
                     avg_timestamp_ms = excluded.avg_timestamp_ms""",
                (
                    scene_id,
                    video_id,
                    scene_data.face_visible_ratio,
                    scene_data.sample_count,
                    None if avg is None else avg.x,
                    None if avg is None else avg.y,
                    None if avg is None else avg.width,
                    None if avg is None else avg.height,
                    None if avg is None else avg.confidence,
                    None if avg is None else avg.timestamp_ms,
                ),
            )
            self._conn.execute(
                "DELETE FROM scene_face_boxes WHERE scene_id = ?",
                (scene_id,),
            )
            for idx, box in enumerate(scene_data.bounding_boxes):
                self._conn.execute(
                    """INSERT INTO scene_face_boxes
                       (scene_id, box_index, timestamp_ms, x, y, width, height, confidence)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        scene_id,
                        idx,
                        box.timestamp_ms,
                        box.x,
                        box.y,
                        box.width,
                        box.height,
                        box.confidence,
                    ),
                )
            self._conn.execute(
                "UPDATE scenes SET face_visible_ratio = ? WHERE scene_id = ?",
                (scene_data.face_visible_ratio, scene_id),
            )
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    def get_cached_face_scene_ids(self, video_id: str) -> set[str]:
        """Return scene_ids that already have persisted face summaries."""
        rows = self._conn.execute(
            "SELECT scene_id FROM scene_face_data WHERE video_id = ?",
            (video_id,),
        ).fetchall()
        return {row["scene_id"] for row in rows}

    def get_face_detection_result(self, video_id: str) -> FaceDetectionResult | None:
        """Reconstruct FaceDetectionResult from persisted tables."""
        summary_rows = self._conn.execute(
            """SELECT * FROM scene_face_data
               WHERE video_id = ?
               ORDER BY rowid ASC""",
            (video_id,),
        ).fetchall()
        if not summary_rows:
            return None
        box_rows = self._conn.execute(
            """SELECT * FROM scene_face_boxes
               WHERE scene_id IN (
                   SELECT scene_id FROM scene_face_data WHERE video_id = ?
               )
               ORDER BY scene_id ASC, box_index ASC""",
            (video_id,),
        ).fetchall()
        boxes_by_scene: dict[str, list[FaceBBox]] = {}
        for row in box_rows:
            boxes_by_scene.setdefault(row["scene_id"], []).append(
                FaceBBox(
                    x=float(row["x"]),
                    y=float(row["y"]),
                    width=float(row["width"]),
                    height=float(row["height"]),
                    confidence=float(row["confidence"]),
                    timestamp_ms=int(row["timestamp_ms"]),
                )
            )
        scene_data: list[SceneFaceData] = []
        for row in summary_rows:
            avg_bbox = None
            if row["avg_x"] is not None:
                avg_bbox = FaceBBox(
                    x=float(row["avg_x"]),
                    y=float(row["avg_y"]),
                    width=float(row["avg_width"]),
                    height=float(row["avg_height"]),
                    confidence=float(row["avg_confidence"]),
                    timestamp_ms=int(row["avg_timestamp_ms"] or 0),
                )
            scene_data.append(
                SceneFaceData(
                    scene_id=row["scene_id"],
                    face_visible_ratio=float(row["face_visible_ratio"]),
                    bounding_boxes=tuple(boxes_by_scene.get(row["scene_id"], [])),
                    average_bbox=avg_bbox,
                    sample_count=int(row["sample_count"]),
                )
            )
        average_visibility = (
            sum(s.face_visible_ratio for s in scene_data) / len(scene_data)
            if scene_data else 0.0
        )
        faceless = sum(1 for s in scene_data if s.face_visible_ratio == 0.0)
        return FaceDetectionResult(
            video_id=video_id,
            scene_data=tuple(scene_data),
            average_visibility=average_visibility,
            faceless_scene_count=faceless,
        )

    def persist_scored_scenes(
        self,
        scored_scene_list: ScoredSceneList,
        transcript_text_by_scene: dict[str, str] | None = None,
    ) -> None:
        """Persist scored scene metrics and transcript text into scenes."""
        transcript_text_by_scene = transcript_text_by_scene or {}
        try:
            self._conn.execute("BEGIN")
            for scene in scored_scene_list.scenes:
                self._conn.execute(
                    """UPDATE scenes
                       SET keyword_score = ?,
                           audio_energy_score = ?,
                           face_presence_score = ?,
                           scene_activity_score = ?,
                           sentence_density_score = ?,
                           image_quality_score = ?,
                           composite_score = ?,
                           transcript_text = ?
                       WHERE scene_id = ?""",
                    (
                        scene.keyword_score,
                        scene.audio_energy_score,
                        scene.face_presence_score,
                        scene.scene_activity_score,
                        scene.sentence_density_score,
                        scene.image_quality_score,
                        scene.composite_score,
                        transcript_text_by_scene.get(scene.scene_id, ""),
                        scene.scene_id,
                    ),
                )
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    def get_scored_scene_list(self, video_id: str) -> ScoredSceneList | None:
        """Reconstruct persisted scored scenes for a video."""
        rows = self._conn.execute(
            """SELECT * FROM scenes
               WHERE video_id = ? AND composite_score IS NOT NULL
               ORDER BY composite_score DESC, start_time ASC""",
            (video_id,),
        ).fetchall()
        if not rows:
            return None
        scenes: list[ScoredScene] = []
        for rank, row in enumerate(rows, start=1):
            scenes.append(
                ScoredScene(
                    scene_id=row["scene_id"],
                    video_id=row["video_id"],
                    start_time=round(float(row["start_time"]) * 1000),
                    end_time=round(float(row["end_time"]) * 1000),
                    duration=float(row["duration"]),
                    keyword_score=float(row["keyword_score"] or 0.0),
                    audio_energy_score=float(row["audio_energy_score"] or 0.0),
                    face_presence_score=float(row["face_presence_score"] or 0.0),
                    scene_activity_score=float(row["scene_activity_score"] or 0.0),
                    sentence_density_score=float(row["sentence_density_score"] or 0.0),
                    image_quality_score=float(row["image_quality_score"] or 0.0),
                    composite_score=float(row["composite_score"] or 0.0),
                    rank=rank,
                )
            )
        composites = [scene.composite_score for scene in scenes]
        return ScoredSceneList(
            video_id=video_id,
            scenes=tuple(scenes),
            min_score=min(composites),
            max_score=max(composites),
            avg_score=sum(composites) / len(composites),
        )

    def acquire_scheduler_lock(self, lock_name: str, owner_id: str, stale_after_seconds: int) -> bool:
        """Acquire a coarse scheduler lock with stale-owner takeover."""
        row = self._conn.execute(
            "SELECT owner_id FROM scheduler_locks WHERE lock_name = ?",
            (lock_name,),
        ).fetchone()
        if row is None:
            self._conn.execute(
                """INSERT INTO scheduler_locks (lock_name, owner_id)
                   VALUES (?, ?)""",
                (lock_name, owner_id),
            )
            self._conn.commit()
            return True

        cursor = self._conn.execute(
            """UPDATE scheduler_locks
               SET owner_id = ?, acquired_at = CURRENT_TIMESTAMP, heartbeat_at = CURRENT_TIMESTAMP
               WHERE lock_name = ?
                 AND (
                   owner_id = ?
                   OR heartbeat_at <= datetime('now', ?)
                 )""",
            (owner_id, lock_name, owner_id, f"-{int(stale_after_seconds)} seconds"),
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def heartbeat_scheduler_lock(self, lock_name: str, owner_id: str) -> bool:
        """Update heartbeat for a held scheduler lock."""
        cursor = self._conn.execute(
            """UPDATE scheduler_locks
               SET heartbeat_at = CURRENT_TIMESTAMP
               WHERE lock_name = ? AND owner_id = ?""",
            (lock_name, owner_id),
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def release_scheduler_lock(self, lock_name: str, owner_id: str) -> None:
        """Release a scheduler lock owned by the given owner."""
        self._conn.execute(
            "DELETE FROM scheduler_locks WHERE lock_name = ? AND owner_id = ?",
            (lock_name, owner_id),
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Clip operations
    # ------------------------------------------------------------------

    def insert_clip(
        self,
        clip_id: str,
        video_id: str,
        start_time: float,
        end_time: float,
        duration: float,
        composite_score: float | None = None,
        video_path: str | None = None,
        thumbnail_path: str | None = None,
        account_name: str = "",
    ) -> None:
        """Insert a clip record. Idempotent via ON CONFLICT DO NOTHING."""
        self._conn.execute(
            """INSERT INTO clips
               (clip_id, video_id, start_time, end_time, duration, composite_score,
                video_path, thumbnail_path, account_name)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (clip_id) DO UPDATE SET
                 video_path = COALESCE(excluded.video_path, clips.video_path),
                 thumbnail_path = COALESCE(excluded.thumbnail_path, clips.thumbnail_path)""",
            (clip_id, video_id, start_time, end_time, duration, composite_score,
             video_path, thumbnail_path, account_name),
        )
        self._conn.commit()

    def update_clip_status(
        self,
        clip_id: str,
        new_status: str,
        valid_from: tuple[str, ...],
        error_message: str | None = None,
    ) -> bool:
        """Transition clip status only from valid source states.

        Returns True if update was applied, False if state was invalid.
        """
        placeholders = ",".join("?" * len(valid_from))
        params: tuple[Any, ...] = (new_status, clip_id, *valid_from)

        if error_message is not None:
            cursor = self._conn.execute(
                f"""UPDATE clips SET status = ?, error_message = ?,
                    updated_at = CURRENT_TIMESTAMP
                    WHERE clip_id = ? AND status IN ({placeholders})""",
                (new_status, error_message, clip_id, *valid_from),
            )
        else:
            cursor = self._conn.execute(
                f"""UPDATE clips SET status = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE clip_id = ? AND status IN ({placeholders})""",
                params,
            )

        self._conn.commit()
        return cursor.rowcount > 0

    def get_clips_for_video(self, video_id: str) -> list[dict[str, Any]]:
        """Retrieve all clips for a video, sorted by start_time."""
        rows = self._conn.execute(
            "SELECT * FROM clips WHERE video_id = ? ORDER BY start_time ASC",
            (video_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_clips_by_status(
        self,
        statuses: list[str],
        account_name: str | None = None,
    ) -> list[dict[str, Any]]:
        """Retrieve all clips matching any of the given statuses.

        Args:
            statuses:     List of status values to match (e.g. ["scheduled"]).
            account_name: Optional account filter.  When provided, only clips
                          belonging to that account are returned.  When None,
                          clips from all accounts are returned (backward compat).
        """
        if not statuses:
            return []
        placeholders = ",".join("?" * len(statuses))
        if account_name:
            sql = (
                f"SELECT * FROM clips "
                f"WHERE account_name = ? AND status IN ({placeholders}) "
                f"ORDER BY scheduled_at ASC, clip_id ASC"
            )
            params: tuple = (account_name,) + tuple(statuses)
        else:
            sql = (
                f"SELECT * FROM clips "
                f"WHERE status IN ({placeholders}) "
                f"ORDER BY scheduled_at ASC, clip_id ASC"
            )
            params: tuple = tuple(statuses)
        rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def update_clip_publish_info(
        self,
        clip_id: str,
        youtube_id: str,
        published_at: str | None = None,
    ) -> None:
        """Persist youtube_id and published_at after a successful upload."""
        self._conn.execute(
            """UPDATE clips SET youtube_id = ?, published_at = ?,
                updated_at = CURRENT_TIMESTAMP
                WHERE clip_id = ?""",
            (youtube_id, published_at, clip_id),
        )
        self._conn.commit()

    def update_clip_platform_ids(
        self,
        clip_id: str,
        youtube_id: str | None = None,
        tiktok_id: str | None = None,
        instagram_id: str | None = None,
        facebook_id: str | None = None,
        published_at: str | None = None,
    ) -> None:
        """Persist per-platform video IDs after a multi-platform upload.

        Only non-None values are written so existing IDs are never overwritten
        by a partial retry on a different platform.
        """
        sets: list[str] = ["updated_at = CURRENT_TIMESTAMP"]
        params: list[Any] = []

        if youtube_id is not None:
            sets.append("youtube_id = ?")
            params.append(youtube_id)
        if tiktok_id is not None:
            sets.append("tiktok_id = ?")
            params.append(tiktok_id)
        if instagram_id is not None:
            sets.append("instagram_id = ?")
            params.append(instagram_id)
        if facebook_id is not None:
            sets.append("facebook_id = ?")
            params.append(facebook_id)
        if published_at is not None:
            sets.append("published_at = ?")
            params.append(published_at)

        if len(sets) == 1:
            # Nothing to update besides the timestamp
            return

        params.append(clip_id)
        sql = f"UPDATE clips SET {', '.join(sets)} WHERE clip_id = ?"
        self._conn.execute(sql, params)
        self._conn.commit()

    # ------------------------------------------------------------------
    # Pipeline run operations
    # ------------------------------------------------------------------

    def create_pipeline_run(
        self,
        run_id: str,
        video_id: str,
        config_snapshot: str | None = None,
    ) -> None:
        """Create a new pipeline run. Idempotent via ON CONFLICT DO NOTHING."""
        self._conn.execute(
            """INSERT INTO pipeline_runs (run_id, video_id, config_snapshot)
               VALUES (?, ?, ?)
               ON CONFLICT (run_id) DO NOTHING""",
            (run_id, video_id, config_snapshot),
        )
        self._conn.commit()

    def update_checkpoint(self, run_id: str, stage: str) -> None:
        """Record last completed stage for resume."""
        self._conn.execute(
            """UPDATE pipeline_runs
               SET last_completed_stage = ?
               WHERE run_id = ?""",
            (stage, run_id),
        )
        self._conn.commit()

    def update_pipeline_status(
        self,
        run_id: str,
        status: str,
        clips_generated: int | None = None,
        clips_failed: int | None = None,
        error_message: str | None = None,
    ) -> None:
        """Update pipeline run status and counters."""
        updates = ["status = ?"]
        params: list[Any] = [status]

        if clips_generated is not None:
            updates.append("clips_generated = ?")
            params.append(clips_generated)
        if clips_failed is not None:
            updates.append("clips_failed = ?")
            params.append(clips_failed)
        if error_message is not None:
            updates.append("error_log = ?")
            params.append(error_message)
        if status in ("completed", "partial", "failed"):
            updates.append("completed_at = CURRENT_TIMESTAMP")

        params.append(run_id)

        self._conn.execute(
            f"UPDATE pipeline_runs SET {', '.join(updates)} WHERE run_id = ?",
            tuple(params),
        )
        self._conn.commit()

    def get_active_run(self, video_id: str) -> dict[str, Any] | None:
        """Get the most recent non-terminal pipeline run for a video."""
        row = self._conn.execute(
            """SELECT * FROM pipeline_runs
               WHERE video_id = ? AND status NOT IN ('completed', 'failed', 'partial')
               ORDER BY started_at DESC LIMIT 1""",
            (video_id,),
        ).fetchone()
        return dict(row) if row else None

    def get_last_completed_stage(self, video_id: str) -> str | None:
        """Get last completed stage for resume."""
        row = self._conn.execute(
            """SELECT last_completed_stage FROM pipeline_runs
               WHERE video_id = ? AND status NOT IN ('completed', 'failed', 'partial')
               ORDER BY started_at DESC LIMIT 1""",
            (video_id,),
        ).fetchone()
        return row["last_completed_stage"] if row else None

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()
