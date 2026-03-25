"""Scene splitter module for Shorts Factory.

Detects scene boundaries in a video using PySceneDetect, applies
post-processing to enforce duration constraints, and returns a
deterministic SceneList DTO.

Public API:
    split_scenes(ingestion_result, config) -> SceneList
"""

from __future__ import annotations

import logging
from typing import Any

from contracts.ingestion import IngestionResult
from contracts.scene import SceneList, SceneSegment

logger = logging.getLogger(__name__)


class SceneSplitterError(Exception):
    """Raised when scene splitting fails unrecoverably."""


def split_scenes(
    ingestion_result: IngestionResult, config: dict[str, Any]
) -> SceneList:
    """Detect and return scene boundaries for the given video.

    Args:
        ingestion_result: Validated ingestion result for the source video.
        config: Pipeline configuration dictionary.

    Returns:
        SceneList with deterministic, duration-constrained scene segments.

    Raises:
        SceneSplitterError: If scene detection fails and fallback also fails.
    """
    scene_cfg = config["scene_splitter"]
    threshold = float(scene_cfg["threshold"])
    min_dur = float(scene_cfg["min_scene_duration"])
    max_dur = float(scene_cfg["max_scene_duration"])
    fallback_threshold = float(scene_cfg.get("fallback_threshold", 27.0))
    fallback_target_dur = float(scene_cfg.get("fallback_target_duration", 10.0))

    video_path = ingestion_result.path
    video_id = ingestion_result.video_id
    total_secs = ingestion_result.duration_seconds

    raw_scenes = _detect_scenes(
        video_path, threshold, total_secs, video_id,
        fallback_threshold=fallback_threshold,
        fallback_target_duration=fallback_target_dur,
    )

    processed = _post_process(raw_scenes, min_dur, max_dur, total_secs, video_id)

    segments = _build_segments(processed, video_id)

    if not segments:
        logger.warning(
            "No scenes produced after post-processing; creating single full-video scene",
            extra={"stage": "scene_splitter", "video_id": video_id},
        )
        segments = _single_scene_fallback(video_id, total_secs)

    total_duration = round(sum(s.duration for s in segments), 6)

    logger.info(
        "Scene splitting complete",
        extra={
            "stage": "scene_splitter",
            "video_id": video_id,
            "scene_count": len(segments),
            "total_duration": total_duration,
        },
    )

    return SceneList(
        video_id=video_id,
        scenes=tuple(segments),
        total_duration=total_duration,
    )


def _detect_scenes(
    video_path: str,
    threshold: float,
    total_secs: float,
    video_id: str = "",
    *,
    fallback_threshold: float = 27.0,
    fallback_target_duration: float = 10.0,
) -> list[tuple[float, float]]:
    """Run PySceneDetect and return (start_sec, end_sec) pairs.

    Falls back to uniform splitting if PySceneDetect is unavailable or fails.

    Returns:
        List of (start_seconds, end_seconds) tuples covering the full video.
    """
    try:
        return _detect_with_scenedetect(video_path, threshold)
    except ImportError:
        logger.warning(
            "PySceneDetect not available; using uniform scene splitting",
            extra={"stage": "scene_splitter", "video_id": video_id},
        )
        return _uniform_split(total_secs, target_duration=fallback_target_duration)
    except Exception as exc:
        logger.warning(
            "PySceneDetect failed; retrying with fallback threshold",
            extra={"stage": "scene_splitter", "video_id": video_id, "error": str(exc)},
        )
        try:
            return _detect_with_scenedetect(video_path, threshold=fallback_threshold)
        except Exception as exc2:
            logger.warning(
                "PySceneDetect retry failed; using uniform scene splitting",
                extra={
                    "stage": "scene_splitter",
                    "video_id": video_id,
                    "error": str(exc2),
                },
            )
            return _uniform_split(total_secs, target_duration=fallback_target_duration)


def _detect_with_scenedetect(
    video_path: str, threshold: float
) -> list[tuple[float, float]]:
    """Use PySceneDetect ContentDetector to find scene boundaries.

    Args:
        video_path: Absolute path to the video file.
        threshold: Detection sensitivity threshold.

    Returns:
        List of (start_seconds, end_seconds) tuples.

    Raises:
        ImportError: If scenedetect is not installed.
        SceneSplitterError: If detection fails.
    """
    from scenedetect import open_video, SceneManager  # type: ignore[import]
    from scenedetect.detectors import ContentDetector  # type: ignore[import]

    video = open_video(video_path)
    scene_manager = SceneManager()
    scene_manager.add_detector(ContentDetector(threshold=threshold))
    scene_manager.detect_scenes(video, show_progress=False)

    scene_list = scene_manager.get_scene_list()

    if not scene_list:
        duration = video.duration.get_seconds() if video.duration else 0.0
        return [(0.0, duration)]

    pairs: list[tuple[float, float]] = []
    for start_tc, end_tc in scene_list:
        pairs.append((start_tc.get_seconds(), end_tc.get_seconds()))

    return pairs


def _uniform_split(
    total_secs: float, target_duration: float = 10.0
) -> list[tuple[float, float]]:
    """Split a video uniformly into segments of approximately target_duration.

    Args:
        total_secs: Total video duration in seconds.
        target_duration: Target segment duration in seconds.

    Returns:
        List of (start_seconds, end_seconds) tuples.
    """
    if total_secs <= 0:
        return []

    segments: list[tuple[float, float]] = []
    start = 0.0
    while start < total_secs:
        end = min(start + target_duration, total_secs)
        segments.append((start, end))
        start = end

    return segments


def _post_process(
    raw: list[tuple[float, float]],
    min_dur: float,
    max_dur: float,
    total_secs: float,
    video_id: str = "",
) -> list[tuple[float, float]]:
    """Enforce min/max duration constraints on scene list.

    1. Merge scenes shorter than min_dur into their predecessor.
    2. Force-split scenes longer than max_dur at the midpoint.
    3. Warn if scene count is excessive (> 500 for < 3600s).

    Args:
        raw: Detected (start_sec, end_sec) scene pairs.
        min_dur: Minimum scene duration in seconds.
        max_dur: Maximum scene duration in seconds.
        total_secs: Total video duration in seconds.
        video_id: Video identifier for structured logging.

    Returns:
        Post-processed list of (start_sec, end_sec) pairs.
    """
    # Ensure deterministic ordering by start and end time before processing
    ordered = sorted(raw, key=lambda pair: (pair[0], pair[1]))

    # Merge micro-scenes first
    merged = _merge_short_scenes(ordered, min_dur)
    # Then enforce maximum duration by splitting long scenes
    expanded = _split_long_scenes(merged, max_dur)

    if total_secs < 3600.0 and len(expanded) > 500:
        logger.warning(
            "Excessive scene count detected; running additional merge pass",
            extra={
                "stage": "scene_splitter",
                "video_id": video_id,
                "scene_count": len(expanded),
            },
        )
        while len(expanded) > 300:
            prev_len = len(expanded)
            expanded = _merge_short_scenes(expanded, min_dur * 1.5)
            if len(expanded) == prev_len:
                break

    return expanded


def _merge_short_scenes(
    scenes: list[tuple[float, float]], min_dur: float
) -> list[tuple[float, float]]:
    """Merge scenes shorter than min_dur with an adjacent scene.

    - If a short scene has a predecessor, merge it into the predecessor.
    - If a short scene is first (no predecessor), merge it into the successor.

    Args:
        scenes: List of (start_sec, end_sec) pairs, sorted by start.
        min_dur: Minimum duration in seconds.

    Returns:
        Merged scene list.
    """
    if not scenes:
        return []

    result: list[tuple[float, float]] = list(scenes)
    changed = True
    while changed:
        changed = False
        new_result: list[tuple[float, float]] = []
        i = 0
        while i < len(result):
            start, end = result[i]
            dur = end - start
            if dur < min_dur:
                if new_result:
                    # Merge into predecessor
                    prev_start, _ = new_result[-1]
                    new_result[-1] = (prev_start, end)
                    changed = True
                elif i + 1 < len(result):
                    # No predecessor — merge forward into successor
                    _, next_end = result[i + 1]
                    new_result.append((start, next_end))
                    i += 2
                    changed = True
                    continue
                else:
                    # Single element that is too short — keep as-is
                    new_result.append((start, end))
            else:
                new_result.append((start, end))
            i += 1
        result = new_result

    return result


def _split_long_scenes(
    scenes: list[tuple[float, float]], max_dur: float
) -> list[tuple[float, float]]:
    """Recursively split scenes longer than max_dur at midpoint.

    Args:
        scenes: List of (start_sec, end_sec) pairs.
        max_dur: Maximum duration in seconds.

    Returns:
        List with all scenes within max_dur.
    """
    result: list[tuple[float, float]] = []
    for start, end in scenes:
        _split_one(start, end, max_dur, result)
    return result


def _split_one(
    start: float,
    end: float,
    max_dur: float,
    out: list[tuple[float, float]],
) -> None:
    """Recursively split a single scene at its midpoint until it fits max_dur."""
    dur = end - start
    if dur <= max_dur:
        out.append((start, end))
        return
    mid = start + dur / 2.0
    _split_one(start, mid, max_dur, out)
    _split_one(mid, end, max_dur, out)


def _build_segments(
    scenes: list[tuple[float, float]], video_id: str
) -> list[SceneSegment]:
    """Convert (start_sec, end_sec) pairs to SceneSegment DTOs.

    Args:
        scenes: List of (start_sec, end_sec) pairs.
        video_id: Parent video identifier.

    Returns:
        List of SceneSegment DTOs sorted by start_time.
    """
    segments: list[SceneSegment] = []
    for start_sec, end_sec in sorted(scenes, key=lambda p: p[0]):
        start_ms = int(round(start_sec * 1000))
        end_ms = int(round(end_sec * 1000))
        if end_ms <= start_ms:
            continue
        duration = (end_ms - start_ms) / 1000.0
        scene_id = f"{video_id}_{start_ms}_{end_ms}"
        segments.append(
            SceneSegment(
                scene_id=scene_id,
                video_id=video_id,
                start_time=start_ms,
                end_time=end_ms,
                duration=duration,
            )
        )
    return segments


def _single_scene_fallback(
    video_id: str, total_secs: float
) -> list[SceneSegment]:
    """Create a single scene spanning the full video.

    Used when scene detection produces no valid scenes.

    Args:
        video_id: Parent video identifier.
        total_secs: Total video duration in seconds.

    Returns:
        Single-element list with one SceneSegment.
    """
    start_ms = 0
    end_ms = int(round(total_secs * 1000))
    duration = end_ms / 1000.0
    scene_id = f"{video_id}_{start_ms}_{end_ms}"
    return [
        SceneSegment(
            scene_id=scene_id,
            video_id=video_id,
            start_time=start_ms,
            end_time=end_ms,
            duration=duration,
        )
    ]
