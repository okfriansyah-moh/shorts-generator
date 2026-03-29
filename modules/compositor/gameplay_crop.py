"""Gameplay region crop filter builder.

Produces an FFmpeg filtergraph fragment that crops the source
to the target aspect ratio (excluding any face cam PiP region) and
scales to target dimensions.
"""

from __future__ import annotations

from contracts.face import FaceBBox


def build_gameplay_crop_filter(
    input_label: str,
    output_label: str,
    target_width: int,
    target_height: int,
    bbox: "FaceBBox | None" = None,
    src_width: int = 0,
    src_height: int = 0,
) -> str:
    """Build FFmpeg filter fragment for cropping gameplay to target aspect.

    When a face bounding box is provided, the crop avoids the face cam
    PiP region so the face does not appear duplicated in the gameplay
    portion of the split layout.

    Strategy: crop from the side of the frame farthest from the face cam,
    ensuring the face cam overlay is excluded from the gameplay view.

    Args:
        input_label: FFmpeg filter input stream label (e.g. '[gp_in]').
        output_label: FFmpeg filter output stream label (e.g. '[gameplay]').
        target_width: Output width in pixels (1080).
        target_height: Output height in pixels (1248).
        bbox: Optional face bounding box (normalized 0-1). When provided,
              the crop avoids this region.
        src_width: Source video width in pixels (needed when bbox provided).
        src_height: Source video height in pixels (needed when bbox provided).

    Returns:
        FFmpeg filtergraph fragment string (no trailing semicolon).
    """
    if bbox is not None and src_width > 0 and src_height > 0:
        return _build_face_aware_crop(
            input_label, output_label,
            target_width, target_height,
            bbox, src_width, src_height,
        )

    # No face info — center crop to target aspect then scale to exact dims
    target_aspect = target_width / target_height  # 1080/1248 ≈ 0.865
    return (
        f"{input_label}"
        f"crop=ih*{target_aspect:.4f}:ih,"
        f"scale={target_width}:{target_height}:force_original_aspect_ratio=disable"
        f"{output_label}"
    )


def _build_face_aware_crop(
    input_label: str,
    output_label: str,
    target_width: int,
    target_height: int,
    bbox: FaceBBox,
    src_width: int,
    src_height: int,
) -> str:
    """Build crop filter that avoids the face cam PiP overlay region.

    Determines the largest rectangular area of the source frame that:
      1. Does NOT overlap the face cam PiP bounding box
      2. Maintains the target aspect ratio (target_width / target_height)
      3. Is as large as possible (to preserve gameplay detail)

    The approach: figure out which edge the face cam is nearest, then
    crop from the opposite side.
    """
    target_aspect = target_width / target_height  # 1080/1248 ≈ 0.865

    # Face cam center in normalized coords
    face_cx = bbox.x + bbox.width / 2
    face_cy = bbox.y + bbox.height / 2

    # Calculate available regions (above, below, left, right of face cam)
    face_top_px = int(bbox.y * src_height)
    face_bottom_px = int((bbox.y + bbox.height) * src_height)
    face_left_px = int(bbox.x * src_width)
    face_right_px = int((bbox.x + bbox.width) * src_width)

    # Available height above and below face cam
    space_above = face_top_px
    space_below = src_height - face_bottom_px

    # If face cam is on the left or right side AND covers significant vertical
    # space, use a horizontal strategy instead
    face_covers_vertical = bbox.height > 0.4
    face_is_narrow = bbox.width < 0.4

    if face_is_narrow and face_covers_vertical:
        # Face cam covers a tall strip on one side — crop from opposite side
        crop_h = src_height
        crop_y = 0
        crop_w = int(crop_h * target_aspect)

        if face_cx < 0.5:
            # Face cam on left — crop from right side
            crop_x = max(face_right_px, src_width - crop_w)
            crop_x = min(crop_x, src_width - crop_w)
        else:
            # Face cam on right — crop from left side
            crop_x = min(face_left_px - crop_w, 0)
            crop_x = max(crop_x, 0)

        crop_w = min(crop_w, src_width - crop_x)
        return (
            f"{input_label}"
            f"crop={crop_w}:{crop_h}:{crop_x}:{crop_y},"
            f"scale={target_width}:{target_height}:force_original_aspect_ratio=disable"
            f"{output_label}"
        )

    # Vertical strategy: crop a horizontal strip that excludes the face cam
    # If face is in bottom half, crop from top; if top half, crop from bottom
    if face_cy >= 0.5:
        # Face cam in bottom half — crop from top, exclude bottom
        crop_h = max(space_above, int(src_height * 0.6))
        crop_h = min(crop_h, face_top_px) if face_top_px > 0 else src_height
        crop_y = 0
    else:
        # Face cam in top half — crop from bottom, exclude top
        crop_h = max(space_below, int(src_height * 0.6))
        crop_h = min(crop_h, space_below) if space_below > 0 else src_height
        crop_y = src_height - crop_h

    # Use target aspect ratio: crop_w = crop_h * (target_width / target_height)
    crop_w = int(crop_h * target_aspect)
    crop_w = min(crop_w, src_width)

    # Center horizontally
    crop_x = max(0, (src_width - crop_w) // 2)

    return (
        f"{input_label}"
        f"crop={crop_w}:{crop_h}:{crop_x}:{crop_y},"
        f"scale={target_width}:{target_height}:force_original_aspect_ratio=disable"
        f"{output_label}"
    )
