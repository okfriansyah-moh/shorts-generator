"""Pipeline run summary report for Shorts Factory analytics.

Aggregates clip-level and scene-level data into a PipelineReport DTO,
prints a structured summary to stdout (via logging), and writes a
JSON report to ``{output_dir}/{video_id}/report.json``.

This module does NOT access the database. All inputs are frozen DTOs
passed by the orchestrator.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict
from datetime import datetime, timezone

from contracts.analytics import PipelineReport
from contracts.clip import ClipList
from contracts.scoring import ScoredSceneList
from contracts.storage import StorageRecord

from .publish_report import compute as compute_publish_report
from .quality_metrics import compute as compute_quality_metrics

logger = logging.getLogger(__name__)


def _report_to_dict(report: PipelineReport) -> dict:
    """Convert PipelineReport DTO to a JSON-serialisable dict."""
    return asdict(report)


def _write_json_report(report: PipelineReport, output_dir: str, config: dict | None = None) -> str:
    """Write the JSON report to ``{output_dir}/{video_dir_name}/report.json``.

    Creates intermediate directories if they do not exist.

    Returns:
        Absolute path to the written file.

    Raises:
        OSError: If the file cannot be written. Caller handles this gracefully.
    """
    video_dir_name = (
        config.get("_runtime", {}).get("video_dir_name", report.video_id)
        if config else report.video_id
    )
    video_dir = os.path.join(output_dir, video_dir_name)
    os.makedirs(video_dir, exist_ok=True)
    report_path = os.path.join(video_dir, "report.json")
    with open(report_path, "w", encoding="utf-8") as fh:
        json.dump(_report_to_dict(report), fh, indent=2, sort_keys=True)
    return os.path.abspath(report_path)


def _print_summary(report: PipelineReport) -> None:
    """Log a human-readable summary of the pipeline report."""
    pub = report.publishing
    qual = report.quality

    logger.info(
        "=== PIPELINE REPORT ===\n"
        "  run_id             : %s\n"
        "  video_id           : %s\n"
        "  clips generated    : %d\n"
        "  clips stored       : %d\n"
        "  avg clip score     : %.3f\n"
        "  duration (min/max/mean): %.1fs / %.1fs / %.1fs\n"
        "  scenes scored      : %d  (rejection rate: %.1f%%)\n"
        "  avg face visibility: %.1f%%\n"
        "  published / scheduled / queued / failed: %d / %d / %d / %d\n"
        "  upload success rate: %.1f%%\n"
        "  queue depth        : %.1f days\n"
        "  report written to  : %s",
        report.run_id,
        report.video_id,
        report.total_clips_generated,
        report.total_clips_stored,
        report.avg_composite_score,
        report.min_duration_seconds,
        report.max_duration_seconds,
        report.mean_duration_seconds,
        qual.total_scenes_scored,
        qual.rejection_rate * 100,
        qual.avg_face_visibility * 100,
        pub.published_count,
        pub.scheduled_count,
        pub.queued_count,
        pub.failed_count,
        pub.upload_success_rate * 100,
        pub.queue_depth_days,
        report.report_path,
    )


def process(
    video_id: str,
    run_id: str,
    clip_list: ClipList,
    scored_scenes: ScoredSceneList,
    storage_records: tuple[StorageRecord, ...],
    output_dir: str,
    config: dict,
) -> PipelineReport:
    """Generate a full analytics report for a completed pipeline run.

    Computes quality metrics and publishing status, prints a structured
    summary via logging, and writes a JSON report to disk.

    Args:
        video_id: Parent video reference. 16 lowercase hex chars.
        run_id: Pipeline run identifier. May be empty string if unavailable.
        clip_list: ClipList produced by clip_builder (provides duration data).
        scored_scenes: ScoredSceneList produced by scoring (provides quality data).
        storage_records: All StorageRecords for this video (provides publish status).
        output_dir: Root output directory. Report is written to
            ``{output_dir}/{video_id}/report.json``.
        config: Full pipeline configuration dict (passed by orchestrator).

    Returns:
        PipelineReport DTO. If the JSON write fails, ``report_path`` will be
        an empty string and a WARN log is emitted — the report itself is
        returned normally so the pipeline is not blocked.
    """
    generated_at = datetime.now(tz=timezone.utc).isoformat()

    # ── Clip-level duration statistics ───────────────────────────────────────
    clips = sorted(clip_list.clips, key=lambda c: c.start_time)
    total_clips_generated = clip_list.total_clips
    total_clips_stored = len(storage_records)

    durations = [c.duration for c in clips]
    if durations:
        min_dur = min(durations)
        max_dur = max(durations)
        mean_dur = sum(durations) / len(durations)
    else:
        min_dur = max_dur = mean_dur = 0.0

    clip_scores = sorted(r.composite_score for r in storage_records)
    avg_clip_score = sum(clip_scores) / len(clip_scores) if clip_scores else 0.0

    # ── Sub-report computation ────────────────────────────────────────────────
    quality = compute_quality_metrics(scored_scenes, config)
    publishing = compute_publish_report(video_id, storage_records, config)

    # ── Assemble preliminary report (path unknown until write) ───────────────
    report = PipelineReport(
        run_id=run_id,
        video_id=video_id,
        total_clips_generated=total_clips_generated,
        total_clips_stored=total_clips_stored,
        avg_composite_score=round(avg_clip_score, 4),
        min_duration_seconds=round(min_dur, 2),
        max_duration_seconds=round(max_dur, 2),
        mean_duration_seconds=round(mean_dur, 2),
        quality=quality,
        publishing=publishing,
        report_path="",
        generated_at=generated_at,
    )

    # ── Write JSON to disk ────────────────────────────────────────────────────
    report_path = ""
    try:
        report_path = _write_json_report(report, output_dir, config)
        logger.debug("analytics: report written to %s", report_path)
    except OSError as exc:
        logger.warning(
            "analytics: failed to write JSON report for video_id=%s: %s",
            video_id,
            exc,
        )

    # Re-create with final path (frozen dataclass)
    from dataclasses import replace

    report = replace(report, report_path=report_path)

    _print_summary(report)
    return report
