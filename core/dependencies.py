"""External dependency checker for Shorts Factory.

Verifies that FFmpeg, FFprobe, and the correct Python version are available
at startup. When GPU mode is enabled, also checks for nvidia-smi and
FFmpeg NVENC encoder support. Exits with code 1 and human-readable
instructions on failure.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from typing import Any

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


def check_all_dependencies(config: dict[str, Any] | None = None) -> bool:
    """Run all dependency checks.

    When config is provided and gpu.enabled is True, also verifies
    NVIDIA GPU availability and FFmpeg NVENC encoder support.

    Args:
        config: Optional pipeline configuration dict.

    Returns:
        True if all checks pass.

    Raises:
        SystemExit: If any dependency is missing.
    """
    check_python_version()
    check_ffmpeg()
    check_ffprobe()

    gpu_enabled = (
        config is not None
        and config.get("gpu", {}).get("enabled", False)
    )
    if gpu_enabled:
        check_nvidia_gpu()
        check_cuda_for_whisper()

    logger.info(
        "All dependencies verified",
        extra={"stage": "startup", "video_id": "", "gpu_enabled": gpu_enabled},
    )
    return True


def check_nvidia_gpu() -> bool:
    """Verify NVIDIA GPU is available via nvidia-smi and FFmpeg NVENC support.

    Returns:
        True if GPU and NVENC are available.

    Raises:
        SystemExit: If nvidia-smi is missing or FFmpeg lacks NVENC support.
    """
    if not shutil.which("nvidia-smi"):
        logger.critical(
            "nvidia-smi not found. GPU mode requires NVIDIA drivers. "
            "Install from: https://www.nvidia.com/Download/index.aspx",
            extra={"stage": "startup", "video_id": ""},
        )
        sys.exit(1)

    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            logger.critical(
                "nvidia-smi returned non-zero exit code",
                extra={"stage": "startup", "video_id": "", "returncode": result.returncode},
            )
            sys.exit(1)
        gpu_info = result.stdout.strip().split("\n")[0] if result.stdout else "unknown"
        logger.info(
            "NVIDIA GPU found",
            extra={"stage": "startup", "video_id": "", "gpu_info": gpu_info},
        )
    except (subprocess.SubprocessError, OSError) as exc:
        logger.critical(
            "nvidia-smi check failed",
            extra={"stage": "startup", "video_id": "", "error": str(exc)},
        )
        sys.exit(1)

    # Verify FFmpeg has NVENC encoder support
    try:
        result = subprocess.run(
            ["ffmpeg", "-encoders"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if "h264_nvenc" not in result.stdout:
            logger.critical(
                "FFmpeg does not support h264_nvenc. Rebuild FFmpeg with --enable-nvenc.",
                extra={"stage": "startup", "video_id": ""},
            )
            sys.exit(1)
        logger.info(
            "FFmpeg NVENC encoder available",
            extra={"stage": "startup", "video_id": ""},
        )
    except (subprocess.SubprocessError, OSError) as exc:
        logger.critical(
            "FFmpeg encoder check failed",
            extra={"stage": "startup", "video_id": "", "error": str(exc)},
        )
        sys.exit(1)

    return True


def check_cuda_for_whisper() -> bool:
    """Verify PyTorch CUDA support is available for GPU transcription.

    This is a soft check — logs a warning but does not exit, since
    the transcription module can fall back to CPU mode.

    Returns:
        True if CUDA is available, False otherwise.
    """
    try:
        import torch  # type: ignore[import]
        if torch.cuda.is_available():
            device_name = torch.cuda.get_device_name(0)
            logger.info(
                "CUDA available for transcription",
                extra={"stage": "startup", "video_id": "", "device": device_name},
            )
            return True
        else:
            logger.warning(
                "PyTorch CUDA not available — transcription will fall back to CPU. "
                "Install PyTorch with CUDA: pip install torch --index-url "
                "https://download.pytorch.org/whl/cu121",
                extra={"stage": "startup", "video_id": ""},
            )
            return False
    except ImportError:
        logger.warning(
            "PyTorch not installed — transcription CUDA check skipped. "
            "GPU transcription will fall back to CPU.",
            extra={"stage": "startup", "video_id": ""},
        )
        return False
