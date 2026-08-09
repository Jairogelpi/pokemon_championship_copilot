from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .events import BattleEvent
from .models import BattleState


def normalize(values: dict[str, float]) -> dict[str, float]:
    cleaned = {key: max(0.0, float(value)) for key, value in values.items()}
    total = sum(cleaned.values())
    if total <= 0:
        share = 1.0 / max(1, len(cleaned))
        return {key: share for key in cleaned}
    return {key: value / total for key, value in cleaned.items()}


@dataclass(slots=True)
class PokemonBelief:
    pokemon_id: str
    known_moves: list[str] = field(default_factory=list)
    mega_probability: float = 0.0
    action_categories: dict[str, float] = field(
        default_factory=lambda: {
            "attack": 0.48,
            "protect": 0.16,
            "switch": 0.16,
            "speed_control": 0.10,
            "setup_or_control": 0.05,
            "other": 0.05,
        }
    )
    evidence: list[str] = field(default_factory=list)

    def normalize(self) -> None:
        self.action_categories = normalize(self.action_categories)
        self.mega_probability = max(0.0, min(1.0, self.mega_probability))

    def to_dict(self) -> dict[str, Any]:
        return {
            "pokemon_id": self.pokemon_id,
            "known_moves": list(self.known_moves),
            "mega_probability": self.mega_probability,
            "action_categories": dict(self.action_categories),
            "evidence": list(self.evidence),
        }


@dataclass(slots=True)
class BeliefState:
    opponent: dict[str, PokemonBelief]
    version: int = 1

    @classmethod
    def from_battle(cls, state: BattleState) -> BeliefState:
        candidates = [
            member
            for member in state.opponent.roster.values()
            if member.role in {"special-pressure", "speed-control", "setup", "unknown"}
        ]
        candidate_probability = 0.9 / max(1, len(candidates))
        beliefs: dict[str, PokemonBelief] = {}
        for member in state.opponent.roster.values():
            belief = PokemonBelief(
                pokemon_id=member.id,
                mega_probability=candidate_probability if member in candidates else 0.02,
            )
            if member.role == "trick-room":
                belief.action_categories.update(
                    {"attack": 0.25, "speed_control": 0.4, "setup_or_control": 0.15}
                )
            elif member.role == "pivot":
                belief.action_categories.update({"attack": 0.32, "switch": 0.33})
            belief.normalize()
            beliefs[member.id] = belief
        cls._normalize_mega(beliefs)
        return cls(opponent=beliefs)

    @staticmethod
    def _normalize_mega(beliefs: dict[str, PokemonBelief]) -> None:
        total = sum(value.mega_probability for value in beliefs.values())
        if total <= 0:
            return
        for value in beliefs.values():
            value.mega_probability /= total

    def observe(self, state: BattleState, event: BattleEvent) -> None:
        payload = event.payload
        if payload.get("side") != "opponent":
            return
        pokemon_id = payload.get("pokemon") or payload.get("in")
        belief = self.opponent.get(str(pokemon_id))
        if belief is None:
            return

        if event.type == "move_used":
            move = str(payload.get("move", "")).strip()
            if move and move not in belief.known_moves:
                belief.known_moves.append(move)
            lower = move.lower()
            if lower == "protect":
                belief.action_categories["protect"] += 0.45
            elif lower in {"tailwind", "trick room", "icy wind"}:
                belief.action_categories["speed_control"] += 0.45
            elif lower in {"parting shot", "haze", "toxic spikes", "recover"}:
                belief.action_categories["setup_or_control"] += 0.4
            else:
                belief.action_categories["attack"] += 0.35
            belief.evidence.append(f"revealed move: {move}")

        elif event.type == "switch":
            belief.action_categories["switch"] += 0.3
            belief.evidence.append(f"switched in on turn {state.turn}")

        elif event.type == "mega_evolved":
            for id, candidate in self.opponent.items():
                candidate.mega_probability = 1.0 if id == pokemon_id else 0.0
            belief.evidence.append("Mega Evolution confirmed")

        elif event.type == "fact_revealed":
            key = payload.get("key")
            belief.evidence.append(f"confirmed {key}: {payload.get('value')}")

        belief.normalize()
        self._normalize_mega(self.opponent)
        self.version += 1

    def active_action_distribution(self, state: BattleState) -> dict[str, float]:
        active = [self.opponent[id].action_categories for id in state.opponent.active]
        if not active:
            return {"other": 1.0}
        keys = set().union(*(distribution.keys() for distribution in active))
        return normalize(
            {key: sum(distribution.get(key, 0.0) for distribution in active) / len(active) for key in keys}
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "opponent": {id: belief.to_dict() for id, belief in self.opponent.items()},
        }
