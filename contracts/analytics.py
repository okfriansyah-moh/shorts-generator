"""Analytics DTOs for Shorts Factory.

Produced by the analytics module after each pipeline run.
Summarizes pipeline performance, quality distribution, and publishing status.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ScoreBin:
    """A single histogram bin used in score and visibility distributions.

    Fields:
        bin_start: Lower bound of the bin (inclusive). 0.0–1.0.
        bin_end: Upper bound of the bin (exclusive, except last). 0.0–1.0.
        count: Number of items in this bin. >= 0.
    """

    bin_start: float
    bin_end: float
    count: int


@dataclass(frozen=True)
class QualityMetrics:
    """Frozen DTO summarizing scene and clip quality statistics for a video.

    Fields:
        video_id: Parent video reference. 16 lowercase hex chars.
        total_scenes_scored: Total number of scenes in the ScoredSceneList.
        avg_composite_score: Mean composite_score across all scored scenes. 0.0–1.0.
        score_distribution: 10-bin histogram of composite_score values. Bins: 0.0–0.1, …, 0.9–1.0.
        face_visibility_distribution: 10-bin histogram of face_presence_score values.
        avg_face_visibility: Mean face_presence_score across all scored scenes. 0.0–1.0.
        rejection_count: Scenes scoring below min_composite_score threshold.
        rejection_rate: rejection_count / total_scenes_scored. 0.0–1.0. 0.0 if no scenes.
    """

    video_id: str
    total_scenes_scored: int
    avg_composite_score: float
    score_distribution: tuple[ScoreBin, ...]
    face_visibility_distribution: tuple[ScoreBin, ...]
    avg_face_visibility: float
    rejection_count: int
    rejection_rate: float


@dataclass(frozen=True)
class PublishReport:
    """Frozen DTO summarizing publishing lifecycle status for all clips.

    Fields:
        video_id: Parent video reference. 16 lowercase hex chars.
        total_clips: Total number of StorageRecords processed.
        published_count: Clips with status == 'published'.
        scheduled_count: Clips with status == 'scheduled'.
        queued_count: Clips with status == 'queued'.
        generated_count: Clips with status == 'generated'.
        failed_count: Clips with status == 'failed'.
        upload_success_rate: published_count / total_clips. 0.0–1.0. 0.0 if no clips.
        queue_depth_days: (queued_count + scheduled_count) / posts_per_day. >= 0.0.
    """

    video_id: str
    total_clips: int
    published_count: int
    scheduled_count: int
    queued_count: int
    generated_count: int
    failed_count: int
    upload_success_rate: float
    queue_depth_days: float


@dataclass(frozen=True)
class PipelineReport:
    """Frozen DTO representing the full analytics report for a pipeline run.

    Fields:
        run_id: Pipeline run identifier. May be empty string if unavailable.
        video_id: Parent video reference. 16 lowercase hex chars.
        total_clips_generated: Number of clips selected by clip_builder.
        total_clips_stored: Number of clips with an existing StorageRecord.
        avg_composite_score: Mean clip composite_score from StorageRecords. 0.0–1.0.
        min_duration_seconds: Shortest clip duration in seconds. 0.0 if no clips.
        max_duration_seconds: Longest clip duration in seconds. 0.0 if no clips.
        mean_duration_seconds: Average clip duration in seconds. 0.0 if no clips.
        quality: QualityMetrics DTO.
        publishing: PublishReport DTO.
        report_path: Absolute path to the written JSON report file.
        generated_at: ISO 8601 UTC timestamp when the report was generated.
    """

    run_id: str
    video_id: str
    total_clips_generated: int
    total_clips_stored: int
    avg_composite_score: float
    min_duration_seconds: float
    max_duration_seconds: float
    mean_duration_seconds: float
    quality: QualityMetrics
    publishing: PublishReport
    report_path: str
    generated_at: str
