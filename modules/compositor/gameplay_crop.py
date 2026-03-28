"""Gameplay region crop filter builder.

Produces an FFmpeg filtergraph fragment that crops the source
to 9:16 aspect ratio (excluding any face cam PiP region) and
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
    """Build FFmpeg filter fragment for cropping gameplay to 9:16.

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

    # No face info — simple center crop
    return (
        f"{input_label}"
        f"crop=ih*9/16:ih,"
        f"scale={target_width}:{target_height}"
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
      2. Maintains approximately 9:16 aspect ratio
      3. Is as large as possible (to preserve gameplay detail)

    The approach: figure out which edge the face cam is nearest, then
    crop from the opposite side.
    """
    # Face cam center in normalized coords
    face_cx = bbox.x + bbox.width / 2
    face_cy = bbox.y + bbox.height / 2

    # Determine best crop region that avoids the face cam
    # Face cam at bottom → crop from top
    # Face cam at top → crop from bottom
    # Face cam at left → crop from right
    # Face cam at right → crop from left

    # Calculate available regions (above, below, left, right of face cam)
    face_top_px = int(bbox.y * src_height)
    face_bottom_px = int((bbox.y + bbox.height) * src_height)
    face_left_px = int(bbox.x * src_width)
    face_right_px = int((bbox.x + bbox.width) * src_width)

    # Available height above and below face cam
    space_above = face_top_px
    space_below = src_height - face_bottom_px

    # For 9:16 aspect, ideal crop: width = height * 9/16
    # Try cropping from the region with most space away from face cam

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

    # If face cam is on the left or right side AND covers significant vertical
    # space, use a horizontal strategy instead
    face_covers_vertical = bbox.height > 0.4
    face_is_narrow = bbox.width < 0.4

    if face_is_narrow and face_covers_vertical:
        # Face cam covers a tall strip on one side — crop from opposite side
        crop_h = src_height
        crop_y = 0
        crop_w = int(crop_h * 9 / 16)

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
            f"scale={target_width}:{target_height}"
            f"{output_label}"
        )

    # Vertical strategy: use crop_h, compute crop_w for 9:16
    crop_w = int(crop_h * 9 / 16)
    crop_w = min(crop_w, src_width)

    # Center horizontally
    crop_x = max(0, (src_width - crop_w) // 2)

    return (
        f"{input_label}"
        f"crop={crop_w}:{crop_h}:{crop_x}:{crop_y},"
        f"scale={target_width}:{target_height}"
        f"{output_label}"
    )
