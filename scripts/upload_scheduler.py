#!/usr/bin/env python3
"""Upload scheduler for Shorts Factory.

Runs on a recurring schedule (e.g., 3× daily). Each invocation:
  1. Finds the next due 'scheduled' clip and uploads it to all enabled
     platforms (YouTube, TikTok, Instagram Reels, Facebook Reels) concurrently.
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
from dataclasses import replace
from datetime import datetime, timezone

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from core.config import load_config                                            # noqa: E402
from core.account_loader import load_account_config, resolve_account          # noqa: E402
from core.logging import configure_logging                                     # noqa: E402
from contracts.storage import StorageRecord                                    # noqa: E402
from database.adapter import DatabaseAdapter                                   # noqa: E402
from database.connection import initialize_database                            # noqa: E402
from modules.publisher.youtube_client import YouTubeClient                     # noqa: E402
from modules.publisher.multi_platform import publish_to_all_platforms          # noqa: E402
from modules.publisher.visibility import check_visibility_transitions          # noqa: E402
from modules.notifier.telegram import TelegramNotifier, build_publish_message  # noqa: E402

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _db_path(config: dict) -> str:
    path = config.get("paths", {}).get("database", "output/shorts_factory.db")
    return path if os.path.isabs(path) else os.path.join(_PROJECT_ROOT, path)


def _resolve_path(path: str, output_dir: str) -> str:
    """Resolve a DB-stored path to absolute using the account-scoped output_dir as base."""
    if not path or os.path.isabs(path):
        return path
    resolved = os.path.join(output_dir, path)
    if os.path.exists(resolved):
        return resolved
    return os.path.join(_PROJECT_ROOT, path)


def _row_to_storage_record(row: dict, output_dir: str) -> StorageRecord:
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
            "video":      _resolve_path(row.get("video_path", "") or "", output_dir),
            "thumbnail":  _resolve_path(row.get("thumbnail_path", "") or "", output_dir),
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


def _next_due_record(adapter: DatabaseAdapter, account_name: str, output_dir: str) -> StorageRecord | None:
    """Return the oldest scheduled clip whose scheduled_at <= now, or None."""
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = adapter.get_clips_by_status(["scheduled"], account_name=account_name)
    due = [
        r for r in rows
        if r.get("scheduled_at") and r["scheduled_at"] <= now_iso
    ]
    if not due:
        return None
    due.sort(key=lambda r: (r.get("scheduled_at", ""), r.get("clip_id", "")))
    return _row_to_storage_record(due[0], output_dir)


def _remaining_scheduled_count(adapter: DatabaseAdapter, account_name: str) -> int:
    return len(adapter.get_clips_by_status(["scheduled"], account_name=account_name))


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


def _spawn_generation(account_name: str) -> None:
    """Fire-and-forget: launch generation_scheduler.py in the background."""
    script = os.path.join(_PROJECT_ROOT, "scripts", "generation_scheduler.py")
    log_path = os.path.join(_PROJECT_ROOT, "output", "generation_scheduler.log")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "a") as logf:
        subprocess.Popen(
            [sys.executable, script, "--account", account_name],
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
# Telegram notification
# ---------------------------------------------------------------------------

def _notify_telegram(record, config: dict, platform_results, published_at: str) -> None:
    """Send a Telegram notification after a successful publish.

    Reads ``telegram.enabled`` from config (default: True).
    Silently skips if credentials are missing or disabled.
    A Telegram failure never aborts the pipeline.
    """
    tg_config = config.get("telegram", {})
    if not tg_config.get("enabled", True):
        return

    try:
        notifier = TelegramNotifier.from_config(config)
    except ValueError as exc:
        logger.info(
            "upload_scheduler: Telegram not configured — skipping notification (%s)",
            exc,
            extra={"stage": "upload_scheduler"},
        )
        return

    msg = build_publish_message(
        title=record.title or "(no title)",
        clip_id=record.clip_id,
        composite_score=record.composite_score,
        scheduled_at=record.scheduled_at,
        published_at=published_at,
        youtube_id=platform_results.youtube_id,
        tiktok_id=platform_results.tiktok_id,
        instagram_id=platform_results.instagram_id,
        facebook_id=platform_results.facebook_id,
        error_summary=platform_results.error_summary,
    )
    notifier.send_message(msg)
    logger.info(
        "upload_scheduler: Telegram notification sent for clip %s",
        record.clip_id,
        extra={"stage": "upload_scheduler", "clip_id": record.clip_id},
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Shorts Factory — Upload Scheduler")
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
        print(f"[upload_scheduler] FATAL: config load failed: {exc}", file=sys.stderr)
        return 2

    # ── Resolve + load account config ─────────────────────────────────────
    try:
        account_name = resolve_account(args.account)
        config = load_account_config(account_name, config, project_root=_PROJECT_ROOT)
    except (FileNotFoundError, ValueError) as exc:
        print(f"[upload_scheduler] FATAL: account config error: {exc}", file=sys.stderr)
        return 2

    configure_logging(
        level=config.get("logging", {}).get("level", "INFO"),
        log_file=config.get("logging", {}).get("log_file"),
    )
    logger.info(
        "upload_scheduler: starting (account=%s)",
        account_name,
        extra={"stage": "upload_scheduler", "video_id": ""},
    )

    # ── DB ────────────────────────────────────────────────────────────────
    try:
        conn = initialize_database(_db_path(config))
        adapter = DatabaseAdapter(conn)
    except Exception as exc:
        logger.error("upload_scheduler: DB error", extra={"stage": "upload_scheduler", "error": str(exc)})
        return 1

    # ── Resolve account-scoped output dir for path resolution ─────────────
    output_dir = config.get("paths", {}).get("output_dir", "output")
    if not os.path.isabs(output_dir):
        output_dir = os.path.join(_PROJECT_ROOT, output_dir)

    # ── Find next due clip ─────────────────────────────────────────────────
    record = _next_due_record(adapter, account_name, output_dir)

    if record is None:
        remaining = _remaining_scheduled_count(adapter, account_name)
        if remaining == 0:
            logger.info(
                "upload_scheduler: queue exhausted — spawning generation",
                extra={"stage": "upload_scheduler"},
            )
            _spawn_generation(account_name)
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

    # ── Authenticate YouTube (pre-auth so multi_platform can reuse the client) ─
    youtube_client: YouTubeClient | None = None
    try:
        publisher_config = config.get("publisher", {})
        youtube_client = YouTubeClient(publisher_config)
        youtube_client.authenticate()
    except FileNotFoundError as exc:
        logger.warning(
            "upload_scheduler: YouTube credentials not found — YouTube will be skipped: %s",
            exc,
            extra={"stage": "upload_scheduler"},
        )
        youtube_client = None
    except (ValueError, RuntimeError) as exc:
        logger.warning(
            "upload_scheduler: YouTube auth failed — YouTube will be skipped: %s",
            exc,
            extra={"stage": "upload_scheduler"},
        )
        youtube_client = None

    # ── Multi-platform upload ──────────────────────────────────────────────
    platform_results = publish_to_all_platforms(record, config, youtube_client=youtube_client)

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    if platform_results.any_success:
        updated = replace(
            record,
            status="published",
            youtube_id=platform_results.youtube_id or record.youtube_id,
            tiktok_id=platform_results.tiktok_id or record.tiktok_id,
            instagram_id=platform_results.instagram_id or record.instagram_id,
            facebook_id=platform_results.facebook_id or record.facebook_id,
            published_at=now_iso,
            error_message=platform_results.error_summary,  # partial failures logged
        )
    else:
        updated = replace(
            record,
            status="failed",
            error_message=platform_results.error_summary or "All platforms failed",
            retry_count=record.retry_count + 1,
        )

    # ── Persist result ─────────────────────────────────────────────────────
    try:
        adapter.update_clip_status(
            clip_id=updated.clip_id,
            new_status=updated.status,
            valid_from=(record.status,),
            error_message=updated.error_message,
        )
        if platform_results.any_success:
            adapter.update_clip_platform_ids(
                clip_id=updated.clip_id,
                youtube_id=platform_results.youtube_id,
                tiktok_id=platform_results.tiktok_id,
                instagram_id=platform_results.instagram_id,
                facebook_id=platform_results.facebook_id,
                published_at=now_iso,
            )
    except Exception as exc:
        logger.error(
            "upload_scheduler: failed to persist clip status",
            extra={"stage": "upload_scheduler", "clip_id": updated.clip_id, "error": str(exc)},
        )

    # ── Post-upload YouTube visibility transition ──────────────────────────
    if platform_results.youtube_id and youtube_client:
        try:
            check_visibility_transitions([updated], youtube_client, config)
        except Exception as exc:
            logger.warning("upload_scheduler: visibility transition failed", extra={"stage": "upload_scheduler", "error": str(exc)})

    # ── On success: delete artefacts only when all enabled platforms succeeded ──
    if updated.status == "published":
        logger.info(
            "upload_scheduler: publish succeeded — YT:%s TT:%s IG:%s FB:%s",
            platform_results.youtube_id,
            platform_results.tiktok_id,
            platform_results.instagram_id,
            platform_results.facebook_id,
            extra={"stage": "upload_scheduler"},
        )
        if not platform_results.errors:
            _delete_clip_artefacts(updated, config)
        else:
            logger.warning(
                "upload_scheduler: partial failure on %s — artefacts kept for retry",
                ", ".join(platform_results.errors.keys()),
                extra={"stage": "upload_scheduler", "clip_id": updated.clip_id},
            )
        _notify_telegram(updated, config, platform_results, now_iso)

        # Check if queue is now empty → spawn generation
        remaining = _remaining_scheduled_count(adapter, account_name)
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
            _spawn_generation(account_name)
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
