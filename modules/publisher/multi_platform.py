"""Multi-platform publisher orchestrator for Shorts Factory.

Fans out a single clip upload to all enabled platforms (YouTube,
TikTok, Instagram Reels, Facebook Reels) concurrently using
ThreadPoolExecutor.  Each platform is independent — a failure on
one does not block the others.

The clip is considered successfully published if at least one
platform succeeds.  Per-platform IDs are returned in PlatformResults
for the caller to persist.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from contracts.storage import StorageRecord
from .youtube_client import YouTubeClient, UploadResult
from .tiktok_client import TikTokClient, TikTokUploadResult
from .meta_client import MetaClient, MetaUploadResult

logger = logging.getLogger(__name__)


@dataclass
class PlatformResults:
    """Holds per-platform upload outcomes for a single clip."""
    youtube_id: str | None = None
    tiktok_id: str | None = None
    instagram_id: str | None = None
    facebook_id: str | None = None
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def any_success(self) -> bool:
        return bool(self.youtube_id or self.tiktok_id or self.instagram_id or self.facebook_id)

    @property
    def error_summary(self) -> str | None:
        if not self.errors:
            return None
        return "; ".join(f"{p}: {msg}" for p, msg in self.errors.items())


def _is_platform_enabled(config: dict, platform: str) -> bool:
    """Return True if the platform is listed as enabled in config."""
    platforms = config.get("platforms", {})
    val = platforms.get(platform, "disabled")
    if isinstance(val, bool):
        return val
    return str(val).lower() in ("enabled", "true", "yes", "1")


def publish_to_all_platforms(
    record: StorageRecord,
    config: dict,
    youtube_client: YouTubeClient | None = None,
) -> PlatformResults:
    """Publish a clip to all enabled platforms concurrently.

    Args:
        record: StorageRecord with video path, title, description, tags.
        config: Full pipeline config dict.
        youtube_client: Pre-authenticated YouTubeClient (passed from
            upload_scheduler to avoid double-auth).  If None and YouTube
            is enabled, a new client is created and authenticated.

    Returns:
        PlatformResults with each platform's ID (or None on failure).
    """
    video_path = record.file_paths.get("video", "")
    results = PlatformResults()

    futures: dict[str, object] = {}

    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="publisher") as pool:

        # ── YouTube ───────────────────────────────────────────────────────
        if _is_platform_enabled(config, "youtube"):
            if youtube_client is None:
                try:
                    youtube_client = YouTubeClient(config.get("publisher", {}))
                    youtube_client.authenticate()
                except Exception as exc:
                    results.errors["youtube"] = f"Auth failed: {exc}"
                    youtube_client = None

            if youtube_client is not None:
                _client = youtube_client
                _cfg = config
                _rec = record

                def _yt_upload() -> UploadResult:
                    publisher_cfg = _cfg.get("publisher", {})
                    privacy = publisher_cfg.get("initial_visibility", "unlisted")
                    return _client.upload_video(
                        video_path=video_path,
                        title=_rec.title,
                        description=_rec.description,
                        tags=_rec.tags,
                        category=_rec.category,
                        privacy=privacy,
                    )

                futures["youtube"] = pool.submit(_yt_upload)

        # ── TikTok ───────────────────────────────────────────────────────
        if _is_platform_enabled(config, "tiktok"):
            try:
                tiktok = TikTokClient(config)
                tiktok.authenticate()
                _tt = tiktok
                _rec_tt = record

                def _tt_upload() -> TikTokUploadResult:
                    return _tt.upload_video(
                        video_path=video_path,
                        title=_rec_tt.title,
                        description=_rec_tt.description,
                        tags=_rec_tt.tags,
                    )

                futures["tiktok"] = pool.submit(_tt_upload)
            except Exception as exc:
                results.errors["tiktok"] = f"Auth failed: {exc}"

        # ── Meta (Instagram + Facebook) ───────────────────────────────────
        if _is_platform_enabled(config, "instagram") or _is_platform_enabled(config, "facebook"):
            # Build a merged meta config section with per-platform enable flags
            meta_cfg = dict(config.get("meta", {}))
            meta_cfg["instagram_enabled"] = _is_platform_enabled(config, "instagram")
            meta_cfg["facebook_enabled"] = _is_platform_enabled(config, "facebook")
            merged = dict(config)
            merged["meta"] = meta_cfg

            try:
                meta = MetaClient(merged)
                meta.authenticate()
                _meta = meta
                _rec_meta = record

                def _meta_upload() -> MetaUploadResult:
                    return _meta.upload_video(
                        video_path=video_path,
                        title=_rec_meta.title,
                        description=_rec_meta.description,
                        tags=_rec_meta.tags,
                    )

                futures["meta"] = pool.submit(_meta_upload)
            except Exception as exc:
                if _is_platform_enabled(config, "instagram"):
                    results.errors["instagram"] = f"Auth failed: {exc}"
                if _is_platform_enabled(config, "facebook"):
                    results.errors["facebook"] = f"Auth failed: {exc}"

        # ── Collect results ────────────────────────────────────────────────
        for platform, future in futures.items():
            try:
                outcome = future.result(timeout=700)
            except Exception as exc:
                results.errors[platform] = f"Unexpected error: {exc}"
                logger.exception(
                    "Platform upload raised an exception",
                    extra={"stage": "publisher", "platform": platform, "clip_id": record.clip_id},
                )
                continue

            if platform == "youtube":
                if outcome.success:
                    results.youtube_id = outcome.youtube_id
                    logger.info(
                        "YouTube upload success: %s", outcome.youtube_id,
                        extra={"stage": "publisher", "clip_id": record.clip_id},
                    )
                else:
                    results.errors["youtube"] = outcome.error_message or "Unknown error"
                    logger.warning(
                        "YouTube upload failed: %s", outcome.error_message,
                        extra={"stage": "publisher", "clip_id": record.clip_id},
                    )

            elif platform == "tiktok":
                if outcome.success:
                    results.tiktok_id = outcome.tiktok_id
                    logger.info(
                        "TikTok upload success: %s", outcome.tiktok_id,
                        extra={"stage": "publisher", "clip_id": record.clip_id},
                    )
                else:
                    results.errors["tiktok"] = outcome.error_message or "Unknown error"
                    logger.warning(
                        "TikTok upload failed: %s", outcome.error_message,
                        extra={"stage": "publisher", "clip_id": record.clip_id},
                    )

            elif platform == "meta":
                if outcome.success:
                    results.instagram_id = outcome.instagram_id
                    results.facebook_id = outcome.facebook_id
                    logger.info(
                        "Meta upload success — IG: %s, FB: %s",
                        outcome.instagram_id, outcome.facebook_id,
                        extra={"stage": "publisher", "clip_id": record.clip_id},
                    )
                else:
                    msg = outcome.error_message or "Unknown error"
                    if _is_platform_enabled(config, "instagram"):
                        results.errors["instagram"] = msg
                    if _is_platform_enabled(config, "facebook"):
                        results.errors["facebook"] = msg
                    logger.warning(
                        "Meta upload failed: %s", msg,
                        extra={"stage": "publisher", "clip_id": record.clip_id},
                    )

    if results.any_success:
        logger.info(
            "Multi-platform publish complete — YT:%s TT:%s IG:%s FB:%s",
            results.youtube_id, results.tiktok_id, results.instagram_id, results.facebook_id,
            extra={"stage": "publisher", "clip_id": record.clip_id},
        )
    else:
        logger.error(
            "Multi-platform publish: ALL platforms failed — %s",
            results.error_summary,
            extra={"stage": "publisher", "clip_id": record.clip_id},
        )

    return results
