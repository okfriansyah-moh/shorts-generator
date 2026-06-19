"""YouTube Data API v3 wrapper for Shorts Factory.

Handles OAuth2 authentication, video upload, thumbnail upload,
and privacy status updates. All network I/O is isolated here so
that the rest of the publisher module can be tested with mocks.

This module does NOT access the database. The orchestrator handles
all DB reads/writes via database/adapter.py.
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


@dataclass(frozen=True)
class UploadResult:
    """Result of a video upload attempt.

    Fields:
        success: Whether the upload completed without error.
        youtube_id: The YouTube video ID if successful, None otherwise.
        error_message: Human-readable error description if failed.
        quota_exceeded: True if failure was due to YouTube API quota limits.
    """

    success: bool
    youtube_id: str | None = None
    error_message: str | None = None
    quota_exceeded: bool = False


@dataclass(frozen=True)
class ThumbnailUploadResult:
    """Result of a thumbnail upload attempt.

    Fields:
        success: Whether the thumbnail was set successfully.
        error_message: Human-readable error description if failed.
    """

    success: bool
    error_message: str | None = None


@dataclass(frozen=True)
class VisibilityUpdateResult:
    """Result of a privacy status update attempt.

    Fields:
        success: Whether the visibility change completed.
        error_message: Human-readable error description if failed.
    """

    success: bool
    error_message: str | None = None


class YouTubeClient:
    """YouTube Data API v3 client with OAuth2 authentication.

    Credentials are loaded from a local JSON file whose path is
    specified in the config. The file must contain ``client_id``,
    ``client_secret``, and ``refresh_token``.

    Args:
        config: Publisher configuration dict. Expected keys:
            - ``credentials_path``: path to OAuth2 credentials JSON.
    """

    def __init__(self, config: dict) -> None:
        self._config = config
        self._credentials_path = config.get("credentials_path", "")
        self._access_token: str | None = None
        self._authenticated = False

    def authenticate(self) -> bool:
        """Authenticate with YouTube API using stored OAuth2 credentials.

        Reads the credentials file, validates required fields, and
        obtains an access token via the refresh token flow.

        Returns:
            True if authentication succeeded.

        Raises:
            FileNotFoundError: If the credentials file does not exist.
            ValueError: If required credential fields are missing.
        """
        if not self._credentials_path:
            raise ValueError(
                "publisher.credentials_path not configured"
            )

        if not os.path.isfile(self._credentials_path):
            raise FileNotFoundError(
                f"Credentials file not found: {self._credentials_path}"
            )

        with open(self._credentials_path, "r") as f:
            creds = json.load(f)

        required_fields = ("client_id", "client_secret", "refresh_token")
        missing = [k for k in required_fields if not creds.get(k)]
        if missing:
            raise ValueError(
                f"Missing required credential fields: {', '.join(sorted(missing))}"
            )

        # Token refresh would happen here in production.
        # The actual HTTP call to Google OAuth2 is intentionally left
        # as a thin integration point so the rest of the module can be
        # fully unit-tested with mocks.
        self._access_token = self._refresh_access_token(creds)
        self._authenticated = True

        logger.info(
            "YouTube authentication successful",
            extra={"stage": "publisher", "status": "authenticated"},
        )
        return True

    def _refresh_access_token(self, creds: dict) -> str:
        """Exchange refresh token for a new access token via Google OAuth2.

        Args:
            creds: Dict with ``client_id``, ``client_secret``, ``refresh_token``.

        Returns:
            Access token string.

        Raises:
            RuntimeError: If the token exchange fails.
        """
        payload = urllib.parse.urlencode({
            "client_id": creds["client_id"],
            "client_secret": creds["client_secret"],
            "refresh_token": creds["refresh_token"],
            "grant_type": "refresh_token",
        }).encode()

        req = urllib.request.Request(
            "https://oauth2.googleapis.com/token",
            data=payload,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode(errors="replace")
            raise RuntimeError(
                f"OAuth2 token refresh failed ({exc.code}): {error_body}"
            ) from exc

        access_token = body.get("access_token")
        if not access_token:
            raise RuntimeError(
                f"OAuth2 token refresh returned no access_token: {body}"
            )
        logger.debug(
            "OAuth2 token refreshed",
            extra={"stage": "publisher", "expires_in": body.get("expires_in")},
        )
        return access_token

    def upload_video(
        self,
        video_path: str,
        title: str,
        description: str,
        tags: tuple[str, ...],
        category: str,
        privacy: str = "unlisted",
    ) -> UploadResult:
        """Upload a video file to YouTube.

        Args:
            video_path: Absolute path to the video file.
            title: Video title (40–60 characters).
            description: Video description (150–300 characters).
            tags: Tuple of tag strings (10–15 tags).
            category: YouTube category (e.g., "Gaming").
            privacy: Initial privacy status ("unlisted" or "public").

        Returns:
            UploadResult with success status and youtube_id.
        """
        if not self._authenticated:
            return UploadResult(
                success=False,
                error_message="Not authenticated. Call authenticate() first.",
            )

        if not os.path.isfile(video_path):
            return UploadResult(
                success=False,
                error_message=f"Video file not found: {video_path}",
            )

        logger.info(
            "Uploading video to YouTube",
            extra={
                "stage": "publisher",
                "status": "uploading",
                "title": title,
                "privacy": privacy,
            },
        )

        return self._do_upload(
            video_path=video_path,
            title=title,
            description=description,
            tags=tags,
            category=category,
            privacy=privacy,
        )

    def _do_upload(
        self,
        video_path: str,
        title: str,
        description: str,
        tags: tuple[str, ...],
        category: str,
        privacy: str,
    ) -> UploadResult:
        """Execute a resumable upload to the YouTube Data API v3.

        Uses the resumable upload protocol so large files are handled
        reliably. Steps:
          1. Initiate a resumable session → get upload URI.
          2. Upload the file in a single PUT (files are ≤100 MB per config).

        Returns:
            UploadResult from the YouTube API response.
        """
        file_size = os.path.getsize(video_path)

        # ── Step 1: initiate resumable session ────────────────────────────
        metadata = {
            "snippet": {
                "title": title,
                "description": description,
                "tags": list(tags),
                "categoryId": self._category_id(category),
            },
            "status": {
                "privacyStatus": privacy,
                "selfDeclaredMadeForKids": False,
            },
        }
        metadata_bytes = json.dumps(metadata).encode()
        init_url = (
            "https://www.googleapis.com/upload/youtube/v3/videos"
            "?uploadType=resumable&part=snippet,status"
        )
        init_req = urllib.request.Request(
            init_url,
            data=metadata_bytes,
            method="POST",
            headers={
                "Authorization": f"Bearer {self._access_token}",
                "Content-Type": "application/json; charset=UTF-8",
                "X-Upload-Content-Type": "video/*",
                "X-Upload-Content-Length": str(file_size),
            },
        )
        try:
            with urllib.request.urlopen(init_req, timeout=30) as resp:
                upload_uri = resp.headers.get("Location")
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode(errors="replace")
            quota_exceeded = exc.code == 403 and "quotaExceeded" in error_body
            return UploadResult(
                success=False,
                error_message=f"Upload initiation failed ({exc.code}): {error_body}",
                quota_exceeded=quota_exceeded,
            )

        if not upload_uri:
            return UploadResult(
                success=False,
                error_message="Upload initiation returned no Location URI.",
            )

        # ── Step 2: upload file bytes ──────────────────────────────────────
        with open(video_path, "rb") as fh:
            video_bytes = fh.read()

        upload_req = urllib.request.Request(
            upload_uri,
            data=video_bytes,
            method="PUT",
            headers={
                "Content-Type": "video/*",
                "Content-Length": str(file_size),
            },
        )
        try:
            with urllib.request.urlopen(upload_req, timeout=600) as resp:
                body = json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode(errors="replace")
            quota_exceeded = exc.code == 403 and "quotaExceeded" in error_body
            return UploadResult(
                success=False,
                error_message=f"Upload failed ({exc.code}): {error_body}",
                quota_exceeded=quota_exceeded,
            )

        youtube_id = body.get("id")
        if not youtube_id:
            return UploadResult(
                success=False,
                error_message=f"Upload response contained no video id: {body}",
            )

        logger.info(
            "Video uploaded successfully",
            extra={
                "stage": "publisher",
                "youtube_id": youtube_id,
                "title": title,
                "privacy": privacy,
            },
        )
        return UploadResult(success=True, youtube_id=youtube_id)

    @staticmethod
    def _category_id(category: str) -> str:
        """Map a human-readable category name to a YouTube category ID."""
        _MAP = {
            "Gaming": "20",
            "Entertainment": "24",
            "Education": "27",
            "Science & Technology": "28",
            "People & Blogs": "22",
            "Music": "10",
            "Sports": "17",
            "News & Politics": "25",
            "Howto & Style": "26",
            "Film & Animation": "1",
        }
        return _MAP.get(category, "22")  # default: People & Blogs

    def set_thumbnail(
        self,
        youtube_id: str,
        thumbnail_path: str,
    ) -> ThumbnailUploadResult:
        """Upload a custom thumbnail for an already-uploaded video.

        Args:
            youtube_id: The YouTube video ID.
            thumbnail_path: Absolute path to the JPEG thumbnail.

        Returns:
            ThumbnailUploadResult with success status.
        """
        if not self._authenticated:
            return ThumbnailUploadResult(
                success=False,
                error_message="Not authenticated. Call authenticate() first.",
            )

        if not os.path.isfile(thumbnail_path):
            return ThumbnailUploadResult(
                success=False,
                error_message=f"Thumbnail not found: {thumbnail_path}",
            )

        logger.info(
            "Setting thumbnail on YouTube",
            extra={
                "stage": "publisher",
                "status": "thumbnail_upload",
                "youtube_id": youtube_id,
            },
        )

        return self._do_set_thumbnail(youtube_id, thumbnail_path)

    def _do_set_thumbnail(
        self,
        youtube_id: str,
        thumbnail_path: str,
    ) -> ThumbnailUploadResult:
        """Upload a JPEG thumbnail via the YouTube thumbnails.set endpoint."""
        with open(thumbnail_path, "rb") as fh:
            thumb_bytes = fh.read()

        url = (
            f"https://www.googleapis.com/upload/youtube/v3/thumbnails/set"
            f"?videoId={urllib.parse.quote(youtube_id)}&uploadType=media"
        )
        req = urllib.request.Request(
            url,
            data=thumb_bytes,
            method="POST",
            headers={
                "Authorization": f"Bearer {self._access_token}",
                "Content-Type": "image/jpeg",
                "Content-Length": str(len(thumb_bytes)),
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                resp.read()  # consume response
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode(errors="replace")
            return ThumbnailUploadResult(
                success=False,
                error_message=f"Thumbnail upload failed ({exc.code}): {error_body}",
            )

        logger.info(
            "Thumbnail set successfully",
            extra={"stage": "publisher", "youtube_id": youtube_id},
        )
        return ThumbnailUploadResult(success=True)

    def update_visibility(
        self,
        youtube_id: str,
        privacy: str,
    ) -> VisibilityUpdateResult:
        """Update the privacy status of a published video.

        Args:
            youtube_id: The YouTube video ID.
            privacy: New privacy status ("public", "unlisted", "private").

        Returns:
            VisibilityUpdateResult with success status.
        """
        if not self._authenticated:
            return VisibilityUpdateResult(
                success=False,
                error_message="Not authenticated.",
            )

        logger.info(
            "Updating video visibility",
            extra={
                "stage": "publisher",
                "status": "visibility_update",
                "youtube_id": youtube_id,
                "privacy": privacy,
            },
        )

        return self._do_update_visibility(youtube_id, privacy)

    def _do_update_visibility(
        self,
        youtube_id: str,
        privacy: str,
    ) -> VisibilityUpdateResult:
        """Update a video's privacy status via the YouTube videos.update endpoint."""
        payload = json.dumps({
            "id": youtube_id,
            "status": {"privacyStatus": privacy},
        }).encode()

        url = "https://www.googleapis.com/youtube/v3/videos?part=status"
        req = urllib.request.Request(
            url,
            data=payload,
            method="PUT",
            headers={
                "Authorization": f"Bearer {self._access_token}",
                "Content-Type": "application/json; charset=UTF-8",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                resp.read()
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode(errors="replace")
            return VisibilityUpdateResult(
                success=False,
                error_message=f"Visibility update failed ({exc.code}): {error_body}",
            )

        logger.info(
            "Video visibility updated",
            extra={
                "stage": "publisher",
                "youtube_id": youtube_id,
                "privacy": privacy,
            },
        )
        return VisibilityUpdateResult(success=True)
