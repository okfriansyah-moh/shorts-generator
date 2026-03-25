"""Unit tests for modules/ingestion/ingest.py."""

from __future__ import annotations

import hashlib
import json
from unittest.mock import MagicMock, patch

import pytest

from modules.ingestion.ingest import (
    IngestionError,
    _compute_video_id,
    _parse_fps,
    _validate_format,
    ingest,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ffprobe_output(
    duration: float = 3600.0,
    width: int = 1920,
    height: int = 1080,
    video_codec: str = "h264",
    audio_codec: str = "aac",
    r_frame_rate: str = "30/1",
    has_audio: bool = True,
) -> str:
    streams = [
        {
            "codec_type": "video",
            "codec_name": video_codec,
            "width": width,
            "height": height,
            "r_frame_rate": r_frame_rate,
        }
    ]
    if has_audio:
        streams.append({"codec_type": "audio", "codec_name": audio_codec})
    return json.dumps({"streams": streams, "format": {"duration": str(duration)}})


def _minimal_config() -> dict:
    return {
        "ingestion": {
            "min_duration_seconds": 1800,
            "max_duration_seconds": 7200,
            "supported_formats": ["mp4", "mkv", "avi", "mov", "webm"],
        }
    }


# ---------------------------------------------------------------------------
# Format validation
# ---------------------------------------------------------------------------


class TestValidateFormat:
    """Tests for _validate_format."""

    def test_valid_mp4(self, tmp_path):
        path = str(tmp_path / "video.mp4")
        _validate_format(path, _minimal_config())  # No exception

    def test_valid_mkv(self, tmp_path):
        path = str(tmp_path / "video.mkv")
        _validate_format(path, _minimal_config())

    def test_unsupported_format_raises(self, tmp_path):
        path = str(tmp_path / "video.flv")
        with pytest.raises(IngestionError, match="Unsupported video format"):
            _validate_format(path, _minimal_config())

    def test_unsupported_ts_raises(self, tmp_path):
        path = str(tmp_path / "video.ts")
        with pytest.raises(IngestionError):
            _validate_format(path, _minimal_config())


# ---------------------------------------------------------------------------
# FPS parsing
# ---------------------------------------------------------------------------


class TestParseFps:
    """Tests for _parse_fps."""

    def test_integer_fps(self):
        assert _parse_fps("30/1") == pytest.approx(30.0)

    def test_fractional_fps(self):
        assert _parse_fps("30000/1001") == pytest.approx(29.97, abs=0.01)

    def test_plain_float(self):
        assert _parse_fps("25.0") == pytest.approx(25.0)

    def test_zero_denominator(self):
        assert _parse_fps("30/0") == 0.0

    def test_invalid_string(self):
        assert _parse_fps("invalid") == 0.0


# ---------------------------------------------------------------------------
# Video ID determinism
# ---------------------------------------------------------------------------


class TestComputeVideoId:
    """Tests for _compute_video_id (video_id determinism)."""

    def test_same_file_same_id(self, tmp_path):
        f = tmp_path / "video.mp4"
        f.write_bytes(b"x" * 1024)
        id1 = _compute_video_id(str(f), f.stat().st_size)
        id2 = _compute_video_id(str(f), f.stat().st_size)
        assert id1 == id2

    def test_id_is_16_hex_chars(self, tmp_path):
        f = tmp_path / "video.mp4"
        f.write_bytes(b"abc" * 100)
        vid_id = _compute_video_id(str(f), f.stat().st_size)
        assert len(vid_id) == 16
        assert all(c in "0123456789abcdef" for c in vid_id)

    def test_different_content_different_id(self, tmp_path):
        f1 = tmp_path / "a.mp4"
        f2 = tmp_path / "b.mp4"
        f1.write_bytes(b"aaa" * 100)
        f2.write_bytes(b"bbb" * 100)
        assert _compute_video_id(str(f1), f1.stat().st_size) != _compute_video_id(
            str(f2), f2.stat().st_size
        )

    def test_same_content_different_size_different_id(self, tmp_path):
        f1 = tmp_path / "a.mp4"
        f2 = tmp_path / "b.mp4"
        f1.write_bytes(b"abc" * 100)
        f2.write_bytes(b"abc" * 100)
        # Override file size to simulate difference
        id1 = _compute_video_id(str(f1), 300)
        id2 = _compute_video_id(str(f2), 999)
        assert id1 != id2

    def test_video_id_matches_sha256_formula(self, tmp_path):
        content = b"test_content_for_hash"
        f = tmp_path / "video.mp4"
        f.write_bytes(content)
        size = len(content)
        expected = hashlib.sha256(content + str(size).encode("ascii")).hexdigest()[:16]
        assert _compute_video_id(str(f), size) == expected


# ---------------------------------------------------------------------------
# ingest() function
# ---------------------------------------------------------------------------


class TestIngest:
    """Tests for the main ingest() function."""

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(IngestionError, match="not found"):
            ingest(str(tmp_path / "nonexistent.mp4"), _minimal_config())

    def test_unsupported_format_raises(self, tmp_path):
        f = tmp_path / "video.flv"
        f.write_bytes(b"\x00" * 10)
        with pytest.raises(IngestionError, match="Unsupported"):
            ingest(str(f), _minimal_config())

    def test_duration_too_short_raises(self, tmp_path):
        f = tmp_path / "video.mp4"
        f.write_bytes(b"\x00" * 10)
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = _make_ffprobe_output(duration=100.0)
        mock_result.stderr = ""
        with patch("subprocess.run", return_value=mock_result):
            with pytest.raises(IngestionError, match="duration"):
                ingest(str(f), _minimal_config())

    def test_duration_too_long_raises(self, tmp_path):
        f = tmp_path / "video.mp4"
        f.write_bytes(b"\x00" * 10)
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = _make_ffprobe_output(duration=8000.0)
        mock_result.stderr = ""
        with patch("subprocess.run", return_value=mock_result):
            with pytest.raises(IngestionError, match="duration"):
                ingest(str(f), _minimal_config())

    def test_no_audio_raises(self, tmp_path):
        f = tmp_path / "video.mp4"
        f.write_bytes(b"\x00" * 10)
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = _make_ffprobe_output(has_audio=False)
        mock_result.stderr = ""
        with patch("subprocess.run", return_value=mock_result):
            with pytest.raises(IngestionError, match="audio"):
                ingest(str(f), _minimal_config())

    def test_ffprobe_failure_raises(self, tmp_path):
        f = tmp_path / "video.mp4"
        f.write_bytes(b"\x00" * 10)
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "invalid data"
        with patch("subprocess.run", return_value=mock_result):
            with pytest.raises(IngestionError, match="FFprobe failed"):
                ingest(str(f), _minimal_config())

    def test_valid_file_returns_ingestion_result(self, tmp_path):
        f = tmp_path / "video.mp4"
        f.write_bytes(b"\x00" * 1024)
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = _make_ffprobe_output(duration=3600.0)
        mock_result.stderr = ""
        with patch("subprocess.run", return_value=mock_result):
            result = ingest(str(f), _minimal_config())

        assert result.video_id
        assert len(result.video_id) == 16
        assert result.has_audio is True
        assert result.duration_seconds == 3600.0
        assert result.resolution == (1920, 1080)
        assert result.codec == "h264"
        assert result.audio_codec == "aac"
        assert result.fps == pytest.approx(30.0)
        assert result.file_size_bytes == 1024

    def test_video_id_is_deterministic(self, tmp_path):
        f = tmp_path / "video.mp4"
        f.write_bytes(b"\x01" * 2048)
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = _make_ffprobe_output()
        mock_result.stderr = ""
        with patch("subprocess.run", return_value=mock_result):
            r1 = ingest(str(f), _minimal_config())
            r2 = ingest(str(f), _minimal_config())
        assert r1.video_id == r2.video_id

    def test_result_is_frozen(self, tmp_path):
        f = tmp_path / "video.mp4"
        f.write_bytes(b"\x00" * 512)
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = _make_ffprobe_output()
        mock_result.stderr = ""
        with patch("subprocess.run", return_value=mock_result):
            result = ingest(str(f), _minimal_config())

        with pytest.raises((AttributeError, TypeError)):
            result.video_id = "mutated"  # type: ignore[misc]
