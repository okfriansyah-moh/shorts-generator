#!/usr/bin/env python3
"""Upload scheduler for Shorts Factory.

Runs on a recurring schedule (e.g., 3× daily). Each invocation:
  1. Finds the next due 'scheduled' clip and uploads it to YouTube.
  2. After a successful upload, deletes all on-disk clip artefacts
     (composite, final video, thumbnail, subtitles) to reclaim space.
  3. If the queue is now empty, spawns generation_scheduler.py to
     kick off a new generation cycle — so the pipeline is always fed.

Exit codes:
  0 — success (including no-op when nothing is due)
  1 — retryable error (quota, transient network, etc.)
  2 — fatal / config error (operator intervention required)
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from core.config import load_config                          # noqa: E402
from core.logging import configure_logging                   # noqa: E402
from contracts.storage import StorageRecord                  # noqa: E402
from database.adapter import DatabaseAdapter                 # noqa: E402
from database.connection import initialize_database          # noqa: E402
from modules.publisher.publish import publish_single         # noqa: E402
from modules.publisher.youtube_client import YouTubeClient   # noqa: E402
from modules.publisher.visibility import check_visibility_transitions  # noqa: E402

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _db_path(config: dict) -> str:
    path = config.get("paths", {}).get("database", "output/shorts_factory.db")
    return path if os.path.isabs(path) else os.path.join(_PROJECT_ROOT, path)


def _row_to_storage_record(row: dict) -> StorageRecord:
    tags_raw = row.get("tags", "")
    if isinstance(tags_raw, str) and tags_raw:
        try:
            tags = tuple(json.loads(tags_raw))
        except (ValueError, TypeError):
            tags = tuple(t.strip() for t in tags_raw.split(",") if t.strip())
    elif isinstance(tags_raw, (list, tuple)):
        tags = tuple(tags_raw)
    else:
        tags = ()

    return StorageRecord(
        clip_id=row["clip_id"],
        video_id=row["video_id"],
        status=row.get("status", "scheduled"),
        composite_score=float(row.get("composite_score", 0.0) or 0.0),
        file_paths={
            "video":      row.get("video_path", "") or "",
            "thumbnail":  row.get("thumbnail_path", "") or "",
            "metadata":   "",
            "subtitles":  "",
            "narration":  "",
        },
        title=row.get("title", "") or "",
        description=row.get("description", "") or "",
        tags=tags,
        category=row.get("category", "Gaming") if "category" in row else "Gaming",
        created_at=row.get("created_at", "") or "",
        scheduled_at=row.get("scheduled_at"),
        published_at=row.get("published_at"),
        youtube_id=row.get("youtube_id"),
        error_message=row.get("error_message"),
        retry_count=int(row.get("retry_count", 0) or 0),
    )


def _check_duplicate_upload(record: StorageRecord, adapter: DatabaseAdapter) -> bool:
    """Return True if this clip has already been uploaded to YouTube.

    Performs a fresh DB lookup so we're never working from a stale in-memory
    record — catches the race where two scheduler instances run concurrently,
    or where the status was reset accidentally.

    Logs a warning and returns True when a duplicate is detected.
    """
    # 1. Fast path: record already carries a youtube_id from the DB query
    if record.youtube_id:
        logger.warning(
            "upload_scheduler: DUPLICATE UPLOAD blocked — clip %s already "
            "published as https://youtu.be/%s",
            record.clip_id, record.youtube_id,
            extra={"stage": "upload_scheduler", "clip_id": record.clip_id,
                   "youtube_id": record.youtube_id},
        )
        return True

    # 2. Re-query DB for a fresh youtube_id in case our record is stale
    live_youtube_id = adapter.get_clip_youtube_id(record.clip_id)
    if live_youtube_id:
        logger.warning(
            "upload_scheduler: DUPLICATE UPLOAD blocked (stale record) — "
            "clip %s already published as https://youtu.be/%s",
            record.clip_id, live_youtube_id,
            extra={"stage": "upload_scheduler", "clip_id": record.clip_id,
                   "youtube_id": live_youtube_id},
        )
        return True

    return False


def _next_due_record(adapter: DatabaseAdapter) -> StorageRecord | None:
    """Return the oldest scheduled clip whose scheduled_at <= now, or None."""
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = adapter.get_clips_by_status(["scheduled"])
    due = [
        r for r in rows
        if r.get("scheduled_at") and r["scheduled_at"] <= now_iso
    ]
    if not due:
        return None
    due.sort(key=lambda r: (r.get("scheduled_at", ""), r.get("clip_id", "")))
    return _row_to_storage_record(due[0])


def _remaining_scheduled_count(adapter: DatabaseAdapter) -> int:
    return len(adapter.get_clips_by_status(["scheduled"]))


def _delete_clip_artefacts(record: StorageRecord, config: dict) -> None:
    """Delete all on-disk files for a clip after successful upload.

    Removes:
      - The clip directory under output/<video_dir>/clips/shorts-N/
        (contains composite.mp4, final.mp4, thumbnail.jpg, subtitles.ass)

    The database record is preserved for audit / YouTube link tracking.
    """
    output_dir = config.get("paths", {}).get("output_dir", "output")
    if not os.path.isabs(output_dir):
        output_dir = os.path.join(_PROJECT_ROOT, output_dir)

    # Derive clip directory from video_path stored in DB.
    # video_path is absolute (set by storage module).
    video_path = record.file_paths.get("video", "")
    if video_path and os.path.isfile(video_path):
        clip_dir = os.path.dirname(video_path)
        if os.path.isdir(clip_dir):
            try:
                shutil.rmtree(clip_dir)
                logger.info(
                    "Deleted clip artefacts",
                    extra={"clip_id": record.clip_id, "dir": clip_dir, "stage": "upload_scheduler"},
                )
            except OSError as exc:
                logger.warning(
                    "Could not delete clip directory",
                    extra={"clip_id": record.clip_id, "dir": clip_dir, "error": str(exc), "stage": "upload_scheduler"},
                )
        return

    # Fallback: try to find directory by video_id + clip_id pattern in output/
    logger.warning(
        "video_path not found for clip — skipping artefact deletion",
        extra={"clip_id": record.clip_id, "stage": "upload_scheduler"},
    )


def _spawn_generation() -> None:
    """Fire-and-forget: launch generation_scheduler.py in the background."""
    script = os.path.join(_PROJECT_ROOT, "scripts", "generation_scheduler.py")
    log_path = os.path.join(_PROJECT_ROOT, "output", "generation_scheduler.log")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "a") as logf:
        subprocess.Popen(
            [sys.executable, script],
            cwd=_PROJECT_ROOT,
            stdout=logf,
            stderr=logf,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    logger.info(
        "Spawned generation_scheduler.py",
        extra={"stage": "upload_scheduler"},
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    os.chdir(_PROJECT_ROOT)

    try:
        config = load_config()
    except Exception as exc:
        print(f"[upload_scheduler] FATAL: config load failed: {exc}", file=sys.stderr)
        return 2

    configure_logging(
        level=config.get("logging", {}).get("level", "INFO"),
        log_file=config.get("logging", {}).get("log_file"),
    )
    logger.info("upload_scheduler: starting", extra={"stage": "upload_scheduler", "video_id": ""})

    # ── DB ────────────────────────────────────────────────────────────────
    try:
        conn = initialize_database(_db_path(config))
        adapter = DatabaseAdapter(conn)
    except Exception as exc:
        logger.error("upload_scheduler: DB error", extra={"stage": "upload_scheduler", "error": str(exc)})
        return 1

    # ── Find next due clip ─────────────────────────────────────────────────
    record = _next_due_record(adapter)

    if record is None:
        remaining = _remaining_scheduled_count(adapter)
        if remaining == 0:
            logger.info(
                "upload_scheduler: queue exhausted — spawning generation",
                extra={"stage": "upload_scheduler"},
            )
            _spawn_generation()
        else:
            logger.info(
                "upload_scheduler: %d clip(s) scheduled but none due yet",
                remaining,
                extra={"stage": "upload_scheduler", "remaining": remaining},
            )
        conn.close()
        return 0

    logger.info(
        "upload_scheduler: uploading clip %s — '%s'",
        record.clip_id,
        record.title[:50],
        extra={"stage": "upload_scheduler", "clip_id": record.clip_id},
    )

    # ── Guard: abort if already uploaded ──────────────────────────────────
    if _check_duplicate_upload(record, adapter):
        # Ensure status reflects reality — move away from 'scheduled' so the
        # same clip is not attempted again on the next scheduler tick.
        adapter.update_clip_status(
            clip_id=record.clip_id,
            new_status="published",
            valid_from=("scheduled", "generated", "queued"),
        )
        conn.close()
        return 0

    # ── Authenticate YouTube ───────────────────────────────────────────────
    try:
        publisher_config = config.get("publisher", {})
        client = YouTubeClient(publisher_config)
        client.authenticate()
    except FileNotFoundError as exc:
        logger.error("upload_scheduler: credentials not found", extra={"stage": "upload_scheduler", "error": str(exc)})
        conn.close()
        return 1
    except (ValueError, RuntimeError) as exc:
        logger.error("upload_scheduler: YouTube auth failed", extra={"stage": "upload_scheduler", "error": str(exc)})
        conn.close()
        return 1

    # ── Upload ────────────────────────────────────────────────────────────
    updated = publish_single(record, client, config)

    # ── Persist result ─────────────────────────────────────────────────────
    try:
        adapter.update_clip_status(
            clip_id=updated.clip_id,
            new_status=updated.status,
            valid_from=(record.status,),
            error_message=updated.error_message,
        )
        if updated.youtube_id and updated.status == "published":
            adapter.update_clip_publish_info(
                clip_id=updated.clip_id,
                youtube_id=updated.youtube_id,
                published_at=updated.published_at,
            )
    except Exception as exc:
        logger.error(
            "upload_scheduler: failed to persist clip status",
            extra={"stage": "upload_scheduler", "clip_id": updated.clip_id, "error": str(exc)},
        )

    # ── Post-upload visibility transition ─────────────────────────────────
    try:
        check_visibility_transitions([updated], client, config)
    except Exception as exc:
        logger.warning("upload_scheduler: visibility transition failed", extra={"stage": "upload_scheduler", "error": str(exc)})

    # ── On success: delete artefacts ──────────────────────────────────────
    if updated.status == "published":
        logger.info(
            "upload_scheduler: upload succeeded — https://youtu.be/%s",
            updated.youtube_id,
            extra={"stage": "upload_scheduler", "youtube_id": updated.youtube_id},
        )
        _delete_clip_artefacts(updated, config)

        # Check if queue is now empty → spawn generation
        remaining = _remaining_scheduled_count(adapter)
        logger.info(
            "upload_scheduler: %d clip(s) still in queue",
            remaining,
            extra={"stage": "upload_scheduler", "remaining": remaining},
        )
        if remaining == 0:
            logger.info(
                "upload_scheduler: queue exhausted after last upload — spawning generation",
                extra={"stage": "upload_scheduler"},
            )
            _spawn_generation()
    else:
        logger.error(
            "upload_scheduler: upload failed for clip %s — %s",
            updated.clip_id,
            updated.error_message,
            extra={"stage": "upload_scheduler", "clip_id": updated.clip_id},
        )
        conn.close()
        return 1

    conn.close()
    logger.info("upload_scheduler: done", extra={"stage": "upload_scheduler", "video_id": ""})
    return 0


if __name__ == "__main__":
    sys.exit(main())
