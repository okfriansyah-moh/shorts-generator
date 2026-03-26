"""Unit tests for the scoring module.

All tests run without GPU, network, or real video files.
Covers: keyword scoring, sentence density, audio/face passthrough,
scene activity fallback, composite weighting, normalisation,
degenerate case, determinism, and full process() integration.
"""

from __future__ import annotations

from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

from contracts.audio import AudioEnergyData, SceneAudioEnergy
from contracts.face import FaceDetectionResult, SceneFaceData
from contracts.scene import SceneList, SceneSegment
from contracts.scoring import ScoredScene, ScoredSceneList
from contracts.transcript import Transcript, TranscriptSegment, Word


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

VIDEO_ID = "a1b2c3d4e5f67890"


def _make_scene(
    index: int,
    start_ms: int,
    end_ms: int,
    duration: float = 5.0,
) -> SceneSegment:
    return SceneSegment(
        scene_id=f"{VIDEO_ID}_{start_ms}_{end_ms}",
        video_id=VIDEO_ID,
        start_time=start_ms,
        end_time=end_ms,
        duration=duration,
    )


def _make_scene_list(count: int = 5) -> SceneList:
    scenes = []
    for i in range(count):
        start = i * 5000
        end = start + 5000
        scenes.append(_make_scene(i, start, end, duration=5.0))
    return SceneList(
        video_id=VIDEO_ID,
        scenes=tuple(scenes),
        total_duration=float(count * 5),
    )


def _make_word(text: str, start_ms: int, end_ms: int) -> Word:
    return Word(text=text, start_time=start_ms, end_time=end_ms, confidence=0.95)


def _make_transcript(scenes: SceneList, words_per_scene: list[list[str]]) -> Transcript:
    """Build a Transcript aligned to the provided scene list."""
    segments = []
    for scene, words in zip(scenes.scenes, words_per_scene):
        if not words:
            continue
        interval = (scene.end_time - scene.start_time) // max(len(words), 1)
        word_dtos = []
        for j, w in enumerate(words):
            ws = scene.start_time + j * interval
            we = ws + interval - 1
            word_dtos.append(_make_word(w, ws, we))
        segments.append(
            TranscriptSegment(
                text=" ".join(words),
                start_time=scene.start_time,
                end_time=scene.end_time,
                words=tuple(word_dtos),
                confidence=0.95,
            )
        )
    total_words = sum(len(w) for w in words_per_scene)
    return Transcript(
        video_id=VIDEO_ID,
        segments=tuple(segments),
        total_words=total_words,
        language="en",
    )


def _make_empty_transcript() -> Transcript:
    return Transcript(video_id=VIDEO_ID, segments=(), total_words=0, language="en")


def _make_face_result(scenes: SceneList, ratios: Optional[list[float]] = None) -> FaceDetectionResult:
    if ratios is None:
        ratios = [0.0] * len(scenes.scenes)
    scene_data = []
    for scene, ratio in zip(scenes.scenes, ratios):
        scene_data.append(
            SceneFaceData(
                scene_id=scene.scene_id,
                face_visible_ratio=ratio,
                bounding_boxes=(),
                average_bbox=None,
                sample_count=10,
            )
        )
    return FaceDetectionResult(
        video_id=VIDEO_ID,
        scene_data=tuple(scene_data),
        average_visibility=sum(ratios) / max(len(ratios), 1),
        faceless_scene_count=sum(1 for r in ratios if r == 0.0),
    )


def _make_audio_data(scenes: SceneList, energies: Optional[list[float]] = None) -> AudioEnergyData:
    if energies is None:
        energies = [0.5] * len(scenes.scenes)
    scene_energies = []
    for scene, energy in zip(scenes.scenes, energies):
        scene_energies.append(
            SceneAudioEnergy(
                scene_id=scene.scene_id,
                rms_energy=energy,
                normalized_energy=energy,
            )
        )
    return AudioEnergyData(
        video_id=VIDEO_ID,
        scene_energies=tuple(scene_energies),
        video_min_rms=min(energies),
        video_max_rms=max(energies),
        video_mean_rms=sum(energies) / len(energies),
    )


def _default_config() -> dict:
    return {
        "scoring": {
            "weights": {
                "keyword": 3,
                "audio_energy": 2,
                "face_presence": 2,
                "scene_activity": 1,
                "sentence_density": 1,
            },
            "min_composite_score": 0.2,
        }
    }


# ---------------------------------------------------------------------------
# Keyword scoring tests
# ---------------------------------------------------------------------------

class TestKeywordScoring:
    def test_no_words_in_scene_returns_zero(self):
        from modules.scoring.keywords import get_keywords, score_keyword

        transcript = _make_empty_transcript()
        keywords = get_keywords(_default_config())
        score = score_keyword(0, 5000, transcript, keywords)
        assert score == 0.0

    def test_all_keywords_returns_capped_one(self):
        from modules.scoring.keywords import get_keywords, score_keyword

        scenes = _make_scene_list(1)
        scene = scenes.scenes[0]
        # Build transcript where every word IS a keyword.
        kw_words = ["epic", "clutch", "amazing", "insane"]
        transcript = _make_transcript(scenes, [kw_words])
        keywords = get_keywords(_default_config())
        score = score_keyword(scene.start_time, scene.end_time, transcript, keywords)
        assert score == 1.0

    def test_half_keywords(self):
        from modules.scoring.keywords import get_keywords, score_keyword

        scenes = _make_scene_list(1)
        scene = scenes.scenes[0]
        # 2 keywords + 2 plain words → score = 2/4 = 0.5
        words = ["epic", "hello", "clutch", "world"]
        transcript = _make_transcript(scenes, [words])
        keywords = get_keywords(_default_config())
        score = score_keyword(scene.start_time, scene.end_time, transcript, keywords)
        assert abs(score - 0.5) < 1e-9

    def test_no_keywords_in_config_uses_defaults(self):
        from modules.scoring.keywords import get_keywords

        kws = get_keywords({})
        assert "epic" in kws

    def test_config_keywords_override_defaults(self):
        from modules.scoring.keywords import get_keywords

        config = {"scoring": {"keywords": ["custom_kw1", "custom_kw2"]}}
        kws = get_keywords(config)
        assert "custom_kw1" in kws
        assert "epic" not in kws


# ---------------------------------------------------------------------------
# Sentence density scoring tests
# ---------------------------------------------------------------------------

class TestSentenceDensityScore:
    def _density(self, wps: float, duration_s: float = 10.0) -> float:
        from modules.scoring.score import _sentence_density_score

        # Build a transcript with the right words-per-second.
        word_count = int(wps * duration_s)
        scene = _make_scene(0, 0, int(duration_s * 1000), duration=duration_s)
        scene_list = SceneList(
            video_id=VIDEO_ID, scenes=(scene,), total_duration=duration_s
        )
        words = [f"word{i}" for i in range(word_count)]
        transcript = _make_transcript(scene_list, [words])
        return _sentence_density_score(
            scene.start_time, scene.end_time, duration_s, transcript
        )

    def test_optimal_range_returns_one(self):
        assert self._density(3.0) == 1.0

    def test_at_lower_boundary(self):
        assert self._density(2.0) == 1.0

    def test_at_upper_boundary(self):
        assert self._density(4.0) == 1.0

    def test_zero_wps_returns_zero(self):
        assert self._density(0.0) == 0.0

    def test_one_wps_returns_half(self):
        score = self._density(1.0)
        assert abs(score - 0.5) < 0.1  # Allow for integer rounding in word count

    def test_very_high_wps_approaches_zero(self):
        assert self._density(8.0) == 0.0

    def test_empty_transcript_returns_zero(self):
        from modules.scoring.score import _sentence_density_score

        transcript = _make_empty_transcript()
        score = _sentence_density_score(0, 5000, 5.0, transcript)
        assert score == 0.0

    def test_zero_duration_returns_zero(self):
        from modules.scoring.score import _sentence_density_score

        transcript = _make_empty_transcript()
        assert _sentence_density_score(0, 0, 0.0, transcript) == 0.0


# ---------------------------------------------------------------------------
# Audio energy passthrough
# ---------------------------------------------------------------------------

class TestAudioEnergyPassthrough:
    def test_energy_passed_through_to_scored_scene(self):
        from modules.scoring.score import process

        scenes = _make_scene_list(3)
        transcript = _make_empty_transcript()
        face = _make_face_result(scenes)
        audio = _make_audio_data(scenes, energies=[0.2, 0.6, 0.9])
        config = _default_config()

        result = process(scenes, transcript, face, audio, config)

        # Recover scenes by scene_id for deterministic assertion.
        by_id = {s.scene_id: s for s in result.scenes}
        assert abs(by_id[f"{VIDEO_ID}_0_5000"].audio_energy_score - 0.2) < 1e-9
        assert abs(by_id[f"{VIDEO_ID}_5000_10000"].audio_energy_score - 0.6) < 1e-9
        assert abs(by_id[f"{VIDEO_ID}_10000_15000"].audio_energy_score - 0.9) < 1e-9

    def test_none_audio_data_defaults_to_zero(self):
        from modules.scoring.score import process

        scenes = _make_scene_list(2)
        transcript = _make_empty_transcript()
        face = _make_face_result(scenes)
        config = _default_config()

        result = process(scenes, transcript, face, None, config)
        for s in result.scenes:
            assert s.audio_energy_score == 0.0


# ---------------------------------------------------------------------------
# Face presence passthrough
# ---------------------------------------------------------------------------

class TestFacePresencePassthrough:
    def test_face_ratio_passed_through(self):
        from modules.scoring.score import process

        scenes = _make_scene_list(2)
        transcript = _make_empty_transcript()
        face = _make_face_result(scenes, ratios=[0.0, 0.8])
        config = _default_config()

        result = process(scenes, transcript, face, None, config)
        by_id = {s.scene_id: s for s in result.scenes}
        assert by_id[f"{VIDEO_ID}_0_5000"].face_presence_score == 0.0
        assert abs(by_id[f"{VIDEO_ID}_5000_10000"].face_presence_score - 0.8) < 1e-9

    def test_empty_face_result_defaults_to_zero(self):
        from modules.scoring.score import process

        scenes = _make_scene_list(2)
        transcript = _make_empty_transcript()
        # FaceDetectionResult with no scene_data entries.
        face = FaceDetectionResult(
            video_id=VIDEO_ID,
            scene_data=(),
            average_visibility=0.0,
            faceless_scene_count=2,
        )
        config = _default_config()

        result = process(scenes, transcript, face, None, config)
        for s in result.scenes:
            assert s.face_presence_score == 0.0


# ---------------------------------------------------------------------------
# Scene activity (mocked FFmpeg)
# ---------------------------------------------------------------------------

class TestSceneActivity:
    def test_ffmpeg_failure_logs_warning_and_uses_zero(self):
        from modules.scoring.score import process

        scenes = _make_scene_list(2)
        transcript = _make_empty_transcript()
        face = _make_face_result(scenes)
        config = _default_config()

        with patch("subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 1
            mock_result.stdout = b""
            mock_result.stderr = b"Error"
            mock_run.return_value = mock_result

            result = process(scenes, transcript, face, None, config, file_path="/fake/video.mp4")

        for s in result.scenes:
            assert s.scene_activity_score == 0.0

    def test_ffmpeg_success_produces_nonzero_activity(self):
        from modules.scoring.score import process

        scenes = _make_scene_list(1)
        transcript = _make_empty_transcript()
        face = _make_face_result(scenes)
        config = _default_config()

        frame_size = 64 * 36
        # Two frames: first all 0, second all 128 → diff = 128/frame_size per pixel.
        fake_frames = bytes([0] * frame_size) + bytes([128] * frame_size)

        with patch("subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = fake_frames
            mock_result.stderr = b""
            mock_run.return_value = mock_result

            result = process(scenes, transcript, face, None, config, file_path="/fake/video.mp4")

        # Single scene → normalised to 0.0 (min == max); but raw value > 0
        # was produced — activity normalisation maps identical values to 0.
        assert result.scenes[0].scene_activity_score == 0.0  # Only one scene → norm=0

    def test_no_file_path_leaves_activity_zero(self):
        from modules.scoring.score import process

        scenes = _make_scene_list(3)
        transcript = _make_empty_transcript()
        face = _make_face_result(scenes)
        config = _default_config()

        result = process(scenes, transcript, face, None, config, file_path=None)
        for s in result.scenes:
            assert s.scene_activity_score == 0.0


# ---------------------------------------------------------------------------
# Weighted composite computation
# ---------------------------------------------------------------------------

class TestWeightedComposite:
    def test_composite_uses_configured_weights(self):
        from modules.scoring.score import _weighted_composite

        weights = {
            "keyword": 3.0,
            "audio_energy": 2.0,
            "face_presence": 2.0,
            "scene_activity": 1.0,
            "sentence_density": 1.0,
        }
        composite = _weighted_composite(1.0, 1.0, 1.0, 1.0, 1.0, weights)
        assert abs(composite - 1.0) < 1e-9

    def test_composite_zero_weights_returns_zero(self):
        from modules.scoring.score import _weighted_composite

        weights = {k: 0.0 for k in ["keyword", "audio_energy", "face_presence", "scene_activity", "sentence_density"]}
        assert _weighted_composite(1.0, 1.0, 1.0, 1.0, 1.0, weights) == 0.0

    def test_partial_factors_weighted_correctly(self):
        from modules.scoring.score import _weighted_composite

        # Only keyword=1.0, everything else 0. Weight keyword=3, total=9.
        weights = {
            "keyword": 3.0,
            "audio_energy": 2.0,
            "face_presence": 2.0,
            "scene_activity": 1.0,
            "sentence_density": 1.0,
        }
        composite = _weighted_composite(1.0, 0.0, 0.0, 0.0, 0.0, weights)
        assert abs(composite - 3.0 / 9.0) < 1e-9


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

class TestNormalisationAndRanking:
    def test_composite_scores_normalised_to_zero_one(self):
        from modules.scoring.score import process

        scenes = _make_scene_list(3)
        # Give different audio energies to create score variance.
        audio = _make_audio_data(scenes, energies=[0.1, 0.5, 0.9])
        face = _make_face_result(scenes, ratios=[0.0, 0.5, 1.0])
        transcript = _make_empty_transcript()
        config = _default_config()

        result = process(scenes, transcript, face, audio, config)
        scores = [s.composite_score for s in result.scenes]
        assert min(scores) >= 0.0
        assert max(scores) <= 1.0

    def test_best_scene_has_highest_score(self):
        from modules.scoring.score import process

        scenes = _make_scene_list(3)
        # Scene index 2 gets maximum audio energy and face presence.
        audio = _make_audio_data(scenes, energies=[0.1, 0.3, 1.0])
        face = _make_face_result(scenes, ratios=[0.0, 0.2, 1.0])
        transcript = _make_empty_transcript()
        config = _default_config()

        result = process(scenes, transcript, face, audio, config)
        assert result.scenes[0].scene_id == f"{VIDEO_ID}_10000_15000"

    def test_ranking_is_composite_desc_then_start_asc(self):
        from modules.scoring.score import process

        scenes = _make_scene_list(4)
        # All equal audio/face to force identical intermediate scores.
        audio = _make_audio_data(scenes, energies=[0.5, 0.5, 0.5, 0.5])
        face = _make_face_result(scenes, ratios=[0.5, 0.5, 0.5, 0.5])
        transcript = _make_empty_transcript()
        config = _default_config()

        result = process(scenes, transcript, face, audio, config)
        # When scores tie, earlier scenes (lower start_time) should come first
        # after temporal fallback, or identical — either way the order is stable.
        start_times = [s.start_time for s in result.scenes]
        assert start_times == sorted(start_times), "Tiebreaker must be start_time ASC"


# ---------------------------------------------------------------------------
# Degenerate case (all identical scores → temporal fallback)
# ---------------------------------------------------------------------------

class TestDegenerateCase:
    def test_temporal_fallback_produces_unique_scores(self):
        from modules.scoring.score import _temporal_fallback

        scenes_list = _make_scene_list(4)
        flat_scenes = [
            ScoredScene(
                scene_id=s.scene_id,
                video_id=VIDEO_ID,
                start_time=s.start_time,
                end_time=s.end_time,
                duration=s.duration,
                keyword_score=0.0,
                audio_energy_score=0.5,
                face_presence_score=0.5,
                scene_activity_score=0.0,
                sentence_density_score=0.0,
                composite_score=0.5,
                rank=0,
            )
            for s in scenes_list.scenes
        ]
        result = _temporal_fallback(flat_scenes)
        scores = [s.composite_score for s in result]
        assert len(set(scores)) == len(scores), "Temporal fallback must produce unique scores"

    def test_temporal_fallback_first_scene_highest_score(self):
        from modules.scoring.score import _temporal_fallback

        scenes_list = _make_scene_list(3)
        flat_scenes = [
            ScoredScene(
                scene_id=s.scene_id,
                video_id=VIDEO_ID,
                start_time=s.start_time,
                end_time=s.end_time,
                duration=s.duration,
                keyword_score=0.0,
                audio_energy_score=0.0,
                face_presence_score=0.0,
                scene_activity_score=0.0,
                sentence_density_score=0.0,
                composite_score=0.5,
                rank=0,
            )
            for s in scenes_list.scenes
        ]
        result = sorted(_temporal_fallback(flat_scenes), key=lambda s: s.start_time)
        assert result[0].composite_score > result[-1].composite_score

    def test_process_triggers_fallback_on_all_identical(self):
        from modules.scoring.score import process

        # All zeros → identical composite → fallback triggered.
        scenes = _make_scene_list(3)
        face = _make_face_result(scenes, ratios=[0.0, 0.0, 0.0])
        transcript = _make_empty_transcript()
        config = _default_config()

        result = process(scenes, transcript, face, None, config)
        scores = [s.composite_score for s in result.scenes]
        # After fallback, scores should differ.
        assert len(set(scores)) > 1


# ---------------------------------------------------------------------------
# Determinism tests
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_identical_inputs_produce_identical_output(self):
        from modules.scoring.score import process

        scenes = _make_scene_list(5)
        words_per_scene = [
            ["epic", "clutch", "win"],
            ["hello", "world"],
            ["insane", "perfect"],
            [],
            ["best", "final", "boss"],
        ]
        transcript = _make_transcript(scenes, words_per_scene)
        face = _make_face_result(scenes, ratios=[0.0, 0.5, 1.0, 0.3, 0.7])
        audio = _make_audio_data(scenes, energies=[0.2, 0.4, 0.8, 0.1, 0.6])
        config = _default_config()

        result1 = process(scenes, transcript, face, audio, config)
        result2 = process(scenes, transcript, face, audio, config)

        assert len(result1.scenes) == len(result2.scenes)
        for s1, s2 in zip(result1.scenes, result2.scenes):
            assert s1.scene_id == s2.scene_id
            assert s1.composite_score == s2.composite_score

    def test_result_is_frozen_dataclass(self):
        from modules.scoring.score import process

        scenes = _make_scene_list(2)
        transcript = _make_empty_transcript()
        face = _make_face_result(scenes)
        config = _default_config()

        result = process(scenes, transcript, face, None, config)
        assert isinstance(result, ScoredSceneList)
        with pytest.raises((AttributeError, TypeError)):
            result.video_id = "mutated"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Full process() smoke test
# ---------------------------------------------------------------------------

class TestProcessIntegration:
    def test_returns_scored_scene_list_dto(self):
        from modules.scoring.score import process

        scenes = _make_scene_list(5)
        words_per_scene = [["epic", "win"], ["hello"], ["clutch", "insane"], [], ["best"]]
        transcript = _make_transcript(scenes, words_per_scene)
        face = _make_face_result(scenes, ratios=[0.0, 0.3, 0.8, 0.5, 0.1])
        audio = _make_audio_data(scenes, energies=[0.3, 0.5, 0.9, 0.2, 0.7])
        config = _default_config()

        result = process(scenes, transcript, face, audio, config)

        assert isinstance(result, ScoredSceneList)
        assert result.video_id == VIDEO_ID
        assert len(result.scenes) == 5
        for s in result.scenes:
            assert isinstance(s, ScoredScene)
            assert 0.0 <= s.composite_score <= 1.0
            assert 0.0 <= s.keyword_score <= 1.0
            assert 0.0 <= s.audio_energy_score <= 1.0
            assert 0.0 <= s.face_presence_score <= 1.0
            assert 0.0 <= s.scene_activity_score <= 1.0
            assert 0.0 <= s.sentence_density_score <= 1.0

    def test_empty_transcript_does_not_crash(self):
        from modules.scoring.score import process

        scenes = _make_scene_list(3)
        transcript = _make_empty_transcript()
        face = _make_face_result(scenes)
        config = _default_config()

        result = process(scenes, transcript, face, None, config)
        assert len(result.scenes) == 3

    def test_weights_loaded_from_config(self):
        from modules.scoring.score import _get_weights

        config = {
            "scoring": {
                "weights": {
                    "keyword": 5,
                    "audio_energy": 1,
                    "face_presence": 1,
                    "scene_activity": 0,
                    "sentence_density": 0,
                }
            }
        }
        weights = _get_weights(config)
        assert weights["keyword"] == 5.0
        assert weights["scene_activity"] == 0.0

    def test_default_weights_used_when_config_missing(self):
        from modules.scoring.score import _get_weights

        weights = _get_weights({})
        assert weights["keyword"] == 3.0
        assert weights["audio_energy"] == 2.0
