"""Quality metrics computation for Shorts Factory analytics.

Computes score distribution histograms and face visibility statistics
from a ScoredSceneList. All computation is deterministic and read-only.
"""

from __future__ import annotations

import logging

from contracts.analytics import QualityMetrics, ScoreBin
from contracts.scoring import ScoredSceneList

logger = logging.getLogger(__name__)

# Number of histogram bins covering [0.0, 1.0]
_NUM_BINS = 10
_BIN_WIDTH = 1.0 / _NUM_BINS


def _build_score_bins(values: list[float]) -> tuple[ScoreBin, ...]:
    """Build a 10-bin histogram over [0.0, 1.0] for the given values.

    The last bin [0.9, 1.0] is inclusive on both ends to capture score == 1.0.
    Returns bins sorted by bin_start ASC.
    """
    counts = [0] * _NUM_BINS
    for v in values:
        # Clamp to valid range and compute bin index
        v_clamped = max(0.0, min(1.0, v))
        idx = min(int(v_clamped / _BIN_WIDTH), _NUM_BINS - 1)
        counts[idx] += 1

    bins = tuple(
        ScoreBin(
            bin_start=round(i * _BIN_WIDTH, 1),
            bin_end=round((i + 1) * _BIN_WIDTH, 1),
            count=counts[i],
        )
        for i in range(_NUM_BINS)
    )
    return bins


def compute(
    scored_scenes: ScoredSceneList,
    config: dict,
) -> QualityMetrics:
    """Compute quality metrics from scored scenes.

    Args:
        scored_scenes: Full ScoredSceneList for the video.
        config: Pipeline configuration dict. Reads ``scoring.min_composite_score``.

    Returns:
        QualityMetrics DTO with score and face visibility distributions.
    """
    min_score: float = float(
        config.get("scoring", {}).get("min_composite_score", 0.2)
    )
    scenes = sorted(scored_scenes.scenes, key=lambda s: s.start_time)
    total = len(scenes)

    if total == 0:
        empty_bins = _build_score_bins([])
        logger.warning(
            "quality_metrics: no scored scenes for video_id=%s",
            scored_scenes.video_id,
        )
        return QualityMetrics(
            video_id=scored_scenes.video_id,
            total_scenes_scored=0,
            avg_composite_score=0.0,
            score_distribution=empty_bins,
            face_visibility_distribution=empty_bins,
            avg_face_visibility=0.0,
            rejection_count=0,
            rejection_rate=0.0,
        )

    composite_scores = [s.composite_score for s in scenes]
    face_scores = [s.face_presence_score for s in scenes]

    avg_composite = sum(composite_scores) / total
    avg_face = sum(face_scores) / total
    rejection_count = sum(1 for s in composite_scores if s < min_score)
    rejection_rate = rejection_count / total

    score_dist = _build_score_bins(composite_scores)
    face_dist = _build_score_bins(face_scores)

    logger.info(
        "quality_metrics: video_id=%s total_scenes=%d avg_score=%.3f "
        "avg_face=%.3f rejection_rate=%.3f",
        scored_scenes.video_id,
        total,
        avg_composite,
        avg_face,
        rejection_rate,
    )

    return QualityMetrics(
        video_id=scored_scenes.video_id,
        total_scenes_scored=total,
        avg_composite_score=round(avg_composite, 4),
        score_distribution=score_dist,
        face_visibility_distribution=face_dist,
        avg_face_visibility=round(avg_face, 4),
        rejection_count=rejection_count,
        rejection_rate=round(rejection_rate, 4),
    )
