"""RenderedClip DTO for Shorts Factory.

Produced by the renderer module. Consumed by the storage module.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RenderedClip:
    """Frozen DTO representing a fully rendered, publish-ready clip.

    Fields:
        clip_id: Reference to parent clip. 16 lowercase hex chars.
        video_id: Parent video reference. 16 lowercase hex chars.
        output_path: Absolute path to the final MP4 file. Non-empty.
        duration_seconds: Rendered clip duration. 30.0 <= duration <= 60.0.
        resolution: (width, height) in pixels. Must be (1080, 1920).
        codec: Video codec used. Must be "h264".
        fps: Frame rate. Must be 30.
        file_size_bytes: Output file size in bytes. > 0. Max governed by renderer.max_file_size_mb config (default 300MB).
        has_narration: True if TTS narration audio was mixed in.
        has_subtitles: True if ASS subtitles were burned in.
    """

    clip_id: str
    video_id: str
    output_path: str
    duration_seconds: float
    resolution: tuple[int, int]
    codec: str
    fps: int
    file_size_bytes: int
    has_narration: bool
    has_subtitles: bool
