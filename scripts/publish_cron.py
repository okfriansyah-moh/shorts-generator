#!/usr/bin/env python3
"""Standalone cron entry point for the Shorts Factory publisher.

This script is designed to be invoked by a cron daemon on a regular
schedule (e.g., every 15 minutes).  It is deliberately decoupled from
the main pipeline: it does NOT import any pipeline modules.  Its only
dependencies are the database adapter (for state reads/writes), the
publisher module (for upload logic), and the project configuration.

Example crontab entry (every 15 minutes):
    */15 * * * * cd /path/to/shorts-generator && python3 scripts/publish_cron.py

Exit codes:
    0 — success (including no-op when nothing is due to publish)
    1 — configuration / credential error (retryable on next cron tick)
    2 — unrecoverable error (operator intervention required)
"""

from __future__ import annotations

import logging
import os
import sys

# Ensure the project root is on sys.path when running directly.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from core.config import load_config  # noqa: E402
from core.logging import configure_logging  # noqa: E402
from contracts.storage import StorageRecord  # noqa: E402
from database.adapter import DatabaseAdapter  # noqa: E402
from database.connection import initialize_database  # noqa: E402
from modules.publisher import process  # noqa: E402
from modules.publisher.youtube_client import YouTubeClient  # noqa: E402
from modules.publisher.visibility import check_visibility_transitions  # noqa: E402

logger = logging.getLogger(__name__)


def _row_to_storage_record(row: dict) -> StorageRecord:
    """Convert a raw database row dict to a StorageRecord DTO."""
    import json as _json

    tags_raw = row.get("tags", "")
    if isinstance(tags_raw, str) and tags_raw:
        try:
            tags = tuple(_json.loads(tags_raw))
        except (ValueError, TypeError):
            tags = tuple(t.strip() for t in tags_raw.split(",") if t.strip())
    elif isinstance(tags_raw, (list, tuple)):
        tags = tuple(tags_raw)
    else:
        tags = ()

    return StorageRecord(
        clip_id=row["clip_id"],
        video_id=row["video_id"],
        status=row.get("status", "generated"),
        composite_score=float(row.get("composite_score", 0.0) or 0.0),
        file_paths={
            "video": row.get("video_path", "") or "",
            "thumbnail": row.get("thumbnail_path", "") or "",
            "metadata": "",
            "subtitles": "",
            "narration": "",
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


def _build_client(config: dict) -> YouTubeClient:
    """Instantiate and authenticate a YouTubeClient.

    Args:
        config: Full pipeline configuration dict.

    Returns:
        Authenticated YouTubeClient.

    Raises:
        FileNotFoundError: If the credentials file is missing.
        ValueError: If required credential fields are absent.
        RuntimeError: If OAuth2 token refresh fails.
    """
    publisher_config = config.get("publisher", {})
    client = YouTubeClient(publisher_config)
    client.authenticate()
    return client


def _load_scheduled_records(adapter: DatabaseAdapter, video_id: str | None) -> list:
    """Load StorageRecords that are candidates for publishing or visibility update.

    If *video_id* is provided, only records for that video are loaded.
    Otherwise all records with status in ('scheduled', 'published') are loaded.

    Args:
        adapter: DatabaseAdapter instance.
        video_id: Optional filter; None means process all videos.

    Returns:
        List of StorageRecord DTOs.
    """
    if video_id:
        rows = adapter.get_clips_for_video(video_id)
        return [
            _row_to_storage_record(r)
            for r in rows
            if r.get("status") in ("scheduled", "published")
        ]
    rows = adapter.get_clips_by_status(["scheduled", "published"])
    return [_row_to_storage_record(r) for r in rows]


def main(argv: list[str] | None = None) -> int:
    """Entry point for the publish cron job.

    Args:
        argv: Command-line arguments (defaults to sys.argv[1:]).
            Optional positional argument: video_id to restrict processing.

    Returns:
        Exit code (0, 1, or 2).
    """
    argv = argv if argv is not None else sys.argv[1:]

    # Optional: restrict to a single video_id
    video_id: str | None = argv[0] if argv else None

    # ── Configuration ─────────────────────────────────────────────────────
    try:
        config = load_config()
    except Exception as exc:
        # Configuration errors are not retryable without operator action.
        print(f"[publish_cron] FATAL: failed to load config: {exc}", file=sys.stderr)
        return 2

    log_level = config.get("logging", {}).get("level", "INFO")
    log_file = config.get("logging", {}).get("log_file")
    configure_logging(level=log_level, log_file=log_file)

    logger.info(
        "publish_cron: starting",
        extra={"stage": "publish_cron", "video_id": video_id or "all"},
    )

    # ── Database ───────────────────────────────────────────────────────────
    try:
        db_path = config.get("database", {}).get("path", "shorts.db")
        conn = initialize_database(db_path)
        adapter = DatabaseAdapter(conn)
    except Exception as exc:
        logger.error(
            "publish_cron: database initialisation failed",
            extra={"stage": "publish_cron", "error": str(exc)},
        )
        return 1

    # ── YouTube client ─────────────────────────────────────────────────────
    try:
        client = _build_client(config)
    except FileNotFoundError as exc:
        logger.error(
            "publish_cron: credentials file not found",
            extra={"stage": "publish_cron", "error": str(exc)},
        )
        return 1
    except (ValueError, RuntimeError) as exc:
        logger.error(
            "publish_cron: YouTube authentication failed",
            extra={"stage": "publish_cron", "error": str(exc)},
        )
        return 1

    # ── Load candidate records ─────────────────────────────────────────────
    try:
        records = _load_scheduled_records(adapter, video_id)
    except Exception as exc:
        logger.error(
            "publish_cron: failed to load records from database",
            extra={"stage": "publish_cron", "error": str(exc)},
        )
        return 1

    if not records:
        logger.info(
            "publish_cron: no candidate records, exiting",
            extra={"stage": "publish_cron", "status": "no_op"},
        )
        return 0

    # ── Publish eligible clips ─────────────────────────────────────────────
    try:
        updated_records = process(records, client, config)
    except Exception as exc:
        logger.error(
            "publish_cron: publish batch failed unexpectedly",
            extra={"stage": "publish_cron", "error": str(exc)},
        )
        return 1

    # ── Persist updated states ─────────────────────────────────────────────
    original_by_id = {r.clip_id: r for r in records}
    for record in updated_records:
        original = original_by_id.get(record.clip_id)
        if original is None or original.status == record.status:
            # No change — skip DB write.
            continue
        try:
            adapter.update_clip_status(
                clip_id=record.clip_id,
                new_status=record.status,
                valid_from=(original.status,),
                error_message=record.error_message,
            )
            # Persist youtube_id and published_at if the clip was published
            if record.youtube_id and record.status == "published":
                adapter.update_clip_publish_info(
                    clip_id=record.clip_id,
                    youtube_id=record.youtube_id,
                    published_at=record.published_at,
                )
        except Exception as exc:
            logger.error(
                "publish_cron: failed to persist status for clip",
                extra={
                    "stage": "publish_cron",
                    "clip_id": record.clip_id,
                    "error": str(exc),
                },
            )

    # ── Visibility transitions (unlisted → public) ─────────────────────────
    try:
        check_visibility_transitions(updated_records, client, config)
    except Exception as exc:
        # Non-fatal: log and continue.
        logger.warning(
            "publish_cron: visibility transition check failed",
            extra={"stage": "publish_cron", "error": str(exc)},
        )

    logger.info(
        "publish_cron: finished",
        extra={"stage": "publish_cron", "status": "done"},
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
