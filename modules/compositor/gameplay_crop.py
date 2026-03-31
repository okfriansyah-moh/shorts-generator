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

    Strategy: identify where the PiP overlay is and crop the largest
    gameplay rectangle from the opposite region of the frame.

    Args:
        input_label: FFmpeg filter input stream label (e.g. '[gp_in]').
        output_label: FFmpeg filter output stream label (e.g. '[gameplay]').
        target_width: Output width in pixels (1080).
        target_height: Output height in pixels (1248).
        bbox: Optional PiP bounding box (normalized 0-1). When provided,
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

    The approach:
      - Calculate the four candidate rectangles (above/below/left/right of PiP)
      - Pick the one with the largest area
      - Crop to target aspect within that rectangle

    Special case: when the PiP is in a left-side zone (middle_left,
    bottom_left, upper_middle_left) the candidate algorithm tends to
    pick a region that still contains the PiP or drifts off-center.
    For these regions we use a strict center crop instead, which
    captures the middle gameplay area perfectly.  The already-perfect
    bottom_middle path is explicitly preserved and never enters this
    fast path.
    """
    target_aspect = target_width / target_height  # 1080/1248 ≈ 0.865

    # --- Left-side PiP fast path: strict center gameplay crop -----------
    # PiP center in normalized coordinates
    pip_cx = bbox.x + bbox.width / 2
    pip_cy = bbox.y + bbox.height / 2
    _is_left_side = pip_cx <= 0.33
    _is_bottom_middle = 0.33 <= pip_cx <= 0.67 and pip_cy >= 0.55

    if _is_left_side and not _is_bottom_middle:
        # Force a center crop of the full source frame.  This avoids
        # the PiP (which sits in the left margin) and grabs the middle
        # gameplay area that the viewer expects.
        return (
            f"{input_label}"
            f"crop=ih*{target_aspect:.4f}:ih:(iw-ih*{target_aspect:.4f})/2:0,"
            f"scale={target_width}:{target_height}:force_original_aspect_ratio=disable"
            f"{output_label}"
        )
    # --- End left-side fast path ----------------------------------------

    # PiP pixel boundaries (with small safety margin)
    margin = 4  # pixels
    pip_left = max(0, int(bbox.x * src_width) - margin)
    pip_right = min(src_width, int((bbox.x + bbox.width) * src_width) + margin)
    pip_top = max(0, int(bbox.y * src_height) - margin)
    pip_bottom = min(src_height, int((bbox.y + bbox.height) * src_height) + margin)

    # Four candidate regions (avoid PiP)
    candidates = []

    # Region above PiP
    if pip_top > 0:
        h = pip_top
        w = src_width
        candidates.append((0, 0, w, h, "above"))

    # Region below PiP
    if pip_bottom < src_height:
        h = src_height - pip_bottom
        w = src_width
        candidates.append((0, pip_bottom, w, h, "below"))

    # Region left of PiP
    if pip_left > 0:
        w = pip_left
        h = src_height
        candidates.append((0, 0, w, h, "left"))

    # Region right of PiP
    if pip_right < src_width:
        w = src_width - pip_right
        h = src_height
        candidates.append((pip_right, 0, w, h, "right"))

    # Also consider the full frame minus a horizontal strip (for corner PiPs)
    # If PiP is in a corner, we can use the full width and crop only the PiP's row
    # (pip_cx, pip_cy already computed above)

    if pip_cy >= 0.5:
        # PiP in bottom half — use top portion of full width
        h = pip_top if pip_top > src_height * 0.3 else int(src_height * 0.65)
        h = min(h, pip_top) if pip_top > 0 else int(src_height * 0.65)
        candidates.append((0, 0, src_width, h, "top_strip"))
    else:
        # PiP in top half — use bottom portion of full width
        start_y = pip_bottom if pip_bottom < src_height * 0.7 else int(src_height * 0.35)
        start_y = max(start_y, pip_bottom) if pip_bottom < src_height else int(src_height * 0.35)
        h = src_height - start_y
        candidates.append((0, start_y, src_width, h, "bottom_strip"))

    # Score candidates by usable area after aspect-ratio fitting
    best_area = 0
    best_crop = (0, 0, src_width, src_height)

    for cx, cy, cw, ch, _label in candidates:
        if cw <= 0 or ch <= 0:
            continue

        # Fit target aspect inside this candidate region
        region_aspect = cw / ch
        if region_aspect > target_aspect:
            # Region is wider — constrain by height
            fit_h = ch
            fit_w = int(ch * target_aspect)
        else:
            # Region is taller — constrain by width
            fit_w = cw
            fit_h = int(cw / target_aspect)

        fit_w = min(fit_w, cw)
        fit_h = min(fit_h, ch)

        area = fit_w * fit_h
        if area > best_area:
            best_area = area
            # Center the fitted rect within the candidate region
            fit_x = cx + max(0, (cw - fit_w) // 2)
            fit_y = cy + max(0, (ch - fit_h) // 2)
            best_crop = (fit_x, fit_y, fit_w, fit_h)

    crop_x, crop_y, crop_w, crop_h = best_crop

    # Final safety clamp
    crop_x = max(0, min(crop_x, src_width - crop_w))
    crop_y = max(0, min(crop_y, src_height - crop_h))
    crop_w = min(crop_w, src_width - crop_x)
    crop_h = min(crop_h, src_height - crop_y)

    return (
        f"{input_label}"
        f"crop={crop_w}:{crop_h}:{crop_x}:{crop_y},"
        f"scale={target_width}:{target_height}:force_original_aspect_ratio=disable"
        f"{output_label}"
    )
