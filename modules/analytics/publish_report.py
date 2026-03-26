"""Publishing status report computation for Shorts Factory analytics.

Aggregates StorageRecord lifecycle statuses into a PublishReport DTO.
All computation is deterministic and read-only.
"""

from __future__ import annotations

import logging

from contracts.analytics import PublishReport
from contracts.storage import StorageRecord

logger = logging.getLogger(__name__)

# Valid status values ordered for deterministic processing
_STATUS_FIELDS = ("published", "scheduled", "queued", "generated", "failed")


def compute(
    video_id: str,
    storage_records: tuple[StorageRecord, ...],
    config: dict,
) -> PublishReport:
    """Compute publishing status report from a collection of StorageRecords.

    Args:
        video_id: Parent video reference identifier.
        storage_records: All StorageRecords for this video, in any order.
        config: Pipeline configuration dict. Reads ``scheduler.posts_per_day``.

    Returns:
        PublishReport DTO with counts and derived rates.
    """
    posts_per_day: float = float(
        config.get("scheduler", {}).get("posts_per_day", 1)
    )

    # Sort for determinism before counting
    sorted_records = sorted(storage_records, key=lambda r: r.clip_id)
    total = len(sorted_records)

    counts: dict[str, int] = {s: 0 for s in _STATUS_FIELDS}
    for record in sorted_records:
        status = record.status
        if status in counts:
            counts[status] += 1
        else:
            logger.warning(
                "publish_report: unknown status %r for clip_id=%s",
                status,
                record.clip_id,
            )

    published = counts["published"]
    scheduled = counts["scheduled"]
    queued = counts["queued"]
    generated = counts["generated"]
    failed = counts["failed"]

    upload_success_rate = published / total if total > 0 else 0.0
    pending = queued + scheduled
    queue_depth_days = pending / posts_per_day if posts_per_day > 0 else 0.0

    logger.info(
        "publish_report: video_id=%s total=%d published=%d scheduled=%d "
        "queued=%d generated=%d failed=%d queue_depth_days=%.2f",
        video_id,
        total,
        published,
        scheduled,
        queued,
        generated,
        failed,
        queue_depth_days,
    )

    return PublishReport(
        video_id=video_id,
        total_clips=total,
        published_count=published,
        scheduled_count=scheduled,
        queued_count=queued,
        generated_count=generated,
        failed_count=failed,
        upload_success_rate=round(upload_success_rate, 4),
        queue_depth_days=round(queue_depth_days, 4),
    )
