from __future__ import annotations

import concurrent.futures
import math
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from video_edit_automation.domain.errors import AnalyzerUnavailableError, MediaToolError
from video_edit_automation.domain.gaming import (
    GameContext,
    HighlightAnalysis,
    HighlightSignal,
    HighlightSignalType,
)
from video_edit_automation.domain.models import MediaAsset

_TIMESTAMP_RE = re.compile(r"pts_time:([0-9]+(?:\.[0-9]+)?)")
_RMS_RE = re.compile(r"RMS_level=(-?(?:[0-9]+(?:\.[0-9]+)?|inf))")
_KILL_RE = re.compile(
    r"(?P<killer>[A-Za-z][A-Za-z0-9_.]{1,23})\s+has[\s.'’]*slain\s+"
    r"(?P<victim>[A-Za-z][A-Za-z0-9_.]{1,23})",
    re.IGNORECASE,
)
_OBJECTIVE_RE = re.compile(
    r"(?P<team>blue|red)[\s._-]*team\s+has[\s.'’]*slain\s+(?:the\s+)?"
    r"(?P<objective>(?:[A-Za-z]+\s+){0,4}?(?:drake|dragon|baron\s+nashor))",
    re.IGNORECASE,
)
_MULTI_KILL_RE = re.compile(r"(?P<tier>double|triple|quadra|penta)\s*kill", re.IGNORECASE)
_STRUCTURE_RE = re.compile(
    r"(?:(?P<team>blue|red)\s+)?(?P<structure>turret|inhibitor|nexus)\s+destroyed",
    re.IGNORECASE,
)
_VICTORY_RE = re.compile(r"\b(victory|defeat)\b", re.IGNORECASE)
_STREAK_RE = re.compile(
    r"\b(killing spree|rampage|unstoppable|dominating|godlike|legendary|shutdown)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class OcrEvent:
    timestamp_seconds: float
    event_name: str
    confidence: float
    raw_text: str
    metadata: dict[str, str]


def _clean_text(value: str) -> str:
    return " ".join(value.replace("|", " ").split())


def parse_league_ocr_text(text: str, timestamp_seconds: float) -> list[OcrEvent]:
    """Convert visible League announcements into normalized, evidence-carrying events."""
    cleaned = _clean_text(text)
    if not cleaned:
        return []
    events: list[OcrEvent] = []

    objective = _OBJECTIVE_RE.search(cleaned)
    if objective:
        events.append(
            OcrEvent(
                timestamp_seconds=timestamp_seconds,
                event_name="objective",
                confidence=0.95,
                raw_text=cleaned,
                metadata={
                    "team": objective.group("team").lower(),
                    "objective": _clean_text(objective.group("objective")).lower(),
                },
            )
        )
    else:
        kill = _KILL_RE.search(cleaned)
        normalized_killer = kill.group("killer").lower().strip("._") if kill else None
        if kill and normalized_killer not in {
            "blue",
            "red",
            "team",
            "blue.team",
            "red.team",
        }:
            events.append(
                OcrEvent(
                    timestamp_seconds=timestamp_seconds,
                    event_name="champion_kill",
                    confidence=0.92,
                    raw_text=cleaned,
                    metadata={
                        "killer": kill.group("killer"),
                        "victim": kill.group("victim"),
                    },
                )
            )

    multi_kill = _MULTI_KILL_RE.search(cleaned)
    if multi_kill:
        events.append(
            OcrEvent(
                timestamp_seconds=timestamp_seconds,
                event_name="multi_kill",
                confidence=0.96,
                raw_text=cleaned,
                metadata={"tier": multi_kill.group("tier").lower()},
            )
        )

    structure = _STRUCTURE_RE.search(cleaned)
    if structure:
        metadata = {"structure": structure.group("structure").lower()}
        if structure.group("team"):
            metadata["team"] = structure.group("team").lower()
        events.append(
            OcrEvent(
                timestamp_seconds=timestamp_seconds,
                event_name="objective",
                confidence=0.82,
                raw_text=cleaned,
                metadata=metadata,
            )
        )

    victory = _VICTORY_RE.search(cleaned)
    if victory:
        events.append(
            OcrEvent(
                timestamp_seconds=timestamp_seconds,
                event_name="victory" if victory.group(1).lower() == "victory" else "defeat",
                confidence=0.95,
                raw_text=cleaned,
                metadata={"announcement": victory.group(1).lower()},
            )
        )

    streak = _STREAK_RE.search(cleaned)
    if streak:
        events.append(
            OcrEvent(
                timestamp_seconds=timestamp_seconds,
                event_name="kill_streak",
                confidence=0.84,
                raw_text=cleaned,
                metadata={"streak": _clean_text(streak.group(1)).lower()},
            )
        )
    return events


class LeagueOcrSignalAnalyzer:
    """Local League replay analyser using audio candidates plus focused HUD OCR."""

    def __init__(
        self,
        ffmpeg_binary: str = "ffmpeg",
        tesseract_binary: str = "tesseract",
        audio_peak_limit: int = 18,
        candidate_radius_seconds: int = 4,
        ocr_workers: int = 4,
        tail_seconds: int = 60,
        tail_interval_seconds: int = 2,
    ) -> None:
        self.ffmpeg_binary = ffmpeg_binary
        self.tesseract_binary = tesseract_binary
        self.audio_peak_limit = audio_peak_limit
        self.candidate_radius_seconds = candidate_radius_seconds
        self.ocr_workers = ocr_workers
        self.tail_seconds = tail_seconds
        self.tail_interval_seconds = tail_interval_seconds

    @property
    def name(self) -> str:
        return "league-ocr-audio-v1"

    def available(self) -> bool:
        return bool(shutil.which(self.ffmpeg_binary) and shutil.which(self.tesseract_binary))

    @staticmethod
    def _completed(command: list[str], purpose: str) -> subprocess.CompletedProcess[str]:
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                shell=False,
            )
        except OSError as exc:
            raise MediaToolError(f"Unable to start {purpose}: {exc}") from exc
        if completed.returncode != 0:
            detail = completed.stderr.strip()[-4_000:]
            raise MediaToolError(f"{purpose} failed: {detail}")
        return completed

    def _audio_levels(self, asset: MediaAsset) -> list[tuple[int, float]]:
        if not asset.has_audio:
            return []
        command = [
            self.ffmpeg_binary,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-i",
            str(asset.source_path),
            "-vn",
            "-af",
            (
                "asetnsamples=n=48000:p=0,astats=metadata=1:reset=1,"
                "ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-"
            ),
            "-f",
            "null",
            "-",
        ]
        output = self._completed(command, "FFmpeg audio analysis").stdout
        levels: list[tuple[int, float]] = []
        timestamp: int | None = None
        for line in output.splitlines():
            timestamp_match = _TIMESTAMP_RE.search(line)
            if timestamp_match:
                timestamp = int(float(timestamp_match.group(1)))
                continue
            rms_match = _RMS_RE.search(line)
            if not rms_match or timestamp is None or rms_match.group(1) == "-inf":
                continue
            levels.append((timestamp, float(rms_match.group(1))))
        return levels

    def _audio_peaks(self, levels: list[tuple[int, float]]) -> list[tuple[int, float]]:
        peaks: list[tuple[int, float]] = []
        for timestamp, rms_db in sorted(levels, key=lambda item: item[1], reverse=True):
            if all(abs(timestamp - existing[0]) >= 4 for existing in peaks):
                peaks.append((timestamp, rms_db))
            if len(peaks) >= self.audio_peak_limit:
                break
        return peaks

    def _candidate_seconds(
        self,
        duration_seconds: float,
        peaks: list[tuple[int, float]],
    ) -> list[int]:
        duration = max(1, math.ceil(duration_seconds))
        candidates = {
            second
            for timestamp, _ in peaks
            for second in range(
                max(0, timestamp - self.candidate_radius_seconds),
                min(duration, timestamp + self.candidate_radius_seconds + 1),
            )
        }
        tail_start = max(0, duration - self.tail_seconds)
        candidates.update(range(tail_start, duration, self.tail_interval_seconds))
        if not candidates:
            candidates.update(range(0, duration, self.tail_interval_seconds))
        return sorted(candidates)

    def _extract_hud_frames(self, asset: MediaAsset, directory: Path) -> dict[int, Path]:
        output_pattern = directory / "frame-%06d.png"
        command = [
            self.ffmpeg_binary,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-i",
            str(asset.source_path),
            "-vf",
            (
                "fps=1,"
                "crop=trunc(iw*0.6/2)*2:trunc(ih*0.14/2)*2:"
                "trunc(iw*0.2/2)*2:trunc(ih*0.16/2)*2,"
                "scale=iw*2:ih*2,format=gray,eq=contrast=2.0:brightness=0.08"
            ),
            "-frame_pts",
            "1",
            "-y",
            str(output_pattern),
        ]
        self._completed(command, "FFmpeg League HUD sampling")
        return {
            int(path.stem.rsplit("-", maxsplit=1)[1]): path
            for path in directory.glob("frame-*.png")
        }

    def _ocr_frame(self, timestamp: int, path: Path) -> tuple[int, str]:
        environment = dict(os.environ)
        environment["OMP_THREAD_LIMIT"] = "1"
        try:
            completed = subprocess.run(
                [self.tesseract_binary, str(path), "stdout", "--psm", "11"],
                check=False,
                capture_output=True,
                text=True,
                shell=False,
                timeout=30,
                env=environment,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise MediaToolError(f"Tesseract OCR failed for frame {timestamp}: {exc}") from exc
        if completed.returncode != 0:
            detail = completed.stderr.strip()[-2_000:]
            raise MediaToolError(f"Tesseract OCR failed for frame {timestamp}: {detail}")
        return timestamp, completed.stdout

    def _ocr_events(
        self,
        frame_paths: dict[int, Path],
        candidate_seconds: list[int],
    ) -> list[OcrEvent]:
        work = [
            (timestamp, frame_paths[timestamp])
            for timestamp in candidate_seconds
            if timestamp in frame_paths
        ]
        events: list[OcrEvent] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.ocr_workers) as executor:
            for timestamp, text in executor.map(lambda item: self._ocr_frame(*item), work):
                events.extend(parse_league_ocr_text(text, timestamp))
        return self._deduplicate_events(events)

    @staticmethod
    def _deduplicate_events(events: list[OcrEvent]) -> list[OcrEvent]:
        deduplicated: list[OcrEvent] = []
        for event in sorted(events, key=lambda item: item.timestamp_seconds):
            duplicate = next(
                (
                    previous
                    for previous in reversed(deduplicated)
                    if previous.event_name == event.event_name
                    and event.timestamp_seconds - previous.timestamp_seconds <= 2
                ),
                None,
            )
            if duplicate is None:
                deduplicated.append(event)
        return deduplicated

    @staticmethod
    def _event_signals(asset: MediaAsset, events: list[OcrEvent]) -> list[HighlightSignal]:
        signals: list[HighlightSignal] = []
        for index, event in enumerate(events):
            metadata = {**event.metadata, "raw_text": event.raw_text}
            identifier = (
                f"league-ocr-{event.event_name}-"
                f"{int(event.timestamp_seconds):06d}-{index:03d}"
            )
            signals.append(
                HighlightSignal(
                    id=identifier,
                    asset_id=asset.id,
                    timestamp_seconds=event.timestamp_seconds,
                    signal_type=HighlightSignalType.OCR_EVENT,
                    event_name=event.event_name,
                    confidence=event.confidence,
                    source="league_ocr:tesseract",
                    metadata=metadata,
                )
            )
            if event.event_name == "champion_kill":
                signals.extend(
                    [
                        HighlightSignal(
                            id=f"{identifier}-kill",
                            asset_id=asset.id,
                            timestamp_seconds=event.timestamp_seconds,
                            signal_type=HighlightSignalType.GAME_EVENT,
                            event_name="champion_kill",
                            confidence=event.confidence,
                            source="league_ocr:tesseract",
                            metadata=metadata,
                        ),
                        HighlightSignal(
                            id=f"{identifier}-death",
                            asset_id=asset.id,
                            timestamp_seconds=event.timestamp_seconds,
                            signal_type=HighlightSignalType.GAME_EVENT,
                            event_name="death",
                            confidence=event.confidence,
                            source="league_ocr:tesseract",
                            metadata=metadata,
                        ),
                    ]
                )
            elif event.event_name in {"multi_kill", "objective", "victory", "kill_streak"}:
                signals.append(
                    HighlightSignal(
                        id=f"{identifier}-game",
                        asset_id=asset.id,
                        timestamp_seconds=event.timestamp_seconds,
                        signal_type=HighlightSignalType.GAME_EVENT,
                        event_name=event.event_name,
                        confidence=event.confidence,
                        source="league_ocr:tesseract",
                        metadata=metadata,
                    )
                )
        return signals

    @staticmethod
    def _team_fight_signals(asset: MediaAsset, events: list[OcrEvent]) -> list[HighlightSignal]:
        kills = sorted(
            event.timestamp_seconds for event in events if event.event_name == "champion_kill"
        )
        clusters: list[list[float]] = []
        for timestamp in kills:
            if clusters and timestamp - clusters[-1][-1] <= 25:
                clusters[-1].append(timestamp)
            else:
                clusters.append([timestamp])
        signals = []
        for index, cluster in enumerate(item for item in clusters if len(item) >= 2):
            midpoint = (cluster[0] + cluster[-1]) / 2
            signals.append(
                HighlightSignal(
                    id=f"league-team-fight-{int(midpoint):06d}-{index:03d}",
                    asset_id=asset.id,
                    timestamp_seconds=midpoint,
                    signal_type=HighlightSignalType.GAME_EVENT,
                    event_name="team_fight",
                    confidence=min(0.98, 0.72 + len(cluster) * 0.08),
                    source="league_ocr:kill_cluster",
                    metadata={
                        "kill_count": len(cluster),
                        "cluster_start_seconds": cluster[0],
                        "cluster_end_seconds": cluster[-1],
                    },
                )
            )
        return signals

    @staticmethod
    def _audio_signals(
        asset: MediaAsset,
        peaks: list[tuple[int, float]],
    ) -> list[HighlightSignal]:
        if not peaks:
            return []
        loudest = max(level for _, level in peaks)
        quietest = min(level for _, level in peaks)
        spread = max(0.1, loudest - quietest)
        audio_stream = asset.audio_tracks[0].stream_index if asset.audio_tracks else None
        return [
            HighlightSignal(
                id=f"league-audio-peak-{timestamp:06d}",
                asset_id=asset.id,
                timestamp_seconds=timestamp,
                signal_type=HighlightSignalType.AUDIO_PEAK,
                event_name="fight_audio_peak",
                confidence=round(0.5 + ((rms_db - quietest) / spread) * 0.4, 4),
                source="ffmpeg:astats",
                source_audio_stream_index=audio_stream,
                metadata={"rms_db": round(rms_db, 4)},
            )
            for timestamp, rms_db in peaks
        ]

    def analyze(self, asset: MediaAsset, game: GameContext) -> HighlightAnalysis:
        if game.game_id != "league_of_legends":
            raise MediaToolError("The v1 OCR analyser currently supports League of Legends only")
        if not self.available():
            raise AnalyzerUnavailableError(
                "League VOD analysis requires both FFmpeg and Tesseract on the local machine"
            )

        levels = self._audio_levels(asset)
        peaks = self._audio_peaks(levels)
        candidates = self._candidate_seconds(asset.duration_seconds, peaks)
        with tempfile.TemporaryDirectory(prefix="vea-league-ocr-") as temporary:
            frame_paths = self._extract_hud_frames(asset, Path(temporary))
            events = self._ocr_events(frame_paths, candidates)

        signals = [
            *self._event_signals(asset, events),
            *self._team_fight_signals(asset, events),
            *self._audio_signals(asset, peaks),
        ]
        signals.sort(key=lambda item: (item.timestamp_seconds, item.id))
        return HighlightAnalysis(
            analyzer=self.name,
            asset_id=asset.id,
            source_fingerprint=asset.source_fingerprint,
            game=game,
            sampled_frame_count=len(candidates),
            audio_peak_count=len(peaks),
            signals=signals,
        )
