"""Audio analysis module public interface.

Exposes analyze_audio() — the only entry point for audio energy analysis.
"""

from modules.audio_analysis.analyze import analyze_audio

__all__ = ["analyze_audio"]
