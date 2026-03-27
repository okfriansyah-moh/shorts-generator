"""Unit tests for core/gpu.py — GPU configuration resolver."""

from __future__ import annotations

import pytest

from core.gpu import resolve_gpu_settings


class TestResolveCPUSettings:
    """Tests for CPU-only mode (gpu.enabled=False or absent)."""

    def test_no_gpu_section_returns_cpu(self):
        """Config without gpu section returns CPU defaults."""
        config = {"renderer": {"codec": "libx264", "crf": 20, "preset": "medium"}}
        result = resolve_gpu_settings(config)

        assert result["enabled"] is False
        assert result["transcription_device"] == "cpu"
        assert result["transcription_compute_type"] == "int8"
        assert result["ffmpeg_encoder"] == "libx264"

    def test_gpu_disabled_returns_cpu(self):
        """Config with gpu.enabled=False returns CPU defaults."""
        config = {
            "renderer": {"codec": "libx264", "crf": 22, "preset": "slow"},
            "gpu": {"enabled": False},
        }
        result = resolve_gpu_settings(config)

        assert result["enabled"] is False
        assert result["transcription_device"] == "cpu"
        assert result["ffmpeg_encoder"] == "libx264"

    def test_cpu_encode_args_use_renderer_config(self):
        """CPU encode args are derived from renderer section."""
        config = {
            "renderer": {"codec": "libx264", "crf": 18, "preset": "slow"},
        }
        result = resolve_gpu_settings(config)

        assert "-c:v" in result["ffmpeg_encode_args"]
        assert "libx264" in result["ffmpeg_encode_args"]
        assert "-crf" in result["ffmpeg_encode_args"]
        assert "18" in result["ffmpeg_encode_args"]
        assert "-preset" in result["ffmpeg_encode_args"]
        assert "slow" in result["ffmpeg_encode_args"]

    def test_cpu_fallback_args_higher_crf(self):
        """CPU fallback args use CRF + 8."""
        config = {
            "renderer": {"codec": "libx264", "crf": 20, "preset": "medium"},
        }
        result = resolve_gpu_settings(config)

        assert "28" in result["ffmpeg_encode_args_fallback"]
        assert "fast" in result["ffmpeg_encode_args_fallback"]

    def test_empty_config_uses_defaults(self):
        """Empty config uses sensible defaults."""
        result = resolve_gpu_settings({})

        assert result["enabled"] is False
        assert result["ffmpeg_encoder"] == "libx264"
        assert result["transcription_device"] == "cpu"


class TestResolveGPUSettings:
    """Tests for GPU-enabled mode (gpu.enabled=True)."""

    def test_gpu_enabled_returns_nvenc(self):
        """GPU enabled returns NVENC encoder."""
        config = {
            "renderer": {"codec": "libx264", "crf": 20, "preset": "medium"},
            "gpu": {"enabled": True},
        }
        result = resolve_gpu_settings(config)

        assert result["enabled"] is True
        assert result["ffmpeg_encoder"] == "h264_nvenc"
        assert result["transcription_device"] == "cuda"
        assert result["transcription_compute_type"] == "float16"

    def test_gpu_encode_args_contain_nvenc_params(self):
        """GPU primary encode args include NVENC-specific parameters."""
        config = {
            "renderer": {"codec": "libx264", "crf": 20, "preset": "medium"},
            "gpu": {"enabled": True},
        }
        result = resolve_gpu_settings(config)
        args = result["ffmpeg_encode_args"]

        assert "-c:v" in args
        assert "h264_nvenc" in args
        assert "-preset" in args
        assert "p4" in args
        assert "-rc" in args
        assert "vbr" in args
        assert "-cq" in args
        assert "-spatial_aq" in args
        assert "-temporal_aq" in args

    def test_gpu_fallback_args_lower_quality(self):
        """GPU fallback args use p1 preset and higher CQ."""
        config = {
            "renderer": {"codec": "libx264", "crf": 20, "preset": "medium"},
            "gpu": {"enabled": True},
        }
        result = resolve_gpu_settings(config)
        fallback = result["ffmpeg_encode_args_fallback"]

        assert "h264_nvenc" in fallback
        assert "p1" in fallback
        assert "28" in fallback

    def test_gpu_custom_params_override_defaults(self):
        """Custom GPU params in config override defaults."""
        config = {
            "renderer": {"codec": "libx264", "crf": 20, "preset": "medium"},
            "gpu": {
                "enabled": True,
                "encoder": "h264_nvenc",
                "preset": "p7",
                "cq": 15,
                "cq_fallback": 25,
            },
        }
        result = resolve_gpu_settings(config)

        assert result["ffmpeg_encoder"] == "h264_nvenc"
        assert "p7" in result["ffmpeg_encode_args"]
        assert "15" in result["ffmpeg_encode_args"]
        assert "25" in result["ffmpeg_encode_args_fallback"]

    def test_gpu_transcription_custom_device(self):
        """Custom transcription device is respected."""
        config = {
            "gpu": {
                "enabled": True,
                "transcription_device": "cpu",
                "transcription_compute_type": "int8",
            },
        }
        result = resolve_gpu_settings(config)

        assert result["enabled"] is True
        assert result["transcription_device"] == "cpu"
        assert result["transcription_compute_type"] == "int8"


class TestSettingsStructure:
    """Tests for the structure of returned settings dict."""

    @pytest.mark.parametrize("enabled", [True, False])
    def test_all_required_keys_present(self, enabled):
        """All required keys are present regardless of mode."""
        config = {
            "renderer": {"codec": "libx264", "crf": 20, "preset": "medium"},
            "gpu": {"enabled": enabled},
        }
        result = resolve_gpu_settings(config)

        required_keys = {
            "enabled",
            "transcription_device",
            "transcription_compute_type",
            "ffmpeg_encoder",
            "ffmpeg_encode_args",
            "ffmpeg_encode_args_fallback",
        }
        assert required_keys == set(result.keys())

    @pytest.mark.parametrize("enabled", [True, False])
    def test_encode_args_are_lists(self, enabled):
        """Encode args are always lists."""
        config = {
            "renderer": {"codec": "libx264", "crf": 20, "preset": "medium"},
            "gpu": {"enabled": enabled},
        }
        result = resolve_gpu_settings(config)

        assert isinstance(result["ffmpeg_encode_args"], list)
        assert isinstance(result["ffmpeg_encode_args_fallback"], list)
        assert all(isinstance(a, str) for a in result["ffmpeg_encode_args"])
        assert all(isinstance(a, str) for a in result["ffmpeg_encode_args_fallback"])
