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
from core.logging import setup_logging  # noqa: E402
from database.adapter import DatabaseAdapter  # noqa: E402
from modules.publisher import process  # noqa: E402
from modules.publisher.youtube_client import YouTubeClient  # noqa: E402
from modules.publisher.visibility import check_visibility_transitions  # noqa: E402

logger = logging.getLogger(__name__)


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
        return list(adapter.get_clips_by_video(video_id))
    return list(adapter.get_clips_by_status(["scheduled", "published"]))


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

    setup_logging(config)

    logger.info(
        "publish_cron: starting",
        extra={"stage": "publish_cron", "video_id": video_id or "all"},
    )

    # ── Database ───────────────────────────────────────────────────────────
    try:
        adapter = DatabaseAdapter(config)
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
                status=record.status,
                youtube_id=record.youtube_id,
                published_at=record.published_at,
                error_message=record.error_message,
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
