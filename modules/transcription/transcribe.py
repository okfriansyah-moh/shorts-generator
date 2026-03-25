"""Transcription module — faster-whisper integration with word-level timestamps.

Implements deterministic transcription using faster-whisper (CTranslate2).
The same input + same config always produces the same Transcript DTO.

Entry point: transcribe(ingestion_result, config) -> Transcript
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from typing import TYPE_CHECKING, Any

from contracts.transcript import Transcript, TranscriptSegment, Word

if TYPE_CHECKING:
    from contracts.ingestion import IngestionResult

logger = logging.getLogger(__name__)

# Default config values — overridden by config.yaml at runtime
_DEFAULT_MODEL_SIZE = "small"
_DEFAULT_LANGUAGE = "en"
_DEFAULT_BEAM_SIZE = 5


def transcribe(
    ingestion_result: "IngestionResult",
    config: dict[str, Any],
) -> Transcript:
    """Transcribe audio from a video file using faster-whisper.

    Extracts audio via FFmpeg, runs faster-whisper in CPU mode with fixed
    deterministic settings, and returns a word-level Transcript DTO.

    Empty speech (no speech detected) is a valid result — not an error.

    Args:
        ingestion_result: DTO from the ingestion stage.
        config: Pipeline configuration dict (from config.yaml).

    Returns:
        Transcript DTO with word-level timestamps.

    Raises:
        RuntimeError: If faster-whisper cannot be loaded (dependency error).
        RuntimeError: If audio extraction via FFmpeg fails.
    """
    transcription_cfg = config.get("transcription", {})
    model_size: str = transcription_cfg.get("model_size", _DEFAULT_MODEL_SIZE)
    language: str = transcription_cfg.get("language", _DEFAULT_LANGUAGE)
    beam_size: int = int(transcription_cfg.get("beam_size", _DEFAULT_BEAM_SIZE))

    video_id = ingestion_result.video_id
    video_path = ingestion_result.path

    logger.info(
        "Transcription stage started",
        extra={
            "stage": "transcription",
            "video_id": video_id,
            "model_size": model_size,
            "language": language,
        },
    )

    wav_path = _extract_audio_to_wav(video_path, video_id, config)
    try:
        transcript = _run_faster_whisper(
            wav_path, video_id, model_size, language, beam_size
        )
    finally:
        _cleanup_temp_file(wav_path)

    logger.info(
        "Transcription stage completed",
        extra={
            "stage": "transcription",
            "video_id": video_id,
            "total_words": transcript.total_words,
            "segment_count": len(transcript.segments),
            "detected_language": transcript.language,
        },
    )
    return transcript


def _extract_audio_to_wav(
    video_path: str, video_id: str, config: dict[str, Any]
) -> str:
    """Extract audio from video to a temporary WAV file using FFmpeg.

    Args:
        video_path: Path to the input video file.
        video_id: Video identifier for temp file naming.
        config: Pipeline configuration dict.

    Returns:
        Absolute path to the temporary WAV file.

    Raises:
        RuntimeError: If FFmpeg audio extraction fails.
    """
    temp_dir = config.get("paths", {}).get("temp_dir", "output/temp")
    os.makedirs(temp_dir, exist_ok=True)

    fd, wav_path = tempfile.mkstemp(
        suffix=".wav", prefix=f"transcription_{video_id}_", dir=temp_dir
    )
    os.close(fd)

    cmd = [
        "ffmpeg",
        "-y",
        "-i", video_path,
        "-vn",
        "-acodec", "pcm_s16le",
        "-ar", "16000",
        "-ac", "1",
        "-loglevel", "error",
        wav_path,
    ]
    timeout = config.get("pipeline", {}).get("ffmpeg_timeout", 300)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        _cleanup_temp_file(wav_path)
        raise RuntimeError(
            f"FFmpeg audio extraction timed out after {timeout}s for video {video_id}"
        )

    if result.returncode != 0:
        _cleanup_temp_file(wav_path)
        raise RuntimeError(
            f"FFmpeg audio extraction failed for video {video_id}: {result.stderr}"
        )

    return wav_path


def _run_faster_whisper(
    wav_path: str,
    video_id: str,
    model_size: str,
    language: str,
    beam_size: int,
) -> Transcript:
    """Run faster-whisper transcription and return a Transcript DTO.

    Uses deterministic settings: fixed model size, fixed beam size, CPU mode,
    no VAD filter to ensure reproducibility across runs.

    Args:
        wav_path: Path to the WAV audio file.
        video_id: Video identifier for the DTO.
        model_size: faster-whisper model name (e.g. "small", "base").
        language: Language code (e.g. "en").
        beam_size: Beam search width for deterministic decoding.

    Returns:
        Transcript DTO.

    Raises:
        RuntimeError: If faster-whisper is not installed or model cannot be loaded.
    """
    try:
        from faster_whisper import WhisperModel  # type: ignore[import]
    except ImportError:
        raise RuntimeError(
            "faster-whisper is not installed. "
            "Run: pip install faster-whisper"
        )

    logger.debug(
        "Loading faster-whisper model",
        extra={"stage": "transcription", "video_id": video_id, "model_size": model_size},
    )

    try:
        model = WhisperModel(model_size, device="cpu", compute_type="int8")
    except Exception as exc:
        raise RuntimeError(
            f"Failed to load faster-whisper model '{model_size}': {exc}"
        ) from exc

    segments_raw, info = model.transcribe(
        wav_path,
        language=language,
        beam_size=beam_size,
        word_timestamps=True,
        vad_filter=False,
        temperature=0.0,
    )

    detected_language: str = info.language if info.language else language

    all_segments: list[TranscriptSegment] = []
    for seg in segments_raw:
        words_raw = seg.words or []
        words = tuple(
            Word(
                text=w.word.strip(),
                start_time=_seconds_to_ms(w.start),
                end_time=_seconds_to_ms(w.end),
                confidence=float(w.probability),
            )
            for w in words_raw
            if w.word.strip()
        )
        avg_confidence = (
            sum(w.confidence for w in words) / len(words) if words else 0.0
        )
        segment = TranscriptSegment(
            text=seg.text.strip(),
            start_time=_seconds_to_ms(seg.start),
            end_time=_seconds_to_ms(seg.end),
            words=words,
            confidence=avg_confidence,
        )
        all_segments.append(segment)

    segments_tuple = tuple(sorted(all_segments, key=lambda s: s.start_time))
    total_words = sum(len(s.words) for s in segments_tuple)

    return Transcript(
        video_id=video_id,
        segments=segments_tuple,
        total_words=total_words,
        language=detected_language,
    )


def _seconds_to_ms(seconds: float) -> int:
    """Convert seconds (float) to milliseconds (int), rounding to nearest ms."""
    return int(round(seconds * 1000))


def _cleanup_temp_file(path: str) -> None:
    """Remove a temporary file, logging a warning if removal fails."""
    try:
        if os.path.exists(path):
            os.unlink(path)
    except OSError as exc:
        logger.warning(
            "Failed to clean up temporary file",
            extra={"stage": "transcription", "path": path, "error": str(exc)},
        )
