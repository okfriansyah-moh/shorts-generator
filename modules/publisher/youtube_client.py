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
        """Exchange refresh token for a new access token.

        In production this makes an HTTP POST to
        ``https://oauth2.googleapis.com/token``.  The method is
        extracted so tests can mock it without touching the filesystem.

        Args:
            creds: Dict with ``client_id``, ``client_secret``, ``refresh_token``.

        Returns:
            Access token string.

        Raises:
            RuntimeError: If the token exchange fails.
        """
        # This is the network boundary — override in tests.
        raise NotImplementedError(
            "YouTubeClient._refresh_access_token must be overridden "
            "or mocked in tests. In production, implement the OAuth2 "
            "token refresh HTTP call here."
        )

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
        """Execute the actual upload HTTP request.

        Extracted as a method so tests can mock the network call
        without touching the validation logic in ``upload_video``.

        Returns:
            UploadResult from the YouTube API response.
        """
        raise NotImplementedError(
            "YouTubeClient._do_upload must be overridden or mocked."
        )

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
        """Execute the actual thumbnail upload HTTP request.

        Returns:
            ThumbnailUploadResult from the YouTube API response.
        """
        raise NotImplementedError(
            "YouTubeClient._do_set_thumbnail must be overridden or mocked."
        )

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
        """Execute the actual visibility update HTTP request.

        Returns:
            VisibilityUpdateResult from the YouTube API response.
        """
        raise NotImplementedError(
            "YouTubeClient._do_update_visibility must be overridden or mocked."
        )
