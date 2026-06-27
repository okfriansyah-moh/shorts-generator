#!/usr/bin/env python3
"""Scheduled runner for Shorts Factory.

Queue-aware logic (runs twice daily via Cowork scheduler):
  1. If there are clips with status 'generated' or 'scheduled' in the DB
     → run publish_cron.py to exhaust the queue first.
  2. If the queue is empty
     → pick the oldest unprocessed video from raw/ and run run_pipeline.py on it.
     → after the pipeline, export newly generated clip data to
       output/pending_ai_metadata.json so the Cowork Claude agent can
       enhance the metadata without a separate API key.

The raw/.processed file tracks which videos have already been sent through
the pipeline so the same file is never processed twice.

Usage (direct):
    python3 scripts/scheduled_run.py

Cron-equivalent (via Cowork scheduler — 8am and 8pm daily):
    0 8 * * *  cd /path/to/shorts-generator && python3 scripts/scheduled_run.py
    0 20 * * * cd /path/to/shorts-generator && python3 scripts/scheduled_run.py
"""

from __future__ import annotations

import glob
import json
import logging
import os
import subprocess
import sys

# ---------------------------------------------------------------------------
# Project root on sys.path
# ---------------------------------------------------------------------------
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from core.config import load_config  # noqa: E402
from core.logging import configure_logging  # noqa: E402
from database.adapter import DatabaseAdapter  # noqa: E402
from database.connection import initialize_database  # noqa: E402

logger = logging.getLogger(__name__)

RAW_FOLDER = os.path.join(_PROJECT_ROOT, "raw")
PROCESSED_LEDGER = os.path.join(RAW_FOLDER, ".processed")
VIDEO_EXTENSIONS = (".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pending_clip_count(adapter: DatabaseAdapter) -> int:
    """Return number of clips that are generated/scheduled but not yet published."""
    rows = adapter.get_clips_by_status(["generated", "scheduled"])
    return len(rows)


def _load_processed() -> set[str]:
    """Return the set of video basenames already sent through the pipeline."""
    if not os.path.exists(PROCESSED_LEDGER):
        return set()
    with open(PROCESSED_LEDGER) as fh:
        return {line.strip() for line in fh if line.strip()}


def _mark_processed(basename: str) -> None:
    """Append *basename* to the processed ledger."""
    processed = _load_processed()
    processed.add(basename)
    os.makedirs(RAW_FOLDER, exist_ok=True)
    with open(PROCESSED_LEDGER, "w") as fh:
        fh.write("\n".join(sorted(processed)) + "\n")


def _export_pending_ai_metadata(config: dict) -> str | None:
    """Export newly-generated clips to output/pending_ai_metadata.json.

    This file is read by the Cowork Claude agent, which generates viral
    metadata and writes output/ai_metadata_results.json, which is then
    applied by scripts/apply_ai_metadata.py — no Anthropic API key needed.

    Returns the export file path, or None if nothing to export.
    """
    export_path = os.path.join(_PROJECT_ROOT, "output", "pending_ai_metadata.json")
    try:
        db_path = config.get("paths", {}).get("database", "output/shorts_factory.db")
        if not os.path.isabs(db_path):
            db_path = os.path.join(_PROJECT_ROOT, db_path)
        conn = initialize_database(db_path)
        adapter = DatabaseAdapter(conn)
        rows = adapter.get_clips_by_status(["generated"])
        conn.close()
    except Exception as exc:
        logger.warning(f"[scheduled_run] Could not export clip data: {exc}")
        return None

    if not rows:
        return None

    clips_data = []
    video_type = config.get("video_type", "gameplay")
    channel_name = config.get("channel", {}).get("name", "")

    for row in rows:
        clips_data.append({
            "clip_id": row.get("clip_id", ""),
            "video_id": row.get("video_id", ""),
            "composite_score": float(row.get("composite_score") or 0.0),
            "duration_seconds": float(row.get("duration") or 0.0),
            "current_title": row.get("title", "") or "",
            "current_description": row.get("description", "") or "",
            "current_tags": row.get("tags", "") or "",
            "category": row.get("category", "Gaming") or "Gaming",
            "video_type": video_type,
            "channel_name": channel_name,
        })

    os.makedirs(os.path.dirname(export_path), exist_ok=True)
    with open(export_path, "w") as f:
        json.dump({"clips": clips_data}, f, indent=2)

    logger.info(f"[scheduled_run] Exported {len(clips_data)} clips to {export_path}")
    return export_path


def _next_raw_video() -> str | None:
    """Return the path of any unprocessed video in raw/, or None.

    Ordering does not matter — the pipeline processes one video per run and
    every unprocessed file will be picked up on a subsequent run regardless.
    Filenames are sorted alphabetically for deterministic selection.
    Already-processed filenames are tracked in raw/.processed and skipped.
    """
    os.makedirs(RAW_FOLDER, exist_ok=True)
    candidates: list[str] = []
    for ext in VIDEO_EXTENSIONS:
        candidates.extend(glob.glob(os.path.join(RAW_FOLDER, f"*{ext}")))
        candidates.extend(glob.glob(os.path.join(RAW_FOLDER, f"*{ext.upper()}")))

    processed = _load_processed()
    unprocessed = sorted(
        p for p in candidates
        if os.path.basename(p) not in processed
    )
    return unprocessed[0] if unprocessed else None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    os.chdir(_PROJECT_ROOT)

    # Load config & logging
    try:
        config = load_config()
    except Exception as exc:
        print(f"[scheduled_run] FATAL: config load failed: {exc}", file=sys.stderr)
        return 2

    log_level = config.get("logging", {}).get("level", "INFO")
    log_file = config.get("logging", {}).get("log_file")
    configure_logging(level=log_level, log_file=log_file)

    logger.info("scheduled_run: starting", extra={"stage": "scheduled_run", "video_id": ""})

    # Open DB to check queue
    try:
        db_path = config.get("paths", {}).get("database", "output/shorts_factory.db")
        if not os.path.isabs(db_path):
            db_path = os.path.join(_PROJECT_ROOT, db_path)
        conn = initialize_database(db_path)
        adapter = DatabaseAdapter(conn)
        pending = _pending_clip_count(adapter)
        conn.close()
    except Exception as exc:
        logger.error(
            "scheduled_run: DB error",
            extra={"stage": "scheduled_run", "video_id": "", "error": str(exc)},
        )
        return 1

    # ── Branch: publish first if queue has clips ──────────────────────────
    if pending > 0:
        logger.info(
            "scheduled_run: %d clip(s) pending — running publish_cron",
            pending,
            extra={"stage": "scheduled_run", "video_id": "", "pending": pending},
        )
        result = subprocess.run(
            [sys.executable, os.path.join(_PROJECT_ROOT, "scripts", "publish_cron.py")],
            cwd=_PROJECT_ROOT,
        )
        logger.info(
            "scheduled_run: publish_cron exited",
            extra={"stage": "scheduled_run", "video_id": "", "exit_code": result.returncode},
        )
        return result.returncode

    # ── Branch: queue empty — generate new shorts ─────────────────────────
    video_path = _next_raw_video()
    if video_path is None:
        logger.info(
            "scheduled_run: queue empty and no unprocessed videos in raw/ — nothing to do",
            extra={"stage": "scheduled_run", "video_id": ""},
        )
        return 0

    logger.info(
        "scheduled_run: queue empty — generating from %s",
        os.path.basename(video_path),
        extra={"stage": "scheduled_run", "video_id": "", "video_path": video_path},
    )
    result = subprocess.run(
        [sys.executable, os.path.join(_PROJECT_ROOT, "scripts", "generation_scheduler.py")],
        cwd=_PROJECT_ROOT,
    )
    if result.returncode != 0:
        logger.error(
            "scheduled_run: generation scheduler failed for %s (exit %d)",
            os.path.basename(video_path),
            result.returncode,
            extra={"stage": "scheduled_run", "video_id": "", "exit_code": result.returncode},
        )
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
