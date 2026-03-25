"""Clip builder — greedy scene merging into 30-60 second clips.

Receives a ScoredSceneList and merges adjacent high-scoring scenes
into publishable clip definitions of exactly 30-60 seconds.

Algorithm (nucleus expansion):
  1. Sort scenes by composite score descending (start_time ASC tiebreaker)
  2. Pick highest-scored unconsumed scene as nucleus
  3. Expand outward temporally with adjacent contiguous scenes
  4. Continue until cumulative duration >= 30 seconds
  5. If > 60 seconds, trim lowest-scored edge scene
  6. Compute deterministic clip_id and average_score
  7. Apply rejection criteria
  8. If too few clips, lower threshold and retry (up to 3 times)
"""

from __future__ import annotations

import hashlib
import logging
from typing import Optional

from contracts.clip import ClipDefinition, ClipList
from contracts.scoring import ScoredScene, ScoredSceneList

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def process(scored_scene_list: ScoredSceneList, config: dict) -> ClipList:
    """Build clips from scored scenes using greedy nucleus expansion.

    Args:
        scored_scene_list: All scenes ranked by composite score.
        config: Full pipeline configuration dict.

    Returns:
        ClipList with 1-20 clips sorted by start_time ASC.

    Raises:
        ValueError: If no valid clips can be produced at all.
    """
    video_id = scored_scene_list.video_id
    cb_config = config.get("clip_builder", {})
    pipeline_config = config.get("pipeline", {})

    min_duration = float(cb_config.get("target_duration_min", 30))
    max_duration = float(cb_config.get("target_duration_max", 60))
    max_clips = int(cb_config.get("max_clips_per_video", 15))
    min_clips = int(cb_config.get("min_clips_per_video", 1))
    max_overlap_ratio = float(cb_config.get("max_overlap_ratio", 0.5))
    min_composite_score = float(
        config.get("scoring", {}).get("min_composite_score", 0.2)
    )
    max_clips_cap = int(pipeline_config.get("max_clips_per_run", 20))

    # Build temporal index of scenes sorted by start_time for adjacency lookups
    scenes_by_time = sorted(scored_scene_list.scenes, key=lambda s: s.start_time)

    accepted: list[_CandidateClip] = []
    rejected_count = 0

    # Retry loop: lower threshold up to 3 times if too few clips
    threshold = min_composite_score
    max_retries = 3

    for attempt in range(max_retries + 1):
        consumed: set[str] = set()
        # Mark scenes already consumed by accepted clips
        for clip in accepted:
            for scene in clip.scenes:
                consumed.add(scene.scene_id)

        candidates = _build_candidates(
            scenes_by_time=scenes_by_time,
            consumed=consumed,
            min_duration=min_duration,
            max_duration=max_duration,
            video_id=video_id,
        )

        for candidate in candidates:
            if len(accepted) >= max_clips_cap:
                break
            if candidate.average_score < threshold:
                rejected_count += 1
                continue
            if _has_excessive_overlap(candidate, accepted, max_overlap_ratio):
                rejected_count += 1
                continue
            accepted.append(candidate)
            for scene in candidate.scenes:
                consumed.add(scene.scene_id)

        if len(accepted) >= min_clips:
            break

        if attempt < max_retries:
            threshold = max(0.0, threshold - 0.05)
            logger.warning(
                "Insufficient clips, lowering threshold",
                extra={
                    "video_id": video_id,
                    "stage": "clip_builder",
                    "attempt": attempt + 1,
                    "threshold": threshold,
                    "clips_so_far": len(accepted),
                },
            )

    if not accepted:
        logger.error(
            "No valid clips produced",
            extra={
                "video_id": video_id,
                "stage": "clip_builder",
                "status": "failed",
            },
        )
        raise ValueError(
            f"No valid clips could be produced for video {video_id}"
        )

    # Sort accepted clips by start_time ASC, assign clip_index
    accepted.sort(key=lambda c: c.start_time)

    # Cap at max_clips_cap
    accepted = accepted[:max_clips_cap]

    clip_definitions: list[ClipDefinition] = []
    for idx, candidate in enumerate(accepted):
        clip_def = ClipDefinition(
            clip_id=candidate.clip_id,
            video_id=video_id,
            scenes=tuple(candidate.scenes),
            start_time=candidate.start_time,
            end_time=candidate.end_time,
            duration=candidate.duration,
            average_score=candidate.average_score,
            clip_index=idx,
        )
        clip_definitions.append(clip_def)

    clip_list = ClipList(
        video_id=video_id,
        clips=tuple(clip_definitions),
        total_clips=len(clip_definitions),
        clips_rejected=rejected_count,
    )

    logger.info(
        "Clip building complete",
        extra={
            "video_id": video_id,
            "stage": "clip_builder",
            "status": "success",
            "total_clips": clip_list.total_clips,
            "clips_rejected": clip_list.clips_rejected,
        },
    )

    return clip_list


# ---------------------------------------------------------------------------
# Internal data structures
# ---------------------------------------------------------------------------


class _CandidateClip:
    """Mutable working structure for clip construction (internal only)."""

    __slots__ = (
        "scenes", "start_time", "end_time", "duration",
        "average_score", "clip_id", "video_id",
    )

    def __init__(
        self,
        scenes: list[ScoredScene],
        video_id: str,
    ) -> None:
        self.scenes = sorted(scenes, key=lambda s: s.start_time)
        self.video_id = video_id
        self.start_time = self.scenes[0].start_time
        self.end_time = self.scenes[-1].end_time
        self.duration = (self.end_time - self.start_time) / 1000.0
        self.average_score = (
            sum(s.composite_score for s in self.scenes) / len(self.scenes)
        )
        self.clip_id = _compute_clip_id(
            video_id, self.start_time, self.end_time
        )


# ---------------------------------------------------------------------------
# Core algorithm
# ---------------------------------------------------------------------------


def _build_candidates(
    scenes_by_time: list[ScoredScene],
    consumed: set[str],
    min_duration: float,
    max_duration: float,
    video_id: str,
) -> list[_CandidateClip]:
    """Build candidate clips via greedy nucleus expansion.

    Returns candidates sorted by average_score DESC, start_time ASC.
    """
    if not scenes_by_time:
        return []

    # Position index for each scene in temporal order
    pos_by_id: dict[str, int] = {
        s.scene_id: i for i, s in enumerate(scenes_by_time)
    }

    # Sort by composite_score DESC, start_time ASC for nucleus selection
    ranked = sorted(
        scenes_by_time,
        key=lambda s: (-s.composite_score, s.start_time),
    )

    local_consumed: set[str] = set(consumed)
    candidates: list[_CandidateClip] = []

    for nucleus in ranked:
        if nucleus.scene_id in local_consumed:
            continue

        clip_scenes = _expand_nucleus(
            nucleus=nucleus,
            scenes_by_time=scenes_by_time,
            pos_by_id=pos_by_id,
            consumed=local_consumed,
            min_duration=min_duration,
            max_duration=max_duration,
        )

        if clip_scenes is None:
            continue

        candidate = _CandidateClip(clip_scenes, video_id)

        if candidate.duration < min_duration:
            continue
        if candidate.duration > max_duration:
            # Try to split oversized clip
            split = _split_oversized(
                clip_scenes, min_duration, max_duration, video_id
            )
            for sc in split:
                for s in sc.scenes:
                    local_consumed.add(s.scene_id)
                candidates.append(sc)
            continue

        for s in clip_scenes:
            local_consumed.add(s.scene_id)
        candidates.append(candidate)

    # Deterministic sort: average_score DESC, start_time ASC
    candidates.sort(key=lambda c: (-c.average_score, c.start_time))
    return candidates


def _expand_nucleus(
    nucleus: ScoredScene,
    scenes_by_time: list[ScoredScene],
    pos_by_id: dict[str, int],
    consumed: set[str],
    min_duration: float,
    max_duration: float,
) -> Optional[list[ScoredScene]]:
    """Expand from nucleus scene outward to build a clip of valid duration.

    Expands by adding contiguous adjacent scenes, preferring higher-scored
    neighbours. Stops when duration >= min_duration or no more contiguous
    unconsumed scenes are available.
    """
    nucleus_pos = pos_by_id[nucleus.scene_id]
    included: list[ScoredScene] = [nucleus]
    included_positions: set[int] = {nucleus_pos}

    left = nucleus_pos - 1
    right = nucleus_pos + 1
    n = len(scenes_by_time)

    def _current_duration() -> float:
        times = [(s.start_time, s.end_time) for s in included]
        return (max(t[1] for t in times) - min(t[0] for t in times)) / 1000.0

    while _current_duration() < min_duration:
        # Check left and right for contiguous, unconsumed scenes
        can_left = (
            left >= 0
            and scenes_by_time[left].scene_id not in consumed
            and left not in included_positions
            and _is_contiguous(scenes_by_time[left], scenes_by_time[left + 1])
        )
        can_right = (
            right < n
            and scenes_by_time[right].scene_id not in consumed
            and right not in included_positions
            and _is_contiguous(scenes_by_time[right - 1], scenes_by_time[right])
        )

        if not can_left and not can_right:
            break

        # Prefer the higher-scored adjacent scene
        if can_left and can_right:
            if scenes_by_time[left].composite_score >= scenes_by_time[right].composite_score:
                chosen_pos = left
            else:
                chosen_pos = right
        elif can_left:
            chosen_pos = left
        else:
            chosen_pos = right

        included.append(scenes_by_time[chosen_pos])
        included_positions.add(chosen_pos)

        if chosen_pos == left:
            left -= 1
        else:
            right += 1

        # Check if exceeding max duration after adding
        if _current_duration() > max_duration:
            # Remove the just-added scene (lowest scored at the edges)
            included.pop()
            included_positions.discard(chosen_pos)
            break

    duration = _current_duration()
    if duration < min_duration:
        return None

    return sorted(included, key=lambda s: s.start_time)


def _is_contiguous(scene_a: ScoredScene, scene_b: ScoredScene) -> bool:
    """Check if two scenes are temporally contiguous (no gap between them)."""
    return scene_a.end_time == scene_b.start_time


def _split_oversized(
    scenes: list[ScoredScene],
    min_duration: float,
    max_duration: float,
    video_id: str,
) -> list[_CandidateClip]:
    """Split an oversized clip at the scene boundary nearest to 45 seconds.

    Returns one or two valid candidate clips. Discards fragments < min_duration.
    """
    sorted_scenes = sorted(scenes, key=lambda s: s.start_time)
    clip_start = sorted_scenes[0].start_time
    target_ms = clip_start + int(45 * 1000)

    # Find split point: scene boundary nearest to 45s from clip start
    best_split_idx = 0
    best_distance = float("inf")
    for i in range(1, len(sorted_scenes)):
        boundary = sorted_scenes[i].start_time
        distance = abs(boundary - target_ms)
        if distance < best_distance:
            best_distance = distance
            best_split_idx = i

    if best_split_idx == 0:
        best_split_idx = 1

    part_a = sorted_scenes[:best_split_idx]
    part_b = sorted_scenes[best_split_idx:]

    results: list[_CandidateClip] = []
    for part in [part_a, part_b]:
        if not part:
            continue
        dur = (part[-1].end_time - part[0].start_time) / 1000.0
        if dur < min_duration:
            continue
        if dur > max_duration:
            continue
        results.append(_CandidateClip(part, video_id))

    return results


def _has_excessive_overlap(
    candidate: _CandidateClip,
    accepted: list[_CandidateClip],
    max_overlap_ratio: float,
) -> bool:
    """Check if candidate overlaps > max_overlap_ratio with any accepted clip."""
    for existing in accepted:
        overlap = _compute_overlap_ratio(candidate, existing)
        if overlap > max_overlap_ratio:
            return True
    return False


def _compute_overlap_ratio(
    clip_a: _CandidateClip,
    clip_b: _CandidateClip,
) -> float:
    """Compute the temporal overlap ratio between two clips.

    Overlap ratio = overlap_duration / min(clip_a.duration, clip_b.duration).
    """
    overlap_start = max(clip_a.start_time, clip_b.start_time)
    overlap_end = min(clip_a.end_time, clip_b.end_time)
    overlap_ms = max(0, overlap_end - overlap_start)

    if overlap_ms == 0:
        return 0.0

    min_duration_ms = min(
        clip_a.end_time - clip_a.start_time,
        clip_b.end_time - clip_b.start_time,
    )
    if min_duration_ms == 0:
        return 0.0

    return overlap_ms / min_duration_ms


def _compute_clip_id(video_id: str, start_ms: int, end_ms: int) -> str:
    """Compute deterministic clip ID: SHA256(video_id + str(start_ms) + str(end_ms))[:16]."""
    raw = f"{video_id}{start_ms}{end_ms}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]
