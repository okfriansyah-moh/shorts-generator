"""Scheduler implementation for Shorts Factory.

Assigns publish dates to queued StorageRecords. Clips are ordered
by composite score (descending), then by clip_id (ascending) as
a deterministic tiebreaker. One clip per day, at the configured
publish time.

This module does NOT access the database. The orchestrator handles
all DB reads/writes via database/adapter.py.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from contracts.storage import StorageRecord

logger = logging.getLogger(__name__)


def _parse_publish_time(time_str: str) -> tuple[int, int]:
    """Parse a HH:MM time string into (hour, minute) tuple.

    Raises:
        ValueError: If the string is not in HH:MM 24-hour format or is out of range.
    """
    if not isinstance(time_str, str):
        raise ValueError(
            f"publish_time_utc must be a string in 'HH:MM' 24-hour UTC format, "
            f"got {type(time_str).__name__}"
        )
    parts = time_str.split(":")
    if len(parts) != 2:
        raise ValueError(
            f"publish_time_utc must be in 'HH:MM' format, got {time_str!r}"
        )
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError:
        raise ValueError(
            f"publish_time_utc contains non-numeric values: {time_str!r}"
        )
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(
            f"publish_time_utc out of range (hour 0-23, minute 0-59): {time_str!r}"
        )
    return hour, minute


def _find_next_available_date(
    start_date: datetime,
    occupied_dates: set[str],
    publish_hour: int,
    publish_minute: int,
) -> datetime:
    """Find the next date that doesn't have a scheduled/published clip.

    Iterates forward from start_date until finding an unoccupied date.
    Dates are compared as YYYY-MM-DD strings for determinism.

    Args:
        start_date: The earliest date to consider.
        occupied_dates: Set of YYYY-MM-DD strings already taken.
        publish_hour: Hour component of publish time (UTC).
        publish_minute: Minute component of publish time (UTC).

    Returns:
        datetime with the assigned publish date and time (UTC).
    """
    candidate = start_date
    max_lookahead = 365

    for _ in range(max_lookahead):
        date_str = candidate.strftime("%Y-%m-%d")
        if date_str not in occupied_dates:
            return candidate.replace(
                hour=publish_hour,
                minute=publish_minute,
                second=0,
                microsecond=0,
            )
        candidate += timedelta(days=1)

    # Fallback: use the day after max lookahead (shouldn't happen in practice)
    return candidate.replace(
        hour=publish_hour,
        minute=publish_minute,
        second=0,
        microsecond=0,
    )


def process(
    records: list[StorageRecord],
    existing_scheduled: list[StorageRecord],
    config: dict,
    *,
    reference_time: datetime | None = None,
) -> list[StorageRecord]:
    """Assign publish dates to queued StorageRecords.

    Clips are sorted by composite_score descending (best first),
    with clip_id ascending as a deterministic tiebreaker.
    Each clip gets a unique date, one per day, at the configured
    publish time (UTC).

    Args:
        records: List of StorageRecords with status 'queued' to schedule.
        existing_scheduled: Already-scheduled records to avoid date conflicts.
        config: Pipeline configuration dict.
        reference_time: Optional explicit UTC datetime for determinism.
            When provided, scheduling starts from this timestamp instead
            of wall-clock time. The orchestrator should pass the pipeline
            run's started_at timestamp here.

    Returns:
        List of updated StorageRecords with status 'scheduled' and
        scheduled_at populated. Records not in 'queued' status are
        returned unchanged.
    """
    scheduler_config = config.get("scheduler", {})
    publish_time_str = scheduler_config.get("publish_time_utc", "10:00")
    publish_hour, publish_minute = _parse_publish_time(publish_time_str)

    # Collect occupied dates from existing scheduled/published clips
    occupied_dates: set[str] = set()
    for rec in sorted(existing_scheduled, key=lambda r: (r.clip_id,)):
        if rec.scheduled_at and rec.status in ("scheduled", "published"):
            try:
                dt = datetime.fromisoformat(
                    rec.scheduled_at.replace("Z", "+00:00")
                )
                occupied_dates.add(dt.strftime("%Y-%m-%d"))
            except (ValueError, AttributeError):
                pass

    # Filter to only queued records and sort deterministically
    # Best scores first, clip_id as tiebreaker
    queued = [r for r in records if r.status == "queued"]
    queued.sort(key=lambda r: (-r.composite_score, r.clip_id))

    non_queued = [r for r in records if r.status != "queued"]

    if not queued:
        logger.info(
            "No queued clips to schedule",
            extra={"stage": "scheduler", "status": "no_op"},
        )
        return list(records)

    # Determine start date: tomorrow (UTC)
    # Accept an explicit reference_time for determinism; fall back to
    # wall-clock only when the caller (orchestrator) does not provide one.
    now = reference_time if reference_time is not None else datetime.now(timezone.utc)
    start_date = (now + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    scheduled_results: list[StorageRecord] = []

    for record in queued:
        publish_dt = _find_next_available_date(
            start_date, occupied_dates, publish_hour, publish_minute
        )

        scheduled_at_str = publish_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        date_str = publish_dt.strftime("%Y-%m-%d")
        occupied_dates.add(date_str)

        updated = replace(
            record,
            status="scheduled",
            scheduled_at=scheduled_at_str,
        )
        scheduled_results.append(updated)

        logger.info(
            "Clip scheduled",
            extra={
                "clip_id": record.clip_id,
                "video_id": record.video_id,
                "scheduled_at": scheduled_at_str,
                "composite_score": record.composite_score,
                "stage": "scheduler",
                "status": "scheduled",
            },
        )

        # Move start_date forward to prevent reusing this date
        start_date = publish_dt + timedelta(days=1)
        start_date = start_date.replace(
            hour=0, minute=0, second=0, microsecond=0
        )

    # Return all records: scheduled ones + non-queued ones (unchanged)
    # Sort by scheduled_at for deterministic output
    result = scheduled_results + non_queued
    result.sort(key=lambda r: (r.scheduled_at or "", r.clip_id))

    logger.info(
        "Scheduling complete",
        extra={
            "total_scheduled": len(scheduled_results),
            "total_records": len(result),
            "stage": "scheduler",
            "status": "completed",
        },
    )

    return result
