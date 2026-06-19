"""TikTok Content Posting API client for Shorts Factory.

Handles OAuth2 token refresh and direct video upload (file-based) via
the TikTok Content Posting API v2.  Only the upload flow is implemented
here; all DB I/O lives in the orchestrator.

Required credentials file (JSON):
    {
        "client_key": "...",
        "client_secret": "...",
        "refresh_token": "..."
    }

TikTok API docs:
    https://developers.tiktok.com/doc/content-posting-api-get-started
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
_INIT_URL = "https://open.tiktokapis.com/v2/post/publish/video/init/"
_STATUS_URL = "https://open.tiktokapis.com/v2/post/publish/status/fetch/"

# Poll interval / max polls when waiting for TikTok to process the upload
_POLL_INTERVAL_SECONDS = 10
_MAX_POLLS = 30  # 5 minutes max


@dataclass(frozen=True)
class TikTokUploadResult:
    success: bool
    tiktok_id: str | None = None
    error_message: str | None = None


class TikTokClient:
    """TikTok Content Posting API client.

    Args:
        config: Dict with key ``tiktok`` containing:
            - ``credentials_path``: path to credentials JSON
            - ``privacy_level``: "PUBLIC_TO_EVERYONE" | "MUTUAL_FOLLOW_FRIENDS" |
              "SELF_ONLY" (default: "PUBLIC_TO_EVERYONE")
            - ``disable_duet``: bool (default False)
            - ``disable_stitch``: bool (default False)
            - ``disable_comment``: bool (default False)
    """

    def __init__(self, config: dict) -> None:
        self._cfg = config.get("tiktok", {})
        self._credentials_path = self._cfg.get("credentials_path", "config/tiktok_credentials.json")
        self._access_token: str | None = None
        self._open_id: str | None = None
        self._authenticated = False

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    def authenticate(self) -> bool:
        """Refresh OAuth2 access token using the stored refresh token.

        Returns:
            True on success.

        Raises:
            FileNotFoundError: credentials file missing.
            ValueError: required fields absent.
            RuntimeError: token exchange failed.
        """
        if not os.path.isfile(self._credentials_path):
            raise FileNotFoundError(f"TikTok credentials not found: {self._credentials_path}")

        with open(self._credentials_path) as f:
            creds = json.load(f)

        for field in ("client_key", "client_secret", "refresh_token"):
            if not creds.get(field):
                raise ValueError(f"TikTok credentials missing field: {field}")

        payload = urllib.parse.urlencode({
            "client_key": creds["client_key"],
            "client_secret": creds["client_secret"],
            "grant_type": "refresh_token",
            "refresh_token": creds["refresh_token"],
        }).encode()

        req = urllib.request.Request(
            _TOKEN_URL,
            data=payload,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode(errors="replace")
            raise RuntimeError(f"TikTok token refresh failed ({exc.code}): {error_body}") from exc

        data = body.get("data", body)
        access_token = data.get("access_token")
        open_id = data.get("open_id")
        if not access_token:
            raise RuntimeError(f"TikTok token refresh returned no access_token: {body}")

        # Persist refreshed tokens back to disk so the next run uses them
        new_refresh = data.get("refresh_token")
        if new_refresh and new_refresh != creds.get("refresh_token"):
            creds["refresh_token"] = new_refresh
            with open(self._credentials_path, "w") as f:
                json.dump(creds, f, indent=2)
            logger.debug("TikTok refresh_token rotated and saved")

        self._access_token = access_token
        self._open_id = open_id
        self._authenticated = True
        logger.info("TikTok authentication successful", extra={"stage": "publisher"})
        return True

    # ------------------------------------------------------------------
    # Upload
    # ------------------------------------------------------------------

    def upload_video(
        self,
        video_path: str,
        title: str,
        description: str,
        tags: tuple[str, ...],
    ) -> TikTokUploadResult:
        """Upload a video to TikTok via the file-based Content Posting API.

        The flow:
          1. POST /v2/post/publish/video/init/  → get upload_url + publish_id
          2. PUT upload_url (raw file bytes)
          3. Poll /v2/post/publish/status/fetch/ until PUBLISH_COMPLETE

        Args:
            video_path: Absolute path to the mp4 video file.
            title: Video caption (max 2200 chars for TikTok; we use description).
            description: Extended description — prepended to hashtags.
            tags: Tuple of tag strings (used as hashtags in caption).

        Returns:
            TikTokUploadResult with success and tiktok_id.
        """
        if not self._authenticated:
            return TikTokUploadResult(success=False, error_message="Not authenticated. Call authenticate() first.")

        if not os.path.isfile(video_path):
            return TikTokUploadResult(success=False, error_message=f"Video file not found: {video_path}")

        # Build caption: description + hashtags
        hashtags = " ".join(f"#{t.replace(' ', '')}" for t in tags[:10])
        caption = f"{description}\n\n{hashtags}"[:2200]

        file_size = os.path.getsize(video_path)

        privacy = self._cfg.get("privacy_level", "PUBLIC_TO_EVERYONE")

        # ── Step 1: init upload ────────────────────────────────────────────
        init_payload = json.dumps({
            "post_info": {
                "title": caption,
                "privacy_level": privacy,
                "disable_duet": self._cfg.get("disable_duet", False),
                "disable_stitch": self._cfg.get("disable_stitch", False),
                "disable_comment": self._cfg.get("disable_comment", False),
            },
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": file_size,
                "chunk_size": file_size,
                "total_chunk_count": 1,
            },
        }).encode()

        init_req = urllib.request.Request(
            _INIT_URL,
            data=init_payload,
            method="POST",
            headers={
                "Authorization": f"Bearer {self._access_token}",
                "Content-Type": "application/json; charset=UTF-8",
            },
        )
        try:
            with urllib.request.urlopen(init_req, timeout=30) as resp:
                init_body = json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode(errors="replace")
            return TikTokUploadResult(
                success=False,
                error_message=f"TikTok upload init failed ({exc.code}): {error_body}",
            )

        error = init_body.get("error", {})
        if error.get("code", "ok") != "ok":
            return TikTokUploadResult(
                success=False,
                error_message=f"TikTok upload init error: {error}",
            )

        data = init_body.get("data", {})
        upload_url = data.get("upload_url")
        publish_id = data.get("publish_id")

        if not upload_url or not publish_id:
            return TikTokUploadResult(
                success=False,
                error_message=f"TikTok init missing upload_url or publish_id: {init_body}",
            )

        # ── Step 2: upload file bytes ──────────────────────────────────────
        with open(video_path, "rb") as fh:
            video_bytes = fh.read()

        upload_req = urllib.request.Request(
            upload_url,
            data=video_bytes,
            method="PUT",
            headers={
                "Content-Type": "video/mp4",
                "Content-Length": str(file_size),
                "Content-Range": f"bytes 0-{file_size - 1}/{file_size}",
            },
        )
        try:
            with urllib.request.urlopen(upload_req, timeout=600) as resp:
                resp.read()
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode(errors="replace")
            return TikTokUploadResult(
                success=False,
                error_message=f"TikTok file upload failed ({exc.code}): {error_body}",
            )

        # ── Step 3: poll for publish completion ───────────────────────────
        return self._poll_publish_status(publish_id)

    def _poll_publish_status(self, publish_id: str) -> TikTokUploadResult:
        """Poll TikTok until the video is published or an error is returned."""
        payload = json.dumps({"publish_id": publish_id}).encode()

        for attempt in range(_MAX_POLLS):
            time.sleep(_POLL_INTERVAL_SECONDS)

            req = urllib.request.Request(
                _STATUS_URL,
                data=payload,
                method="POST",
                headers={
                    "Authorization": f"Bearer {self._access_token}",
                    "Content-Type": "application/json; charset=UTF-8",
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    body = json.loads(resp.read())
            except urllib.error.HTTPError as exc:
                error_body = exc.read().decode(errors="replace")
                return TikTokUploadResult(
                    success=False,
                    error_message=f"TikTok status poll failed ({exc.code}): {error_body}",
                )

            error = body.get("error", {})
            if error.get("code", "ok") != "ok":
                return TikTokUploadResult(
                    success=False,
                    error_message=f"TikTok status poll error: {error}",
                )

            data = body.get("data", {})
            status = data.get("status", "")
            tiktok_id = data.get("publicaly_available_post_id") or data.get("video_id")

            logger.debug(
                "TikTok publish poll",
                extra={"stage": "publisher", "publish_id": publish_id, "status": status, "attempt": attempt + 1},
            )

            if status == "PUBLISH_COMPLETE":
                logger.info(
                    "TikTok video published",
                    extra={"stage": "publisher", "publish_id": publish_id, "tiktok_id": tiktok_id},
                )
                return TikTokUploadResult(success=True, tiktok_id=str(tiktok_id or publish_id))

            if status in ("FAILED", "PUBLISH_FAILED"):
                return TikTokUploadResult(
                    success=False,
                    error_message=f"TikTok publish failed: {data}",
                )

        return TikTokUploadResult(
            success=False,
            error_message=f"TikTok publish timed out after {_MAX_POLLS * _POLL_INTERVAL_SECONDS}s",
        )
