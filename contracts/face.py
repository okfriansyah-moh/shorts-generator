"""Face detection DTOs for Shorts Factory.

Produced by the face_detection module. Consumed by scoring,
compositor, and thumbnail modules.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class FaceBBox:
    """Frozen DTO representing a single face bounding box in normalized coordinates.

    Fields:
        x: Left edge, normalized. 0.0 <= x <= 1.0.
        y: Top edge, normalized. 0.0 <= y <= 1.0.
        width: Box width, normalized. 0.0 < width <= 1.0. x + width <= 1.0.
        height: Box height, normalized. 0.0 < height <= 1.0. y + height <= 1.0.
        confidence: Detection confidence. 0.0 <= confidence <= 1.0.
        timestamp_ms: Frame timestamp in milliseconds. >= 0.
    """

    x: float
    y: float
    width: float
    height: float
    confidence: float
    timestamp_ms: int


@dataclass(frozen=True)
class SceneFaceData:
    """Frozen DTO representing face detection results for a single scene.

    Fields:
        scene_id: Reference to parent scene. Matches SceneSegment.scene_id.
        face_visible_ratio: Fraction of sampled frames with face detected. 0.0–1.0.
        bounding_boxes: Per-frame bounding boxes, sorted by timestamp_ms ASC.
        average_bbox: EMA-smoothed average bounding box. None if no faces detected.
        sample_count: Number of frames sampled. > 0. Equals ceil(duration_s * 2).
    """

    scene_id: str
    face_visible_ratio: float
    bounding_boxes: tuple[FaceBBox, ...]
    average_bbox: Optional[FaceBBox]
    sample_count: int


@dataclass(frozen=True)
class FaceDetectionResult:
    """Frozen DTO representing face detection results for the entire video.

    Fields:
        video_id: Parent video reference. 16 lowercase hex chars.
        scene_data: Per-scene face detection. One entry per scene, same order as SceneList.
        average_visibility: Mean face_visible_ratio across all scenes. 0.0–1.0.
        faceless_scene_count: Number of scenes with face_visible_ratio == 0.0. >= 0.
    """

    video_id: str
    scene_data: tuple[SceneFaceData, ...]
    average_visibility: float
    faceless_scene_count: int
