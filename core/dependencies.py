"""External dependency checker for Shorts Factory.

Verifies that FFmpeg, FFprobe, and the correct Python version are available
at startup. Exits with code 1 and human-readable instructions on failure.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys

logger = logging.getLogger(__name__)

MIN_PYTHON_VERSION = (3, 10)


def check_python_version() -> bool:
    """Verify Python version is >= 3.10.

    Returns:
        True if version is sufficient.

    Raises:
        SystemExit: If Python version is too old.
    """
    current = sys.version_info[:2]
    if current < MIN_PYTHON_VERSION:
        logger.critical(
            "Python version too old",
            extra={
                "stage": "startup",
                "video_id": "",
                "required": f"{MIN_PYTHON_VERSION[0]}.{MIN_PYTHON_VERSION[1]}",
                "current": f"{current[0]}.{current[1]}",
            },
        )
        sys.exit(1)
    return True


def check_ffmpeg() -> bool:
    """Verify FFmpeg is available in PATH.

    Returns:
        True if FFmpeg is found.

    Raises:
        SystemExit: If FFmpeg is not available.
    """
    if not shutil.which("ffmpeg"):
        logger.critical(
            "FFmpeg not found in PATH. Install: https://ffmpeg.org/download.html",
            extra={"stage": "startup", "video_id": ""},
        )
        sys.exit(1)

    try:
        result = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            logger.critical(
                "FFmpeg found but returned non-zero exit code",
                extra={"stage": "startup", "video_id": "", "returncode": result.returncode},
            )
            sys.exit(1)
        version_line = result.stdout.split("\n")[0] if result.stdout else "unknown"
        logger.info(
            "FFmpeg found",
            extra={"stage": "startup", "video_id": "", "version": version_line},
        )
    except (subprocess.SubprocessError, OSError) as exc:
        logger.critical(
            "FFmpeg check failed",
            extra={"stage": "startup", "video_id": "", "error": str(exc)},
        )
        sys.exit(1)

    return True


def check_ffprobe() -> bool:
    """Verify FFprobe is available in PATH.

    Returns:
        True if FFprobe is found.

    Raises:
        SystemExit: If FFprobe is not available.
    """
    if not shutil.which("ffprobe"):
        logger.critical(
            "FFprobe not found in PATH. Install FFmpeg (includes FFprobe): "
            "https://ffmpeg.org/download.html",
            extra={"stage": "startup", "video_id": ""},
        )
        sys.exit(1)

    try:
        result = subprocess.run(
            ["ffprobe", "-version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            logger.critical(
                "FFprobe found but returned non-zero exit code",
                extra={"stage": "startup", "video_id": "", "returncode": result.returncode},
            )
            sys.exit(1)
        version_line = result.stdout.split("\n")[0] if result.stdout else "unknown"
        logger.info(
            "FFprobe found",
            extra={"stage": "startup", "video_id": "", "version": version_line},
        )
    except (subprocess.SubprocessError, OSError) as exc:
        logger.critical(
            "FFprobe check failed",
            extra={"stage": "startup", "video_id": "", "error": str(exc)},
        )
        sys.exit(1)

    return True


def check_all_dependencies() -> bool:
    """Run all dependency checks.

    Returns:
        True if all checks pass.

    Raises:
        SystemExit: If any dependency is missing.
    """
    check_python_version()
    check_ffmpeg()
    check_ffprobe()
    logger.info(
        "All dependencies verified",
        extra={"stage": "startup", "video_id": ""},
    )
    return True
