"""Integration tests for Phase 3 — Scoring pipeline.

Validates the full signal chain: SceneList + Transcript + FaceDetectionResult
+ AudioEnergyData → ScoredSceneList. Tests DTO compatibility at every boundary,
deterministic ordering, and graceful handling of missing signals.

No GPU, no network, no real video files required.
"""

from __future__ import annotations

from typing import Any

import pytest

from contracts.audio import AudioEnergyData, SceneAudioEnergy
from contracts.face import FaceBBox, FaceDetectionResult, SceneFaceData
from contracts.scene import SceneList, SceneSegment
from contracts.scoring import ScoredScene, ScoredSceneList
from contracts.transcript import Transcript, TranscriptSegment, Word

# ---------------------------------------------------------------------------
# Shared fixture helpers
# ---------------------------------------------------------------------------

VIDEO_ID = "a1b2c3d4e5f67890"


def _make_scene(start_ms: int, end_ms: int) -> SceneSegment:
    return SceneSegment(
        scene_id=f"{VIDEO_ID}_{start_ms}_{end_ms}",
        video_id=VIDEO_ID,
        start_time=start_ms,
        end_time=end_ms,
        duration=(end_ms - start_ms) / 1000.0,
    )


def _make_scene_list(count: int = 5) -> SceneList:
    scenes = tuple(_make_scene(i * 5000, (i + 1) * 5000) for i in range(count))
    return SceneList(
        video_id=VIDEO_ID,
        scenes=scenes,
        total_duration=float(count * 5),
    )


def _make_transcript(scenes: SceneList, words_per_scene: list[list[str]]) -> Transcript:
    segments = []
    for scene, words in zip(scenes.scenes, words_per_scene):
        if not words:
            continue
        step = (scene.end_time - scene.start_time) // max(len(words), 1)
        word_dtos = tuple(
            Word(
                text=w,
                start_time=scene.start_time + j * step,
                end_time=scene.start_time + (j + 1) * step - 1,
                confidence=0.95,
            )
            for j, w in enumerate(words)
        )
        segments.append(
            TranscriptSegment(
                text=" ".join(words),
                start_time=scene.start_time,
                end_time=scene.end_time,
                words=word_dtos,
                confidence=0.95,
            )
        )
    return Transcript(
        video_id=VIDEO_ID,
        segments=tuple(segments),
        total_words=sum(len(w) for w in words_per_scene),
        language="en",
    )


def _make_face_result(scenes: SceneList, ratios: list[float] | None = None) -> FaceDetectionResult:
    ratios = ratios or [0.0] * len(scenes.scenes)
    scene_data = []
    for scene, ratio in zip(scenes.scenes, ratios):
        if ratio > 0.0:
            bbox = FaceBBox(x=0.2, y=0.1, width=0.3, height=0.4, confidence=0.9,
                            timestamp_ms=scene.start_time)
            scene_data.append(SceneFaceData(
                scene_id=scene.scene_id,
                face_visible_ratio=ratio,
                bounding_boxes=(bbox,),
                average_bbox=bbox,
                sample_count=10,
            ))
        else:
            scene_data.append(SceneFaceData(
                scene_id=scene.scene_id,
                face_visible_ratio=0.0,
                bounding_boxes=(),
                average_bbox=None,
                sample_count=10,
            ))
    avg = sum(ratios) / len(ratios)
    faceless = sum(1 for r in ratios if r == 0.0)
    return FaceDetectionResult(
        video_id=VIDEO_ID,
        scene_data=tuple(scene_data),
        average_visibility=avg,
        faceless_scene_count=faceless,
    )


def _make_audio_data(scenes: SceneList, energies: list[float]) -> AudioEnergyData:
    rms_min = min(energies)
    rms_max = max(energies)
    rms_range = rms_max - rms_min
    scene_energies = tuple(
        SceneAudioEnergy(
            scene_id=scene.scene_id,
            rms_energy=e,
            normalized_energy=(e - rms_min) / rms_range if rms_range > 0 else 0.0,
        )
        for scene, e in zip(scenes.scenes, energies)
    )
    return AudioEnergyData(
        video_id=VIDEO_ID,
        scene_energies=scene_energies,
        video_min_rms=rms_min,
        video_max_rms=rms_max,
        video_mean_rms=sum(energies) / len(energies),
    )


def _default_config() -> dict[str, Any]:
    return {
        "scoring": {
            "weights": {
                "keyword": 3,
                "audio_energy": 2,
                "face_presence": 2,
                "scene_activity": 1,
                "sentence_density": 1,
            },
            "keywords": ["epic", "insane", "clutch", "win", "best", "boss"],
        }
    }


# ---------------------------------------------------------------------------
# DTO compatibility across stage boundaries
# ---------------------------------------------------------------------------

class TestScoringDTOCompatibility:
    """Verify SceneList + signals produce a well-formed ScoredSceneList DTO."""

    def test_output_is_scored_scene_list(self) -> None:
        from modules.scoring.score import process

        scenes = _make_scene_list(3)
        transcript = _make_transcript(scenes, [["epic", "win"], ["hello"], ["insane"]])
        face = _make_face_result(scenes, ratios=[0.8, 0.0, 0.5])
        audio = _make_audio_data(scenes, energies=[0.3, 0.6, 0.9])

        result = process(scenes, transcript, face, audio, _default_config())

        assert isinstance(result, ScoredSceneList)
        assert result.video_id == VIDEO_ID

    def test_scene_count_preserved(self) -> None:
        from modules.scoring.score import process

        scenes = _make_scene_list(5)
        face = _make_face_result(scenes)
        audio = _make_audio_data(scenes, energies=[0.1, 0.2, 0.3, 0.4, 0.5])
        transcript = Transcript(video_id=VIDEO_ID, segments=(), total_words=0, language="en")

        result = process(scenes, transcript, face, audio, _default_config())

        assert len(result.scenes) == 5

    def test_all_score_fields_in_valid_range(self) -> None:
        from modules.scoring.score import process

        scenes = _make_scene_list(4)
        transcript = _make_transcript(scenes, [["epic"], ["clutch", "win"], [], ["boss"]])
        face = _make_face_result(scenes, ratios=[0.0, 1.0, 0.5, 0.3])
        audio = _make_audio_data(scenes, energies=[0.2, 0.8, 0.4, 0.6])

        result = process(scenes, transcript, face, audio, _default_config())

        for s in result.scenes:
            assert isinstance(s, ScoredScene)
            assert 0.0 <= s.composite_score <= 1.0
            assert 0.0 <= s.keyword_score <= 1.0
            assert 0.0 <= s.audio_energy <= 1.0
            assert 0.0 <= s.face_presence <= 1.0
            assert 0.0 <= s.scene_activity <= 1.0
            assert 0.0 <= s.sentence_density <= 1.0

    def test_scene_id_and_video_id_preserved(self) -> None:
        from modules.scoring.score import process

        scenes = _make_scene_list(3)
        face = _make_face_result(scenes)
        audio = _make_audio_data(scenes, energies=[0.1, 0.5, 0.9])
        transcript = Transcript(video_id=VIDEO_ID, segments=(), total_words=0, language="en")

        result = process(scenes, transcript, face, audio, _default_config())

        input_ids = {s.scene_id for s in scenes.scenes}
        output_ids = {s.scene_id for s in result.scenes}
        assert input_ids == output_ids

        for s in result.scenes:
            assert s.video_id == VIDEO_ID


# ---------------------------------------------------------------------------
# Signal flow: transcript → keyword_score, sentence_density
# ---------------------------------------------------------------------------

class TestTranscriptSignalFlow:
    """Verify transcript words feed keyword_score and sentence_density correctly."""

    def test_high_keyword_density_produces_high_keyword_score(self) -> None:
        from modules.scoring.score import process

        scenes = _make_scene_list(2)
        # Scene 0: all keywords; scene 1: no keywords
        transcript = _make_transcript(scenes, [["epic", "win", "insane"], ["the", "a", "is"]])
        face = _make_face_result(scenes, ratios=[0.5, 0.5])
        audio = _make_audio_data(scenes, energies=[0.5, 0.5])

        result = process(scenes, transcript, face, audio, _default_config())

        # Find scores by scene_id
        score_map = {s.scene_id: s for s in result.scenes}
        scene0_id = scenes.scenes[0].scene_id
        scene1_id = scenes.scenes[1].scene_id
        assert score_map[scene0_id].keyword_score > score_map[scene1_id].keyword_score

    def test_empty_transcript_scores_zero_keyword(self) -> None:
        from modules.scoring.score import process

        scenes = _make_scene_list(3)
        transcript = Transcript(video_id=VIDEO_ID, segments=(), total_words=0, language="en")
        face = _make_face_result(scenes)
        audio = _make_audio_data(scenes, energies=[0.2, 0.5, 0.8])

        result = process(scenes, transcript, face, audio, _default_config())

        for s in result.scenes:
            assert s.keyword_score == 0.0


# ---------------------------------------------------------------------------
# Signal flow: face_result → face_presence
# ---------------------------------------------------------------------------

class TestFaceSignalFlow:
    """Verify face_visible_ratio feeds correctly into face_presence scores."""

    def test_face_ratio_maps_to_face_presence(self) -> None:
        from modules.scoring.score import process

        scenes = _make_scene_list(2)
        transcript = Transcript(video_id=VIDEO_ID, segments=(), total_words=0, language="en")
        # Scene 0 fully visible, scene 1 no face
        face = _make_face_result(scenes, ratios=[1.0, 0.0])
        audio = _make_audio_data(scenes, energies=[0.5, 0.5])

        result = process(scenes, transcript, face, audio, _default_config())

        score_map = {s.scene_id: s for s in result.scenes}
        s0 = score_map[scenes.scenes[0].scene_id]
        s1 = score_map[scenes.scenes[1].scene_id]
        assert s0.face_presence == pytest.approx(1.0)
        assert s1.face_presence == pytest.approx(0.0)

    def test_missing_face_data_defaults_to_zero(self) -> None:
        """FaceDetectionResult with empty scene_data → face_presence = 0.0."""
        from modules.scoring.score import process

        scenes = _make_scene_list(2)
        transcript = Transcript(video_id=VIDEO_ID, segments=(), total_words=0, language="en")
        face = FaceDetectionResult(
            video_id=VIDEO_ID,
            scene_data=(),
            average_visibility=0.0,
            faceless_scene_count=2,
        )
        audio = _make_audio_data(scenes, energies=[0.3, 0.7])

        result = process(scenes, transcript, face, audio, _default_config())

        for s in result.scenes:
            assert s.face_presence == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Signal flow: audio_data → audio_energy
# ---------------------------------------------------------------------------

class TestAudioSignalFlow:
    """Verify AudioEnergyData normalized_energy feeds audio_energy correctly."""

    def test_audio_energy_passes_through_from_dto(self) -> None:
        from modules.scoring.score import process

        scenes = _make_scene_list(3)
        transcript = Transcript(video_id=VIDEO_ID, segments=(), total_words=0, language="en")
        face = _make_face_result(scenes)
        audio = _make_audio_data(scenes, energies=[0.0, 0.5, 1.0])

        result = process(scenes, transcript, face, audio, _default_config())

        score_map = {s.scene_id: s for s in result.scenes}
        s0 = score_map[scenes.scenes[0].scene_id]
        s2 = score_map[scenes.scenes[2].scene_id]
        # Scene 2 has max audio energy → higher audio_energy score than scene 0
        assert s2.audio_energy > s0.audio_energy

    def test_none_audio_data_defaults_to_zero(self) -> None:
        from modules.scoring.score import process

        scenes = _make_scene_list(3)
        transcript = Transcript(video_id=VIDEO_ID, segments=(), total_words=0, language="en")
        face = _make_face_result(scenes)

        result = process(scenes, transcript, face, None, _default_config())

        for s in result.scenes:
            assert s.audio_energy == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Deterministic ordering
# ---------------------------------------------------------------------------

class TestScoringDeterminism:
    """Verify identical inputs always produce identical ranked output."""

    def test_same_input_same_output(self) -> None:
        from modules.scoring.score import process

        scenes = _make_scene_list(5)
        transcript = _make_transcript(
            scenes,
            [["epic", "clutch"], ["hello"], ["insane", "win"], [], ["best", "boss"]],
        )
        face = _make_face_result(scenes, ratios=[0.0, 0.5, 1.0, 0.3, 0.7])
        audio = _make_audio_data(scenes, energies=[0.2, 0.4, 0.8, 0.1, 0.6])
        config = _default_config()

        r1 = process(scenes, transcript, face, audio, config)
        r2 = process(scenes, transcript, face, audio, config)

        assert [s.scene_id for s in r1.scenes] == [s.scene_id for s in r2.scenes]
        for s1, s2 in zip(r1.scenes, r2.scenes):
            assert s1.composite_score == pytest.approx(s2.composite_score)

    def test_ranking_is_composite_desc_start_asc(self) -> None:
        from modules.scoring.score import process

        scenes = _make_scene_list(4)
        face = _make_face_result(scenes, ratios=[0.5, 0.5, 0.5, 0.5])
        audio = _make_audio_data(scenes, energies=[0.5, 0.5, 0.5, 0.5])
        transcript = Transcript(video_id=VIDEO_ID, segments=(), total_words=0, language="en")

        result = process(scenes, transcript, face, audio, _default_config())

        for i in range(len(result.scenes) - 1):
            a = result.scenes[i]
            b = result.scenes[i + 1]
            # composite DESC
            assert a.composite_score >= b.composite_score
            # start_time ASC as tiebreaker
            if a.composite_score == b.composite_score:
                assert a.start_time <= b.start_time

    def test_result_is_frozen(self) -> None:
        from modules.scoring.score import process

        scenes = _make_scene_list(2)
        face = _make_face_result(scenes)
        audio = _make_audio_data(scenes, energies=[0.3, 0.7])
        transcript = Transcript(video_id=VIDEO_ID, segments=(), total_words=0, language="en")

        result = process(scenes, transcript, face, audio, _default_config())

        assert isinstance(result, ScoredSceneList)
        with pytest.raises((AttributeError, TypeError)):
            result.video_id = "mutated"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Full signal chain: Phase 1 + Phase 2 outputs → Phase 3
# ---------------------------------------------------------------------------

class TestFullSignalChainToScoring:
    """End-to-end chain: IngestionResult → SceneList → all signals → ScoredSceneList."""

    def test_phase1_scene_list_compatible_with_scoring(self) -> None:
        """SceneList produced by scene_splitter passes correctly into scoring."""
        from contracts.ingestion import IngestionResult

        _ingestion = IngestionResult(
            video_id=VIDEO_ID,
            path="/fake/video.mp4",
            duration_seconds=3600.0,
            resolution=(1920, 1080),
            codec="h264",
            audio_codec="aac",
            has_audio=True,
            file_size_bytes=100_000_000,
            fps=30.0,
        )
        # Simulate what scene_splitter produces
        scenes = SceneList(
            video_id=VIDEO_ID,
            scenes=tuple(
                SceneSegment(
                    scene_id=f"{VIDEO_ID}_{i * 10000}_{(i + 1) * 10000}",
                    video_id=VIDEO_ID,
                    start_time=i * 10000,
                    end_time=(i + 1) * 10000,
                    duration=10.0,
                )
                for i in range(5)
            ),
            total_duration=50.0,
        )

        # Simulate Phase 2 outputs
        transcript = Transcript(
            video_id=VIDEO_ID,
            segments=(
                TranscriptSegment(
                    text="epic clutch win insane",
                    start_time=0,
                    end_time=10000,
                    words=(
                        Word("epic", 0, 2000, 0.95),
                        Word("clutch", 2500, 4500, 0.92),
                        Word("win", 5000, 7000, 0.90),
                        Word("insane", 7500, 9500, 0.88),
                    ),
                    confidence=0.91,
                ),
            ),
            total_words=4,
            language="en",
        )
        face = _make_face_result(scenes, ratios=[0.8, 0.6, 0.0, 0.4, 0.9])
        audio = _make_audio_data(scenes, energies=[0.3, 0.5, 0.7, 0.4, 0.9])

        from modules.scoring.score import process
        result = process(scenes, transcript, face, audio, _default_config())

        assert isinstance(result, ScoredSceneList)
        assert result.video_id == VIDEO_ID
        assert len(result.scenes) == 5
        # All scene_ids from input are present in output
        input_ids = {s.scene_id for s in scenes.scenes}
        output_ids = {s.scene_id for s in result.scenes}
        assert input_ids == output_ids
        # Best scene is first
        assert result.scenes[0].composite_score >= result.scenes[-1].composite_score

    def test_scoring_handles_all_signal_types_simultaneously(self) -> None:
        """All four inputs populated simultaneously produce a valid ScoredSceneList."""
        from modules.scoring.score import process

        scenes = _make_scene_list(4)
        transcript = _make_transcript(
            scenes,
            [["epic", "clutch", "win"], ["hello", "world"], ["insane", "best"], ["boss"]],
        )
        face = _make_face_result(scenes, ratios=[0.9, 0.2, 0.7, 0.5])
        audio = _make_audio_data(scenes, energies=[0.8, 0.3, 0.6, 0.1])

        result = process(scenes, transcript, face, audio, _default_config())

        # High-keyword + high-face + high-audio scene should outrank low-signal scenes
        score_map = {s.scene_id: s for s in result.scenes}
        s0 = score_map[scenes.scenes[0].scene_id]  # Strongest signals
        s3 = score_map[scenes.scenes[3].scene_id]  # Weakest audio
        assert s0.composite_score >= s3.composite_score
