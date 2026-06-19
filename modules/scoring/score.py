"""Main scoring engine for Shorts Factory.

Evaluates every scene across five factors (keyword, audio_energy,
face_presence, scene_activity, sentence_density), computes a weighted
composite score, and returns a deterministically ranked ScoredSceneList.

All factor weights are loaded from config.yaml — never hardcoded.
Missing signals default to 0.0 and never cause a crash.
"""

from __future__ import annotations

import logging
from typing import Optional

from contracts.audio import AudioEnergyData
from contracts.face import FaceDetectionResult
from contracts.scene import SceneList
from contracts.scoring import ScoredScene, ScoredSceneList
from contracts.transcript import Transcript

from .activity import compute_scene_activities
from .keywords import get_keywords, score_keyword
from .quality import compute_scene_qualities

logger = logging.getLogger(__name__)


def process(
    scene_list: SceneList,
    transcript: Transcript,
    face_result: FaceDetectionResult,
    audio_data: Optional[AudioEnergyData],
    config: dict,
    file_path: Optional[str] = None,
) -> ScoredSceneList:
    """Score all scenes and return a ranked ScoredSceneList.

    Args:
        scene_list: All detected scenes for the video.
        transcript: Full word-level transcript (may have empty segments).
        face_result: Per-scene face detection data.
        audio_data: Per-scene audio energy (may be None — treated as 0.0).
        config: Full pipeline configuration dict.
        file_path: Absolute path to source video for activity computation.
                   Omitting this sets scene_activity = 0.0 for all scenes.

    Returns:
        ScoredSceneList sorted by composite_score DESC, then start_time ASC.
    """
    video_id = scene_list.video_id
    keywords = get_keywords(config)
    weights = _get_weights(config)

    # Build O(1) lookups keyed by scene_id.
    face_by_scene = {fd.scene_id: fd for fd in face_result.scene_data}
    audio_by_scene: dict[str, float] = {}
    if audio_data is not None:
        audio_by_scene = {
            ae.scene_id: ae.normalized_energy
            for ae in audio_data.scene_energies
        }

    # Scene activity requires the actual video file; normalise video-wide.
    raw_activities: dict[str, float] = {}
    raw_qualities: dict[str, float] = {}
    if file_path is not None:
        raw_activities = compute_scene_activities(scene_list, file_path, config)
        raw_qualities = compute_scene_qualities(scene_list, file_path, config)
    activity_scores = _normalize_values(
        raw_activities, [s.scene_id for s in scene_list.scenes]
    )
    quality_scores = _normalize_values(
        raw_qualities, [s.scene_id for s in scene_list.scenes]
    )

    # Compute all five factor scores and a raw weighted composite per scene.
    scored: list[ScoredScene] = []
    for scene in scene_list.scenes:
        kw = score_keyword(scene.start_time, scene.end_time, transcript, keywords)
        ae = audio_by_scene.get(scene.scene_id, 0.0)
        fp = face_by_scene[scene.scene_id].face_visible_ratio if scene.scene_id in face_by_scene else 0.0
        sa = activity_scores.get(scene.scene_id, 0.0)
        sd = _sentence_density_score(
            scene.start_time, scene.end_time, scene.duration, transcript
        )
        iq = quality_scores.get(scene.scene_id, 0.0)
        composite = _weighted_composite(kw, ae, fp, sa, sd, iq, weights)
        scored.append(
            ScoredScene(
                scene_id=scene.scene_id,
                video_id=video_id,
                start_time=scene.start_time,
                end_time=scene.end_time,
                duration=scene.duration,
                keyword_score=kw,
                audio_energy_score=ae,
                face_presence_score=fp,
                scene_activity_score=sa,
                sentence_density_score=sd,
                image_quality_score=iq,
                composite_score=composite,
                rank=0,  # Placeholder — assigned after sorting.
            )
        )

    # Min-max normalise composite scores across the whole video.
    scored = _normalize_composite_scores(scored)

    # Detect degenerate case: all scores identical → temporal fallback.
    unique_scores = {s.composite_score for s in scored}
    if len(unique_scores) <= 1 and len(scored) > 1:
        logger.warning(
            "All scenes have identical composite scores; applying temporal fallback",
            extra={"video_id": video_id, "stage": "scoring", "scene_count": len(scored)},
        )
        scored = _temporal_fallback(scored)

    # Deterministic sort: composite DESC, start_time ASC as tiebreaker.
    ranked = sorted(scored, key=lambda s: (-s.composite_score, s.start_time))

    # Assign rank (1-based) by sort position.
    ranked = [
        ScoredScene(
            scene_id=s.scene_id,
            video_id=s.video_id,
            start_time=s.start_time,
            end_time=s.end_time,
            duration=s.duration,
            keyword_score=s.keyword_score,
            audio_energy_score=s.audio_energy_score,
            face_presence_score=s.face_presence_score,
            scene_activity_score=s.scene_activity_score,
            sentence_density_score=s.sentence_density_score,
            image_quality_score=s.image_quality_score,
            composite_score=s.composite_score,
            rank=i + 1,
        )
        for i, s in enumerate(ranked)
    ]

    composites = [s.composite_score for s in ranked]
    logger.info(
        "Scoring complete",
        extra={
            "video_id": video_id,
            "stage": "scoring",
            "status": "success",
            "scene_count": len(ranked),
            "min_score": min(composites),
            "max_score": max(composites),
            "avg_score": sum(composites) / len(composites),
        },
    )
    return ScoredSceneList(
        video_id=video_id,
        scenes=tuple(ranked),
        min_score=min(composites),
        max_score=max(composites),
        avg_score=sum(composites) / len(composites),
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_weights(config: dict) -> dict[str, float]:
    """Load factor weights from config, falling back to documented defaults."""
    w: dict = config.get("scoring", {}).get("weights", {})
    return {
        "keyword": float(w.get("keyword", 3)),
        "audio_energy": float(w.get("audio_energy", 2)),
        "face_presence": float(w.get("face_presence", 2)),
        "scene_activity": float(w.get("scene_activity", 1)),
        "sentence_density": float(w.get("sentence_density", 1)),
        "image_quality": float(w.get("image_quality", 2)),
    }


def _weighted_composite(
    keyword: float,
    audio_energy: float,
    face_presence: float,
    scene_activity: float,
    sentence_density: float,
    image_quality: float,
    weights: dict[str, float],
) -> float:
    """Return weighted average of all factor scores."""
    total_weight = sum(weights.values())
    if total_weight == 0.0:
        return 0.0
    return (
        keyword * weights["keyword"]
        + audio_energy * weights["audio_energy"]
        + face_presence * weights["face_presence"]
        + scene_activity * weights["scene_activity"]
        + sentence_density * weights["sentence_density"]
        + image_quality * weights["image_quality"]
    ) / total_weight


def _normalize_values(
    raw_values: dict[str, float],
    scene_ids: list[str],
) -> dict[str, float]:
    """Min-max normalise raw values for the given scene IDs to [0, 1].

    Scenes absent from raw_values receive 0.0.
    When all values are identical the normalised value is 0.0.
    """
    if not raw_values:
        return {sid: 0.0 for sid in scene_ids}

    vmin = min(raw_values.values())
    vmax = max(raw_values.values())
    span = vmax - vmin

    result: dict[str, float] = {}
    for sid in scene_ids:
        raw = raw_values.get(sid, 0.0)
        result[sid] = (raw - vmin) / span if span > 0.0 else 0.0
    return result


def _normalize_composite_scores(scenes: list[ScoredScene]) -> list[ScoredScene]:
    """Return new ScoredScene list with composite_score min-max normalised."""
    if not scenes:
        return scenes

    scores = [s.composite_score for s in scenes]
    vmin = min(scores)
    vmax = max(scores)
    span = vmax - vmin

    if span == 0.0:
        return scenes  # Degenerate case handled by caller.

    return [
        ScoredScene(
            scene_id=s.scene_id,
            video_id=s.video_id,
            start_time=s.start_time,
            end_time=s.end_time,
            duration=s.duration,
            keyword_score=s.keyword_score,
            audio_energy_score=s.audio_energy_score,
            face_presence_score=s.face_presence_score,
            scene_activity_score=s.scene_activity_score,
            sentence_density_score=s.sentence_density_score,
            image_quality_score=s.image_quality_score,
            composite_score=(s.composite_score - vmin) / span,
            rank=s.rank,
        )
        for s in scenes
    ]


def _sentence_density_score(
    start_ms: int,
    end_ms: int,
    duration_s: float,
    transcript: Transcript,
) -> float:
    """Score based on words-per-second in the scene window.

    - 2.0–4.0 wps  → 1.0  (optimal engagement rate)
    - 0.0–2.0 wps  → linear 0.0 → 1.0
    - 4.0–8.0 wps  → linear 1.0 → 0.0
    - >8.0 wps     → 0.0
    """
    if duration_s <= 0.0:
        return 0.0

    word_count = sum(
        1
        for segment in transcript.segments
        for word in segment.words
        if word.start_time >= start_ms and word.end_time <= end_ms
    )

    wps = word_count / duration_s

    if 2.0 <= wps <= 4.0:
        return 1.0
    elif wps < 2.0:
        return wps / 2.0
    else:
        return max(0.0, 1.0 - (wps - 4.0) / 4.0)


def _temporal_fallback(scenes: list[ScoredScene]) -> list[ScoredScene]:
    """Assign descending scores by temporal position when all scores tie.

    Scenes are ordered by start_time ASC and assigned scores evenly spaced
    in (0.0, 1.0] so that earlier scenes receive higher priority.  This is
    deterministic and spreads clip selection across the video.
    """
    n = len(scenes)
    if n == 0:
        return scenes

    sorted_scenes = sorted(scenes, key=lambda s: s.start_time)
    result: list[ScoredScene] = []
    for i, scene in enumerate(sorted_scenes):
        score = 1.0 - (i / n)
        result.append(
            ScoredScene(
                scene_id=scene.scene_id,
                video_id=scene.video_id,
                start_time=scene.start_time,
                end_time=scene.end_time,
                duration=scene.duration,
                keyword_score=scene.keyword_score,
                audio_energy_score=scene.audio_energy_score,
                face_presence_score=scene.face_presence_score,
                scene_activity_score=scene.scene_activity_score,
                sentence_density_score=scene.sentence_density_score,
                image_quality_score=scene.image_quality_score,
                composite_score=score,
                rank=scene.rank,
            )
        )
    return result
