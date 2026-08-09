from __future__ import annotations

from dataclasses import dataclass, field
from threading import RLock
from typing import Any

from champions_copilot.beliefs import BeliefState
from champions_copilot.events import BattleEvent
from champions_copilot.models import BattleState


@dataclass(slots=True)
class MatchRecord:
    initial_state: BattleState
    state: BattleState
    beliefs: BeliefState
    events: list[BattleEvent] = field(default_factory=list)
    preview: dict[str, Any] = field(default_factory=dict)
    recommendation_revision: int = -1
    cached_recommendation: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.to_dict(),
            "beliefs": self.beliefs.to_dict(),
            "events": [event.to_dict() for event in self.events],
            "preview": self.preview,
        }


class InMemoryStore:
    def __init__(self) -> None:
        self._records: dict[str, MatchRecord] = {}
        self._lock = RLock()

    def create(self, record: MatchRecord) -> MatchRecord:
        with self._lock:
            match_id = record.state.match_id
            if match_id in self._records:
                raise ValueError("match already exists")
            self._records[match_id] = record
            return record

    def get(self, match_id: str) -> MatchRecord:
        with self._lock:
            try:
                return self._records[match_id]
            except KeyError as exc:
                raise KeyError(f"unknown match: {match_id}") from exc

    def all(self) -> list[MatchRecord]:
        with self._lock:
            return list(self._records.values())
