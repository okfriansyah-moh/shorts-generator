"""Face region extraction via PiP bounding-box crop.

Produces an FFmpeg filtergraph fragment that:
  - Crops the source to the PiP overlay bounding box (the entire face cam region)
  - Scales the cropped region to 1080×672 (bottom 35% of 1920)
  - Clamps all crop coordinates to valid frame bounds

The bbox passed here must represent the *PiP overlay region*, NOT a tiny
MediaPipe face rectangle.  Call ``estimate_pip_region()`` first to expand
a raw face bbox to its enclosing PiP overlay before feeding it here.
"""

from __future__ import annotations

from contracts.face import FaceBBox

FACE_REGION_WIDTH = 1080
FACE_REGION_HEIGHT = 672
FACE_ASPECT = FACE_REGION_WIDTH / FACE_REGION_HEIGHT  # ≈ 1.607

# Heuristics for expanding a MediaPipe face bbox to the surrounding PiP overlay.
# A face typically occupies ~40% of the PiP height and ~50% of its width.
_FACE_TO_PIP_HEIGHT_RATIO = 0.40
_FACE_TO_PIP_WIDTH_RATIO = 0.50
# PiP overlays are typically 20–50% of the source frame height
_MIN_PIP_HEIGHT_RATIO = 0.18
_MAX_PIP_HEIGHT_RATIO = 0.55
_MIN_PIP_WIDTH_RATIO = 0.15
_MAX_PIP_WIDTH_RATIO = 0.55


def estimate_pip_region(
    face_bbox: FaceBBox,
    src_width: int,
    src_height: int,
) -> FaceBBox:
    """Expand a MediaPipe face bbox to its enclosing PiP overlay region.

    MediaPipe returns a tight rectangle around the face (~8-15% of frame).
    The actual PiP overlay (webcam window) is much larger, containing the
    face plus head/shoulders/background.  This function estimates the PiP
    bounds by scaling up from the face bbox.

    If the bbox already looks like a PiP region (height > 18% of frame),
    it is returned unchanged.

    Args:
        face_bbox: Normalized face bounding box from MediaPipe.
        src_width: Source video width in pixels.
        src_height: Source video height in pixels.

    Returns:
        FaceBBox representing the estimated PiP overlay region.
    """
    # If the bbox is already large enough to be a PiP region, use it as-is
    if face_bbox.height >= _MIN_PIP_HEIGHT_RATIO and face_bbox.width >= _MIN_PIP_WIDTH_RATIO:
        return face_bbox

    # Estimate PiP size from face size
    pip_h = min(face_bbox.height / _FACE_TO_PIP_HEIGHT_RATIO, _MAX_PIP_HEIGHT_RATIO)
    pip_h = max(pip_h, _MIN_PIP_HEIGHT_RATIO)
    pip_w = min(face_bbox.width / _FACE_TO_PIP_WIDTH_RATIO, _MAX_PIP_WIDTH_RATIO)
    pip_w = max(pip_w, _MIN_PIP_WIDTH_RATIO)

    # Keep aspect ratio reasonable for a PiP window (roughly 4:3 to 16:9)
    pip_aspect = pip_w / pip_h if pip_h > 0 else 1.0
    if pip_aspect > 1.8:
        pip_h = pip_w / 1.5
    elif pip_aspect < 0.6:
        pip_w = pip_h * 0.8

    # Center the PiP estimate on the face center
    face_cx = face_bbox.x + face_bbox.width / 2
    face_cy = face_bbox.y + face_bbox.height / 2

    # Snap to nearest edge/corner — PiP overlays are usually edge-aligned
    pip_x = face_cx - pip_w / 2
    pip_y = face_cy - pip_h / 2

    # Snap to edges if close
    if pip_x < 0.08:
        pip_x = 0.0
    elif pip_x + pip_w > 0.92:
        pip_x = max(0.0, 1.0 - pip_w)
    if pip_y < 0.08:
        pip_y = 0.0
    elif pip_y + pip_h > 0.92:
        pip_y = max(0.0, 1.0 - pip_h)

    # Clamp to [0, 1] range
    pip_x = max(0.0, min(pip_x, 1.0 - pip_w))
    pip_y = max(0.0, min(pip_y, 1.0 - pip_h))

    return FaceBBox(
        x=pip_x,
        y=pip_y,
        width=min(pip_w, 1.0 - pip_x),
        height=min(pip_h, 1.0 - pip_y),
        confidence=face_bbox.confidence,
        timestamp_ms=face_bbox.timestamp_ms,
    )


def compute_face_crop_params(
    bbox: FaceBBox,
    src_width: int,
    src_height: int,
    zoom: float = 1.2,
) -> tuple[int, int, int, int]:
    """Compute FFmpeg crop parameters to extract the PiP overlay region.

    Crops exactly the bbox rectangle from the source, then applies a
    small inward zoom (padding reduction) to trim PiP borders.

    The ``zoom`` parameter trims (1 - 1/zoom) off each edge — e.g.
    zoom=1.2 trims ~8% per side, zoom=1.0 crops the raw bbox.

    Args:
        bbox: Normalized PiP bounding box (NOT a raw face bbox).
        src_width: Source video width in pixels.
        src_height: Source video height in pixels.
        zoom: Trim factor applied inward on the PiP crop. >= 1.0.

    Returns:
        (crop_w, crop_h, crop_x, crop_y) all in source pixels.
    """
    # Convert normalized bbox to pixel coordinates
    raw_x = bbox.x * src_width
    raw_y = bbox.y * src_height
    raw_w = bbox.width * src_width
    raw_h = bbox.height * src_height

    # Apply inward zoom: trim (1 - 1/zoom)/2 from each side
    if zoom > 1.0:
        trim_frac = (1.0 - 1.0 / zoom) / 2.0
        inset_x = raw_w * trim_frac
        inset_y = raw_h * trim_frac
        crop_x = raw_x + inset_x
        crop_y = raw_y + inset_y
        crop_w = raw_w - 2 * inset_x
        crop_h = raw_h - 2 * inset_y
    else:
        crop_x = raw_x
        crop_y = raw_y
        crop_w = raw_w
        crop_h = raw_h

    # Enforce minimum size (at least 10% of source in each dimension)
    crop_w = max(crop_w, src_width * 0.10)
    crop_h = max(crop_h, src_height * 0.10)

    # Clamp to source bounds
    crop_x = max(0, min(int(crop_x), src_width - int(crop_w)))
    crop_y = max(0, min(int(crop_y), src_height - int(crop_h)))
    crop_w = min(int(crop_w), src_width - crop_x)
    crop_h = min(int(crop_h), src_height - crop_y)

    return crop_w, crop_h, crop_x, crop_y


def build_face_crop_filter(
    input_label: str,
    output_label: str,
    bbox: FaceBBox,
    src_width: int,
    src_height: int,
    zoom: float = 1.0,
    target_height: int = FACE_REGION_HEIGHT,
) -> str:
    """Build FFmpeg filter fragment for PiP region extraction.

    Crops the PiP region from the source and scales it to fit inside
    1080×target_height while preserving the original aspect ratio.
    Pads with a blurred version of the crop to fill remaining space
    (avoids black bars and prevents the face from looking stretched/flat).

    Args:
        input_label: FFmpeg filter input stream label (e.g. '[0:v]').
        output_label: FFmpeg filter output stream label (e.g. '[face]').
        bbox: Normalized PiP bounding box (already expanded via estimate_pip_region).
        src_width: Source video width in pixels.
        src_height: Source video height in pixels.
        zoom: Inward trim factor applied on the PiP crop (default 1.0).
        target_height: Output height in pixels (defaults to FACE_REGION_HEIGHT=672).

    Returns:
        FFmpeg filtergraph fragment string (no trailing semicolon).
    """
    crop_w, crop_h, crop_x, crop_y = compute_face_crop_params(
        bbox, src_width, src_height, zoom
    )
    tw = FACE_REGION_WIDTH
    th = target_height

    # Use split + blurred background to preserve aspect ratio.
    # 1. Crop the PiP region
    # 2. Split into background (blurred fill) and foreground (fit inside)
    # 3. Overlay foreground centered on blurred background
    return (
        f"{input_label}"
        f"crop={crop_w}:{crop_h}:{crop_x}:{crop_y},"
        f"split=2[fc_bg][fc_fg];"
        f"[fc_bg]scale={tw}:{th}:force_original_aspect_ratio=increase,"
        f"crop={tw}:{th},boxblur=15:15[fc_blur];"
        f"[fc_fg]scale={tw}:{th}:force_original_aspect_ratio=decrease,"
        f"pad={tw}:{th}:-1:-1:color=black[fc_pad];"
        f"[fc_blur][fc_pad]overlay=(W-w)/2:(H-h)/2"
        f"{output_label}"
    )
