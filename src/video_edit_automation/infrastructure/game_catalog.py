from __future__ import annotations

from video_edit_automation.domain.capture import CaptureObservation, GameCatalogEntry
from video_edit_automation.domain.gaming import DetectedGame, GameGenre

BUILTIN_GAMES: tuple[GameCatalogEntry, ...] = (
    GameCatalogEntry(
        game_id="league_of_legends",
        display_name="League of Legends",
        genre=GameGenre.MOBA,
        default_profile_id="generic_moba",
        process_names=[
            "League of Legends.exe",
            "LeagueClient.exe",
            "LeagueClientUx.exe",
        ],
        window_title_fragments=["League of Legends"],
    ),
)


def _normalized_process_name(value: str | None) -> str | None:
    if not value or not value.strip():
        return None
    return value.strip().replace("\\", "/").rsplit("/", maxsplit=1)[-1].casefold()


class RegistryGameDetector:
    """Deterministic game matching for observations supplied by any capture adapter."""

    def __init__(self, games: tuple[GameCatalogEntry, ...] = BUILTIN_GAMES) -> None:
        self._games = {game.game_id: game for game in games}

    def list_games(self) -> list[GameCatalogEntry]:
        return sorted(self._games.values(), key=lambda game: game.game_id)

    def get_game(self, game_id: str) -> GameCatalogEntry | None:
        return self._games.get(game_id)

    def detect(self, observation: CaptureObservation) -> DetectedGame | None:
        observed_process = _normalized_process_name(observation.process_name)
        observed_title = (observation.window_title or "").casefold()
        observed_bundle = (observation.bundle_id or "").strip().casefold()
        candidates: list[DetectedGame] = []

        for game in self._games.values():
            evidence: list[str] = []
            scores: list[float] = []

            known_processes = {
                normalized
                for process in game.process_names
                if (normalized := _normalized_process_name(process)) is not None
            }
            if observed_process and observed_process in known_processes:
                evidence.append(f"process name matched {observation.process_name}")
                scores.append(0.95)

            for fragment in game.window_title_fragments:
                if observed_title and fragment.casefold() in observed_title:
                    evidence.append(f"window title contains {fragment}")
                    scores.append(0.75)
                    break

            known_bundles = {bundle.casefold() for bundle in game.bundle_ids}
            if observed_bundle and observed_bundle in known_bundles:
                evidence.append(f"bundle ID matched {observation.bundle_id}")
                scores.append(0.98)

            if scores:
                confidence = min(1.0, max(scores) + 0.03 * (len(scores) - 1))
                candidates.append(
                    DetectedGame(
                        game_id=game.game_id,
                        display_name=game.display_name,
                        genre=game.genre,
                        confidence=confidence,
                        evidence=evidence,
                        game_profile_id=game.default_profile_id,
                    )
                )

        if not candidates:
            return None
        return sorted(candidates, key=lambda item: (-item.confidence, item.game_id))[0]
