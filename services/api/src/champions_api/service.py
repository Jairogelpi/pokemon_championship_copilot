from __future__ import annotations

from typing import Any

from champions_copilot.beliefs import BeliefState
from champions_copilot.decision import recommend_actions, recommend_team_preview
from champions_copilot.events import BattleEvent, apply_event, replay
from champions_copilot.mechanics import calculate_damage_range, effective_speed
from champions_copilot.team import PLAYER_TEAM, create_match

from .battle_tools import BattleKnowledgeTools
from .codex_brain import CodexBattleBrain
from .openai_adapter import OpenAIEventInterpreter
from .meta import MetaRepository
from .multiturn import MultiTurnConfig, MultiTurnPlanner
from .opponent import build_response_model
from .parser import interpret_locally
from .regulation import CurrentChampionsRegulation
from .showdown import ShowdownCalculationError, ShowdownCalculator, ShowdownUnavailable
from .showdown_planner import calculate_turn_damage
from .store import InMemoryStore, MatchRecord


class AppService:
    def __init__(
        self,
        store: InMemoryStore | None = None,
        calculator: ShowdownCalculator | None = None,
        brain: CodexBattleBrain | None = None,
        multiturn: MultiTurnPlanner | None = None,
    ) -> None:
        self.store = store or InMemoryStore()
        self.openai = OpenAIEventInterpreter()
        self.brain = brain or CodexBattleBrain()
        self.calculator = calculator or ShowdownCalculator()
        self.regulation = CurrentChampionsRegulation(self.calculator.repo_root)
        self.meta = MetaRepository(self.calculator.repo_root, self.regulation)
        self.multiturn = multiturn or MultiTurnPlanner(
            self.calculator,
            self.meta,
            MultiTurnConfig.from_environment(default_enabled=self.brain.configured),
        )

    def close(self) -> None:
        self.calculator.close()

    def health(self) -> dict[str, Any]:
        calculator = self.calculator.health()
        regulation = self.regulation.status()
        return {
            "status": (
                "ok"
                if calculator.get("available") and regulation["active"]
                else "degraded"
            ),
            "policy_version": "codex-strategist-0.8",
            "validation_status": (
                "CODEX_STRATEGIST_AVAILABLE"
                if self.brain.configured
                else "ADVERSARIAL_SHOWDOWN_MODEL"
            ),
            "openai_configured": self.openai.configured or self.brain.configured,
            "codex_brain": self.brain.status(),
            "multi_turn": self.multiturn.status(),
            "showdown": calculator,
            "meta": self.meta.status(),
            "regulation": regulation,
        }

    def team(self) -> dict[str, Any]:
        legality = self.regulation.validate_team(PLAYER_TEAM)
        return {
            "id": "GMKXPHAS7D",
            "name": "washy Ranked Season M-4 replica",
            "members": [member.to_dict() for member in PLAYER_TEAM],
            "current_format": self.regulation.status(),
            "legality": legality.to_dict(),
            "warning": (
                "The displayed EVs are offensive archetype assumptions. Garchomp's fourth move and "
                "Kingambit's set still require confirmation. Champions-only Mega forms are pinned in "
                "the current format dataset and their stats, types, abilities, and entry weather/terrain "
                "are applied as calculator overrides. Unpublished custom-effect constants fail closed."
            ),
        }

    def create_match(self, payload: dict[str, Any]) -> dict[str, Any]:
        opponent_team = [str(name).strip() for name in payload.get("opponent_team", [])]
        if any(not name for name in opponent_team):
            raise ValueError("opponent team names cannot be empty")
        self.regulation.assert_preview(opponent_team)
        player_legality = self.regulation.validate_team(PLAYER_TEAM)
        if not player_legality.legal:
            raise ValueError("configured player team is illegal: " + "; ".join(player_legality.errors))
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
        event = self._enrich_event(record.state, event)
        self._validate_event_legality(record.state, event)
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
        if replacement is not None:
            replacement_event = BattleEvent.create(
                str(replacement.get("type", "")),
                dict(replacement.get("payload", {})),
            )
            replacement_event = self._enrich_event(record.initial_state, replacement_event)
            replacement = {
                "type": replacement_event.type,
                "payload": replacement_event.payload,
            }
        correction = BattleEvent.create(
            "correction",
            {"target_event_id": target_event_id, "replacement": replacement},
        )
        record.events.append(correction)
        self._rebuild(record)
        return self._record_payload(record, include_recommendation=True)

    def recommend(self, match_id: str) -> dict[str, Any]:
        record = self.store.get(match_id)
        return self._recommend(record)

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
            event = self._enrich_event(state, event)
            self._validate_event_legality(state, event)
            state = apply_event(state, event)
            beliefs.observe(state, event)
        state.revision = len(record.events)
        record.state = state
        record.beliefs = beliefs

    def _validate_event_legality(self, state: Any, event: BattleEvent) -> None:
        self.regulation.require_active()
        payload = event.payload
        if event.type == "move_used":
            side = state.side(str(payload.get("side", "")))
            pokemon_id = str(payload.get("pokemon", ""))
            pokemon = side.roster.get(pokemon_id)
            if pokemon is not None:
                self.regulation.assert_move(pokemon.name, str(payload.get("move", "")))
        elif event.type == "fact_revealed" and payload.get("key") == "item":
            side = state.side(str(payload.get("side", "")))
            pokemon_id = str(payload.get("pokemon", ""))
            pokemon = side.roster.get(pokemon_id)
            if pokemon is not None:
                self.regulation.assert_item_for_species(
                    pokemon.name,
                    str(payload["value"]) if payload.get("value") else None,
                )
        elif event.type == "mega_evolved":
            side = state.side(str(payload.get("side", "")))
            pokemon_id = str(payload.get("pokemon", ""))
            pokemon = side.roster.get(pokemon_id)
            if pokemon is not None:
                self.regulation.mega_evolution(
                    pokemon.name,
                    item=str(payload.get("mega_stone") or pokemon.item or "") or None,
                    form=str(payload.get("battle_form") or "") or None,
                )

    def _enrich_event(self, state: Any, event: BattleEvent) -> BattleEvent:
        if event.type != "mega_evolved":
            return event
        payload = dict(event.payload)
        side = state.side(str(payload.get("side", "")))
        pokemon_id = str(payload.get("pokemon", ""))
        pokemon = side.roster.get(pokemon_id)
        if pokemon is None:
            return event
        facts = side.known_facts.get(pokemon_id, {})
        item = payload.get("mega_stone") or facts.get("item") or pokemon.item
        resolved = self.regulation.mega_evolution(
            pokemon.name,
            item=str(item) if item else None,
            form=str(payload["battle_form"]) if payload.get("battle_form") else None,
        )
        payload.update(resolved)
        return BattleEvent(
            id=event.id,
            type=event.type,
            payload=payload,
            created_at=event.created_at,
        )

    def damage(self, payload: dict[str, Any]) -> dict[str, Any]:
        return calculate_damage_range(
            level=int(payload["level"]),
            power=int(payload["power"]),
            attack=int(payload["attack"]),
            defense=int(payload["defense"]),
            modifier=float(payload.get("modifier", 1.0)),
        ).to_dict()

    def showdown_damage(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.calculator.calculate(payload)

    def showdown_batch(self, payload: dict[str, Any]) -> dict[str, Any]:
        requests = payload.get("requests")
        if not isinstance(requests, list):
            raise ValueError("requests must be an array")
        return {"results": self.calculator.batch(requests)}

    def knowledge_lookup(self, payload: dict[str, Any]) -> dict[str, Any]:
        kind = str(payload.get("kind", ""))
        name = str(payload.get("name", ""))
        legality = None
        if kind.lower() in {"species", "pokemon", "move", "item"}:
            legality = self.regulation.lookup(kind, name)
            if not legality["legal"]:
                raise ValueError(f"entity is outside current Champions Doubles: {kind} {name}")
        result = self.calculator.lookup(
            kind,
            name,
            generation=int(payload.get("generation", 9)),
        )
        result["champions_legality"] = legality
        return result

    def knowledge_learnset(self, payload: dict[str, Any]) -> dict[str, Any]:
        result = self.regulation.learnset(str(payload.get("species", "")))
        return {
            "source": "Pokemon Champions current legality snapshot",
            "generation": "Champions",
            "species": result["species"],
            "moveCount": result["move_count"],
            "moves": [{"name": move} for move in result["moves"]],
            "regulation": result["regulation"],
            "season": result["season"],
            "snapshot_id": result["snapshot_id"],
        }

    def knowledge_type_matchup(self, payload: dict[str, Any]) -> dict[str, Any]:
        defender = str(payload.get("defender", ""))
        if not self.regulation.is_species_legal(defender):
            raise ValueError(f"defender is outside current Champions Doubles: {defender}")
        return self.calculator.type_matchup(
            str(payload.get("attack_type", "")),
            defender,
            generation=int(payload.get("generation", 9)),
        )

    def regulation_status(self) -> dict[str, Any]:
        return self.regulation.status()

    def regulation_lookup(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.regulation.lookup(
            str(payload.get("kind", "")), str(payload.get("name", ""))
        )

    def regulation_validate(self, payload: dict[str, Any]) -> dict[str, Any]:
        species = payload.get("species")
        if not isinstance(species, list):
            raise ValueError("species must be an array")
        self.regulation.require_active()
        return self.regulation.validate_preview(str(value) for value in species).to_dict()

    def meta_lookup(self, payload: dict[str, Any]) -> dict[str, Any]:
        species = str(payload.get("species", "")).strip()
        if not species:
            raise ValueError("species is required")
        return self.meta.get(species)

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

    def _recommend(self, record: MatchRecord) -> dict[str, Any]:
        self.regulation.require_active()
        if (
            record.recommendation_revision == record.state.revision
            and record.cached_recommendation is not None
        ):
            return record.cached_recommendation
        try:
            response_model = build_response_model(
                self.calculator, self.meta, record.state, record.beliefs
            )
            damage_estimates, incoming_threats, calculator_status = calculate_turn_damage(
                self.calculator,
                record.state,
                opponent_moves=response_model["damage_moves"],
                regulation=self.regulation,
            )
            calculator_status["knowledge"] = self.calculator.health().get("knowledge")
            calculator_status["meta"] = self.meta.status()
            calculator_status["opponent_response_coverage"] = response_model[
                "coverage_mass"
            ]
        except (ShowdownUnavailable, ShowdownCalculationError) as exc:
            damage_estimates = {}
            incoming_threats = {}
            response_model = {}
            calculator_status = {
                "available": False,
                "status": "unavailable",
                "engine": "@smogon/calc",
                "message": str(exc),
            }
        baseline = recommend_actions(
            record.state,
            record.beliefs,
            damage_estimates=damage_estimates,
            incoming_threats=incoming_threats,
            calculator_status=calculator_status,
            concrete_response_model=response_model,
        )
        if response_model:
            baseline = self.multiturn.plan(
                state=record.state,
                beliefs=record.beliefs,
                recommendation=baseline,
                response_model=response_model,
            )
        result = self.brain.decide(
            state=record.state,
            beliefs=record.beliefs,
            recommendation=baseline,
            events=[event.to_dict() for event in record.events],
            knowledge_tools=BattleKnowledgeTools(
                calculator=self.calculator,
                meta=self.meta,
                regulation=self.regulation,
                state=record.state,
                beliefs=record.beliefs,
                recommendation=baseline,
            ),
        )
        record.recommendation_revision = record.state.revision
        record.cached_recommendation = result
        return result

    def _record_payload(self, record: MatchRecord, include_recommendation: bool) -> dict[str, Any]:
        result = record.to_dict()
        if include_recommendation and record.state.phase == "battle":
            try:
                result["recommendation"] = self._recommend(record)
            except ValueError:
                result["recommendation"] = None
        return result
