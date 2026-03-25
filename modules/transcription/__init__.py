"""Transcription module public interface.

Exposes transcribe() — the only entry point for transcription.
"""

from .transcribe import transcribe

__all__ = ["transcribe"]
