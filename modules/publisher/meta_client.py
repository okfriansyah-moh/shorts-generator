"""Meta Graph API client for Shorts Factory.

Publishes Reels to both Instagram and Facebook using the same
Page Access Token.  The two endpoints are separate but the auth is
identical — a single long-lived Page Access Token covers both.

Required credentials file (JSON):
    {
        "page_access_token": "...",
        "instagram_user_id": "...",   // numeric Instagram Business/Creator user ID
        "facebook_page_id": "..."     // numeric Facebook Page ID
    }

Token lifetime:
    Long-lived Page Access Tokens expire in ~60 days.  The Meta API
    returns a new long-lived token on each refresh call; this client
    auto-rotates it back to disk when a fresh token is returned.

Instagram video hosting:
    Instagram's API requires a publicly reachable video URL — it does not
    accept binary uploads.  This client solves that by spinning up a
    temporary single-file HTTP server bound to 0.0.0.0 on a configurable
    port, auto-detecting the machine's public IP (or using a manually
    configured one), and tearing the server down after Instagram has
    fetched the file (container status = FINISHED).

    Prerequisites:
        - Forward the chosen port (default 8080) on your router to this machine.
        - Optionally set meta.public_ip in config to skip auto-detection.

Meta API docs:
    Instagram: https://developers.facebook.com/docs/instagram-api/guides/content-publishing
    Facebook:  https://developers.facebook.com/docs/video-api/guides/reels-publishing
"""

from __future__ import annotations

import http.server
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_GRAPH_BASE = "https://graph.facebook.com/v19.0"

# Poll settings for Instagram container status
_POLL_INTERVAL_SECONDS = 8
_MAX_POLLS = 45  # ~6 minutes max

# How long to keep the server alive after container FINISHED (let Meta retry if needed)
_SERVER_LINGER_SECONDS = 10


@dataclass(frozen=True)
class MetaUploadResult:
    success: bool
    instagram_id: str | None = None
    facebook_id: str | None = None
    error_message: str | None = None


# ---------------------------------------------------------------------------
# Temporary single-file HTTP server
# ---------------------------------------------------------------------------

class _SingleFileHandler(http.server.BaseHTTPRequestHandler):
    """Serves exactly one file at any GET path. Ignores the path."""

    file_path: str = ""  # set on the class before use

    def do_GET(self) -> None:
        try:
            with open(self.__class__.file_path, "rb") as fh:
                data = fh.read()
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except Exception as exc:
            self.send_response(500)
            self.end_headers()
            logger.warning("SingleFileHandler error: %s", exc)

    def log_message(self, fmt: str, *args: object) -> None:  # silence default stdout logs
        logger.debug("TempHTTP: " + fmt, *args)


class _TempFileServer:
    """Context manager: starts a temporary HTTP server in a background thread.

    Usage:
        with _TempFileServer(video_path, port=8080) as server:
            url = server.url   # e.g. "http://203.0.113.4:8080/clip.mp4"
            ...
    """

    def __init__(self, file_path: str, port: int, public_ip: str) -> None:
        self._file_path = file_path
        self._port = port
        self._public_ip = public_ip
        self._server: http.server.HTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.url: str = ""

    def __enter__(self) -> "_TempFileServer":
        filename = os.path.basename(self._file_path)

        # Subclass handler so we can set file_path without global state
        handler_cls = type(
            "_Handler",
            (_SingleFileHandler,),
            {"file_path": self._file_path},
        )

        self._server = http.server.HTTPServer(("0.0.0.0", self._port), handler_cls)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

        self.url = f"http://{self._public_ip}:{self._port}/{filename}"
        logger.info(
            "Temp HTTP server started: %s (serving %s)",
            self.url, self._file_path,
            extra={"stage": "publisher"},
        )
        return self

    def __exit__(self, *_: object) -> None:
        if self._server:
            self._server.shutdown()
            logger.info("Temp HTTP server stopped", extra={"stage": "publisher"})


def _detect_public_ip() -> str:
    """Return the machine's public IPv4 address via api.ipify.org."""
    for url in ("https://api.ipify.org", "https://api4.ipify.org"):
        try:
            req = urllib.request.Request(url, headers={"Accept": "text/plain"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                ip = resp.read().decode().strip()
                if ip:
                    logger.info("Detected public IP: %s", ip, extra={"stage": "publisher"})
                    return ip
        except Exception as exc:
            logger.debug("ipify failed (%s): %s", url, exc)
    raise RuntimeError(
        "Could not detect public IP. Set meta.public_ip in config.yaml to override."
    )


# ---------------------------------------------------------------------------
# Meta client
# ---------------------------------------------------------------------------

class MetaClient:
    """Meta Graph API client for Instagram Reels + Facebook Reels.

    Args:
        config: Dict with key ``meta`` containing:
            - ``credentials_path``: path to credentials JSON
            - ``instagram_enabled``: bool (default True)
            - ``facebook_enabled``: bool (default True)
            - ``public_ip``: override auto-detected public IP (optional)
            - ``serve_port``: local port for temp HTTP server (default 8080)
    """

    def __init__(self, config: dict) -> None:
        self._cfg = config.get("meta", {})
        self._credentials_path = self._cfg.get("credentials_path", "config/meta_credentials.json")
        self._token: str | None = None
        self._ig_user_id: str | None = None
        self._fb_page_id: str | None = None
        self._instagram_enabled = self._cfg.get("instagram_enabled", True)
        self._facebook_enabled = self._cfg.get("facebook_enabled", True)
        self._authenticated = False

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    def authenticate(self) -> bool:
        """Load credentials from disk and validate the token is usable."""
        if not os.path.isfile(self._credentials_path):
            raise FileNotFoundError(f"Meta credentials not found: {self._credentials_path}")

        with open(self._credentials_path) as f:
            creds = json.load(f)

        required = ["page_access_token"]
        if self._instagram_enabled:
            required.append("instagram_user_id")
        if self._facebook_enabled:
            required.append("facebook_page_id")

        for field in required:
            if not creds.get(field):
                raise ValueError(f"Meta credentials missing field: {field}")

        self._token = creds["page_access_token"]
        self._ig_user_id = creds.get("instagram_user_id")
        self._fb_page_id = creds.get("facebook_page_id")

        # Validate token
        url = f"{_GRAPH_BASE}/me?fields=id,name&access_token={urllib.parse.quote(self._token)}"
        req = urllib.request.Request(url)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode(errors="replace")
            raise RuntimeError(f"Meta token validation failed ({exc.code}): {error_body}") from exc

        if "error" in body:
            raise RuntimeError(f"Meta token invalid: {body['error']}")

        self._authenticated = True
        logger.info(
            "Meta authentication successful",
            extra={"stage": "publisher", "meta_name": body.get("name"), "meta_id": body.get("id")},
        )
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
    ) -> MetaUploadResult:
        """Publish a Reel to Instagram and/or Facebook.

        Instagram: uses a temporary local HTTP server on your public IP
        so Meta can fetch the video without any external hosting.

        Facebook: direct binary upload — no public URL needed.
        """
        if not self._authenticated:
            return MetaUploadResult(success=False, error_message="Not authenticated.")

        if not os.path.isfile(video_path):
            return MetaUploadResult(success=False, error_message=f"Video not found: {video_path}")

        hashtags = " ".join(f"#{t.replace(' ', '')}" for t in tags[:10])
        caption = f"{title}\n\n{description}\n\n{hashtags}"[:2200]

        instagram_id: str | None = None
        facebook_id: str | None = None
        errors: list[str] = []

        # ── Instagram Reels (via temp local HTTP server) ──────────────────
        if self._instagram_enabled and self._ig_user_id:
            ig_result = self._publish_instagram_reel_local(video_path, caption)
            if ig_result:
                instagram_id = ig_result
            else:
                errors.append("Instagram Reel upload failed")

        # ── Facebook Reels (direct binary upload) ─────────────────────────
        if self._facebook_enabled and self._fb_page_id:
            fb_result = self._publish_facebook_reel(video_path, caption)
            if fb_result:
                facebook_id = fb_result
            else:
                errors.append("Facebook Reel upload failed")

        if instagram_id or facebook_id:
            return MetaUploadResult(
                success=True,
                instagram_id=instagram_id,
                facebook_id=facebook_id,
            )

        return MetaUploadResult(
            success=False,
            error_message="; ".join(errors) if errors else "No platforms enabled or configured",
        )

    # ------------------------------------------------------------------
    # Instagram Reels — local server flow
    # ------------------------------------------------------------------

    def _publish_instagram_reel_local(self, video_path: str, caption: str) -> str | None:
        """Serve the video from a temp local HTTP server, publish to Instagram."""
        port = int(self._cfg.get("serve_port", 8080))

        # Resolve public IP
        public_ip = self._cfg.get("public_ip", "").strip()
        if not public_ip:
            try:
                public_ip = _detect_public_ip()
            except RuntimeError as exc:
                logger.error("Instagram: %s", exc, extra={"stage": "publisher"})
                return None

        with _TempFileServer(video_path, port=port, public_ip=public_ip) as server:
            media_id = self._publish_instagram_reel(caption, server.url)
            if media_id:
                # Let the server linger briefly in case Meta retries the fetch
                time.sleep(_SERVER_LINGER_SECONDS)
            return media_id

    def _publish_instagram_reel(self, caption: str, video_url: str) -> str | None:
        """Create + publish an Instagram Reel container. Returns media_id or None."""
        container_id = self._ig_create_container(caption, video_url)
        if not container_id:
            return None
        if not self._ig_wait_for_container(container_id):
            return None
        return self._ig_publish_container(container_id)

    def _ig_create_container(self, caption: str, video_url: str) -> str | None:
        url = f"{_GRAPH_BASE}/{self._ig_user_id}/media"
        payload = urllib.parse.urlencode({
            "media_type": "REELS",
            "video_url": video_url,
            "caption": caption,
            "access_token": self._token,
        }).encode()
        req = urllib.request.Request(url, data=payload, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode(errors="replace")
            logger.error("Instagram container creation failed: %s", error_body, extra={"stage": "publisher"})
            return None

        if "error" in body:
            logger.error("Instagram container error: %s", body["error"], extra={"stage": "publisher"})
            return None

        container_id = body.get("id")
        logger.info("Instagram container created: %s", container_id, extra={"stage": "publisher"})
        return container_id

    def _ig_wait_for_container(self, container_id: str) -> bool:
        """Poll until container status is FINISHED."""
        url_base = (
            f"{_GRAPH_BASE}/{container_id}"
            f"?fields=status_code,status&access_token={urllib.parse.quote(self._token)}"
        )
        for attempt in range(_MAX_POLLS):
            time.sleep(_POLL_INTERVAL_SECONDS)
            req = urllib.request.Request(url_base)
            try:
                with urllib.request.urlopen(req, timeout=20) as resp:
                    body = json.loads(resp.read())
            except urllib.error.HTTPError as exc:
                logger.warning("Instagram poll error: %s", exc, extra={"stage": "publisher"})
                continue

            status_code = body.get("status_code", "")
            logger.debug(
                "Instagram container poll",
                extra={"stage": "publisher", "container_id": container_id, "status": status_code, "attempt": attempt + 1},
            )

            if status_code == "FINISHED":
                return True
            if status_code == "ERROR":
                logger.error("Instagram container errored: %s", body, extra={"stage": "publisher"})
                return False

        logger.error("Instagram container timed out: %s", container_id, extra={"stage": "publisher"})
        return False

    def _ig_publish_container(self, container_id: str) -> str | None:
        url = f"{_GRAPH_BASE}/{self._ig_user_id}/media_publish"
        payload = urllib.parse.urlencode({
            "creation_id": container_id,
            "access_token": self._token,
        }).encode()
        req = urllib.request.Request(url, data=payload, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode(errors="replace")
            logger.error("Instagram publish failed: %s", error_body, extra={"stage": "publisher"})
            return None

        if "error" in body:
            logger.error("Instagram publish error: %s", body["error"], extra={"stage": "publisher"})
            return None

        media_id = body.get("id")
        logger.info("Instagram Reel published: %s", media_id, extra={"stage": "publisher"})
        return media_id

    # ------------------------------------------------------------------
    # Facebook Reels — direct binary upload
    # ------------------------------------------------------------------

    def _publish_facebook_reel(self, video_path: str, description: str) -> str | None:
        """Upload + publish a Facebook Reel. Returns video_id or None."""
        video_id, upload_url = self._fb_start_upload(video_path)
        if not video_id or not upload_url:
            return None
        if not self._fb_upload_bytes(video_path, upload_url):
            return None
        return self._fb_finish_upload(video_id, description)

    def _fb_start_upload(self, video_path: str) -> tuple[str | None, str | None]:
        file_size = os.path.getsize(video_path)
        url = f"{_GRAPH_BASE}/{self._fb_page_id}/video_reels"
        payload = urllib.parse.urlencode({
            "upload_phase": "start",
            "file_size": file_size,
            "access_token": self._token,
        }).encode()
        req = urllib.request.Request(url, data=payload, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode(errors="replace")
            logger.error("Facebook Reel start failed: %s", error_body, extra={"stage": "publisher"})
            return None, None

        if "error" in body:
            logger.error("Facebook Reel start error: %s", body["error"], extra={"stage": "publisher"})
            return None, None

        video_id = body.get("video_id")
        upload_url = body.get("upload_url")
        logger.info("Facebook upload session started: video_id=%s", video_id, extra={"stage": "publisher"})
        return video_id, upload_url

    def _fb_upload_bytes(self, video_path: str, upload_url: str) -> bool:
        file_size = os.path.getsize(video_path)
        with open(video_path, "rb") as fh:
            video_bytes = fh.read()

        req = urllib.request.Request(
            upload_url,
            data=video_bytes,
            method="POST",
            headers={
                "Authorization": f"OAuth {self._token}",
                "Content-Type": "video/mp4",
                "Content-Length": str(file_size),
                "offset": "0",
                "file_size": str(file_size),
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=600) as resp:
                body = json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode(errors="replace")
            logger.error("Facebook binary upload failed: %s", error_body, extra={"stage": "publisher"})
            return False

        if not body.get("success"):
            logger.error("Facebook binary upload not acknowledged: %s", body, extra={"stage": "publisher"})
            return False

        logger.info("Facebook binary upload complete", extra={"stage": "publisher"})
        return True

    def _fb_finish_upload(self, video_id: str, description: str) -> str | None:
        url = f"{_GRAPH_BASE}/{self._fb_page_id}/video_reels"
        payload = urllib.parse.urlencode({
            "upload_phase": "finish",
            "video_id": video_id,
            "video_state": "PUBLISHED",
            "description": description,
            "access_token": self._token,
        }).encode()
        req = urllib.request.Request(url, data=payload, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode(errors="replace")
            logger.error("Facebook Reel finish failed: %s", error_body, extra={"stage": "publisher"})
            return None

        if "error" in body:
            logger.error("Facebook Reel finish error: %s", body["error"], extra={"stage": "publisher"})
            return None

        logger.info("Facebook Reel published: video_id=%s", video_id, extra={"stage": "publisher"})
        return video_id
