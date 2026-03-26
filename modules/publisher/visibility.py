"""Visibility transition logic for Shorts Factory publisher.

Handles the delayed unlisted → public transition for published clips.
After a configurable delay (default 30 minutes), clips that were
uploaded as "unlisted" are switched to "public" visibility.

This module does NOT access the database. The orchestrator handles
all DB reads/writes via database/adapter.py.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from contracts.storage import StorageRecord
from .youtube_client import YouTubeClient

logger = logging.getLogger(__name__)


def check_visibility_transitions(
    records: list[StorageRecord],
    client: YouTubeClient,
    config: dict,
    *,
    reference_time: datetime | None = None,
) -> list[StorageRecord]:
    """Check published clips for visibility transition eligibility.

    Clips that were published more than ``public_delay_minutes`` ago
    and still have a youtube_id are transitioned from unlisted to
    public.  This function is idempotent: clips already made public
    (or without a youtube_id) are skipped.

    Args:
        records: List of StorageRecords to check.
        client: Authenticated YouTubeClient instance.
        config: Publisher configuration dict.
        reference_time: Explicit UTC time for deterministic testing.
            Falls back to wall-clock when not provided.

    Returns:
        List of StorageRecords, possibly with updated status info.
    """
    publisher_config = config.get("publisher", {})
    delay_minutes = publisher_config.get("public_delay_minutes", 30)

    now = reference_time if reference_time is not None else datetime.now(timezone.utc)

    updated: list[StorageRecord] = []

    for record in sorted(records, key=lambda r: (r.clip_id,)):
        if record.status != "published" or not record.youtube_id:
            updated.append(record)
            continue

        if not record.published_at:
            updated.append(record)
            continue

        try:
            published_dt = datetime.fromisoformat(
                record.published_at.replace("Z", "+00:00")
            )
        except (ValueError, AttributeError):
            logger.warning(
                "Invalid published_at timestamp, skipping visibility transition",
                extra={
                    "clip_id": record.clip_id,
                    "published_at": record.published_at,
                    "stage": "publisher",
                },
            )
            updated.append(record)
            continue

        elapsed_minutes = (now - published_dt).total_seconds() / 60.0

        if elapsed_minutes < delay_minutes:
            logger.info(
                "Clip not yet eligible for public visibility",
                extra={
                    "clip_id": record.clip_id,
                    "youtube_id": record.youtube_id,
                    "elapsed_minutes": round(elapsed_minutes, 1),
                    "delay_minutes": delay_minutes,
                    "stage": "publisher",
                    "status": "waiting",
                },
            )
            updated.append(record)
            continue

        # Attempt visibility transition
        result = client.update_visibility(record.youtube_id, "public")

        if result.success:
            logger.info(
                "Clip visibility updated to public",
                extra={
                    "clip_id": record.clip_id,
                    "youtube_id": record.youtube_id,
                    "stage": "publisher",
                    "status": "public",
                },
            )
            updated.append(record)
        else:
            logger.warning(
                "Failed to update clip visibility to public",
                extra={
                    "clip_id": record.clip_id,
                    "youtube_id": record.youtube_id,
                    "error": result.error_message,
                    "stage": "publisher",
                    "status": "visibility_failed",
                },
            )
            updated.append(record)

    return updated
