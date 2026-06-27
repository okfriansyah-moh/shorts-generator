"""Video ingestion module for Shorts Factory.

Validates the input video file, extracts metadata via FFprobe, and
computes a deterministic video_id from content fingerprinting.

Public API:
    ingest(file_path, config) -> IngestionResult
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
from typing import Any

from contracts.ingestion import IngestionResult

logger = logging.getLogger(__name__)

# Number of bytes read for content fingerprinting
_FINGERPRINT_BYTES = 10 * 1024 * 1024  # 10 MB


class IngestionError(Exception):
    """Raised when ingestion validation fails."""


def ingest(file_path: str, config: dict[str, Any]) -> IngestionResult:
    """Validate a video file and return its IngestionResult DTO.

    Args:
        file_path: Path to the video file to ingest.
        config: Pipeline configuration dictionary.

    Returns:
        IngestionResult with video metadata and deterministic video_id.

    Raises:
        IngestionError: If the file is invalid, has wrong format, no audio,
            or duration out of the configured range.
    """
    abs_path = os.path.abspath(file_path)

    if not os.path.isfile(abs_path):
        raise IngestionError(f"File not found: {abs_path}")

    _validate_format(abs_path, config)

    ffprobe_timeout = int(config.get("pipeline", {}).get("ffmpeg_timeout", 300))
    probe = _ffprobe(abs_path, timeout=ffprobe_timeout)

    duration = _extract_duration(probe, abs_path)
    _validate_duration(duration, config, abs_path)

    video_stream = _find_video_stream(probe, abs_path)
    audio_stream = _find_audio_stream(probe)

    if audio_stream is None:
        raise IngestionError(
            f"No audio stream found in video: {abs_path}. "
            "Audio is required for the pipeline."
        )

    width = int(video_stream.get("width", 0))
    height = int(video_stream.get("height", 0))
    if width <= 0 or height <= 0:
        raise IngestionError(
            f"Could not determine video resolution from: {abs_path}"
        )

    fps = _parse_fps(video_stream.get("r_frame_rate", "0/1"))
    codec = video_stream.get("codec_name", "unknown")
    audio_codec = audio_stream.get("codec_name", "unknown")
    file_size = os.path.getsize(abs_path)
    video_id = _compute_video_id(abs_path, file_size)

    logger.info(
        "Video ingested successfully",
        extra={
            "stage": "ingestion",
            "video_id": video_id,
            "file_path": abs_path,
            "duration": duration,
            "resolution": f"{width}x{height}",
            "codec": codec,
            "has_audio": True,
        },
    )

    return IngestionResult(
        video_id=video_id,
        path=abs_path,
        duration_seconds=duration,
        resolution=(width, height),
        codec=codec,
        audio_codec=audio_codec,
        has_audio=True,
        file_size_bytes=file_size,
        fps=fps,
    )


def _validate_format(abs_path: str, config: dict[str, Any]) -> None:
    """Check that the file extension matches a supported format.

    Raises:
        IngestionError: If the format is not in the supported list.
    """
    supported = [fmt.lower() for fmt in config["ingestion"]["supported_formats"]]
    ext = os.path.splitext(abs_path)[1].lstrip(".").lower()
    if ext not in supported:
        raise IngestionError(
            f"Unsupported video format '{ext}' for file: {abs_path}. "
            f"Supported formats: {', '.join(sorted(supported))}"
        )


def _ffprobe(abs_path: str, timeout: int = 300) -> dict[str, Any]:
    """Run FFprobe on the file and return parsed JSON output.

    Args:
        abs_path: Absolute path to the video file.
        timeout: Maximum seconds to wait for FFprobe (from config pipeline.ffmpeg_timeout).

    Raises:
        IngestionError: If FFprobe fails or returns invalid output.
    """
    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_streams",
        "-show_format",
        abs_path,
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise IngestionError(
            f"FFprobe timed out after {timeout} seconds for: {abs_path}"
        ) from exc
    except OSError as exc:
        raise IngestionError(
            f"FFprobe not available or failed to start: {exc}"
        ) from exc

    if result.returncode != 0:
        raise IngestionError(
            f"FFprobe failed (exit {result.returncode}) for: {abs_path}. "
            f"stderr: {result.stderr[:200]}"
        )

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise IngestionError(
            f"FFprobe returned invalid JSON for: {abs_path}"
        ) from exc


def _extract_duration(probe: dict[str, Any], abs_path: str) -> float:
    """Extract video duration in seconds from FFprobe output.

    Raises:
        IngestionError: If duration cannot be determined.
    """
    # Try format-level duration first
    fmt = probe.get("format", {})
    raw_duration = fmt.get("duration")

    if raw_duration is None:
        # Fall back to video stream duration
        for stream in probe.get("streams", []):
            if stream.get("codec_type") == "video":
                raw_duration = stream.get("duration")
                break

    if raw_duration is None:
        raise IngestionError(
            f"Could not determine video duration from: {abs_path}"
        )

    try:
        return float(raw_duration)
    except (ValueError, TypeError) as exc:
        raise IngestionError(
            f"Invalid duration value in FFprobe output: {raw_duration!r}"
        ) from exc


def _validate_duration(duration: float, config: dict[str, Any], abs_path: str) -> None:
    """Validate duration is within the configured range.

    Raises:
        IngestionError: If duration is outside [min_duration, max_duration].
    """
    min_dur = config["ingestion"]["min_duration_seconds"]
    max_dur = config["ingestion"]["max_duration_seconds"]

    if duration < min_dur:
        raise IngestionError(
            f"Video duration {duration:.1f}s is outside the allowed range "
            f"[{min_dur}s, {max_dur}s] for: {abs_path}"
        )
    if max_dur > 0 and duration > max_dur:
        raise IngestionError(
            f"Video duration {duration:.1f}s is outside the allowed range "
            f"[{min_dur}s, {max_dur}s] for: {abs_path}"
        )


def _find_video_stream(
    probe: dict[str, Any], abs_path: str
) -> dict[str, Any]:
    """Find the first video stream in FFprobe output.

    Raises:
        IngestionError: If no video stream is found.
    """
    for stream in probe.get("streams", []):
        if stream.get("codec_type") == "video":
            return stream
    raise IngestionError(f"No video stream found in: {abs_path}")


def _find_audio_stream(probe: dict[str, Any]) -> dict[str, Any] | None:
    """Find the first audio stream in FFprobe output, or None."""
    for stream in probe.get("streams", []):
        if stream.get("codec_type") == "audio":
            return stream
    return None


def _parse_fps(r_frame_rate: str) -> float:
    """Parse fps from FFprobe's r_frame_rate fraction string (e.g. '30000/1001').

    Returns 0.0 if parsing fails.
    """
    try:
        if "/" in r_frame_rate:
            num, den = r_frame_rate.split("/")
            den_val = float(den)
            if den_val == 0:
                return 0.0
            return float(num) / den_val
        return float(r_frame_rate)
    except (ValueError, ZeroDivisionError):
        return 0.0


def _compute_video_id(abs_path: str, file_size: int) -> str:
    """Compute deterministic video_id as SHA256(first_10MB + str(file_size))[:16].

    Args:
        abs_path: Absolute path to the video file.
        file_size: File size in bytes.

    Returns:
        First 16 characters of the hex SHA256 digest (lowercase).
    """
    hasher = hashlib.sha256()

    with open(abs_path, "rb") as f:
        chunk = f.read(_FINGERPRINT_BYTES)
        hasher.update(chunk)

    hasher.update(str(file_size).encode("ascii"))
    return hasher.hexdigest()[:16]
