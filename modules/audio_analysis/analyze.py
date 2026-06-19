"""Audio analysis module — per-scene RMS energy extraction via FFmpeg.

Implements deterministic audio energy analysis using FFmpeg's astats filter.
Normalizes RMS energy values within each video to a [0, 1] range.

Entry point: analyze_audio(ingestion_result, scene_list, config) -> AudioEnergyData
"""

from __future__ import annotations

import logging
import re
import subprocess
from typing import TYPE_CHECKING, Any

from contracts.audio import AudioEnergyData, SceneAudioEnergy

if TYPE_CHECKING:
    from contracts.ingestion import IngestionResult
    from contracts.scene import SceneList, SceneSegment

logger = logging.getLogger(__name__)


def analyze_audio(
    ingestion_result: "IngestionResult",
    scene_list: "SceneList",
    config: dict[str, Any],
) -> AudioEnergyData:
    """Compute per-scene RMS audio energy and normalize across the video.

    Uses FFmpeg's astats filter to measure RMS energy for each scene.
    Normalizes using: normalized = (rms - min_rms) / (max_rms - min_rms).
    If all scenes have identical RMS (flat audio), normalized_energy = 0.0.

    Args:
        ingestion_result: DTO from the ingestion stage.
        scene_list: DTO from the scene_splitter stage.
        config: Pipeline configuration dict (from config.yaml).

    Returns:
        AudioEnergyData DTO with per-scene normalized energy values.

    Raises:
        RuntimeError: If audio is unavailable (ingestion_result.has_audio is False).
    """
    video_id = ingestion_result.video_id
    video_path = ingestion_result.path

    if not ingestion_result.has_audio:
        raise RuntimeError(
            f"Video {video_id} has no audio stream — audio energy analysis requires audio."
        )

    logger.info(
        "Audio analysis stage started",
        extra={
            "stage": "audio_analysis",
            "video_id": video_id,
            "scene_count": len(scene_list.scenes),
        },
    )

    raw_energies: list[tuple[str, float]] = []
    for scene in scene_list.scenes:
        rms = _extract_scene_rms(scene, video_path, video_id, config)
        raw_energies.append((scene.scene_id, rms))

    rms_values = [e for _, e in raw_energies]
    video_min_rms = min(rms_values)
    video_max_rms = max(rms_values)
    video_mean_rms = sum(rms_values) / len(rms_values)

    rms_range = video_max_rms - video_min_rms

    scene_energies: list[SceneAudioEnergy] = []
    for scene_id, rms in raw_energies:
        if rms_range > 0.0:
            normalized = (rms - video_min_rms) / rms_range
        else:
            normalized = 0.0
        scene_energies.append(
            SceneAudioEnergy(
                scene_id=scene_id,
                rms_energy=rms,
                normalized_energy=normalized,
            )
        )

    result = AudioEnergyData(
        video_id=video_id,
        scene_energies=tuple(scene_energies),
        video_min_rms=video_min_rms,
        video_max_rms=video_max_rms,
        video_mean_rms=video_mean_rms,
    )

    logger.info(
        "Audio analysis stage completed",
        extra={
            "stage": "audio_analysis",
            "video_id": video_id,
            "video_min_rms": video_min_rms,
            "video_max_rms": video_max_rms,
            "video_mean_rms": video_mean_rms,
        },
    )
    return result


def _extract_scene_rms(
    scene: "SceneSegment",
    video_path: str,
    video_id: str,
    config: dict[str, Any],
) -> float:
    """Extract RMS audio energy for a single scene using FFmpeg astats filter.

    Args:
        scene: The SceneSegment to analyze.
        video_path: Path to the source video.
        video_id: Video identifier for logging.
        config: Pipeline configuration dict.

    Returns:
        RMS energy value (float >= 0.0). Returns 0.0 on FFmpeg failure.
    """
    start_s = scene.start_time / 1000.0
    duration_s = scene.duration
    timeout = config.get("pipeline", {}).get("ffmpeg_timeout", 300)

    cmd = [
        "ffmpeg",
        "-y",
        "-ss", f"{start_s:.3f}",
        "-i", video_path,
        "-t", f"{duration_s:.3f}",
        "-vn",
        "-af", "astats=metadata=1:reset=1,ametadata=print:key=lavfi.astats.Overall.RMS_level",
        "-f", "null",
        "-loglevel", "info",
        "-",
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        logger.warning(
            "FFmpeg audio energy extraction timed out",
            extra={"stage": "audio_analysis", "scene_id": scene.scene_id},
        )
        return 0.0

    if result.returncode != 0:
        logger.warning(
            "FFmpeg audio energy extraction failed",
            extra={
                "stage": "audio_analysis",
                "scene_id": scene.scene_id,
                "stderr": result.stderr[:200],
            },
        )
        return 0.0

    return _parse_rms_from_output(result.stderr + result.stdout, scene.scene_id)


def _parse_rms_from_output(output: str, scene_id: str) -> float:
    """Parse the RMS level value from FFmpeg astats output.

    FFmpeg outputs lines like:
        lavfi.astats.Overall.RMS_level=-23.456789

    Converts dB to linear: linear = 10^(db/20). Returns 0.0 for -inf or on parse failure.

    Args:
        output: Combined stdout + stderr from FFmpeg.
        scene_id: Scene identifier for logging.

    Returns:
        RMS energy as a linear float >= 0.0.
    """
    pattern = re.compile(
        r"lavfi\.astats\.Overall\.RMS_level=([^\s]+)"
    )
    matches = pattern.findall(output)

    if not matches:
        # Fallback: try the simpler "RMS level dB" format from astats summary
        fallback_pattern = re.compile(r"RMS level dB\s*:\s*([-\d.]+|-inf)")
        matches = fallback_pattern.findall(output)

    if not matches:
        logger.debug(
            "No RMS level found in FFmpeg output for scene",
            extra={"stage": "audio_analysis", "scene_id": scene_id},
        )
        return 0.0

    db_str = matches[-1].strip()
    if db_str in ("-inf", "inf", "nan"):
        return 0.0

    try:
        db_value = float(db_str)
    except ValueError:
        logger.warning(
            "Could not parse RMS dB value from FFmpeg output",
            extra={"stage": "audio_analysis", "scene_id": scene_id, "raw": db_str},
        )
        return 0.0

    # Convert dB to linear amplitude
    linear = 10.0 ** (db_value / 20.0)
    return max(0.0, linear)
