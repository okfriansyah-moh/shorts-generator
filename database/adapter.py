"""Database adapter for Shorts Factory.

Single entry point for all database access. Modules under modules/
MUST NOT import this — only the orchestrator calls the adapter.
All operations accept and return frozen dataclass DTOs where applicable.
All SQL uses portable syntax (ON CONFLICT DO NOTHING, not INSERT OR IGNORE).
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

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
    ) -> None:
        """Insert a video record. Idempotent via ON CONFLICT DO NOTHING."""
        self._conn.execute(
            """INSERT INTO videos
               (video_id, file_path, duration_seconds, resolution_width,
                resolution_height, file_size_bytes)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT (video_id) DO NOTHING""",
            (video_id, file_path, duration_seconds, width, height, file_size_bytes),
        )
        self._conn.commit()

    def get_video(self, video_id: str) -> dict[str, Any] | None:
        """Retrieve a video record by ID."""
        row = self._conn.execute(
            "SELECT * FROM videos WHERE video_id = ?", (video_id,)
        ).fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------------
    # Scene operations
    # ------------------------------------------------------------------

    def insert_scenes(self, scenes: list[dict[str, Any]]) -> None:
        """Batch insert scene records. Idempotent via ON CONFLICT DO NOTHING."""
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
                    (s["scene_id"], s["video_id"], s["start_time"], s["end_time"], s["duration"])
                    for s in sorted(scenes, key=lambda s: (s["video_id"], s["start_time"]))
                ],
            )
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    def get_scenes_for_video(self, video_id: str) -> list[dict[str, Any]]:
        """Retrieve all scenes for a video, sorted by start_time."""
        rows = self._conn.execute(
            "SELECT * FROM scenes WHERE video_id = ? ORDER BY start_time ASC",
            (video_id,),
        ).fetchall()
        return [dict(r) for r in rows]

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
    ) -> None:
        """Insert a clip record. Idempotent via ON CONFLICT DO NOTHING."""
        self._conn.execute(
            """INSERT INTO clips
               (clip_id, video_id, start_time, end_time, duration, composite_score)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT (clip_id) DO NOTHING""",
            (clip_id, video_id, start_time, end_time, duration, composite_score),
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
                    updated_at = datetime('now')
                    WHERE clip_id = ? AND status IN ({placeholders})""",
                (new_status, error_message, clip_id, *valid_from),
            )
        else:
            cursor = self._conn.execute(
                f"""UPDATE clips SET status = ?, updated_at = datetime('now')
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
            updates.append("completed_at = datetime('now')")

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
               WHERE video_id = ? AND status NOT IN ('completed', 'failed')
               ORDER BY started_at DESC LIMIT 1""",
            (video_id,),
        ).fetchone()
        return dict(row) if row else None

    def get_last_completed_stage(self, video_id: str) -> str | None:
        """Get last completed stage for resume."""
        row = self._conn.execute(
            """SELECT last_completed_stage FROM pipeline_runs
               WHERE video_id = ? AND status NOT IN ('completed', 'failed')
               ORDER BY started_at DESC LIMIT 1""",
            (video_id,),
        ).fetchone()
        return row["last_completed_stage"] if row else None

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()
