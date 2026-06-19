"""Full-gameplay fallback layout with blurred background fill.

Used when the compositor.default_layout is set to "gameplay_only"
or when all other layout options fail.

Creates a 9:16 output by scaling the source to fill the full frame
(blurred), then overlaying the properly-cropped gameplay centered
on top. This avoids black bars.
"""

from __future__ import annotations

OUTPUT_WIDTH = 1080
OUTPUT_HEIGHT = 1920


def build_fallback_filter(
    input_label: str,
    output_label: str,
    duration_seconds: float,
    fps: int = 30,
) -> str:
    """Build FFmpeg filter chain for gameplay-only with blurred background.

    Layers:
      1. Background: source scaled to fill 1080×1920, heavily blurred
      2. Foreground: source center-cropped to 9:16, scaled to fit

    Args:
        input_label: FFmpeg filter input stream label (e.g. '[0:v]').
        output_label: FFmpeg filter output stream label (e.g. '[v]').
        duration_seconds: Clip duration in seconds (unused but kept for API compat).
        fps: Output frame rate (default 30).

    Returns:
        FFmpeg filtergraph fragment string (no trailing semicolon).
    """
    # Background: scale source to fill 1080×1920, blur via cheap scale-down/up
    # Foreground: center-crop to 9:16 aspect, scale to 1080×1920
    # "Poor man's blur": scale to tiny then back up — orders of magnitude faster
    # than boxblur at full resolution, visually identical for a blurred BG.
    return (
        f"{input_label}split=2[bg_in][fg_in];"
        f"[bg_in]scale=108:192:force_original_aspect_ratio=increase,"
        f"crop=108:192,"
        f"scale={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}:flags=bilinear[bg];"
        f"[fg_in]crop=ih*9/16:ih,"
        f"scale={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2"
        f"{output_label}"
    )


def build_fallback_filter_simple(
    input_label: str,
    output_label: str,
) -> str:
    """Build a simplified fallback filter with blurred background (retry path).

    Same blurred background approach but without any extra effects.

    Args:
        input_label: FFmpeg filter input stream label.
        output_label: FFmpeg filter output stream label.

    Returns:
        FFmpeg filtergraph fragment string.
    """
    return (
        f"{input_label}split=2[bg_in][fg_in];"
        f"[bg_in]scale=108:192:force_original_aspect_ratio=increase,"
        f"crop=108:192,"
        f"scale={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}:flags=bilinear[bg];"
        f"[fg_in]crop=ih*9/16:ih,"
        f"scale={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2"
        f"{output_label}"
    )
