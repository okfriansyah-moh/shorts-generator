"""Face region extraction with bbox-based crop and 1.2× zoom.

Produces an FFmpeg filtergraph fragment that:
  - Crops the source around the detected (EMA-smoothed) face bounding box
  - Applies 1.2× zoom by cropping a proportionally smaller region and scaling up
  - Scales to 1080×672 (bottom 35% of 1920)
  - Clamps all crop coordinates to valid frame bounds
"""

from __future__ import annotations

from contracts.face import FaceBBox

FACE_REGION_WIDTH = 1080
FACE_REGION_HEIGHT = 672
FACE_ASPECT = FACE_REGION_WIDTH / FACE_REGION_HEIGHT  # ≈ 1.607


def compute_face_crop_params(
    bbox: FaceBBox,
    src_width: int,
    src_height: int,
    zoom: float = 1.2,
) -> tuple[int, int, int, int]:
    """Compute FFmpeg crop parameters for face region extraction with zoom.

    The crop region is sized so that, when scaled to 1080×672, the face
    appears magnified at `zoom`× relative to filling the full frame height.

    Args:
        bbox: Normalized face bounding box (EMA-smoothed).
        src_width: Source video width in pixels.
        src_height: Source video height in pixels.
        zoom: Magnification factor. > 1.0 shows less source (closer crop).

    Returns:
        (crop_w, crop_h, crop_x, crop_y) all in source pixels.
    """
    cx = (bbox.x + bbox.width / 2) * src_width
    cy = (bbox.y + bbox.height / 2) * src_height

    # For zoom×: show 1/zoom fraction of the frame height, maintaining aspect
    crop_h = src_height / zoom
    crop_w = crop_h * FACE_ASPECT

    # Constrain to source dimensions while preserving aspect ratio
    if crop_w > src_width:
        crop_w = float(src_width)
        crop_h = crop_w / FACE_ASPECT
    if crop_h > src_height:
        crop_h = float(src_height)
        crop_w = crop_h * FACE_ASPECT

    # Top-left corner, clamped so crop stays within [0, src_dim - crop_dim]
    x = max(0, min(int(cx - crop_w / 2), src_width - int(crop_w)))
    y = max(0, min(int(cy - crop_h / 2), src_height - int(crop_h)))

    return int(crop_w), int(crop_h), x, y


def build_face_crop_filter(
    input_label: str,
    output_label: str,
    bbox: FaceBBox,
    src_width: int,
    src_height: int,
    zoom: float = 1.2,
) -> str:
    """Build FFmpeg filter fragment for face region crop with zoom.

    Args:
        input_label: FFmpeg filter input stream label (e.g. '[0:v]').
        output_label: FFmpeg filter output stream label (e.g. '[face]').
        bbox: Normalized face bounding box (EMA-smoothed).
        src_width: Source video width in pixels.
        src_height: Source video height in pixels.
        zoom: Magnification factor (default 1.2).

    Returns:
        FFmpeg filtergraph fragment string (no trailing semicolon).
    """
    crop_w, crop_h, crop_x, crop_y = compute_face_crop_params(
        bbox, src_width, src_height, zoom
    )
    return (
        f"{input_label}"
        f"crop={crop_w}:{crop_h}:{crop_x}:{crop_y},"
        f"scale={FACE_REGION_WIDTH}:{FACE_REGION_HEIGHT}:force_original_aspect_ratio=disable"
        f"{output_label}"
    )
