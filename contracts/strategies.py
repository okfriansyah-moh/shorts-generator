"""Strategy result DTOs for Shorts Factory.

Produced by modules/strategies/. Consumed by the compositor module.
These DTOs carry pre-computed decisions (crop plans) into the compositor
so that the compositor remains a pure executor — no decision logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class PodcastFramePlan:
    """Frozen DTO representing a podcast crop plan produced by the podcast strategy.

    Generated once per clip by modules/strategies/podcast_strategy.py using
    transcript-aligned speaker detection. Applied by the compositor without
    re-evaluation.

    Fields:
        crop_x: Crop origin x in pixels. >= 0.
        crop_y: Crop origin y in pixels. >= 0.
        crop_width: Crop width in pixels. > 0, <= source width.
        crop_height: Crop height in pixels. > 0, <= source height.
        speaker_face_id: Cluster ID of the selected speaker face (e.g. 'face_0').
            None if no face was detected (center_crop fallback).
        layout: Strategy path taken.
            'speaker_crop'      — transcript-aligned primary speaker selected.
            'center_face_crop'  — no transcript; largest face used.
            'center_crop'       — no face detected; simple center crop.
    """

    crop_x: int
    crop_y: int
    crop_width: int
    crop_height: int
    speaker_face_id: Optional[str]
    layout: str


@dataclass(frozen=True)
class SportsFramePlan:
    """Frozen DTO representing a sports crop plan produced by the sports strategy.

    Generated once per clip by modules/strategies/sports_strategy.py using
    a hybrid tracking approach (face centroid → pose → motion energy → center).
    Applied by the compositor without re-evaluation.

    Fields:
        layout: Compositor layout to apply.
            'sports_action_crop' — crop window anchored on tracked action.
            'sports_center_crop' — fixed center crop (9:16 from 16:9 source).
            'sports_letterbox'   — full 16:9 frame, black bars top/bottom.
        sport: Sub-type identifier (e.g. 'tennis', 'football').
        tracking_method: Which hybrid method succeeded.
            'face_centroid'  — anchored on detected athlete faces.
            'pose'           — anchored on MediaPipe Pose body centroid.
            'motion_energy'  — anchored on highest-motion region (frame diff).
            'center'         — emergency fallback; no tracking data available.
        crop_x: Crop origin x in source pixels. >= 0.
        crop_y: Crop origin y in source pixels. >= 0.
        crop_width: Crop width in source pixels. > 0, <= source width.
        crop_height: Crop height in source pixels. > 0, <= source height.
    """

    layout: str
    sport: str
    tracking_method: str
    crop_x: int
    crop_y: int
    crop_width: int
    crop_height: int
