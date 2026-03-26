"""Publisher implementation for Shorts Factory.

Orchestrates the upload of scheduled clips to YouTube with full
metadata, thumbnail, retry logic, and status tracking.

This module does NOT access the database. The orchestrator handles
all DB reads/writes via database/adapter.py. The publisher receives
StorageRecord DTOs and returns updated StorageRecord DTOs.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import replace
from datetime import datetime, timezone

from contracts.storage import StorageRecord
from .youtube_client import (
    UploadResult,
    YouTubeClient,
)

logger = logging.getLogger(__name__)

# Default retry delays in seconds (exponential backoff)
DEFAULT_RETRY_DELAYS = (60, 300, 900)


def _resolve_file_path(
    relative_path: str,
    output_dir: str,
) -> str:
    """Resolve a relative file path against the output directory.

    Args:
        relative_path: Path relative to output_dir.
        output_dir: Root output directory.

    Returns:
        Absolute path to the file.
    """
    if os.path.isabs(relative_path):
        return relative_path
    return os.path.join(output_dir, relative_path)


def _attempt_upload(
    record: StorageRecord,
    client: YouTubeClient,
    config: dict,
) -> UploadResult:
    """Attempt a single video upload to YouTube.

    Args:
        record: StorageRecord with file paths and metadata.
        client: Authenticated YouTubeClient.
        config: Publisher configuration dict.

    Returns:
        UploadResult from the upload attempt.
    """
    output_dir = config.get("paths", {}).get("output_dir", "output")
    publisher_config = config.get("publisher", {})
    initial_visibility = publisher_config.get("initial_visibility", "unlisted")

    video_path = _resolve_file_path(
        record.file_paths.get("video", ""), output_dir
    )

    return client.upload_video(
        video_path=video_path,
        title=record.title,
        description=record.description,
        tags=record.tags,
        category=record.category,
        privacy=initial_visibility,
    )


def _attempt_thumbnail(
    record: StorageRecord,
    youtube_id: str,
    client: YouTubeClient,
    config: dict,
) -> bool:
    """Attempt to set a custom thumbnail on the uploaded video.

    Thumbnail upload failure is non-fatal — the video keeps YouTube's
    auto-generated thumbnail. A warning is logged but the clip is
    still considered published.

    Args:
        record: StorageRecord with file paths.
        youtube_id: YouTube video ID.
        client: Authenticated YouTubeClient.
        config: Publisher configuration dict.

    Returns:
        True if thumbnail was set successfully.
    """
    output_dir = config.get("paths", {}).get("output_dir", "output")
    thumbnail_path = _resolve_file_path(
        record.file_paths.get("thumbnail", ""), output_dir
    )

    if not thumbnail_path or not os.path.isfile(thumbnail_path):
        logger.warning(
            "Thumbnail file not found, skipping thumbnail upload",
            extra={
                "clip_id": record.clip_id,
                "stage": "publisher",
                "status": "thumbnail_missing",
            },
        )
        return False

    result = client.set_thumbnail(youtube_id, thumbnail_path)

    if not result.success:
        logger.warning(
            "Thumbnail upload failed, video uses auto-generated thumbnail",
            extra={
                "clip_id": record.clip_id,
                "youtube_id": youtube_id,
                "error": result.error_message,
                "stage": "publisher",
                "status": "thumbnail_failed",
            },
        )
        return False

    logger.info(
        "Thumbnail set successfully",
        extra={
            "clip_id": record.clip_id,
            "youtube_id": youtube_id,
            "stage": "publisher",
            "status": "thumbnail_set",
        },
    )
    return True


def publish_single(
    record: StorageRecord,
    client: YouTubeClient,
    config: dict,
    *,
    sleep_fn: object = time.sleep,
    reference_time: datetime | None = None,
) -> StorageRecord:
    """Publish a single clip to YouTube with retry logic.

    Implements exponential backoff retry: up to ``max_retries``
    attempts with configurable delays between attempts. If all
    retries fail, the clip is marked as ``failed``.

    Idempotency: if the record already has a ``youtube_id``, it is
    returned immediately — no re-upload occurs.

    Args:
        record: StorageRecord with status 'scheduled' and
            scheduled_at <= now.
        client: Authenticated YouTubeClient instance.
        config: Pipeline configuration dict.
        sleep_fn: Callable for sleeping between retries (for testing).
        reference_time: Explicit UTC time for deterministic testing.

    Returns:
        Updated StorageRecord with status 'published' or 'failed'.
    """
    # Idempotency: already uploaded
    if record.youtube_id:
        logger.info(
            "Clip already has youtube_id, skipping upload",
            extra={
                "clip_id": record.clip_id,
                "youtube_id": record.youtube_id,
                "stage": "publisher",
                "status": "already_published",
            },
        )
        if record.status != "published":
            return replace(record, status="published")
        return record

    publisher_config = config.get("publisher", {})
    max_retries = publisher_config.get("max_retries", 3)
    retry_delays = tuple(publisher_config.get("retry_delays", DEFAULT_RETRY_DELAYS))

    last_error: str | None = None

    for attempt in range(max_retries):
        logger.info(
            "Publishing attempt",
            extra={
                "clip_id": record.clip_id,
                "video_id": record.video_id,
                "attempt": attempt + 1,
                "max_retries": max_retries,
                "stage": "publisher",
                "status": "attempting",
            },
        )

        upload_result = _attempt_upload(record, client, config)

        if upload_result.success and upload_result.youtube_id:
            # Upload succeeded — set thumbnail (non-fatal if it fails)
            _attempt_thumbnail(
                record, upload_result.youtube_id, client, config
            )

            now = (
                reference_time
                if reference_time is not None
                else datetime.now(timezone.utc)
            )
            published_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")

            updated = replace(
                record,
                status="published",
                youtube_id=upload_result.youtube_id,
                published_at=published_at,
                retry_count=attempt,
            )

            logger.info(
                "Clip published successfully",
                extra={
                    "clip_id": record.clip_id,
                    "video_id": record.video_id,
                    "youtube_id": upload_result.youtube_id,
                    "published_at": published_at,
                    "attempts": attempt + 1,
                    "stage": "publisher",
                    "status": "published",
                },
            )
            return updated

        # Upload failed
        last_error = upload_result.error_message or "Unknown upload error"

        if upload_result.quota_exceeded:
            logger.warning(
                "YouTube API quota exceeded, stopping publish attempts",
                extra={
                    "clip_id": record.clip_id,
                    "attempt": attempt + 1,
                    "stage": "publisher",
                    "status": "quota_exceeded",
                },
            )
            # Don't retry on quota — wait for next cron cycle
            break

        logger.warning(
            "Upload attempt failed",
            extra={
                "clip_id": record.clip_id,
                "attempt": attempt + 1,
                "max_retries": max_retries,
                "error": last_error,
                "stage": "publisher",
                "status": "retry",
            },
        )

        # Sleep before retry (except after last attempt)
        if attempt < max_retries - 1:
            delay_idx = min(attempt, len(retry_delays) - 1)
            delay = retry_delays[delay_idx]
            logger.info(
                "Waiting before retry",
                extra={
                    "clip_id": record.clip_id,
                    "delay_seconds": delay,
                    "stage": "publisher",
                },
            )
            sleep_fn(delay)

    # All retries exhausted — mark as failed
    failed = replace(
        record,
        status="failed",
        error_message=last_error,
        retry_count=max_retries,
    )

    logger.error(
        "Clip publishing failed after all retries",
        extra={
            "clip_id": record.clip_id,
            "video_id": record.video_id,
            "max_retries": max_retries,
            "error": last_error,
            "stage": "publisher",
            "status": "failed",
        },
    )

    return failed


def process(
    records: list[StorageRecord],
    client: YouTubeClient,
    config: dict,
    *,
    sleep_fn: object = time.sleep,
    reference_time: datetime | None = None,
) -> list[StorageRecord]:
    """Publish all eligible scheduled clips to YouTube.

    Only clips with ``status == 'scheduled'`` and
    ``scheduled_at <= now`` are eligible for publishing. Clips are
    processed in deterministic order: sorted by ``scheduled_at``
    ascending, then ``clip_id`` ascending as tiebreaker.

    Failed clips do not block subsequent clips in the queue.

    Args:
        records: List of StorageRecords to consider for publishing.
        client: Authenticated YouTubeClient instance.
        config: Pipeline configuration dict.
        sleep_fn: Callable for sleeping between retries (mockable).
        reference_time: Explicit UTC time for deterministic eligibility
            checks and published_at timestamps.

    Returns:
        List of updated StorageRecords. Published clips have
        status='published' with youtube_id and published_at set.
        Failed clips have status='failed' with error_message set.
        Non-eligible clips are returned unchanged.
    """
    now = (
        reference_time
        if reference_time is not None
        else datetime.now(timezone.utc)
    )
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    # Split into eligible and non-eligible
    eligible: list[StorageRecord] = []
    non_eligible: list[StorageRecord] = []

    for record in records:
        if record.status == "scheduled" and record.scheduled_at:
            if record.scheduled_at <= now_iso:
                eligible.append(record)
            else:
                non_eligible.append(record)
        else:
            non_eligible.append(record)

    # Sort eligible clips deterministically:
    # scheduled_at ascending (publish oldest first), clip_id as tiebreaker
    eligible.sort(key=lambda r: (r.scheduled_at or "", r.clip_id))

    if not eligible:
        logger.info(
            "No eligible clips to publish",
            extra={
                "stage": "publisher",
                "status": "no_op",
                "total_records": len(records),
            },
        )
        return list(records)

    logger.info(
        "Starting publish batch",
        extra={
            "eligible_count": len(eligible),
            "total_records": len(records),
            "stage": "publisher",
            "status": "started",
        },
    )

    results: list[StorageRecord] = []
    published_count = 0
    failed_count = 0

    for record in eligible:
        updated = publish_single(
            record, client, config,
            sleep_fn=sleep_fn,
            reference_time=reference_time,
        )
        results.append(updated)

        if updated.status == "published":
            published_count += 1
        elif updated.status == "failed":
            failed_count += 1

    # Combine results: published/failed + non-eligible (unchanged)
    all_results = results + non_eligible

    # Sort output deterministically: by scheduled_at, then clip_id
    all_results.sort(key=lambda r: (r.scheduled_at or "", r.clip_id))

    logger.info(
        "Publish batch complete",
        extra={
            "published": published_count,
            "failed": failed_count,
            "skipped": len(non_eligible),
            "stage": "publisher",
            "status": "completed",
        },
    )

    return all_results
