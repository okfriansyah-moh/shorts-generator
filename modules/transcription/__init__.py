"""Transcription module public interface.

Exposes transcribe() — the only entry point for transcription.
"""

from modules.transcription.transcribe import transcribe

__all__ = ["transcribe"]
