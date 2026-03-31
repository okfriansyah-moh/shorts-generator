"""Unit tests for the compositor module.

Tests cover:
  - CompositeStream DTO is frozen and has correct fields
  - Split layout selected when avg face visibility >= 0.3
  - Fallback layout selected when avg face visibility < 0.3
  - Fallback layout selected when no face bbox available
  - Idempotency: compositor skips FFmpeg when output already exists
  - Determinism: same inputs → identical CompositeStream
  - Face crop parameter computation (clamping, aspect ratio)
  - Gameplay crop filter string format
  - Fallback filter string format
  - FFmpeg retry with simpler filters on failure
  - No cross-module imports; only contracts/ types used
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import fields
from unittest.mock import MagicMock, patch

import pytest

from contracts.clip import ClipDefinition
from contracts.compositor import CompositeStream
from contracts.face import FaceBBox, FaceDetectionResult, SceneFaceData
from contracts.ingestion import IngestionResult
from contracts.scoring import ScoredScene
from modules.compositor import process
from modules.compositor.face_crop import (
    FACE_REGION_HEIGHT,
    FACE_REGION_WIDTH,
    build_face_crop_filter,
    compute_face_crop_params,
    estimate_pip_region,
)
from modules.compositor.fallback import (
    build_fallback_filter,
    build_fallback_filter_simple,
)
from modules.compositor.gameplay_crop import build_gameplay_crop_filter


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

VIDEO_ID = "a1b2c3d4e5f67890"
CLIP_ID = "c1d2e3f4a5b6c7d8"


def _make_scored_scene(
    video_id: str = VIDEO_ID,
    start_ms: int = 0,
    end_ms: int = 10000,
    score: float = 0.7,
) -> ScoredScene:
    return ScoredScene(
        scene_id=f"{video_id}_{start_ms}_{end_ms}",
        video_id=video_id,
        start_time=start_ms,
        end_time=end_ms,
        duration=(end_ms - start_ms) / 1000.0,
        keyword_score=score,
        audio_energy_score=score,
        face_presence_score=score,
        scene_activity_score=score,
        sentence_density_score=score,
        composite_score=score,
        rank=1,
    )


def _make_clip(
    start_ms: int = 0,
    end_ms: int = 35000,
    clip_index: int = 0,
) -> ClipDefinition:
    scene = _make_scored_scene(start_ms=start_ms, end_ms=end_ms)
    duration = (end_ms - start_ms) / 1000.0
    return ClipDefinition(
        clip_id=CLIP_ID,
        video_id=VIDEO_ID,
        scenes=(scene,),
        start_time=start_ms,
        end_time=end_ms,
        duration=duration,
        average_score=0.7,
        clip_index=clip_index,
    )


def _make_face_bbox(
    x: float = 0.3,
    y: float = 0.1,
    width: float = 0.4,
    height: float = 0.6,
    confidence: float = 0.9,
    timestamp_ms: int = 0,
) -> FaceBBox:
    return FaceBBox(
        x=x,
        y=y,
        width=width,
        height=height,
        confidence=confidence,
        timestamp_ms=timestamp_ms,
    )


def _make_scene_face_data(
    scene_id: str,
    face_visible_ratio: float = 0.8,
    has_bbox: bool = True,
) -> SceneFaceData:
    bbox = _make_face_bbox() if has_bbox else None
    bboxes = (bbox,) if bbox else ()
    return SceneFaceData(
        scene_id=scene_id,
        face_visible_ratio=face_visible_ratio,
        bounding_boxes=bboxes,
        average_bbox=bbox,
        sample_count=10,
    )


def _make_face_result(
    clip: ClipDefinition,
    face_visible_ratio: float = 0.8,
    has_bbox: bool = True,
) -> FaceDetectionResult:
    scene_data = tuple(
        _make_scene_face_data(
            s.scene_id,
            face_visible_ratio=face_visible_ratio,
            has_bbox=has_bbox,
        )
        for s in clip.scenes
    )
    return FaceDetectionResult(
        video_id=VIDEO_ID,
        scene_data=scene_data,
        average_visibility=face_visible_ratio,
        faceless_scene_count=0 if face_visible_ratio >= 0.3 else 1,
    )


def _make_ingestion_result(
    tmp_path: str = "/tmp/video.mp4",
    width: int = 1920,
    height: int = 1080,
    fps: float = 30.0,
) -> IngestionResult:
    return IngestionResult(
        video_id=VIDEO_ID,
        path=tmp_path,
        duration_seconds=3600.0,
        resolution=(width, height),
        codec="h264",
        audio_codec="aac",
        has_audio=True,
        file_size_bytes=500_000_000,
        fps=fps,
    )


def _make_config(output_dir: str | None = None) -> dict:
    tmp = output_dir or tempfile.mkdtemp()
    return {
        "paths": {"output_dir": tmp},
        "pipeline": {
            "output_framerate": 30,
            "ffmpeg_timeout": 300,
        },
    }


def _mock_subprocess_success() -> MagicMock:
    mock = MagicMock()
    mock.returncode = 0
    mock.stdout = ""
    mock.stderr = ""
    return mock


# ---------------------------------------------------------------------------
# DTO contract tests
# ---------------------------------------------------------------------------


def test_composite_stream_is_frozen():
    """CompositeStream must be a frozen dataclass."""
    stream = CompositeStream(
        clip_id=CLIP_ID,
        video_id=VIDEO_ID,
        composite_path="/tmp/composite.mp4",
        source_audio_path="/tmp/source.mp4",
        resolution=(1080, 1920),
        layout="gameplay_only_zoom",
        duration_seconds=35.0,
        has_face=False,
        source_fps=30.0,
    )
    with pytest.raises((TypeError, AttributeError)):
        stream.clip_id = "changed"  # type: ignore[misc]


def test_composite_stream_fields():
    """CompositeStream has the required fields."""
    field_names = {f.name for f in fields(CompositeStream)}
    required = {
        "clip_id", "video_id", "composite_path", "source_audio_path", "resolution",
        "layout", "duration_seconds", "has_face", "source_fps",
    }
    assert required.issubset(field_names)


# ---------------------------------------------------------------------------
# Split layout tests
# ---------------------------------------------------------------------------


def test_process_split_layout_when_face_visible(tmp_path):
    """process() selects face_gameplay_split when visibility >= 0.3."""
    clip = _make_clip()
    face_result = _make_face_result(clip, face_visible_ratio=0.8)
    ingestion = _make_ingestion_result()
    config = _make_config(str(tmp_path))

    # Pre-create the expected output file to bypass FFmpeg
    output_dir = tmp_path / VIDEO_ID / "clips" / f"shorts-{clip.clip_index + 1}"
    output_dir.mkdir(parents=True)
    (output_dir / "composite.mp4").write_bytes(b"")

    result = process(clip, face_result, ingestion, config)

    assert isinstance(result, CompositeStream)
    assert result.layout == "face_gameplay_split"
    assert result.has_face is True
    assert result.clip_id == CLIP_ID
    assert result.video_id == VIDEO_ID
    assert result.resolution == (1080, 1920)


def test_process_fallback_layout_when_no_face(tmp_path):
    """process() selects gameplay_only_zoom when visibility < 0.3."""
    clip = _make_clip()
    face_result = _make_face_result(clip, face_visible_ratio=0.1)
    ingestion = _make_ingestion_result()
    config = _make_config(str(tmp_path))
    config["compositor"] = {"default_layout": "gameplay_only"}

    output_dir = tmp_path / VIDEO_ID / "clips" / f"shorts-{clip.clip_index + 1}"
    output_dir.mkdir(parents=True)
    (output_dir / "composite.mp4").write_bytes(b"")

    result = process(clip, face_result, ingestion, config)

    assert result.layout == "gameplay_only_zoom"
    assert result.has_face is False


def test_process_fallback_when_face_visible_but_no_bbox(tmp_path):
    """process() uses inferred face region when no bbox available (default split layout)."""
    clip = _make_clip()
    face_result = _make_face_result(clip, face_visible_ratio=0.8, has_bbox=False)
    ingestion = _make_ingestion_result()
    config = _make_config(str(tmp_path))

    mock_proc = _mock_subprocess_success()

    def fake_run(*args, **kwargs):
        cmd = args[0]
        for arg in cmd:
            if ".tmp." in arg:
                os.makedirs(os.path.dirname(arg), exist_ok=True)
                open(arg, "wb").close()
        return mock_proc

    with patch("subprocess.run", side_effect=fake_run):
        result = process(clip, face_result, ingestion, config)

    # Default split layout uses inferred face region when no bbox detected
    assert result.layout == "face_gameplay_split"
    assert result.has_face is True


def test_process_auto_uses_estimated_pip_bbox(tmp_path):
    """process() uses estimated_pip_bbox from face_result when face_region=auto."""
    clip = _make_clip()
    # Face result with no per-scene bboxes but with an estimated PiP position
    scene_data = tuple(
        SceneFaceData(
            scene_id=s.scene_id,
            face_visible_ratio=0.0,
            bounding_boxes=(),
            average_bbox=None,
            sample_count=10,
        )
        for s in clip.scenes
    )
    pip_bbox = _make_face_bbox(x=0.35, y=0.65, width=0.3, height=0.35)
    face_result = FaceDetectionResult(
        video_id=VIDEO_ID,
        scene_data=scene_data,
        average_visibility=0.0,
        faceless_scene_count=1,
        estimated_pip_bbox=pip_bbox,
    )
    ingestion = _make_ingestion_result()
    config = _make_config(str(tmp_path))
    config["compositor"] = {"face_region": "auto"}

    mock_proc = _mock_subprocess_success()

    def fake_run(*args, **kwargs):
        cmd = args[0]
        for arg in cmd:
            if ".tmp." in arg:
                os.makedirs(os.path.dirname(arg), exist_ok=True)
                open(arg, "wb").close()
        return mock_proc

    with patch("subprocess.run", side_effect=fake_run):
        result = process(clip, face_result, ingestion, config)

    assert result.layout == "face_gameplay_split"
    assert result.has_face is True


# ---------------------------------------------------------------------------
# Idempotency tests
# ---------------------------------------------------------------------------


def test_process_idempotent_skips_ffmpeg(tmp_path):
    """process() skips FFmpeg if composite.mp4 already exists."""
    clip = _make_clip()
    face_result = _make_face_result(clip, face_visible_ratio=0.8)
    ingestion = _make_ingestion_result()
    config = _make_config(str(tmp_path))

    # Pre-create composite
    output_dir = tmp_path / VIDEO_ID / "clips" / f"shorts-{clip.clip_index + 1}"
    output_dir.mkdir(parents=True)
    composite_path = output_dir / "composite.mp4"
    composite_path.write_bytes(b"existing")

    with patch("subprocess.run") as mock_run:
        result = process(clip, face_result, ingestion, config)
        mock_run.assert_not_called()

    assert result.composite_path == str(composite_path)


def test_process_idempotent_twice_same_result(tmp_path):
    """Running process() twice on the same clip yields identical results."""
    clip = _make_clip()
    face_result = _make_face_result(clip, face_visible_ratio=0.5)
    ingestion = _make_ingestion_result()
    config = _make_config(str(tmp_path))

    mock_proc = _mock_subprocess_success()

    def fake_run(*args, **kwargs):
        # Create the .tmp file so atomic rename succeeds
        cmd = args[0]
        for arg in cmd:
            if ".tmp." in arg:
                os.makedirs(os.path.dirname(arg), exist_ok=True)
                open(arg, "wb").close()
        return mock_proc

    with patch("subprocess.run", side_effect=fake_run):
        result1 = process(clip, face_result, ingestion, config)

    result2 = process(clip, face_result, ingestion, config)

    assert result1.clip_id == result2.clip_id
    assert result1.layout == result2.layout
    assert result1.composite_path == result2.composite_path
    assert result1.has_face == result2.has_face


# ---------------------------------------------------------------------------
# Determinism tests
# ---------------------------------------------------------------------------


def test_process_deterministic(tmp_path):
    """Same inputs produce identical CompositeStream output."""
    clip = _make_clip()
    face_result = _make_face_result(clip, face_visible_ratio=0.8)
    ingestion = _make_ingestion_result()
    config = _make_config(str(tmp_path))

    # Pre-create output to bypass FFmpeg
    output_dir = tmp_path / VIDEO_ID / "clips" / f"shorts-{clip.clip_index + 1}"
    output_dir.mkdir(parents=True)
    (output_dir / "composite.mp4").write_bytes(b"")

    result1 = process(clip, face_result, ingestion, config)
    result2 = process(clip, face_result, ingestion, config)

    assert result1 == result2


# ---------------------------------------------------------------------------
# Gameplay crop filter tests
# ---------------------------------------------------------------------------


def test_gameplay_crop_filter_contains_target_aspect_crop():
    """build_gameplay_crop_filter embeds a target-aspect crop expression."""
    f = build_gameplay_crop_filter("[0:v]", "[gameplay]", 1080, 1248)
    assert "crop=ih*0.8654:ih" in f
    assert "scale=1080:1248" in f
    assert f.startswith("[0:v]")
    assert f.endswith("[gameplay]")


def test_gameplay_crop_filter_full_frame():
    """build_gameplay_crop_filter works for full 1080×1920 output."""
    f = build_gameplay_crop_filter("[0:v]", "[v]", 1080, 1920)
    assert "scale=1080:1920" in f


def test_gameplay_crop_filter_excludes_face_region():
    """When face bbox is provided, crop avoids the face cam region."""
    bbox = _make_face_bbox(x=0.35, y=0.65, width=0.30, height=0.35)
    f = build_gameplay_crop_filter(
        "[gp_in]", "[gameplay]", 1080, 1248,
        bbox=bbox, src_width=1280, src_height=720,
    )
    # Should NOT use the generic center-crop expression
    assert "crop=ih*0.8654:ih" not in f
    assert "scale=1080:1248" in f
    assert f.startswith("[gp_in]")
    assert f.endswith("[gameplay]")


# ---------------------------------------------------------------------------
# Face crop tests
# ---------------------------------------------------------------------------


def test_compute_face_crop_params_center():
    """Face crop extracts the PiP bbox region with inward zoom trim."""
    bbox = _make_face_bbox(x=0.3, y=0.2, width=0.4, height=0.4)
    src_w, src_h = 1920, 1080

    crop_w, crop_h, crop_x, crop_y = compute_face_crop_params(bbox, src_w, src_h, zoom=1.2)

    # Zoom 1.2 trims ~8% per side → crop is ~83% of bbox pixel size
    raw_w = int(0.4 * src_w)  # 768
    raw_h = int(0.4 * src_h)  # 432
    # After zoom trim: each dim shrinks by (1 - 1/1.2) = ~17% total
    expected_w_approx = raw_w / 1.2
    expected_h_approx = raw_h / 1.2
    assert abs(crop_w - expected_w_approx) < 20
    assert abs(crop_h - expected_h_approx) < 20

    # Coordinates within bounds
    assert crop_x >= 0
    assert crop_y >= 0
    assert crop_x + crop_w <= src_w
    assert crop_y + crop_h <= src_h


def test_compute_face_crop_params_clamps_to_bounds():
    """Face crop is clamped when bbox is at the edge."""
    # Bbox at bottom-right corner
    bbox = _make_face_bbox(x=0.9, y=0.9, width=0.1, height=0.1)
    crop_w, crop_h, crop_x, crop_y = compute_face_crop_params(bbox, 1920, 1080)

    assert crop_x >= 0
    assert crop_y >= 0
    assert crop_x + crop_w <= 1920
    assert crop_y + crop_h <= 1080


def test_compute_face_crop_params_top_left_corner():
    """Face crop for tiny bbox at top-left stays near origin."""
    bbox = _make_face_bbox(x=0.0, y=0.0, width=0.05, height=0.05)
    crop_w, crop_h, crop_x, crop_y = compute_face_crop_params(bbox, 1920, 1080)

    # Crop should be near the top-left corner (within a small offset from zoom trim)
    assert crop_x < 20
    assert crop_y < 20
    # Still within bounds
    assert crop_x + crop_w <= 1920
    assert crop_y + crop_h <= 1080


def test_build_face_crop_filter_format():
    """build_face_crop_filter returns correctly formatted filter string."""
    bbox = _make_face_bbox()
    f = build_face_crop_filter("[0:v]", "[face]", bbox, 1920, 1080)

    assert f.startswith("[0:v]crop=")
    assert f"scale={FACE_REGION_WIDTH}:{FACE_REGION_HEIGHT}" in f
    assert f.endswith("[face]")


def test_face_crop_output_dimensions_in_filter():
    """Face crop filter targets 1080×672."""
    bbox = _make_face_bbox()
    f = build_face_crop_filter("[0:v]", "[face]", bbox, 1920, 1080)
    assert "scale=1080:672" in f


# ---------------------------------------------------------------------------
# PiP estimation tests
# ---------------------------------------------------------------------------


def test_estimate_pip_region_small_face_expands():
    """A small MediaPipe face bbox is expanded to PiP overlay size."""
    # Typical MediaPipe face: ~10% of frame
    face = _make_face_bbox(x=0.05, y=0.70, width=0.10, height=0.12)
    pip = estimate_pip_region(face, 1920, 1080)
    # PiP should be significantly larger than the raw face
    assert pip.width > face.width * 1.5
    assert pip.height > face.height * 1.5
    # PiP should still be within [0, 1] bounds
    assert pip.x >= 0.0
    assert pip.y >= 0.0
    assert pip.x + pip.width <= 1.001  # small float tolerance
    assert pip.y + pip.height <= 1.001


def test_estimate_pip_region_large_bbox_unchanged():
    """A bbox already PiP-sized is returned unchanged."""
    pip_bbox = _make_face_bbox(x=0.0, y=0.60, width=0.30, height=0.40)
    result = estimate_pip_region(pip_bbox, 1920, 1080)
    assert result.x == pip_bbox.x
    assert result.y == pip_bbox.y
    assert result.width == pip_bbox.width
    assert result.height == pip_bbox.height


def test_estimate_pip_region_snaps_to_edge():
    """PiP estimate near a frame edge snaps to that edge."""
    face = _make_face_bbox(x=0.03, y=0.75, width=0.08, height=0.10)
    pip = estimate_pip_region(face, 1920, 1080)
    # Should snap to left edge
    assert pip.x == 0.0


def test_estimate_pip_region_right_side_face():
    """A face on the right side produces a right-aligned PiP."""
    face = _make_face_bbox(x=0.85, y=0.70, width=0.10, height=0.12)
    pip = estimate_pip_region(face, 1920, 1080)
    # PiP should be right-aligned (near edge)
    assert pip.x + pip.width >= 0.9


# ---------------------------------------------------------------------------
# Fallback filter tests
# ---------------------------------------------------------------------------


def test_fallback_filter_contains_blurred_bg():
    """build_fallback_filter includes blurred background with overlay."""
    f = build_fallback_filter("[0:v]", "[v]", duration_seconds=35.0, fps=30)
    assert "boxblur" in f
    assert "overlay" in f
    assert "split=2" in f
    assert f"scale={1080}:{1920}" in f


def test_fallback_filter_frame_count():
    """build_fallback_filter produces a blurred background layout."""
    duration = 35.0
    fps = 30
    f = build_fallback_filter("[0:v]", "[v]", duration_seconds=duration, fps=fps)
    # No zoompan — replaced with blurred background
    assert "zoompan" not in f
    assert "boxblur" in f


def test_fallback_filter_simple_no_zoompan():
    """build_fallback_filter_simple uses blurred background, no zoompan."""
    f = build_fallback_filter_simple("[0:v]", "[v]")
    assert "zoompan" not in f
    assert "boxblur" in f
    assert "overlay" in f
    assert "crop=ih*9/16:ih" in f


# ---------------------------------------------------------------------------
# FFmpeg subprocess integration tests (mocked)
# ---------------------------------------------------------------------------


def test_process_calls_ffmpeg_for_split_layout(tmp_path):
    """process() invokes FFmpeg when face is visible (split layout)."""
    clip = _make_clip()
    face_result = _make_face_result(clip, face_visible_ratio=0.8)
    ingestion = _make_ingestion_result()
    config = _make_config(str(tmp_path))

    mock_proc = _mock_subprocess_success()

    def fake_run(*args, **kwargs):
        cmd = args[0]
        for arg in cmd:
            if ".tmp." in arg:
                os.makedirs(os.path.dirname(arg), exist_ok=True)
                open(arg, "wb").close()
        return mock_proc

    with patch("subprocess.run", side_effect=fake_run) as mock_run:
        result = process(clip, face_result, ingestion, config)

    assert mock_run.call_count >= 1
    called_cmd = mock_run.call_args_list[0][0][0]
    assert "ffmpeg" in called_cmd
    assert result.layout == "face_gameplay_split"


def test_process_calls_ffmpeg_for_fallback_layout(tmp_path):
    """process() invokes FFmpeg for fallback layout when gameplay_only is set."""
    clip = _make_clip()
    face_result = _make_face_result(clip, face_visible_ratio=0.1)
    ingestion = _make_ingestion_result()
    config = _make_config(str(tmp_path))
    config["compositor"] = {"default_layout": "gameplay_only"}

    mock_proc = _mock_subprocess_success()

    def fake_run(*args, **kwargs):
        cmd = args[0]
        for arg in cmd:
            if ".tmp." in arg:
                os.makedirs(os.path.dirname(arg), exist_ok=True)
                open(arg, "wb").close()
        return mock_proc

    with patch("subprocess.run", side_effect=fake_run) as mock_run:
        result = process(clip, face_result, ingestion, config)

    assert mock_run.call_count >= 1
    assert result.layout == "gameplay_only_zoom"


def test_process_retries_with_simpler_filters_on_failure(tmp_path):
    """process() retries with simpler filters when first FFmpeg call fails."""
    clip = _make_clip()
    face_result = _make_face_result(clip, face_visible_ratio=0.8)
    ingestion = _make_ingestion_result()
    config = _make_config(str(tmp_path))

    call_count = 0

    def fake_run(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        cmd = args[0]
        if call_count == 1:
            # First call fails
            m = MagicMock()
            m.returncode = 1
            m.stderr = "FFmpeg error"
            m.stdout = ""
            return m
        else:
            # Retry creates the .tmp file
            for arg in cmd:
                if ".tmp." in arg:
                    os.makedirs(os.path.dirname(arg), exist_ok=True)
                    open(arg, "wb").close()
            m = MagicMock()
            m.returncode = 0
            m.stderr = ""
            m.stdout = ""
            return m

    with patch("subprocess.run", side_effect=fake_run):
        result = process(clip, face_result, ingestion, config)

    assert call_count == 2
    assert result.layout == "face_gameplay_split"


def test_process_output_path_structure(tmp_path):
    """process() writes to output/{video_id}/clips/shorts-{N}/composite.mp4."""
    clip = _make_clip()
    face_result = _make_face_result(clip, face_visible_ratio=0.5)
    ingestion = _make_ingestion_result()
    config = _make_config(str(tmp_path))

    def fake_run(*args, **kwargs):
        cmd = args[0]
        for arg in cmd:
            if ".tmp." in arg:
                os.makedirs(os.path.dirname(arg), exist_ok=True)
                open(arg, "wb").close()
        m = MagicMock()
        m.returncode = 0
        m.stderr = ""
        m.stdout = ""
        return m

    with patch("subprocess.run", side_effect=fake_run):
        result = process(clip, face_result, ingestion, config)

    expected_suffix = os.path.join(VIDEO_ID, "clips", f"shorts-{clip.clip_index + 1}", "composite.mp4")
    assert result.composite_path.endswith(expected_suffix)


# ---------------------------------------------------------------------------
# Module boundary tests
# ---------------------------------------------------------------------------


def test_compositor_module_no_cross_module_imports():
    """compositor module must not import from other modules/."""
    import ast

    compositor_dir = os.path.join(
        os.path.dirname(__file__), "..", "..", "modules", "compositor"
    )
    compositor_dir = os.path.realpath(compositor_dir)

    for fname in sorted(os.listdir(compositor_dir)):
        if not fname.endswith(".py"):
            continue
        fpath = os.path.join(compositor_dir, fname)
        with open(fpath) as f:
            tree = ast.parse(f.read())
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                if isinstance(node, ast.ImportFrom) and node.module:
                    assert not node.module.startswith("modules."), (
                        f"{fname} imports from modules.*: {node.module}"
                    )
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        assert not alias.name.startswith("modules."), (
                            f"{fname} imports from modules.*: {alias.name}"
                        )


def test_compositor_init_uses_relative_imports():
    """modules/compositor/__init__.py must use relative imports."""
    init_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "modules", "compositor", "__init__.py"
    )
    init_path = os.path.realpath(init_path)
    with open(init_path) as f:
        source = f.read()
    import ast
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module and not node.module.startswith(".") and node.level == 0:
                # Only contracts/ and stdlib imports are allowed as absolute
                assert node.module.startswith("contracts") or "." not in node.module, (
                    f"__init__.py uses absolute module import: {node.module}"
                )


# ---------------------------------------------------------------------------
# Region-lock tests: bottom_middle must stay untouched, left-side uses center
# ---------------------------------------------------------------------------


def test_gameplay_crop_bottom_middle_unchanged():
    """bottom_middle PiP (cx ~0.5, cy ~0.8) must use the candidate-based
    face-aware crop — NOT the center-crop fast path."""
    bbox = _make_face_bbox(x=0.35, y=0.60, width=0.30, height=0.40)
    f = build_gameplay_crop_filter(
        "[gp_in]", "[gameplay]", 1080, 1248,
        bbox=bbox, src_width=1920, src_height=1080,
    )
    # The candidate-based path produces pixel-level crop=W:H:X:Y,
    # NOT the expression-based crop=ih*ASPECT:ih:(iw-...)/2:0
    assert "crop=ih*" not in f, (
        "bottom_middle should NOT use the center-crop fast path"
    )
    assert "scale=1080:1248" in f


def test_gameplay_crop_middle_left_uses_center():
    """middle_left PiP (cx ~0.15, cy ~0.5) must use strict center crop
    to capture the middle gameplay area."""
    bbox = _make_face_bbox(x=0.0, y=0.30, width=0.30, height=0.40)
    f = build_gameplay_crop_filter(
        "[gp_in]", "[gameplay]", 1080, 1248,
        bbox=bbox, src_width=1920, src_height=1080,
    )
    # Center-crop fast path uses expression-based crop
    assert "crop=ih*0.8654:ih:(iw-ih*0.8654)/2:0" in f
    assert "scale=1080:1248" in f


def test_gameplay_crop_upper_middle_left_uses_center():
    """upper_middle_left PiP (cx ~0.15, cy ~0.3) must also use center crop."""
    bbox = _make_face_bbox(x=0.0, y=0.10, width=0.30, height=0.40)
    f = build_gameplay_crop_filter(
        "[gp_in]", "[gameplay]", 1080, 1248,
        bbox=bbox, src_width=1280, src_height=720,
    )
    assert "crop=ih*0.8654:ih:(iw-ih*0.8654)/2:0" in f
    assert "scale=1080:1248" in f


def test_gameplay_crop_bottom_left_uses_center():
    """bottom_left PiP (cx ~0.15, cy ~0.8) must also use center crop."""
    bbox = _make_face_bbox(x=0.0, y=0.60, width=0.30, height=0.40)
    f = build_gameplay_crop_filter(
        "[gp_in]", "[gameplay]", 1080, 1248,
        bbox=bbox, src_width=1920, src_height=1080,
    )
    assert "crop=ih*0.8654:ih:(iw-ih*0.8654)/2:0" in f
    assert "scale=1080:1248" in f


# ---------------------------------------------------------------------------
# Manual face_region consistency tests (compose.py lock)
# ---------------------------------------------------------------------------


def test_process_manual_middle_left_ignores_bad_detection(tmp_path):
    """When face_region='middle_left', compose must use the fixed region
    coordinates even when MediaPipe detected_bbox points elsewhere."""
    clip = _make_clip()
    # Face detection returned a WRONG bbox (far right, top) — must be ignored
    bad_bbox = _make_face_bbox(x=0.70, y=0.0, width=0.30, height=0.40)
    scene_data = tuple(
        SceneFaceData(
            scene_id=s.scene_id,
            face_visible_ratio=0.9,
            bounding_boxes=(bad_bbox,),
            average_bbox=bad_bbox,
            sample_count=10,
        )
        for s in clip.scenes
    )
    face_result = FaceDetectionResult(
        video_id=VIDEO_ID,
        scene_data=scene_data,
        average_visibility=0.9,
        faceless_scene_count=0,
    )
    ingestion = _make_ingestion_result()
    config = _make_config(str(tmp_path))
    config["compositor"] = {"face_region": "middle_left"}

    mock_proc = _mock_subprocess_success()

    def fake_run(*args, **kwargs):
        cmd = args[0]
        for arg in cmd:
            if ".tmp." in arg:
                os.makedirs(os.path.dirname(arg), exist_ok=True)
                open(arg, "wb").close()
        return mock_proc

    with patch("subprocess.run", side_effect=fake_run):
        result = process(clip, face_result, ingestion, config)

    assert result.layout == "face_gameplay_split"
    assert result.has_face is True


def test_process_manual_bottom_middle_ignores_missing_detection(tmp_path):
    """When face_region='bottom_middle', compose must use the fixed region
    even when per-scene detection returned no bboxes at all."""
    clip = _make_clip()
    # Face detection returned NO bboxes — must still use bottom_middle
    scene_data = tuple(
        SceneFaceData(
            scene_id=s.scene_id,
            face_visible_ratio=0.9,
            bounding_boxes=(),
            average_bbox=None,
            sample_count=10,
        )
        for s in clip.scenes
    )
    face_result = FaceDetectionResult(
        video_id=VIDEO_ID,
        scene_data=scene_data,
        average_visibility=0.9,
        faceless_scene_count=0,
    )
    ingestion = _make_ingestion_result()
    config = _make_config(str(tmp_path))
    config["compositor"] = {"face_region": "bottom_middle"}

    mock_proc = _mock_subprocess_success()

    def fake_run(*args, **kwargs):
        cmd = args[0]
        for arg in cmd:
            if ".tmp." in arg:
                os.makedirs(os.path.dirname(arg), exist_ok=True)
                open(arg, "wb").close()
        return mock_proc

    with patch("subprocess.run", side_effect=fake_run):
        result = process(clip, face_result, ingestion, config)

    assert result.layout == "face_gameplay_split"
    assert result.has_face is True
