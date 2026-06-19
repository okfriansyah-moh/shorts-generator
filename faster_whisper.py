"""Stub for faster_whisper when HuggingFace model downloads are blocked."""
import types as _types

class _StubInfo:
    language = "en"
    language_probability = 1.0
    duration = 0.0
    duration_after_vad = 0.0
    all_language_probs = None
    transcription_options = None
    vad_options = None

class WhisperModel:
    def __init__(self, *args, **kwargs):
        pass

    def transcribe(self, audio, **kwargs):
        return iter([]), _StubInfo()
