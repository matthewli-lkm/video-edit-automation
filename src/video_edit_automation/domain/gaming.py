from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from video_edit_automation.domain.models import AudioOutputMode, CaptureMode, utc_now

NonBlankText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
SignalMetadataValue = str | int | float | bool


class GamingModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GameGenre(StrEnum):
    FPS = "fps"
    MOBA = "moba"
    BATTLE_ROYALE = "battle_royale"
    RACING = "racing"
    SPORTS = "sports"
    HORROR = "horror"
    STORY = "story"
    SANDBOX = "sandbox"
    OTHER = "other"


class HighlightSignalType(StrEnum):
    GAME_EVENT = "game_event"
    TELEMETRY_EVENT = "telemetry_event"
    OCR_EVENT = "ocr_event"
    AUDIO_PEAK = "audio_peak"
    MICROPHONE_REACTION = "microphone_reaction"
    MOTION_PEAK = "motion_peak"
    SCENE_CHANGE = "scene_change"
    MANUAL_MARKER = "manual_marker"


class CaptureAudioPolicy(GamingModel):
    capture_game_audio: bool = True
    capture_microphone: bool = True
    keep_separate_tracks: bool = True
    default_output_mode: AudioOutputMode = AudioOutputMode.GAME_ONLY


class GameContext(GamingModel):
    game_id: NonBlankText
    display_name: NonBlankText
    genre: GameGenre


class DetectedGame(GameContext):
    confidence: float = Field(ge=0, le=1)
    evidence: list[str] = Field(default_factory=list)
    game_profile_id: str | None = None


class HighlightSignal(GamingModel):
    id: NonBlankText
    asset_id: UUID
    timestamp_seconds: float = Field(ge=0)
    duration_seconds: float = Field(default=0, ge=0)
    signal_type: HighlightSignalType
    event_name: str | None = None
    confidence: float = Field(default=1, ge=0, le=1)
    source: str | None = None
    source_audio_stream_index: int | None = Field(default=None, ge=0)
    metadata: dict[str, SignalMetadataValue] = Field(default_factory=dict)

    @property
    def normalized_event_name(self) -> str | None:
        if not self.event_name:
            return None
        return self.event_name.strip().lower().replace(" ", "_")

    def weight_keys(self) -> tuple[str, ...]:
        exact = (
            f"{self.signal_type.value}:{self.normalized_event_name}"
            if self.normalized_event_name
            else None
        )
        return tuple(key for key in (exact, self.signal_type.value) if key)


class HighlightAnalysis(GamingModel):
    id: UUID = Field(default_factory=uuid4)
    analyzer: NonBlankText
    asset_id: UUID
    source_fingerprint: NonBlankText
    game: GameContext
    game_profile_id: NonBlankText | None = None
    sampled_frame_count: int = Field(ge=0)
    audio_peak_count: int = Field(ge=0)
    signals: list[HighlightSignal] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class GameProfile(GamingModel):
    id: NonBlankText
    display_name: NonBlankText
    genre: GameGenre
    capture_mode: CaptureMode = CaptureMode.FULL_SESSION
    capture_audio: CaptureAudioPolicy = Field(default_factory=CaptureAudioPolicy)
    pre_roll_seconds: float = Field(default=12, ge=0, le=180)
    post_roll_seconds: float = Field(default=8, ge=0, le=180)
    merge_gap_seconds: float = Field(default=4, ge=0, le=60)
    minimum_score: float = Field(default=0.5, ge=0)
    signal_weights: dict[str, float] = Field(default_factory=dict)


class HighlightCandidate(GamingModel):
    asset_id: UUID
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    score: float = Field(ge=0)
    signal_ids: list[str] = Field(min_length=1)
    labels: list[str] = Field(default_factory=list)

    @property
    def duration_seconds(self) -> float:
        return self.end_seconds - self.start_seconds

    def model_post_init(self, __context: Any) -> None:
        if self.end_seconds <= self.start_seconds:
            raise ValueError("end_seconds must be greater than start_seconds")


class ManualHighlightClip(GamingModel):
    title: NonBlankText
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)

    @property
    def duration_seconds(self) -> float:
        return self.end_seconds - self.start_seconds

    def model_post_init(self, __context: Any) -> None:
        if self.end_seconds <= self.start_seconds:
            raise ValueError("end_seconds must be greater than start_seconds")
