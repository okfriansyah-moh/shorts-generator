"""Gameplay region center-crop filter builder.

Produces an FFmpeg filtergraph fragment that center-crops the source
to 9:16 aspect ratio and scales to target dimensions.
"""

from __future__ import annotations


def build_gameplay_crop_filter(
    input_label: str,
    output_label: str,
    target_width: int,
    target_height: int,
) -> str:
    """Build FFmpeg filter fragment for center-cropping gameplay to 9:16.

    Center-crops source height-driven (crop width = ih*9/16, height = ih),
    then scales to target_width × target_height.

    Args:
        input_label: FFmpeg filter input stream label (e.g. '[0:v]').
        output_label: FFmpeg filter output stream label (e.g. '[gameplay]').
        target_width: Output width in pixels.
        target_height: Output height in pixels.

    Returns:
        FFmpeg filtergraph fragment string (no trailing semicolon).
    """
    return (
        f"{input_label}"
        f"crop=ih*9/16:ih,"
        f"scale={target_width}:{target_height}"
        f"{output_label}"
    )
