"""Podcast speaker detection strategy for Shorts Factory.

Implements transcript-aligned, deterministic speaker detection for podcast
videos. Maps transcript activity to detected face positions to identify the
primary speaker, then generates a stable crop plan for the compositor.

Algorithm overview:
  1. Divide clip into 1-second time buckets
  2. Score each bucket by text activity (character count from transcript)
  3. Cluster face bboxes by center position using a greedy spatial algorithm
  4. Score each face cluster's frame presence in each bucket
  5. Normalize text scores and combine with presence scores (weighted)
  6. Select primary speaker as cluster with highest cumulative weighted score
  7. Compute stable crop plan from primary speaker's median bbox (1.4× expanded)
  8. Convert to 9:16 aspect ratio crop rect (full-height, centered on speaker)

Fallbacks (all deterministic):
  - No transcript: use face cluster with largest median bbox area
  - No face: use center crop of 9:16 from source
  - Both missing: center crop

Entry point:
    generate_plan(clip, transcript, face_result, ingestion_result, config)
        -> PodcastFramePlan
"""

from __future__ import annotations

import logging
from typing import Optional

from contracts.clip import ClipDefinition
from contracts.face import FaceBBox, FaceDetectionResult
from contracts.ingestion import IngestionResult
from contracts.strategies import PodcastFramePlan
from contracts.transcript import Transcript

logger = logging.getLogger(__name__)

# ── Algorithm constants (all overridable via config.podcast_strategy) ──────────

# Time window for bucketing transcript + face data
_DEFAULT_WINDOW_SECONDS: float = 1.0
# Weight applied to normalised text score when combining with face presence
_DEFAULT_TEXT_WEIGHT: float = 0.7
# Weight applied to face presence alone (no speech in bucket)
_DEFAULT_FACE_WEIGHT: float = 0.3
# Max normalised-coordinate distance within which two face centres are merged
_DEFAULT_CLUSTER_THRESHOLD: float = 0.20
# Expansion factor applied to the base 9:16 crop width around the speaker
_DEFAULT_BBOX_EXPAND_SCALE: float = 1.4
# Aspect ratio target: 9 wide × 16 tall
_VERTICAL_ASPECT: float = 9.0 / 16.0


# ── Helpers ───────────────────────────────────────────────────────────────────


def _build_time_buckets(
    start_ms: int,
    end_ms: int,
    window_ms: int,
) -> list[tuple[int, int]]:
    """Divide [start_ms, end_ms) into fixed-width windows.

    Returns a list of (bucket_start_ms, bucket_end_ms) pairs.
    The final window may be shorter than window_ms.
    """
    buckets: list[tuple[int, int]] = []
    t = start_ms
    while t < end_ms:
        bucket_end = min(t + window_ms, end_ms)
        buckets.append((t, bucket_end))
        t += window_ms
    return buckets


def _text_activity_per_bucket(
    transcript: Transcript,
    buckets: list[tuple[int, int]],
) -> list[int]:
    """Compute character-count text activity for each time bucket.

    Each transcript segment is fractionally distributed across overlapping
    buckets proportional to the overlap duration. Returns a list of int
    character counts aligned with `buckets`.
    """
    scores: list[int] = [0] * len(buckets)
    for seg in transcript.segments:
        seg_duration = seg.end_time - seg.start_time
        if seg_duration <= 0 or not seg.text:
            continue
        text_len = len(seg.text)
        for bi, (b_start, b_end) in enumerate(buckets):
            overlap_start = max(seg.start_time, b_start)
            overlap_end = min(seg.end_time, b_end)
            if overlap_start >= overlap_end:
                continue
            overlap_fraction = (overlap_end - overlap_start) / seg_duration
            scores[bi] += int(round(text_len * overlap_fraction))
    return scores


def _collect_clip_bboxes(
    clip: ClipDefinition,
    face_result: FaceDetectionResult,
) -> list[FaceBBox]:
    """Return all per-frame bboxes whose scene is part of the clip.

    Sorted by (timestamp_ms, x, y) for deterministic processing order.
    """
    clip_scene_ids = {s.scene_id for s in clip.scenes}
    bboxes: list[FaceBBox] = []
    for sfd in face_result.scene_data:
        if sfd.scene_id in clip_scene_ids:
            bboxes.extend(sfd.bounding_boxes)
    return sorted(bboxes, key=lambda b: (b.timestamp_ms, b.x, b.y))


def _cluster_faces(
    bboxes: list[FaceBBox],
    threshold: float,
) -> dict[str, list[FaceBBox]]:
    """Cluster face bboxes into stable spatial identities.

    Algorithm (deterministic greedy):
      1. Sort bboxes by (center_x, center_y) for reproducible ordering.
      2. Assign each bbox to the first existing cluster whose mean centre is
         within `threshold` normalised distance. Create a new cluster if none
         qualifies.
      3. Number clusters by ascending mean centre X (left → right), then Y.
         This gives IDs: face_0, face_1, … in a stable spatial ordering.

    Args:
        bboxes:    All detected face bboxes for the clip (may include multiple
                   faces per frame at the same timestamp_ms).
        threshold: Maximum normalised Euclidean distance to merge centres.

    Returns:
        Mapping {face_id: [FaceBBox, …]}. Empty dict when bboxes is empty.
    """
    if not bboxes:
        return {}

    # Sort by centre coords for determinism independent of input ordering
    sorted_bboxes = sorted(
        bboxes,
        key=lambda b: (b.x + b.width * 0.5, b.y + b.height * 0.5),
    )

    # clusters: list of (running_mean_cx, running_mean_cy, [bboxes])
    clusters: list[tuple[float, float, list[FaceBBox]]] = []

    for bbox in sorted_bboxes:
        cx = bbox.x + bbox.width * 0.5
        cy = bbox.y + bbox.height * 0.5
        assigned = False
        for i, (mcx, mcy, bblist) in enumerate(clusters):
            dist = ((cx - mcx) ** 2 + (cy - mcy) ** 2) ** 0.5
            if dist <= threshold:
                bblist.append(bbox)
                n = len(bblist)
                clusters[i] = (
                    (mcx * (n - 1) + cx) / n,
                    (mcy * (n - 1) + cy) / n,
                    bblist,
                )
                assigned = True
                break
        if not assigned:
            clusters.append((cx, cy, [bbox]))

    # Assign IDs sorted by mean centre X then Y (left-to-right spatial order)
    clusters.sort(key=lambda c: (c[0], c[1]))
    return {f"face_{i}": c[2] for i, c in enumerate(clusters)}


def _face_presence_per_bucket(
    clusters: dict[str, list[FaceBBox]],
    buckets: list[tuple[int, int]],
) -> dict[str, list[int]]:
    """Count frames per face cluster per time bucket.

    Returns {face_id: [frame_count_per_bucket]}.
    """
    presence: dict[str, list[int]] = {
        fid: [0] * len(buckets) for fid in clusters
    }
    for face_id, bboxes in clusters.items():
        for bbox in bboxes:
            ts = bbox.timestamp_ms
            for bi, (b_start, b_end) in enumerate(buckets):
                if b_start <= ts < b_end:
                    presence[face_id][bi] += 1
                    break
    return presence


def _select_primary_speaker(
    text_scores: list[int],
    face_presence: dict[str, list[int]],
    text_weight: float,
    face_weight: float,
) -> Optional[str]:
    """Select the face cluster that matches the speaking activity best.

    Scoring per bucket for each face:
        score = frames * face_weight
              + frames * norm_text_score * text_weight

    Where norm_text_score is normalised to [0, 1] across all buckets.
    This ensures speech windows are upweighted without a scale dependency
    on character counts.

    Primary speaker = face with highest total score.
    Ties are broken deterministically by face index (lower index wins, i.e.
    left-most speaker on screen).

    Returns None when face_presence is empty.
    """
    if not face_presence:
        return None

    # Normalise text scores to [0, 1]
    max_text = max(text_scores) if text_scores else 0
    if max_text > 0:
        norm_text = [t / max_text for t in text_scores]
    else:
        norm_text = [0.0] * len(text_scores)

    total_scores: dict[str, float] = {}
    for face_id, frame_counts in face_presence.items():
        total = 0.0
        for bi, count in enumerate(frame_counts):
            nt = norm_text[bi] if bi < len(norm_text) else 0.0
            total += count * face_weight + count * nt * text_weight
        total_scores[face_id] = total

    # Deterministic tiebreak: prefer lower face index (e.g. face_0 < face_1)
    return max(
        total_scores,
        key=lambda fid: (total_scores[fid], -int(fid.split("_")[1])),
    )


def _median_bbox(bboxes: list[FaceBBox]) -> FaceBBox:
    """Return a synthetic FaceBBox with median coordinates.

    Median is computed independently per coordinate using sorted lists.
    Even-length lists use the lower-middle element to remain deterministic.
    """
    n = len(bboxes)
    mid = (n - 1) // 2  # lower-middle for even n

    xs = sorted(b.x for b in bboxes)
    ys = sorted(b.y for b in bboxes)
    ws = sorted(b.width for b in bboxes)
    hs = sorted(b.height for b in bboxes)

    return FaceBBox(
        x=xs[mid],
        y=ys[mid],
        width=ws[mid],
        height=hs[mid],
        confidence=0.0,   # synthetic — not a raw detection
        timestamp_ms=0,
    )


def _compute_crop_rect(
    median_face: FaceBBox,
    src_width: int,
    src_height: int,
    expand_scale: float,
) -> tuple[int, int, int, int]:
    """Compute (crop_x, crop_y, crop_width, crop_height) centered on speaker.

    Steps:
      1. Base crop: full source height × 9:16 width.
      2. Expand width by expand_scale for framing context.
      3. Clamp expanded width to source width (adjusting height if needed).
      4. Centre crop_x on the face's horizontal midpoint.
      5. Clamp crop_x to [0, src_width - crop_w].

    All arithmetic uses integer rounding. Same inputs always produce
    the same output.
    """
    crop_h = src_height
    base_w = int(round(src_height * _VERTICAL_ASPECT))
    crop_w = int(round(base_w * expand_scale))

    if crop_w > src_width:
        crop_w = src_width
        crop_h = int(round(src_width / (_VERTICAL_ASPECT * expand_scale)))
        crop_h = min(crop_h, src_height)

    face_cx_px = (median_face.x + median_face.width * 0.5) * src_width
    crop_x = int(round(face_cx_px - crop_w * 0.5))
    crop_x = max(0, min(crop_x, src_width - crop_w))
    crop_y = max(0, (src_height - crop_h) // 2)

    return crop_x, crop_y, crop_w, crop_h


def _center_crop_rect(src_width: int, src_height: int) -> tuple[int, int, int, int]:
    """Simple center crop for 9:16 from the source dimensions."""
    crop_w = int(round(src_height * _VERTICAL_ASPECT))
    if crop_w > src_width:
        crop_w = src_width
    crop_x = (src_width - crop_w) // 2
    return crop_x, 0, crop_w, src_height


# ── Public entry point ────────────────────────────────────────────────────────


def generate_plan(
    clip: ClipDefinition,
    transcript: Optional[Transcript],
    face_result: FaceDetectionResult,
    ingestion_result: IngestionResult,
    config: dict,
) -> PodcastFramePlan:
    """Generate a deterministic crop plan for podcast video framing.

    Implements transcript-aligned speaker detection to identify the primary
    speaker and produce a single stable crop covering the entire clip.

    The plan is computed ONCE per clip and applied to every frame by the
    compositor with no per-frame updates (temporal stability guarantee).

    Decision path:
      1. No faces detected          → center_crop (deterministic fallback)
      2. No transcript / no speech  → center_face_crop (largest face, area rank)
      3. Transcript + faces present → speaker_crop (full algorithm)

    Args:
        clip:             ClipDefinition with scene references and timing.
        transcript:       Optional Transcript DTO (may be None or empty).
        face_result:      FaceDetectionResult for the full video.
        ingestion_result: Source video metadata (resolution).
        config:           Pipeline configuration dict.

    Returns:
        PodcastFramePlan with deterministic crop coordinates and layout label.
    """
    strategy_cfg = config.get("podcast_strategy", {})
    text_weight: float = float(strategy_cfg.get("text_weight", _DEFAULT_TEXT_WEIGHT))
    face_weight: float = float(strategy_cfg.get("face_weight", _DEFAULT_FACE_WEIGHT))
    cluster_threshold: float = float(
        strategy_cfg.get("cluster_threshold", _DEFAULT_CLUSTER_THRESHOLD)
    )
    expand_scale: float = float(
        strategy_cfg.get("bbox_expand_scale", _DEFAULT_BBOX_EXPAND_SCALE)
    )
    window_ms: int = int(
        float(strategy_cfg.get("window_seconds", _DEFAULT_WINDOW_SECONDS)) * 1000
    )

    src_width, src_height = ingestion_result.resolution

    # ── Gather face data ───────────────────────────────────────────────────
    all_bboxes = _collect_clip_bboxes(clip, face_result)
    have_faces = len(all_bboxes) > 0

    have_transcript = (
        transcript is not None
        and len(transcript.segments) > 0
        and any(seg.text.strip() for seg in transcript.segments)
    )

    logger.debug(
        "Podcast strategy inputs: have_transcript=%s, total_bboxes=%d",
        have_transcript,
        len(all_bboxes),
    )

    # ── Fallback: no faces ─────────────────────────────────────────────────
    if not have_faces:
        cx, cy, cw, ch = _center_crop_rect(src_width, src_height)
        logger.info(
            "Podcast strategy: no faces → center_crop",
            extra={
                "clip_id": clip.clip_id,
                "video_id": clip.video_id,
                "stage": "compositor",
                "status": "fallback",
            },
        )
        return PodcastFramePlan(
            crop_x=cx,
            crop_y=cy,
            crop_width=cw,
            crop_height=ch,
            speaker_face_id=None,
            layout="center_crop",
        )

    # ── Cluster face bboxes into spatial identities ────────────────────────
    clusters = _cluster_faces(all_bboxes, cluster_threshold)

    # ── Fallback: faces but no usable transcript ───────────────────────────
    if not have_transcript:
        # Select cluster with largest median bbox area (stable area+id tiebreak)
        best_face_id: Optional[str] = None
        best_area = -1.0
        for face_id in sorted(clusters.keys()):  # sorted → deterministic
            med = _median_bbox(clusters[face_id])
            area = med.width * med.height
            if area > best_area:
                best_area = area
                best_face_id = face_id

        assert best_face_id is not None  # clusters is non-empty because have_faces
        med = _median_bbox(clusters[best_face_id])
        cx, cy, cw, ch = _compute_crop_rect(med, src_width, src_height, expand_scale)
        logger.info(
            "Podcast strategy: no transcript → center_face_crop (face=%s)",
            best_face_id,
            extra={
                "clip_id": clip.clip_id,
                "video_id": clip.video_id,
                "stage": "compositor",
                "status": "fallback",
                "speaker_face_id": best_face_id,
            },
        )
        return PodcastFramePlan(
            crop_x=cx,
            crop_y=cy,
            crop_width=cw,
            crop_height=ch,
            speaker_face_id=best_face_id,
            layout="center_face_crop",
        )

    # ── Primary path: transcript-aligned speaker detection ─────────────────
    buckets = _build_time_buckets(clip.start_time, clip.end_time, window_ms)
    text_scores = _text_activity_per_bucket(transcript, buckets)
    face_presence = _face_presence_per_bucket(clusters, buckets)

    primary_face_id = _select_primary_speaker(
        text_scores, face_presence, text_weight, face_weight
    )

    if primary_face_id is None:
        # Should not happen when clusters is non-empty, but guard defensively
        cx, cy, cw, ch = _center_crop_rect(src_width, src_height)
        return PodcastFramePlan(
            crop_x=cx,
            crop_y=cy,
            crop_width=cw,
            crop_height=ch,
            speaker_face_id=None,
            layout="center_crop",
        )

    speaker_bboxes = clusters[primary_face_id]
    med_bbox = _median_bbox(speaker_bboxes)
    cx, cy, cw, ch = _compute_crop_rect(med_bbox, src_width, src_height, expand_scale)

    logger.info(
        "Podcast strategy: speaker_crop (face=%s, clusters=%d, buckets=%d)",
        primary_face_id,
        len(clusters),
        len(buckets),
        extra={
            "clip_id": clip.clip_id,
            "video_id": clip.video_id,
            "stage": "compositor",
            "status": "completed",
            "speaker_face_id": primary_face_id,
            "face_cluster_count": len(clusters),
            "bucket_count": len(buckets),
        },
    )

    return PodcastFramePlan(
        crop_x=cx,
        crop_y=cy,
        crop_width=cw,
        crop_height=ch,
        speaker_face_id=primary_face_id,
        layout="speaker_crop",
    )
