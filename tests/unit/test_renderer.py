"""Unit tests for the renderer module."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from contracts.compositor import CompositeStream
from contracts.render import RenderedClip
from contracts.subtitle import SubtitleResult
from contracts.tts import TTSResult, TTSWordTiming
from modules.renderer.render import (
    _build_render_command,
    _validate_output,
    process,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_composite(
    clip_id: str = "abcd1234abcd1234",
    video_id: str = "a1b2c3d4e5f67890",
    composite_path: str = "/tmp/composite.mp4",
    audio_path: str = "/tmp/gameplay_audio.wav",
    duration: float = 45.0,
) -> CompositeStream:
    return CompositeStream(
        clip_id=clip_id,
        video_id=video_id,
        composite_path=composite_path,
        source_audio_path=audio_path,
        resolution=(1080, 1920),
        layout="face_gameplay_split",
        duration_seconds=duration,
    )


def _make_tts_result(
    clip_id: str = "abcd1234abcd1234",
    audio_path: str = "/tmp/narration.wav",
) -> TTSResult:
    return TTSResult(
        clip_id=clip_id,
        audio_path=audio_path,
        duration_seconds=4.0,
        sample_rate=44100,
        word_timings=(
            TTSWordTiming(text="hello", start_ms=0, end_ms=300),
        ),
        engine_used="edge-tts",
    )


def _make_subtitle_result(
    clip_id: str = "abcd1234abcd1234",
    ass_path: str = "/tmp/subtitles.ass",
) -> SubtitleResult:
    return SubtitleResult(
        clip_id=clip_id,
        ass_path=ass_path,
        has_transcript_subs=True,
        has_narration_subs=True,
        subtitle_count=10,
    )


def _make_config(crf: int = 20) -> dict:
    return {
        "renderer": {
            "codec": "libx264",
            "crf": crf,
            "preset": "medium",
            "fps": 30,
            "max_file_size_mb": 100,
            "audio_mix_gameplay": 0.7,
            "audio_mix_narration": 0.3,
        },
        "pipeline": {
            "ffmpeg_timeout": 300,
        },
    }


def _mock_probe_data(
    duration: float = 45.0,
    width: int = 1080,
    height: int = 1920,
    codec: str = "h264",
    fps_str: str = "30/1",
) -> dict:
    return {
        "streams": [
            {
                "codec_type": "video",
                "width": width,
                "height": height,
                "codec_name": codec,
                "r_frame_rate": fps_str,
            },
            {"codec_type": "audio"},
        ],
        "format": {
            "duration": str(duration),
            "size": str(50 * 1024 * 1024),
        },
    }


# ---------------------------------------------------------------------------
# Tests: Build render command
# ---------------------------------------------------------------------------

class TestBuildRenderCommand:

    def test_includes_composite_input(self) -> None:
        composite = _make_composite()
        args = _build_render_command(
            composite, None, None, "/tmp/out.mp4", _make_config(),
        )
        assert composite.composite_path in args

    def test_includes_audio_input(self) -> None:
        composite = _make_composite()
        args = _build_render_command(
            composite, None, None, "/tmp/out.mp4", _make_config(),
        )
        assert composite.source_audio_path in args

    def test_includes_tts_when_present(self, tmp_path) -> None:
        tts_path = str(tmp_path / "narration.wav")
        with open(tts_path, "wb") as f:
            f.write(b"\x00" * 100)

        composite = _make_composite()
        tts = _make_tts_result(audio_path=tts_path)
        args = _build_render_command(
            composite, tts, None, "/tmp/out.mp4", _make_config(),
        )
        assert tts_path in args

    def test_includes_subtitle_filter(self, tmp_path) -> None:
        ass_path = str(tmp_path / "subs.ass")
        with open(ass_path, "w") as f:
            f.write("[Script Info]\n")

        composite = _make_composite()
        sub = _make_subtitle_result(ass_path=ass_path)
        args = _build_render_command(
            composite, None, sub, "/tmp/out.mp4", _make_config(),
        )
        filter_str = " ".join(args)
        assert "ass=" in filter_str

    def test_codec_settings(self) -> None:
        composite = _make_composite()
        args = _build_render_command(
            composite, None, None, "/tmp/out.mp4", _make_config(),
        )
        assert "libx264" in args
        assert "20" in args  # CRF
        assert "medium" in args  # preset
        assert "high" in args  # profile

    def test_crf_override(self) -> None:
        composite = _make_composite()
        args = _build_render_command(
            composite, None, None, "/tmp/out.mp4", _make_config(),
            crf_override=24,
        )
        assert "24" in args

    def test_output_path_is_last(self) -> None:
        composite = _make_composite()
        args = _build_render_command(
            composite, None, None, "/tmp/out.mp4", _make_config(),
        )
        assert args[-1] == "/tmp/out.mp4"


# ---------------------------------------------------------------------------
# Tests: Validate output
# ---------------------------------------------------------------------------

class TestValidateOutput:

    def test_valid_output(self) -> None:
        probe_data = _mock_probe_data(duration=45.0)

        def mock_run(cmd, **kwargs):
            result = MagicMock()
            result.returncode = 0
            result.stdout = json.dumps(probe_data)
            result.stderr = ""
            return result

        with patch("subprocess.run", side_effect=mock_run), \
             patch("os.path.getsize", return_value=50_000_000):
            duration, res, codec, fps, size = _validate_output("/tmp/out.mp4")

        assert duration == 45.0
        assert res == (1080, 1920)
        assert codec == "h264"

    def test_rejects_wrong_duration(self) -> None:
        probe_data = _mock_probe_data(duration=120.0)

        def mock_run(cmd, **kwargs):
            result = MagicMock()
            result.returncode = 0
            result.stdout = json.dumps(probe_data)
            result.stderr = ""
            return result

        with patch("subprocess.run", side_effect=mock_run), \
             patch("os.path.getsize", return_value=50_000_000):
            with pytest.raises(RuntimeError, match="duration"):
                _validate_output("/tmp/out.mp4")

    def test_rejects_wrong_resolution(self) -> None:
        probe_data = _mock_probe_data(width=1920, height=1080)

        def mock_run(cmd, **kwargs):
            result = MagicMock()
            result.returncode = 0
            result.stdout = json.dumps(probe_data)
            result.stderr = ""
            return result

        with patch("subprocess.run", side_effect=mock_run), \
             patch("os.path.getsize", return_value=50_000_000):
            with pytest.raises(RuntimeError, match="Resolution"):
                _validate_output("/tmp/out.mp4")


# ---------------------------------------------------------------------------
# Tests: Full render process
# ---------------------------------------------------------------------------

class TestRenderProcess:

    def test_idempotent_cache_hit(self, tmp_path) -> None:
        """When final.mp4 exists and is valid, skip rendering."""
        composite = _make_composite(clip_id="cached_clip_1234")
        config = _make_config()
        output_dir = str(tmp_path)

        # Create the expected output file
        clip_dir = tmp_path / "clips" / "cached_clip_1234"
        clip_dir.mkdir(parents=True)
        final_path = clip_dir / "final.mp4"
        final_path.write_bytes(b"\x00" * 100)

        probe_data = _mock_probe_data(duration=45.0)

        def mock_run(cmd, **kwargs):
            result = MagicMock()
            result.returncode = 0
            result.stdout = json.dumps(probe_data)
            result.stderr = ""
            return result

        with patch("subprocess.run", side_effect=mock_run), \
             patch("os.path.getsize", return_value=50_000_000):
            result = process(composite, None, None, config, output_dir)

        assert isinstance(result, RenderedClip)
        assert result.clip_id == "cached_clip_1234"
        assert result.duration_seconds == 45.0

    def test_render_without_narration_or_subs(self, tmp_path) -> None:
        """Render with only composite and gameplay audio."""
        composite = _make_composite()
        config = _make_config()
        output_dir = str(tmp_path)

        probe_data = _mock_probe_data(duration=45.0)
        call_count = [0]

        def mock_run(cmd, **kwargs):
            call_count[0] += 1
            result = MagicMock()
            result.returncode = 0
            result.stdout = json.dumps(probe_data)
            result.stderr = ""

            # Create output file for FFmpeg calls
            if cmd[0] == "ffmpeg":
                for arg in cmd:
                    if arg.endswith(".tmp"):
                        with open(arg, "wb") as f:
                            f.write(b"\x00" * 100)
            return result

        with patch("subprocess.run", side_effect=mock_run), \
             patch("os.path.getsize", return_value=50_000_000):
            result = process(composite, None, None, config, output_dir)

        assert isinstance(result, RenderedClip)
        assert result.has_narration is False
        assert result.has_subtitles is False


# ---------------------------------------------------------------------------
# Tests: DTOs
# ---------------------------------------------------------------------------

class TestCompositeStreamDTO:

    def test_frozen(self) -> None:
        cs = _make_composite()
        with pytest.raises(AttributeError):
            cs.clip_id = "changed"  # type: ignore[misc]

    def test_fields(self) -> None:
        cs = _make_composite()
        assert cs.resolution == (1080, 1920)
        assert cs.layout == "face_gameplay_split"


class TestRenderedClipDTO:

    def test_frozen(self) -> None:
        rc = RenderedClip(
            clip_id="test", video_id="vid1",
            output_path="/tmp/final.mp4",
            duration_seconds=45.0,
            resolution=(1080, 1920),
            codec="h264", fps=30,
            file_size_bytes=50_000_000,
            has_narration=True, has_subtitles=True,
        )
        with pytest.raises(AttributeError):
            rc.clip_id = "changed"  # type: ignore[misc]

    def test_fields(self) -> None:
        rc = RenderedClip(
            clip_id="test", video_id="vid1",
            output_path="/tmp/final.mp4",
            duration_seconds=45.0,
            resolution=(1080, 1920),
            codec="h264", fps=30,
            file_size_bytes=50_000_000,
            has_narration=True, has_subtitles=True,
        )
        assert rc.resolution == (1080, 1920)
        assert rc.has_narration is True
