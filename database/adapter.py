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

import logging
import sqlite3
from typing import Any

from contracts.scene import SceneSegment

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
        """Return True if a video with this content-hash ID is already in the DB.

        Used by generation_scheduler to skip re-processing renamed duplicates.
        """
        row = self._conn.execute(
            "SELECT 1 FROM videos WHERE video_id = ? LIMIT 1", (video_id,)
        ).fetchone()
        return row is not None

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
    ) -> None:
        """Insert a clip record. Idempotent via ON CONFLICT DO NOTHING."""
        self._conn.execute(
            """INSERT INTO clips
               (clip_id, video_id, start_time, end_time, duration, composite_score,
                video_path, thumbnail_path)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (clip_id) DO UPDATE SET
                 video_path = COALESCE(excluded.video_path, clips.video_path),
                 thumbnail_path = COALESCE(excluded.thumbnail_path, clips.thumbnail_path)""",
            (clip_id, video_id, start_time, end_time, duration, composite_score,
             video_path, thumbnail_path),
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
        params: tuple = tuple(statuses)
        if account_name:
            sql = (
                f"SELECT * FROM clips "
                f"WHERE status IN ({placeholders}) AND account_name = ? "
                f"ORDER BY scheduled_at ASC, clip_id ASC"
            )
            params = tuple(statuses) + (account_name,)
        else:
            sql = (
                f"SELECT * FROM clips "
                f"WHERE status IN ({placeholders}) "
                f"ORDER BY scheduled_at ASC, clip_id ASC"
            )
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
