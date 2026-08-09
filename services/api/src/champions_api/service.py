from __future__ import annotations

from typing import Any

from champions_copilot.beliefs import BeliefState
from champions_copilot.decision import recommend_actions, recommend_team_preview
from champions_copilot.events import BattleEvent, apply_event, replay
from champions_copilot.mechanics import calculate_damage_range, effective_speed
from champions_copilot.team import PLAYER_TEAM, create_match

from .openai_adapter import OpenAIEventInterpreter
from .parser import interpret_locally
from .store import InMemoryStore, MatchRecord


class AppService:
    def __init__(self, store: InMemoryStore | None = None) -> None:
        self.store = store or InMemoryStore()
        self.openai = OpenAIEventInterpreter()

    def health(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "policy_version": "baseline-0.1",
            "validation_status": "UNVALIDATED_BASELINE",
            "openai_configured": self.openai.configured,
        }

    def team(self) -> dict[str, Any]:
        return {
            "id": "GMKXPHAS7D",
            "name": "washy Ranked Season M-4 replica",
            "members": [member.to_dict() for member in PLAYER_TEAM],
            "warning": (
                "Garchomp's fourth move and Kingambit's set must be confirmed from the replica team "
                "before mechanics validation. They are marked set_verified=false."
            ),
        }

    def create_match(self, payload: dict[str, Any]) -> dict[str, Any]:
        opponent_team = [str(name).strip() for name in payload.get("opponent_team", [])]
        if any(not name for name in opponent_team):
            raise ValueError("opponent team names cannot be empty")
        preview = recommend_team_preview(opponent_team)
        selected = list(payload.get("selected") or preview["selected"])
        lead = list(payload.get("lead") or preview["lead"])
        opponent_lead = payload.get("opponent_lead")
        state = create_match(
            opponent_team,
            selected_player=selected,
            player_lead=lead,
            opponent_lead=list(opponent_lead) if opponent_lead else None,
        )
        beliefs = BeliefState.from_battle(state)
        record = MatchRecord(
            initial_state=state,
            state=state,
            beliefs=beliefs,
            preview=preview,
        )
        self.store.create(record)
        return self._record_payload(record, include_recommendation=True)

    def list_matches(self) -> dict[str, Any]:
        return {
            "matches": [
                {
                    "match_id": record.state.match_id,
                    "turn": record.state.turn,
                    "phase": record.state.phase,
                    "revision": record.state.revision,
                }
                for record in self.store.all()
            ]
        }

    def get_match(self, match_id: str) -> dict[str, Any]:
        return self._record_payload(self.store.get(match_id), include_recommendation=False)

    def record_event(self, match_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        record = self.store.get(match_id)
        event = BattleEvent.create(str(payload.get("type", "")), dict(payload.get("payload", {})))
        next_state = apply_event(record.state, event)
        record.events.append(event)
        record.state = next_state
        record.beliefs.observe(next_state, event)
        return self._record_payload(record, include_recommendation=True)

    def correct_event(self, match_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        record = self.store.get(match_id)
        target_event_id = str(payload.get("target_event_id", ""))
        target = next(
            (event for event in record.events if event.id == target_event_id and event.type != "correction"),
            None,
        )
        if target is None:
            raise ValueError("target event does not exist")
        replacement = payload.get("replacement")
        if replacement is not None and not isinstance(replacement, dict):
            raise ValueError("replacement must be an event object or null")
        correction = BattleEvent.create(
            "correction",
            {"target_event_id": target_event_id, "replacement": replacement},
        )
        record.events.append(correction)
        self._rebuild(record)
        return self._record_payload(record, include_recommendation=True)

    def recommend(self, match_id: str) -> dict[str, Any]:
        record = self.store.get(match_id)
        return recommend_actions(record.state, record.beliefs).to_dict()

    def interpret(self, match_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        text = str(payload.get("text", "")).strip()
        if not text:
            raise ValueError("text is required")
        record = self.store.get(match_id)
        result = self.openai.interpret(text, record.state)
        if result is not None:
            return result
        return interpret_locally(text, record.state)

    def export_match(self, match_id: str) -> dict[str, Any]:
        record = self.store.get(match_id)
        replayed = replay(record.initial_state, self._effective_events(record.events))
        replayed.revision = len(record.events)
        if replayed.to_dict() != record.state.to_dict():
            raise RuntimeError("match replay diverged from canonical state")
        return {
            "schema_version": 1,
            "initial_state": record.initial_state.to_dict(),
            "events": [event.to_dict() for event in record.events],
            "final_state": record.state.to_dict(),
            "beliefs": record.beliefs.to_dict(),
            "preview": record.preview,
        }

    @staticmethod
    def _effective_events(events: list[BattleEvent]) -> list[BattleEvent]:
        corrections: dict[str, tuple[BattleEvent, dict[str, Any] | None]] = {}
        for event in events:
            if event.type == "correction":
                target = str(event.payload.get("target_event_id", ""))
                corrections[target] = (event, event.payload.get("replacement"))
        effective: list[BattleEvent] = []
        for event in events:
            if event.type == "correction":
                continue
            correction = corrections.get(event.id)
            if correction is None:
                effective.append(event)
                continue
            correction_event, replacement = correction
            if replacement is None:
                continue
            effective.append(
                BattleEvent(
                    id=f"{event.id}:corrected",
                    type=str(replacement.get("type", "")),
                    payload=dict(replacement.get("payload", {})),
                    created_at=correction_event.created_at,
                )
            )
        return effective

    def _rebuild(self, record: MatchRecord) -> None:
        state = replay(record.initial_state, [])
        beliefs = BeliefState.from_battle(state)
        for event in self._effective_events(record.events):
            state = apply_event(state, event)
            beliefs.observe(state, event)
        state.revision = len(record.events)
        record.state = state
        record.beliefs = beliefs

    def damage(self, payload: dict[str, Any]) -> dict[str, Any]:
        return calculate_damage_range(
            level=int(payload["level"]),
            power=int(payload["power"]),
            attack=int(payload["attack"]),
            defense=int(payload["defense"]),
            modifier=float(payload.get("modifier", 1.0)),
        ).to_dict()

    def speed(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "effective_speed": effective_speed(
                raw_speed=int(payload["raw_speed"]),
                stage=int(payload.get("stage", 0)),
                tailwind=bool(payload.get("tailwind", False)),
                paralysis=bool(payload.get("paralysis", False)),
                ability_or_item_modifier=float(payload.get("ability_or_item_modifier", 1.0)),
            )
        }

    @staticmethod
    def _record_payload(record: MatchRecord, include_recommendation: bool) -> dict[str, Any]:
        result = record.to_dict()
        if include_recommendation and record.state.phase == "battle":
            try:
                result["recommendation"] = recommend_actions(
                    record.state, record.beliefs
                ).to_dict()
            except ValueError:
                result["recommendation"] = None
        return result
