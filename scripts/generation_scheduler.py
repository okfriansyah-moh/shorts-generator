#!/usr/bin/env python3
"""Generation scheduler for Shorts Factory.

Runs the full Shorts Factory pipeline on the next unprocessed raw video.
Intended to be triggered automatically by upload_scheduler.py when the
upload queue is exhausted, but can also be run directly or on its own
recurring schedule for a fully hands-off setup.

Workflow:
  1. Find the oldest unprocessed video in raw/ (tracks via raw/.processed).
  2. Run run_pipeline.py on it (blocks until complete).
  3. Export pending_ai_metadata.json so the Cowork Claude agent can
     generate viral metadata without a separate Anthropic API key.
  4. Mark the video as processed so it is never re-ingested.
  5. Exit — the upload_scheduler will pick up newly-generated clips on
     its next scheduled run.

Exit codes:
  0 — success, or no unprocessed video found (nothing to do)
  1 — pipeline failed (retryable on next invocation)
  2 — fatal config error
"""

from __future__ import annotations

import glob
import hashlib
import json
import logging
import os
import subprocess
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from core.config import load_config, load_dotenv                 # noqa: E402
load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))
from core.account_loader import load_account_config, resolve_account  # noqa: E402
from core.logging import configure_logging                       # noqa: E402
from database.adapter import DatabaseAdapter                     # noqa: E402
from database.connection import initialize_database              # noqa: E402

logger = logging.getLogger(__name__)

VIDEO_EXTENSIONS = (".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v")

# Bytes read for content fingerprinting — must match modules/ingestion/ingest.py
_FINGERPRINT_BYTES = 10 * 1024 * 1024  # 10 MB


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_processed(raw_dir: str) -> set[str]:
    ledger = os.path.join(raw_dir, ".processed")
    if not os.path.exists(ledger):
        return set()
    with open(ledger) as fh:
        return {line.strip() for line in fh if line.strip()}


def _mark_processed(basename: str, raw_dir: str) -> None:
    processed = _load_processed(raw_dir)
    processed.add(basename)
    os.makedirs(raw_dir, exist_ok=True)
    ledger = os.path.join(raw_dir, ".processed")
    with open(ledger, "w") as fh:
        fh.write("\n".join(sorted(processed)) + "\n")


def _next_raw_video(raw_dir: str) -> str | None:
    """Return the next unprocessed video in raw/<account>/ using deterministic filename ordering."""
    os.makedirs(raw_dir, exist_ok=True)
    candidates: list[str] = []
    for ext in VIDEO_EXTENSIONS:
        candidates.extend(glob.glob(os.path.join(raw_dir, f"*{ext}")))
        candidates.extend(glob.glob(os.path.join(raw_dir, f"*{ext.upper()}")))

    processed = _load_processed(raw_dir)
    unprocessed = [p for p in candidates if os.path.basename(p) not in processed]
    if not unprocessed:
        return None
    # Alphabetical ascending by filename (process in name order)
    return min(unprocessed, key=lambda p: os.path.basename(p).lower())


def _compute_video_id(file_path: str) -> str:
    """Compute content-based video_id from first 10 MB + file size.

    Identical to the algorithm in modules/ingestion/ingest._compute_video_id
    so IDs are consistent between the scheduler and the pipeline.
    """
    hasher = hashlib.sha256()
    file_size = os.path.getsize(file_path)
    with open(file_path, "rb") as fh:
        hasher.update(fh.read(_FINGERPRINT_BYTES))
    hasher.update(str(file_size).encode("ascii"))
    return hasher.hexdigest()[:16]


def _is_duplicate_source(video_path: str, config: dict) -> tuple[bool, str]:
    """Check if this video's content has already been processed.

    Computes the content hash and queries the database.  Returns
    ``(True, video_id)`` if a matching record already exists so the caller
    can add the filename to the processed ledger and skip the pipeline.

    Filename-based ledger check catches the common case cheaply.  This DB
    check catches renamed copies of the same file.
    """
    try:
        video_id = _compute_video_id(video_path)
    except OSError as exc:
        logger.warning(
            "generation_scheduler: could not fingerprint %s — %s",
            os.path.basename(video_path), exc,
            extra={"stage": "generation_scheduler", "video_id": ""},
        )
        return False, ""

    try:
        db_path = config.get("paths", {}).get("database", "output/shorts_factory.db")
        if not os.path.isabs(db_path):
            db_path = os.path.join(_PROJECT_ROOT, db_path)
        conn = initialize_database(db_path)
        adapter = DatabaseAdapter(conn)
        exists = adapter.video_id_exists(video_id)
        conn.close()
    except Exception as exc:
        logger.warning(
            "generation_scheduler: DB check failed — %s (proceeding anyway)",
            exc,
            extra={"stage": "generation_scheduler", "video_id": ""},
        )
        return False, video_id

    return exists, video_id


def _export_pending_ai_metadata(config: dict) -> str | None:
    """Export newly-generated clips to output/pending_ai_metadata.json.

    The Cowork Claude agent reads this file, generates viral metadata,
    writes output/ai_metadata_results.json, which is applied by
    scripts/apply_ai_metadata.py — no Anthropic API key required.

    Returns the export path, or None if there's nothing to export.
    """
    try:
        db_path = config.get("paths", {}).get("database", "output/shorts_factory.db")
        if not os.path.isabs(db_path):
            db_path = os.path.join(_PROJECT_ROOT, db_path)
        conn = initialize_database(db_path)
        adapter = DatabaseAdapter(conn)
        rows = adapter.get_clips_by_status(["generated", "scheduled"])
        conn.close()
    except Exception as exc:
        logger.warning(f"[generation_scheduler] Could not export clip data: {exc}")
        return None

    if not rows:
        return None

    # Derive the video output folder from clip thumbnail paths so all
    # per-video artefacts land under output/{video_id}_name/ rather than output/
    import glob as _glob
    def _video_dir(rows_: list, output_dir_: str) -> str:
        for r_ in rows_:
            thumb_ = (r_.get("thumbnail_path") or "").replace("\\", "/")
            if thumb_ and os.path.isabs(thumb_):
                # Absolute path — walk up to find the video-level folder
                parts_ = thumb_.split(os.sep)
                for i_ in range(len(parts_) - 1, 0, -1):
                    candidate_ = os.sep.join(parts_[:i_])
                    if os.path.isdir(candidate_) and os.path.dirname(candidate_) == os.path.join(_PROJECT_ROOT, output_dir_):
                        return candidate_
            vid_ = r_.get("video_id", "")
            if vid_:
                matches_ = _glob.glob(os.path.join(_PROJECT_ROOT, output_dir_, f"{vid_}_*"))
                if matches_:
                    return matches_[0]
        return os.path.join(_PROJECT_ROOT, output_dir_)  # fallback

    output_dir = config.get("paths", {}).get("output_dir", "output")
    video_dir = _video_dir(rows, output_dir)
    export_path = os.path.join(video_dir, "pending_ai_metadata.json")

    video_type = config.get("video_type", "gameplay")
    channel_name = config.get("channel", {}).get("name", "")
    clips_data = []
    for row in rows:
        clips_data.append({
            "clip_id":          row.get("clip_id", ""),
            "video_id":         row.get("video_id", ""),
            "composite_score":  float(row.get("composite_score") or 0.0),
            "duration_seconds": float(row.get("duration") or 0.0),
            "start_time_s":     int(float(row.get("start_time") or 0) / 1000),
            "end_time_s":       int(float(row.get("end_time") or 0) / 1000),
            "current_title":    row.get("title", "") or "",
            "current_description": row.get("description", "") or "",
            "current_tags":     row.get("tags", "") or "",
            "category":         row.get("category", "Gaming") or "Gaming",
            "video_type":       video_type,
            "channel_name":     channel_name,
        })

    os.makedirs(video_dir, exist_ok=True)
    with open(export_path, "w") as f:
        json.dump({"clips": clips_data}, f, indent=2)

    logger.info(f"[generation_scheduler] Exported {len(clips_data)} clips → {export_path}")
    return export_path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Shorts Factory — Generation Scheduler")
    parser.add_argument(
        "--account", default=None,
        help="Account name (folder under config/accounts/). "
             "Auto-discovered when only one account exists.",
    )
    args = parser.parse_args()

    os.chdir(_PROJECT_ROOT)

    try:
        config = load_config()
    except Exception as exc:
        print(f"[generation_scheduler] FATAL: config load failed: {exc}", file=sys.stderr)
        return 2

    # ── Resolve + load account config ─────────────────────────────────────
    try:
        account_name = resolve_account(args.account)
        config = load_account_config(account_name, config, project_root=_PROJECT_ROOT)
    except (FileNotFoundError, ValueError) as exc:
        print(f"[generation_scheduler] FATAL: account config error: {exc}", file=sys.stderr)
        return 2

    configure_logging(
        level=config.get("logging", {}).get("level", "INFO"),
        log_file=config.get("logging", {}).get("log_file"),
    )
    logger.info(
        "generation_scheduler: starting (account=%s)",
        account_name,
        extra={"stage": "generation_scheduler", "video_id": ""},
    )

    # ── Resolve account-scoped raw dir ─────────────────────────────────────
    raw_dir = config["paths"].get("raw_dir", os.path.join("raw", account_name))
    if not os.path.isabs(raw_dir):
        raw_dir = os.path.join(_PROJECT_ROOT, raw_dir)

    # ── Find next video ────────────────────────────────────────────────────
    video_path = _next_raw_video(raw_dir)
    if video_path is None:
        logger.info(
            "generation_scheduler: no unprocessed videos in %s — nothing to do",
            raw_dir,
            extra={"stage": "generation_scheduler", "video_id": ""},
        )
        return 0

    basename = os.path.basename(video_path)

    # ── Dedup: content-hash check against DB (catches renamed files) ───────
    is_dup, video_id = _is_duplicate_source(video_path, config)
    if is_dup:
        logger.warning(
            "generation_scheduler: DUPLICATE SOURCE detected — %s has the same "
            "content as an already-processed video (video_id=%s). "
            "Marking as processed and skipping.",
            basename, video_id,
            extra={"stage": "generation_scheduler", "video_id": video_id},
        )
        _mark_processed(basename, raw_dir)
        return 0

    logger.info(
        "generation_scheduler: running pipeline on %s (video_id=%s)",
        basename, video_id or "pending",
        extra={"stage": "generation_scheduler", "video_id": "", "video": basename},
    )

    # ── Run pipeline ───────────────────────────────────────────────────────
    result = subprocess.run(
        [
            sys.executable,
            os.path.join(_PROJECT_ROOT, "run_pipeline.py"),
            "--account", account_name,
            video_path,
        ],
        cwd=_PROJECT_ROOT,
    )

    if result.returncode != 0:
        logger.error(
            "generation_scheduler: pipeline failed for %s (exit %d)",
            basename,
            result.returncode,
            extra={"stage": "generation_scheduler", "video_id": "", "exit_code": result.returncode},
        )
        return 1

    # ── Mark processed ─────────────────────────────────────────────────────
    _mark_processed(basename, raw_dir)
    logger.info(
        "generation_scheduler: pipeline complete — %s marked as processed",
        basename,
        extra={"stage": "generation_scheduler", "video_id": ""},
    )

    # ── Export metadata for Cowork Claude agent ───────────────────────────
    export_path = _export_pending_ai_metadata(config)
    if export_path:
        logger.info(
            "generation_scheduler: exported pending AI metadata → %s\n"
            "  → Open Cowork and ask Claude to generate viral metadata from this file,\n"
            "    then run: python3 scripts/apply_ai_metadata.py",
            export_path,
            extra={"stage": "generation_scheduler", "video_id": ""},
        )
    else:
        logger.info(
            "generation_scheduler: no clips exported (pipeline may have produced none)",
            extra={"stage": "generation_scheduler", "video_id": ""},
        )

    logger.info("generation_scheduler: done", extra={"stage": "generation_scheduler", "video_id": ""})
    return 0


if __name__ == "__main__":
    sys.exit(main())
