"""Face detection module public interface.

Exposes detect_faces() — the only entry point for face detection.
"""

from modules.face_detection.detect import detect_faces

__all__ = ["detect_faces"]
