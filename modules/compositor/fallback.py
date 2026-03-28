"""Full-gameplay fallback layout with Ken Burns zoom effect.

Used when face visibility ratio < 0.3 for a clip.
Applies a slow progressive zoom (Ken Burns) from 1.0× to 1.05×
over the clip duration, centered on the frame.
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
    """Build FFmpeg filter chain for full-gameplay fallback layout.

    Center-crops source to 9:16 aspect ratio, scales to 1080×1920, then
    applies a gentle zoom-in from 1.0× to 1.05× (Ken Burns effect).

    Args:
        input_label: FFmpeg filter input stream label (e.g. '[0:v]').
        output_label: FFmpeg filter output stream label (e.g. '[v]').
        duration_seconds: Clip duration in seconds (used for frame count).
        fps: Output frame rate (default 30).

    Returns:
        FFmpeg filtergraph fragment string (no trailing semicolon).
    """
    total_frames = max(1, int(duration_seconds * fps))
    # Progressive zoom: starts at 1.0 and reaches 1.05 at the last frame.
    # 'on' is the current output frame number (0-based in zoompan).
    # CRITICAL: d=1 means one output frame per input frame.  Setting d to
    # total_frames would generate total_frames outputs *per* input frame,
    # causing an exponential blowup that always exceeds the timeout.
    zoom_expr = f"'1+0.05*on/{total_frames}'"
    return (
        f"{input_label}"
        f"crop=ih*9/16:ih,"
        f"scale={OUTPUT_WIDTH}:{OUTPUT_HEIGHT},"
        f"zoompan="
        f"z={zoom_expr}:"
        f"d=1:"
        f"x='iw/2-(iw/zoom/2)':"
        f"y='ih/2-(ih/zoom/2)':"
        f"s={OUTPUT_WIDTH}x{OUTPUT_HEIGHT}:"
        f"fps={fps}"
        f"{output_label}"
    )


def build_fallback_filter_simple(
    input_label: str,
    output_label: str,
) -> str:
    """Build a simplified fallback filter without zoompan (retry path).

    Used when the full Ken Burns filter fails. Simple center-crop and scale.

    Args:
        input_label: FFmpeg filter input stream label.
        output_label: FFmpeg filter output stream label.

    Returns:
        FFmpeg filtergraph fragment string.
    """
    return (
        f"{input_label}"
        f"crop=ih*9/16:ih,"
        f"scale={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}"
        f"{output_label}"
    )
