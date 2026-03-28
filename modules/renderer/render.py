"""Final MP4 renderer — assembles composite video, TTS audio, and subtitles.

Produces the publish-ready clip by mixing gameplay audio (70%) with
TTS narration (30%), burning in ASS subtitles, and encoding to
H.264 High Profile at 30fps 1080×1920.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess

from contracts.compositor import CompositeStream
from contracts.render import RenderedClip
from contracts.subtitle import SubtitleResult
from contracts.tts import TTSResult

from core.gpu import resolve_gpu_settings

logger = logging.getLogger(__name__)

# Cache the result of the ass filter availability check
_ass_filter_available: bool | None = None


def _check_ass_filter() -> bool:
    """Check if FFmpeg has the 'ass' subtitle filter compiled in (requires libass)."""
    global _ass_filter_available
    if _ass_filter_available is not None:
        return _ass_filter_available
    try:
        result = subprocess.run(
            ["ffmpeg", "-filters"],
            capture_output=True, text=True, timeout=10,
        )
        # Look for the 'ass' filter as a standalone word in the filter list
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "ass":
                _ass_filter_available = True
                return True
        _ass_filter_available = False
        logger.warning(
            "FFmpeg 'ass' filter not available (libass not compiled in). "
            "Subtitles will NOT be burned into rendered clips. "
            "Install FFmpeg with libass to enable subtitle burn-in."
        )
        return False
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        _ass_filter_available = False
        return False




def _run_ffmpeg(args: list[str], timeout: int = 300) -> subprocess.CompletedProcess:
    """Run FFmpeg with -y, capture output, and timeout."""
    cmd = ["ffmpeg", "-y"] + args
    logger.debug("FFmpeg command: %s", " ".join(cmd))
    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout,
    )
    if result.returncode != 0:
        logger.error(
            "FFmpeg failed",
            extra={"stderr": result.stderr[-2000:], "returncode": result.returncode},
        )
        raise RuntimeError(
            f"FFmpeg error (exit {result.returncode}): {result.stderr[-1000:]}"
        )
    return result


def _probe_video(file_path: str) -> dict:
    """Probe video metadata with ffprobe."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_format", "-show_streams",
            "-of", "json", file_path,
        ],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr[:200]}")
    return json.loads(result.stdout)


def _get_duration(file_path: str) -> float:
    """Get media duration in seconds."""
    data = _probe_video(file_path)
    return float(data["format"]["duration"])


def _get_file_size(file_path: str) -> int:
    """Get file size in bytes."""
    return os.path.getsize(file_path)


def _get_video_info(file_path: str) -> tuple[int, int, str, int]:
    """Return (width, height, codec, fps) from video probe."""
    data = _probe_video(file_path)
    for stream in data.get("streams", []):
        if stream.get("codec_type") == "video":
            width = int(stream["width"])
            height = int(stream["height"])
            codec = stream.get("codec_name", "unknown")
            fps_str = stream.get("r_frame_rate", "30/1")
            num, den = fps_str.split("/")
            fps = int(round(int(num) / max(int(den), 1)))
            return width, height, codec, fps
    raise RuntimeError("No video stream found in output")


def _build_render_command(
    composite: CompositeStream,
    tts_result: TTSResult | None,
    subtitle_result: SubtitleResult | None,
    output_path: str,
    config: dict,
    crf_override: int | None = None,
) -> list[str]:
    """Build the FFmpeg render command with audio mixing and subtitle burn-in."""
    renderer_config = config.get("renderer", {})
    codec = renderer_config.get("codec", "libx264")
    crf = crf_override if crf_override is not None else renderer_config.get("crf", 20)
    preset = renderer_config.get("preset", "medium")
    fps = renderer_config.get("fps", 30)
    audio_source = renderer_config.get("audio_source", "original")
    gameplay_vol = renderer_config.get("audio_mix_gameplay", 0.7)
    narration_vol = renderer_config.get("audio_mix_narration", 0.3)

    args: list[str] = []

    # Input 0: composite video
    args.extend(["-i", composite.composite_path])

    # Input 1: gameplay audio — trim to clip time range
    if composite.start_time_ms > 0:
        start_sec = composite.start_time_ms / 1000.0
        args.extend(["-ss", str(start_sec)])
    args.extend(["-t", str(composite.duration_seconds)])
    args.extend(["-i", composite.source_audio_path])

    has_narration = (
        tts_result is not None
        and os.path.exists(tts_result.audio_path)
    )

    if has_narration and audio_source == "mixed":
        # Input 2: TTS narration audio
        args.extend(["-i", tts_result.audio_path])

    # Build filter complex
    filters: list[str] = []
    video_filters: list[str] = []

    # Subtitle burn-in (video filter)
    has_subtitles = (
        subtitle_result is not None
        and os.path.exists(subtitle_result.ass_path)
        and subtitle_result.subtitle_count > 0
        and _check_ass_filter()
    )
    if has_subtitles:
        # Escape path for FFmpeg filter
        escaped_path = subtitle_result.ass_path.replace("\\", "/").replace(":", "\\:")
        video_filters.append(f"ass={escaped_path}")

    # Audio mixing
    # "original" mode: use source audio at full volume, ignore TTS
    # "mixed" mode: mix gameplay audio + TTS narration
    use_narration = has_narration and audio_source == "mixed"

    if use_narration:
        filters.append(f"[1:a]volume={gameplay_vol}[game]")
        filters.append(f"[2:a]volume={narration_vol}[narr]")
        filters.append("[game][narr]amix=inputs=2:duration=first[aout]")
        audio_map = "[aout]"
    else:
        # Original audio at full volume (no reduction)
        filters.append("[1:a]anull[aout]")
        audio_map = "[aout]"

    # Combine video and audio filter complex
    if video_filters:
        vf_chain = ",".join(video_filters)
        filters.insert(0, f"[0:v]{vf_chain}[vout]")
        video_map = "[vout]"
    else:
        video_map = "0:v"

    if filters:
        args.extend(["-filter_complex", ";".join(filters)])

    # Map outputs
    args.extend(["-map", video_map, "-map", audio_map])

    # Encoding settings
    gpu_settings = resolve_gpu_settings(config)
    if crf_override is not None and not gpu_settings["enabled"]:
        # CPU mode with CRF override (retry with lower quality)
        args.extend([
            "-c:v", gpu_settings["ffmpeg_encoder"],
            "-crf", str(crf_override),
            "-preset", preset,
            "-profile:v", "high",
        ])
    elif crf_override is not None and gpu_settings["enabled"]:
        # GPU mode with quality override — use fallback args
        args.extend(gpu_settings["ffmpeg_encode_args_fallback"])
    else:
        # Normal encoding — CPU or GPU primary args
        args.extend(gpu_settings["ffmpeg_encode_args"])

    args.extend([
        "-c:a", "aac",
        "-b:a", "128k",
        "-ar", "44100",
        "-r", str(fps),
        "-t", str(composite.duration_seconds),
        "-movflags", "+faststart",
        output_path,
    ])

    return args


def _validate_output(
    file_path: str,
    min_duration: float = 30.0,
    max_duration: float = 60.0,
    max_file_size: int = 100 * 1024 * 1024,
) -> tuple[float, tuple[int, int], str, int, int]:
    """Validate rendered output meets specifications.

    Returns (duration, resolution, codec, fps, file_size).
    Raises RuntimeError if validation fails.
    """
    duration = _get_duration(file_path)
    width, height, codec, fps = _get_video_info(file_path)
    file_size = _get_file_size(file_path)

    if duration < min_duration - 1.0 or duration > max_duration + 1.0:
        raise RuntimeError(
            f"Rendered duration {duration:.1f}s outside [{min_duration}, {max_duration}]"
        )

    if (width, height) != (1080, 1920):
        raise RuntimeError(f"Resolution {width}x{height} != 1080x1920")

    if codec.lower() != "h264":
        raise RuntimeError(f"Codec {codec!r} != 'h264'")

    if abs(float(fps) - 30.0) > 0.5:
        raise RuntimeError(f"Frame rate {fps} != 30fps")

    if file_size > max_file_size:
        raise RuntimeError(
            f"File size {file_size} bytes exceeds max {max_file_size} bytes"
        )

    return duration, (width, height), codec, fps, file_size


def process(
    composite: CompositeStream,
    tts_result: TTSResult | None,
    subtitle_result: SubtitleResult | None,
    config: dict,
    output_dir: str,
) -> RenderedClip:
    """Render the final MP4 from composite video, TTS audio, and subtitles.

    Args:
        composite: Composited video stream (1080×1920).
        tts_result: TTS narration audio (may be None if TTS failed).
        subtitle_result: Generated ASS subtitles (may be None).
        config: Configuration dict (renderer section used).
        output_dir: Base output directory for the video.

    Returns:
        RenderedClip with path to the final publish-ready MP4.
    """
    # Derive clip directory from composite_path (e.g. .../shorts-1/composite.mp4)
    # This ensures renderer uses the same folder name the compositor created.
    clip_dir = os.path.dirname(composite.composite_path)
    os.makedirs(clip_dir, exist_ok=True)
    output_path = os.path.join(clip_dir, "final.mp4")

    renderer_config = config.get("renderer", {})
    max_size = renderer_config.get("max_file_size_mb", 100) * 1024 * 1024

    # Idempotent: skip if already rendered
    if os.path.exists(output_path):
        try:
            duration, resolution, codec, fps, file_size = _validate_output(
                output_path, max_file_size=max_size,
            )
            logger.info(
                "Render cache hit",
                extra={"clip_id": composite.clip_id, "path": output_path},
            )
            cached_has_narration = (
                tts_result is not None
                and os.path.exists(tts_result.audio_path)
            )
            cached_has_subtitles = (
                subtitle_result is not None
                and os.path.exists(subtitle_result.ass_path)
                and subtitle_result.subtitle_count > 0
            )
            return RenderedClip(
                clip_id=composite.clip_id,
                video_id=composite.video_id,
                output_path=output_path,
                duration_seconds=duration,
                resolution=resolution,
                codec=codec,
                fps=fps,
                file_size_bytes=file_size,
                has_narration=cached_has_narration,
                has_subtitles=cached_has_subtitles,
            )
        except (RuntimeError, Exception) as err:
            logger.warning(
                "Cached render invalid, re-rendering",
                extra={"clip_id": composite.clip_id, "error": str(err)[:100]},
            )

    timeout = config.get("pipeline", {}).get("ffmpeg_timeout", 300)

    # Render with default CRF
    base, ext = os.path.splitext(output_path)
    tmp_path = f"{base}.tmp{ext}"
    args = _build_render_command(
        composite, tts_result, subtitle_result, tmp_path, config,
    )

    try:
        _run_ffmpeg(args, timeout=timeout)
    except (RuntimeError, subprocess.TimeoutExpired) as err:
        # Retry with lower quality CRF
        logger.warning(
            "Render failed, retrying with CRF 24",
            extra={"clip_id": composite.clip_id, "error": str(err)[:100]},
        )
        args = _build_render_command(
            composite, tts_result, subtitle_result, tmp_path, config,
            crf_override=24,
        )
        _run_ffmpeg(args, timeout=timeout)

    # Validate output
    duration, resolution, codec, fps, file_size = _validate_output(
        tmp_path, max_file_size=max_size,
    )

    # Re-encode if file too large
    if file_size > max_size:
        logger.warning(
            "Output exceeds max size, re-encoding with CRF 24",
            extra={
                "clip_id": composite.clip_id,
                "size_mb": round(file_size / 1024 / 1024, 1),
            },
        )
        re_tmp = f"{base}.re.tmp{ext}"
        args = _build_render_command(
            composite, tts_result, subtitle_result, re_tmp, config,
            crf_override=24,
        )
        _run_ffmpeg(args, timeout=timeout)

        file_size = _get_file_size(re_tmp)
        if file_size > max_size:
            logger.warning(
                "Still too large, re-encoding with CRF 28",
                extra={"clip_id": composite.clip_id},
            )
            args = _build_render_command(
                composite, tts_result, subtitle_result, re_tmp, config,
                crf_override=28,
            )
            _run_ffmpeg(args, timeout=timeout)
            file_size = _get_file_size(re_tmp)

        duration, resolution, codec, fps, file_size = _validate_output(
            re_tmp, max_file_size=max_size,
        )

        # Clean up previous tmp and use re-encoded version
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        tmp_path = re_tmp

    # Atomic rename
    os.replace(tmp_path, output_path)

    # Clean intermediate files
    for leftover in [f"{base}.tmp{ext}", f"{base}.re.tmp{ext}"]:
        if os.path.exists(leftover):
            try:
                os.remove(leftover)
            except OSError:
                pass

    logger.info(
        "Render complete",
        extra={
            "clip_id": composite.clip_id,
            "duration_s": round(duration, 2),
            "size_mb": round(file_size / 1024 / 1024, 1),
            "has_narration": tts_result is not None,
            "has_subtitles": subtitle_result is not None,
        },
    )

    rendered_has_narration = (
        tts_result is not None
        and os.path.exists(tts_result.audio_path)
    )
    rendered_has_subtitles = (
        subtitle_result is not None
        and os.path.exists(subtitle_result.ass_path)
        and subtitle_result.subtitle_count > 0
    )

    return RenderedClip(
        clip_id=composite.clip_id,
        video_id=composite.video_id,
        output_path=output_path,
        duration_seconds=duration,
        resolution=resolution,
        codec=codec,
        fps=fps,
        file_size_bytes=file_size,
        has_narration=rendered_has_narration,
        has_subtitles=rendered_has_subtitles,
    )
