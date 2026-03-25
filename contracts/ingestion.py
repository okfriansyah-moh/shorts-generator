"""IngestionResult DTO for Shorts Factory.

Produced by the ingestion module. Consumed by scene_splitter,
transcription, face_detection, and audio_analysis.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class IngestionResult:
    """Frozen DTO representing a validated, fingerprinted input video.

    Fields:
        video_id: SHA256(first_10MB + str(file_size))[:16]. 16 lowercase hex chars.
        path: Absolute path to the source video file.
        duration_seconds: Total video duration. Range: [1800.0, 7200.0].
        resolution: (width, height) in pixels.
        codec: Video codec name (e.g. "h264").
        audio_codec: Audio codec name (e.g. "aac").
        has_audio: True if audio stream is present. Always True in pipeline.
        file_size_bytes: Source file size in bytes. > 0.
        fps: Source video frame rate. > 0.
    """

    video_id: str
    path: str
    duration_seconds: float
    resolution: tuple[int, int]
    codec: str
    audio_codec: str
    has_audio: bool
    file_size_bytes: int
    fps: float
