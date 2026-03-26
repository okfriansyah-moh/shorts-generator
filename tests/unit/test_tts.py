"""Unit tests for the tts module."""

from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from contracts.hook import HookResult
from contracts.tts import TTSResult, TTSWordTiming
from modules.tts.synthesize import (
    _get_audio_duration,
    _get_cache_key,
    _normalize_audio,
    process,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_hook_result(
    clip_id: str = "abcd1234abcd1234",
    video_id: str = "a1b2c3d4e5f67890",
) -> HookResult:
    return HookResult(
        clip_id=clip_id,
        video_id=video_id,
        hook_text="You won't believe this insane gameplay play",
        story_text="Watch this insane moment unfold in the most unexpected way",
        template_id="hook_0",
        keyword_source=("gameplay",),
    )


def _make_config() -> dict:
    return {
        "tts": {
            "engine": "edge-tts",
            "voice": "en-US-AriaNeural",
            "rate": "+0%",
            "volume": "+0%",
            "output_format": "mp3",
            "sample_rate": 44100,
            "volume_normalization_lufs": -14,
        }
    }


def _mock_subprocess_ffprobe_duration(duration: float = 3.5):
    """Create a mock for subprocess.run that returns ffprobe duration."""
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = json.dumps({"format": {"duration": str(duration)}})
    mock_result.stderr = ""
    return mock_result


def _mock_subprocess_ffmpeg_success():
    """Create a mock for successful FFmpeg run."""
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = ""
    mock_result.stderr = ""
    return mock_result


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestCacheKey:
    """Test cache key generation."""

    def test_deterministic(self) -> None:
        key1 = _get_cache_key("hello world", "en-US-AriaNeural")
        key2 = _get_cache_key("hello world", "en-US-AriaNeural")
        assert key1 == key2

    def test_different_text_different_key(self) -> None:
        key1 = _get_cache_key("hello", "en-US-AriaNeural")
        key2 = _get_cache_key("world", "en-US-AriaNeural")
        assert key1 != key2

    def test_different_voice_different_key(self) -> None:
        key1 = _get_cache_key("hello", "en-US-AriaNeural")
        key2 = _get_cache_key("hello", "en-US-GuyNeural")
        assert key1 != key2

    def test_key_length(self) -> None:
        key = _get_cache_key("test", "voice")
        assert len(key) == 16


class TestGetAudioDuration:
    """Test audio duration probing."""

    def test_returns_duration(self) -> None:
        mock = _mock_subprocess_ffprobe_duration(5.25)
        with patch("subprocess.run", return_value=mock):
            duration = _get_audio_duration("/fake/audio.wav")
        assert duration == 5.25

    def test_failure_raises(self) -> None:
        mock = MagicMock()
        mock.returncode = 1
        mock.stderr = "error"
        with patch("subprocess.run", return_value=mock):
            with pytest.raises(RuntimeError, match="ffprobe failed"):
                _get_audio_duration("/fake/audio.wav")


class TestNormalizeAudio:
    """Test audio volume normalization."""

    def test_calls_ffmpeg_and_renames(self, tmp_path) -> None:
        input_file = tmp_path / "input.wav"
        input_file.write_bytes(b"\x00" * 100)
        output_path = str(tmp_path / "output.wav")

        def mock_run(cmd, **kwargs):
            # Create the tmp output file
            for arg in cmd:
                if arg.endswith(".tmp"):
                    with open(arg, "wb") as f:
                        f.write(b"\x00" * 50)
            result = MagicMock()
            result.returncode = 0
            result.stderr = ""
            return result

        with patch("subprocess.run", side_effect=mock_run):
            _normalize_audio(str(input_file), output_path, -14)

        assert os.path.exists(output_path)


class TestTTSProcess:
    """Test the full TTS process function."""

    def test_cache_hit(self, tmp_path) -> None:
        """When cached audio exists, return it without synthesis."""
        hook = _make_hook_result()
        config = _make_config()
        output_dir = str(tmp_path)

        # Pre-populate cache
        text = f"{hook.hook_text}. {hook.story_text}"
        voice = config["tts"]["voice"]
        cache_key = _get_cache_key(text, voice)
        cache_dir = os.path.join(output_dir, "tts_cache")
        os.makedirs(cache_dir, exist_ok=True)
        cached_file = os.path.join(cache_dir, f"{cache_key}.wav")
        with open(cached_file, "wb") as f:
            f.write(b"\x00" * 100)

        mock_probe = _mock_subprocess_ffprobe_duration(4.0)
        with patch("subprocess.run", return_value=mock_probe):
            result = process(hook, config, output_dir)

        assert isinstance(result, TTSResult)
        assert result.clip_id == hook.clip_id
        assert result.duration_seconds == 4.0
        assert result.engine_used == "cached"
        assert result.audio_path == cached_file

    def test_all_engines_fail_raises(self, tmp_path) -> None:
        """When both edge-tts and pyttsx3 fail, raise RuntimeError."""
        hook = _make_hook_result()
        config = _make_config()
        output_dir = str(tmp_path)

        with patch(
            "modules.tts.synthesize._synthesize_edge_tts",
            side_effect=RuntimeError("network error"),
        ), patch(
            "modules.tts.synthesize._synthesize_pyttsx3",
            side_effect=RuntimeError("pyttsx3 error"),
        ):
            with pytest.raises(RuntimeError, match="TTS synthesis failed"):
                process(hook, config, output_dir)

    def test_edge_tts_success(self, tmp_path) -> None:
        """Test successful synthesis via edge-tts mock."""
        hook = _make_hook_result()
        config = _make_config()
        output_dir = str(tmp_path)

        # Mock edge TTS returning timings
        mock_timings = [
            TTSWordTiming(text="You", start_ms=0, end_ms=200),
            TTSWordTiming(text="won't", start_ms=200, end_ms=500),
        ]

        def mock_edge(text, path, voice, rate, volume):
            with open(path, "wb") as f:
                f.write(b"\x00" * 100)
            return path, 3.5, mock_timings

        def mock_normalize(inp, out, lufs=-14):
            with open(out, "wb") as f:
                f.write(b"\x00" * 50)

        with patch(
            "modules.tts.synthesize._synthesize_edge_tts",
            side_effect=mock_edge,
        ), patch(
            "modules.tts.synthesize._normalize_audio",
            side_effect=mock_normalize,
        ), patch(
            "modules.tts.synthesize._get_audio_duration",
            return_value=3.5,
        ):
            result = process(hook, config, output_dir)

        assert isinstance(result, TTSResult)
        assert result.engine_used == "edge-tts"
        assert result.duration_seconds == 3.5
        assert len(result.word_timings) == 2

    def test_fallback_to_pyttsx3(self, tmp_path) -> None:
        """When edge-tts fails, fallback to pyttsx3."""
        hook = _make_hook_result()
        config = _make_config()
        output_dir = str(tmp_path)

        def mock_pyttsx3(text, path):
            with open(path, "wb") as f:
                f.write(b"\x00" * 100)
            return path, 3.0

        def mock_normalize(inp, out, lufs=-14):
            with open(out, "wb") as f:
                f.write(b"\x00" * 50)

        with patch(
            "modules.tts.synthesize._synthesize_edge_tts",
            side_effect=RuntimeError("offline"),
        ), patch(
            "modules.tts.synthesize._synthesize_pyttsx3",
            side_effect=mock_pyttsx3,
        ), patch(
            "modules.tts.synthesize._normalize_audio",
            side_effect=mock_normalize,
        ), patch(
            "modules.tts.synthesize._get_audio_duration",
            return_value=3.0,
        ):
            result = process(hook, config, output_dir)

        assert result.engine_used == "pyttsx3"
        assert result.word_timings == ()


class TestTTSWordTiming:
    """Test TTSWordTiming DTO."""

    def test_frozen(self) -> None:
        wt = TTSWordTiming(text="hello", start_ms=0, end_ms=500)
        with pytest.raises(AttributeError):
            wt.text = "changed"  # type: ignore[misc]

    def test_fields(self) -> None:
        wt = TTSWordTiming(text="hello", start_ms=100, end_ms=500)
        assert wt.text == "hello"
        assert wt.start_ms == 100
        assert wt.end_ms == 500


class TestTTSResultDTO:
    """Test TTSResult DTO."""

    def test_frozen(self) -> None:
        result = TTSResult(
            clip_id="abcd1234abcd1234",
            audio_path="/tmp/audio.wav",
            duration_seconds=3.5,
            sample_rate=44100,
            word_timings=(),
            engine_used="edge-tts",
        )
        with pytest.raises(AttributeError):
            result.clip_id = "changed"  # type: ignore[misc]
