from __future__ import annotations

from uuid import UUID, uuid4

from video_edit_automation.application.ports import GameDetector, Repository
from video_edit_automation.application.services import ProjectService
from video_edit_automation.domain.capture import (
    CaptureObservation,
    CapturePlatform,
    CaptureRecorder,
    CaptureSession,
    GameCatalogEntry,
    ManualBookmarkInput,
)
from video_edit_automation.domain.errors import (
    EntityNotFoundError,
    InvalidCaptureSessionError,
)
from video_edit_automation.domain.gaming import (
    DetectedGame,
    HighlightSignal,
    HighlightSignalType,
)
from video_edit_automation.domain.models import AudioTrackRoleAssignment, MediaAsset


class CaptureSessionService:
    def __init__(
        self,
        repository: Repository,
        projects: ProjectService,
        detector: GameDetector,
        game_profile_ids: set[str],
    ) -> None:
        self.repository = repository
        self.projects = projects
        self.detector = detector
        self.game_profile_ids = game_profile_ids

    def list_games(self) -> list[GameCatalogEntry]:
        return self.detector.list_games()

    def detect_game(self, observation: CaptureObservation) -> DetectedGame | None:
        return self.detector.detect(observation)

    @staticmethod
    def _validate_recorder_platform(
        platform: CapturePlatform,
        recorder: CaptureRecorder,
    ) -> None:
        if recorder == CaptureRecorder.SCREEN_CAPTURE_KIT and platform != CapturePlatform.MACOS:
            raise InvalidCaptureSessionError("ScreenCaptureKit capture requires macOS")
        if (
            recorder == CaptureRecorder.WINDOWS_GRAPHICS_CAPTURE
            and platform != CapturePlatform.WINDOWS
        ):
            raise InvalidCaptureSessionError("Windows Graphics Capture requires Windows")

    def _asset(self, project_id: UUID, asset_id: UUID) -> MediaAsset:
        self.projects.get(project_id)
        asset = self.repository.get_asset(asset_id)
        if asset is None or asset.project_id != project_id:
            raise EntityNotFoundError(
                f"Media asset {asset_id} was not found in project {project_id}"
            )
        return asset

    def _profile(self, profile_id: str | None) -> str | None:
        if profile_id is not None and profile_id not in self.game_profile_ids:
            raise EntityNotFoundError(f"Gaming profile {profile_id!r} was not found")
        return profile_id

    def _detect_best(self, observations: list[CaptureObservation]) -> DetectedGame | None:
        detections = [
            detected
            for observation in observations
            if (detected := self.detector.detect(observation)) is not None
        ]
        if not detections:
            return None
        return sorted(detections, key=lambda item: (-item.confidence, item.game_id))[0]

    def _game_override(self, game_id: str | None) -> DetectedGame | None:
        if game_id is None:
            return None
        game = self.detector.get_game(game_id)
        if game is None:
            raise EntityNotFoundError(f"Game {game_id!r} was not found")
        return DetectedGame(
            game_id=game.game_id,
            display_name=game.display_name,
            genre=game.genre,
            confidence=1,
            evidence=["user-selected game override"],
            game_profile_id=game.default_profile_id,
        )

    def create(
        self,
        project_id: UUID,
        asset_id: UUID,
        platform: CapturePlatform,
        recorder: CaptureRecorder,
        clock_origin_monotonic_ns: int | None,
        observations: list[CaptureObservation],
        audio_track_roles: list[AudioTrackRoleAssignment],
        game_id_override: str | None = None,
        game_profile_id: str | None = None,
    ) -> CaptureSession:
        self._validate_recorder_platform(platform, recorder)
        self._asset(project_id, asset_id)
        if audio_track_roles:
            self.projects.assign_audio_track_roles(project_id, asset_id, audio_track_roles)

        detected = self._game_override(game_id_override) or self._detect_best(observations)
        selected_profile = self._profile(
            game_profile_id or (detected.game_profile_id if detected else None)
        )
        session = CaptureSession(
            project_id=project_id,
            asset_id=asset_id,
            platform=platform,
            recorder=recorder,
            clock_origin_monotonic_ns=clock_origin_monotonic_ns,
            observations=observations,
            detected_game=detected,
            game_profile_id=selected_profile,
            audio_track_roles=audio_track_roles,
        )
        self.repository.save_capture_session(session)
        return session

    def get(self, project_id: UUID, session_id: UUID) -> CaptureSession:
        session = self.repository.get_capture_session(session_id)
        if session is None or session.project_id != project_id:
            raise EntityNotFoundError(
                f"Capture session {session_id} was not found in project {project_id}"
            )
        return session

    def list(self, project_id: UUID) -> list[CaptureSession]:
        self.projects.get(project_id)
        return self.repository.list_capture_sessions(project_id)

    @staticmethod
    def _bookmark_timestamp(session: CaptureSession, bookmark: ManualBookmarkInput) -> float:
        if bookmark.timestamp_seconds is not None:
            return bookmark.timestamp_seconds
        if session.clock_origin_monotonic_ns is None:
            raise InvalidCaptureSessionError(
                "This session has no monotonic clock origin; use timestamp_seconds"
            )
        if bookmark.observed_monotonic_ns is None:
            raise InvalidCaptureSessionError("The bookmark has no usable timestamp")
        elapsed_ns = bookmark.observed_monotonic_ns - session.clock_origin_monotonic_ns
        if elapsed_ns < 0:
            raise InvalidCaptureSessionError(
                "Bookmark monotonic time occurs before the capture clock origin"
            )
        return elapsed_ns / 1_000_000_000

    def add_bookmark(
        self,
        project_id: UUID,
        session_id: UUID,
        bookmark: ManualBookmarkInput,
    ) -> HighlightSignal:
        session = self.get(project_id, session_id)
        asset = self._asset(project_id, session.asset_id)
        timestamp_seconds = self._bookmark_timestamp(session, bookmark)
        if timestamp_seconds > asset.duration_seconds:
            raise InvalidCaptureSessionError(
                f"Bookmark occurs at {timestamp_seconds:.3f}s but the recording is only "
                f"{asset.duration_seconds:.3f}s long"
            )
        signal_id = bookmark.id or f"manual-{uuid4()}"
        if any(signal.id == signal_id for signal in session.signals):
            raise InvalidCaptureSessionError(f"Bookmark ID {signal_id!r} already exists")

        signal = HighlightSignal(
            id=signal_id,
            asset_id=session.asset_id,
            timestamp_seconds=timestamp_seconds,
            signal_type=HighlightSignalType.MANUAL_MARKER,
            event_name=bookmark.label,
            source=f"capture_session:{session.recorder.value}",
            metadata={"label": bookmark.label},
        )
        updated = session.model_copy(update={"signals": [*session.signals, signal]})
        self.repository.save_capture_session(updated)
        return signal

    def highlight_inputs(
        self,
        project_id: UUID,
        session_id: UUID,
    ) -> tuple[str, list[HighlightSignal]]:
        session = self.get(project_id, session_id)
        if session.game_profile_id is None:
            raise InvalidCaptureSessionError(
                "Choose a game or gaming profile before creating a highlight plan"
            )
        if not session.signals:
            raise InvalidCaptureSessionError(
                "Add at least one bookmark or detected signal before creating a highlight plan"
            )
        return session.game_profile_id, session.signals
