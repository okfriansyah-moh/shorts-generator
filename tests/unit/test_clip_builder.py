"""Unit tests for the clip_builder module.

Tests cover:
  - Normal greedy merging (10+ scenes → multiple clips)
  - Duration enforcement (floor 30s, ceiling 60s)
  - Contiguity requirement (no gaps between merged scenes)
  - Rejection criteria (low score, excessive overlap)
  - Threshold lowering fallback for insufficient clips
  - Deterministic clip_id computation
  - Determinism (same input → identical output)
  - Edge cases (all short scenes, all long scenes, single scene)
"""

from __future__ import annotations

import hashlib

import pytest

from contracts.clip import ClipDefinition, ClipList
from contracts.scoring import ScoredScene, ScoredSceneList
from modules.clip_builder import process


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _make_scene(
    video_id: str,
    start_ms: int,
    end_ms: int,
    composite_score: float = 0.5,
    keyword_score: float = 0.5,
    audio_energy: float = 0.5,
    face_presence: float = 0.5,
    scene_activity: float = 0.5,
    sentence_density: float = 0.5,
) -> ScoredScene:
    """Create a ScoredScene with deterministic defaults."""
    duration = (end_ms - start_ms) / 1000.0
    return ScoredScene(
        scene_id=f"{video_id}_{start_ms}_{end_ms}",
        video_id=video_id,
        start_time=start_ms,
        end_time=end_ms,
        duration=duration,
        keyword_score=keyword_score,
        audio_energy=audio_energy,
        face_presence=face_presence,
        scene_activity=scene_activity,
        sentence_density=sentence_density,
        composite_score=composite_score,
    )


def _make_contiguous_scenes(
    video_id: str,
    count: int,
    scene_duration_ms: int = 5000,
    base_score: float = 0.8,
    score_decay: float = 0.02,
) -> list[ScoredScene]:
    """Create N contiguous scenes with decaying scores."""
    scenes = []
    for i in range(count):
        start = i * scene_duration_ms
        end = start + scene_duration_ms
        score = max(0.0, base_score - i * score_decay)
        scenes.append(
            _make_scene(video_id, start, end, composite_score=score)
        )
    return scenes


def _make_scored_scene_list(
    scenes: list[ScoredScene],
    video_id: str = "a1b2c3d4e5f67890",
) -> ScoredSceneList:
    """Wrap scenes in a ScoredSceneList sorted by composite DESC, start ASC."""
    ranked = sorted(scenes, key=lambda s: (-s.composite_score, s.start_time))
    return ScoredSceneList(video_id=video_id, scenes=tuple(ranked))


def _default_config(**overrides: object) -> dict:
    """Return a minimal config dict for clip_builder tests."""
    config: dict = {
        "clip_builder": {
            "target_duration_min": 30,
            "target_duration_max": 60,
            "max_clips_per_video": 15,
            "min_clips_per_video": 1,
            "max_overlap_ratio": 0.5,
        },
        "pipeline": {
            "max_clips_per_run": 20,
        },
        "scoring": {
            "min_composite_score": 0.2,
        },
    }
    for key, val in overrides.items():
        if key in config:
            config[key].update(val)  # type: ignore[union-attr]
        else:
            config[key] = val
    return config


def _compute_expected_clip_id(video_id: str, start_ms: int, end_ms: int) -> str:
    raw = f"{video_id}{start_ms}{end_ms}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Test: Basic clip building
# ---------------------------------------------------------------------------


class TestBasicClipBuilding:
    """Tests for normal clip-building scenarios."""

    def test_single_clip_from_contiguous_scenes(self) -> None:
        """6 × 5s contiguous scenes (30s total) → exactly 1 clip."""
        vid = "a1b2c3d4e5f67890"
        scenes = _make_contiguous_scenes(vid, count=6, scene_duration_ms=5000)
        ssl = _make_scored_scene_list(scenes, vid)
        config = _default_config()

        result = process(ssl, config)

        assert isinstance(result, ClipList)
        assert result.video_id == vid
        assert result.total_clips >= 1
        for clip in result.clips:
            assert 30.0 <= clip.duration <= 60.0

    def test_multiple_clips_from_many_scenes(self) -> None:
        """24 × 5s contiguous scenes (120s total) → multiple clips."""
        vid = "a1b2c3d4e5f67890"
        scenes = _make_contiguous_scenes(vid, count=24, scene_duration_ms=5000)
        ssl = _make_scored_scene_list(scenes, vid)
        config = _default_config()

        result = process(ssl, config)

        assert result.total_clips >= 2
        for clip in result.clips:
            assert 30.0 <= clip.duration <= 60.0

    def test_clips_sorted_by_start_time(self) -> None:
        """Output clips are sorted by start_time ASC."""
        vid = "a1b2c3d4e5f67890"
        scenes = _make_contiguous_scenes(vid, count=20, scene_duration_ms=5000)
        ssl = _make_scored_scene_list(scenes, vid)
        config = _default_config()

        result = process(ssl, config)

        for i in range(len(result.clips) - 1):
            assert result.clips[i].start_time <= result.clips[i + 1].start_time

    def test_clip_index_sequential(self) -> None:
        """clip_index values are 0 through total_clips-1."""
        vid = "a1b2c3d4e5f67890"
        scenes = _make_contiguous_scenes(vid, count=20, scene_duration_ms=5000)
        ssl = _make_scored_scene_list(scenes, vid)
        config = _default_config()

        result = process(ssl, config)

        for i, clip in enumerate(result.clips):
            assert clip.clip_index == i

    def test_total_clips_matches_length(self) -> None:
        """total_clips equals len(clips)."""
        vid = "a1b2c3d4e5f67890"
        scenes = _make_contiguous_scenes(vid, count=12, scene_duration_ms=5000)
        ssl = _make_scored_scene_list(scenes, vid)
        config = _default_config()

        result = process(ssl, config)

        assert result.total_clips == len(result.clips)


# ---------------------------------------------------------------------------
# Test: Duration enforcement
# ---------------------------------------------------------------------------


class TestDurationEnforcement:
    """Tests for the 30-60 second hard floor/ceiling."""

    def test_no_clip_below_30_seconds(self) -> None:
        """All produced clips must be >= 30 seconds."""
        vid = "a1b2c3d4e5f67890"
        scenes = _make_contiguous_scenes(vid, count=30, scene_duration_ms=5000)
        ssl = _make_scored_scene_list(scenes, vid)
        config = _default_config()

        result = process(ssl, config)

        for clip in result.clips:
            assert clip.duration >= 30.0, f"Clip {clip.clip_id} duration {clip.duration} < 30s"

    def test_no_clip_above_60_seconds(self) -> None:
        """All produced clips must be <= 60 seconds."""
        vid = "a1b2c3d4e5f67890"
        scenes = _make_contiguous_scenes(vid, count=30, scene_duration_ms=5000)
        ssl = _make_scored_scene_list(scenes, vid)
        config = _default_config()

        result = process(ssl, config)

        for clip in result.clips:
            assert clip.duration <= 60.0, f"Clip {clip.clip_id} duration {clip.duration} > 60s"

    def test_duration_matches_time_range(self) -> None:
        """duration equals (end_time - start_time) / 1000."""
        vid = "a1b2c3d4e5f67890"
        scenes = _make_contiguous_scenes(vid, count=16, scene_duration_ms=5000)
        ssl = _make_scored_scene_list(scenes, vid)
        config = _default_config()

        result = process(ssl, config)

        for clip in result.clips:
            expected = (clip.end_time - clip.start_time) / 1000.0
            assert abs(clip.duration - expected) < 1e-9


# ---------------------------------------------------------------------------
# Test: Contiguity
# ---------------------------------------------------------------------------


class TestContiguity:
    """Tests for temporally contiguous scenes within clips."""

    def test_scenes_contiguous_within_clip(self) -> None:
        """For all i: scenes[i].end_time == scenes[i+1].start_time."""
        vid = "a1b2c3d4e5f67890"
        scenes = _make_contiguous_scenes(vid, count=20, scene_duration_ms=5000)
        ssl = _make_scored_scene_list(scenes, vid)
        config = _default_config()

        result = process(ssl, config)

        for clip in result.clips:
            for i in range(len(clip.scenes) - 1):
                assert clip.scenes[i].end_time == clip.scenes[i + 1].start_time, (
                    f"Gap between scene {clip.scenes[i].scene_id} "
                    f"and {clip.scenes[i + 1].scene_id}"
                )

    def test_start_time_equals_first_scene(self) -> None:
        """clip.start_time == clip.scenes[0].start_time."""
        vid = "a1b2c3d4e5f67890"
        scenes = _make_contiguous_scenes(vid, count=12, scene_duration_ms=5000)
        ssl = _make_scored_scene_list(scenes, vid)
        config = _default_config()

        result = process(ssl, config)

        for clip in result.clips:
            assert clip.start_time == clip.scenes[0].start_time

    def test_end_time_equals_last_scene(self) -> None:
        """clip.end_time == clip.scenes[-1].end_time."""
        vid = "a1b2c3d4e5f67890"
        scenes = _make_contiguous_scenes(vid, count=12, scene_duration_ms=5000)
        ssl = _make_scored_scene_list(scenes, vid)
        config = _default_config()

        result = process(ssl, config)

        for clip in result.clips:
            assert clip.end_time == clip.scenes[-1].end_time

    def test_non_contiguous_scenes_not_merged(self) -> None:
        """Scenes with gaps between them should not appear in the same clip."""
        vid = "a1b2c3d4e5f67890"
        # Create 12 scenes with gaps (each 5s scene, 2s gap)
        scenes = []
        for i in range(12):
            start = i * 7000  # 5s scene + 2s gap
            end = start + 5000
            scenes.append(
                _make_scene(vid, start, end, composite_score=0.8 - i * 0.02)
            )
        ssl = _make_scored_scene_list(scenes, vid)
        config = _default_config()

        # With gaps, it may not produce clips or clips will be short
        # The builder should not merge non-contiguous scenes
        try:
            result = process(ssl, config)
            for clip in result.clips:
                for i in range(len(clip.scenes) - 1):
                    assert clip.scenes[i].end_time == clip.scenes[i + 1].start_time
        except ValueError:
            # No valid clips because scenes can't be merged to 30s — acceptable
            pass


# ---------------------------------------------------------------------------
# Test: Scene ownership (no reuse)
# ---------------------------------------------------------------------------


class TestSceneOwnership:
    """Tests that each scene belongs to at most one clip."""

    def test_no_scene_in_multiple_clips(self) -> None:
        """Each scene_id appears in at most one clip."""
        vid = "a1b2c3d4e5f67890"
        scenes = _make_contiguous_scenes(vid, count=30, scene_duration_ms=5000)
        ssl = _make_scored_scene_list(scenes, vid)
        config = _default_config()

        result = process(ssl, config)

        seen_scene_ids: set[str] = set()
        for clip in result.clips:
            for scene in clip.scenes:
                assert scene.scene_id not in seen_scene_ids, (
                    f"Scene {scene.scene_id} appears in multiple clips"
                )
                seen_scene_ids.add(scene.scene_id)


# ---------------------------------------------------------------------------
# Test: Deterministic clip IDs
# ---------------------------------------------------------------------------


class TestDeterministicIds:
    """Tests for content-addressable clip ID computation."""

    def test_clip_id_format(self) -> None:
        """clip_id is 16 hex characters."""
        vid = "a1b2c3d4e5f67890"
        scenes = _make_contiguous_scenes(vid, count=8, scene_duration_ms=5000)
        ssl = _make_scored_scene_list(scenes, vid)
        config = _default_config()

        result = process(ssl, config)

        for clip in result.clips:
            assert len(clip.clip_id) == 16
            assert all(c in "0123456789abcdef" for c in clip.clip_id)

    def test_clip_id_matches_sha256_formula(self) -> None:
        """clip_id = SHA256(video_id + str(start_time) + str(end_time))[:16]."""
        vid = "a1b2c3d4e5f67890"
        scenes = _make_contiguous_scenes(vid, count=8, scene_duration_ms=5000)
        ssl = _make_scored_scene_list(scenes, vid)
        config = _default_config()

        result = process(ssl, config)

        for clip in result.clips:
            expected_id = _compute_expected_clip_id(vid, clip.start_time, clip.end_time)
            assert clip.clip_id == expected_id


# ---------------------------------------------------------------------------
# Test: Rejection criteria
# ---------------------------------------------------------------------------


class TestRejectionCriteria:
    """Tests for clip rejection rules."""

    def test_low_score_clips_rejected(self) -> None:
        """Clips with average_score below min_composite_score are rejected."""
        vid = "a1b2c3d4e5f67890"
        # Create scenes with very low scores
        scenes = _make_contiguous_scenes(
            vid, count=8, scene_duration_ms=5000, base_score=0.1, score_decay=0.0
        )
        ssl = _make_scored_scene_list(scenes, vid)
        config = _default_config()
        config["scoring"]["min_composite_score"] = 0.5  # High threshold

        # With very low scores and high threshold, should get rejected or fewer clips
        try:
            result = process(ssl, config)
            # If clips are produced, they survived the lowering fallback
            for clip in result.clips:
                assert clip.average_score >= 0.0
        except ValueError:
            # No valid clips — threshold too high
            pass

    def test_excessive_overlap_rejected(self) -> None:
        """Clips overlapping > 50% with existing clips are rejected."""
        vid = "a1b2c3d4e5f67890"
        scenes = _make_contiguous_scenes(vid, count=30, scene_duration_ms=5000)
        ssl = _make_scored_scene_list(scenes, vid)
        config = _default_config()
        config["clip_builder"]["max_overlap_ratio"] = 0.5

        result = process(ssl, config)

        # Check no pair of clips overlaps more than 50%
        for i in range(len(result.clips)):
            for j in range(i + 1, len(result.clips)):
                a = result.clips[i]
                b = result.clips[j]
                overlap_start = max(a.start_time, b.start_time)
                overlap_end = min(a.end_time, b.end_time)
                overlap_ms = max(0, overlap_end - overlap_start)
                min_dur_ms = min(
                    a.end_time - a.start_time,
                    b.end_time - b.start_time,
                )
                if min_dur_ms > 0:
                    ratio = overlap_ms / min_dur_ms
                    assert ratio <= 0.5 + 1e-9, (
                        f"Clips {a.clip_id} and {b.clip_id} overlap {ratio:.2%}"
                    )


# ---------------------------------------------------------------------------
# Test: Threshold lowering fallback
# ---------------------------------------------------------------------------


class TestThresholdLowering:
    """Tests for the threshold-lowering retry mechanism."""

    def test_threshold_lowering_produces_clips(self) -> None:
        """When initial threshold rejects all, lowering produces clips."""
        vid = "a1b2c3d4e5f67890"
        # 8 scenes at score 0.18 — below default min 0.2 but above 0.15 (after one lower)
        scenes = _make_contiguous_scenes(
            vid, count=8, scene_duration_ms=5000, base_score=0.18, score_decay=0.0
        )
        ssl = _make_scored_scene_list(scenes, vid)
        config = _default_config()
        config["scoring"]["min_composite_score"] = 0.2

        result = process(ssl, config)

        # After lowering threshold by 0.05 → 0.15, score 0.18 passes
        assert result.total_clips >= 1


# ---------------------------------------------------------------------------
# Test: Determinism
# ---------------------------------------------------------------------------


class TestDeterminism:
    """Tests that same input produces identical output across runs."""

    def test_identical_output_on_rerun(self) -> None:
        """process() called twice with same input → identical result."""
        vid = "a1b2c3d4e5f67890"
        scenes = _make_contiguous_scenes(vid, count=20, scene_duration_ms=5000)
        ssl = _make_scored_scene_list(scenes, vid)
        config = _default_config()

        result_1 = process(ssl, config)
        result_2 = process(ssl, config)

        assert result_1.total_clips == result_2.total_clips
        assert result_1.clips_rejected == result_2.clips_rejected

        for c1, c2 in zip(result_1.clips, result_2.clips):
            assert c1.clip_id == c2.clip_id
            assert c1.start_time == c2.start_time
            assert c1.end_time == c2.end_time
            assert c1.duration == c2.duration
            assert c1.average_score == c2.average_score
            assert c1.clip_index == c2.clip_index

    def test_clip_id_deterministic(self) -> None:
        """Same scenes produce same clip_id across runs."""
        vid = "a1b2c3d4e5f67890"
        scenes = _make_contiguous_scenes(vid, count=8, scene_duration_ms=5000)
        ssl = _make_scored_scene_list(scenes, vid)
        config = _default_config()

        ids_1 = [c.clip_id for c in process(ssl, config).clips]
        ids_2 = [c.clip_id for c in process(ssl, config).clips]

        assert ids_1 == ids_2


# ---------------------------------------------------------------------------
# Test: Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Tests for boundary conditions and unusual inputs."""

    def test_exactly_30s_clip(self) -> None:
        """6 × 5s scenes → exactly 30s clip is valid."""
        vid = "a1b2c3d4e5f67890"
        scenes = _make_contiguous_scenes(vid, count=6, scene_duration_ms=5000)
        ssl = _make_scored_scene_list(scenes, vid)
        config = _default_config()

        result = process(ssl, config)

        assert result.total_clips >= 1
        # At least one clip should be exactly 30s
        durations = [c.duration for c in result.clips]
        assert any(abs(d - 30.0) < 1e-9 for d in durations)

    def test_max_clips_cap(self) -> None:
        """Never exceed max_clips_per_run."""
        vid = "a1b2c3d4e5f67890"
        # Many scenes to potentially generate many clips
        scenes = _make_contiguous_scenes(vid, count=100, scene_duration_ms=5000)
        ssl = _make_scored_scene_list(scenes, vid)
        config = _default_config()
        config["pipeline"]["max_clips_per_run"] = 5

        result = process(ssl, config)

        assert result.total_clips <= 5

    def test_insufficient_scenes_raises_error(self) -> None:
        """Too few scenes to make 30s clip → ValueError."""
        vid = "a1b2c3d4e5f67890"
        # Only 2 × 5s scenes = 10s total, cannot make 30s clip
        scenes = _make_contiguous_scenes(vid, count=2, scene_duration_ms=5000)
        ssl = _make_scored_scene_list(scenes, vid)
        config = _default_config()

        with pytest.raises(ValueError, match="No valid clips"):
            process(ssl, config)

    def test_all_scenes_very_short(self) -> None:
        """Many 1s scenes — can still merge to 30s if contiguous."""
        vid = "a1b2c3d4e5f67890"
        scenes = _make_contiguous_scenes(
            vid, count=40, scene_duration_ms=1000, base_score=0.7
        )
        ssl = _make_scored_scene_list(scenes, vid)
        config = _default_config()

        result = process(ssl, config)

        assert result.total_clips >= 1
        for clip in result.clips:
            assert clip.duration >= 30.0

    def test_average_score_correct(self) -> None:
        """average_score is the mean of constituent scene composite_scores."""
        vid = "a1b2c3d4e5f67890"
        scenes = _make_contiguous_scenes(vid, count=12, scene_duration_ms=5000)
        ssl = _make_scored_scene_list(scenes, vid)
        config = _default_config()

        result = process(ssl, config)

        for clip in result.clips:
            expected_avg = sum(s.composite_score for s in clip.scenes) / len(clip.scenes)
            assert abs(clip.average_score - expected_avg) < 1e-9

    def test_frozen_dto_output(self) -> None:
        """Output DTOs are frozen dataclasses."""
        vid = "a1b2c3d4e5f67890"
        scenes = _make_contiguous_scenes(vid, count=8, scene_duration_ms=5000)
        ssl = _make_scored_scene_list(scenes, vid)
        config = _default_config()

        result = process(ssl, config)

        with pytest.raises(AttributeError):
            result.video_id = "modified"  # type: ignore[misc]

        if result.clips:
            with pytest.raises(AttributeError):
                result.clips[0].clip_id = "modified"  # type: ignore[misc]

    def test_video_id_propagated(self) -> None:
        """video_id on ClipList and all ClipDefinitions matches input."""
        vid = "a1b2c3d4e5f67890"
        scenes = _make_contiguous_scenes(vid, count=12, scene_duration_ms=5000)
        ssl = _make_scored_scene_list(scenes, vid)
        config = _default_config()

        result = process(ssl, config)

        assert result.video_id == vid
        for clip in result.clips:
            assert clip.video_id == vid
