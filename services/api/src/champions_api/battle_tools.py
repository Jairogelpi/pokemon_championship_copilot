from __future__ import annotations

from typing import Any, Callable

from champions_copilot.beliefs import BeliefState
from champions_copilot.decision import RankedAction, Recommendation
from champions_copilot.models import BattleState

from .meta import MetaRepository
from .showdown import ShowdownCalculationError, ShowdownCalculator, ShowdownUnavailable
from .showdown_planner import calculate_canonical_damage


class BattleKnowledgeTools:
    """Read-only, source-labelled battle knowledge exposed to Codex.

    These tools never mutate state and never accept free-form damage values. Every
    answer comes from the canonical state, the ranked engine output, pinned
    Pokémon/Showdown data, or the versioned meta snapshot.
    """

    ENTITY_KINDS = ("species", "move", "item", "ability", "nature", "type")

    def __init__(
        self,
        *,
        calculator: ShowdownCalculator,
        meta: MetaRepository,
        state: BattleState,
        beliefs: BeliefState,
        recommendation: Recommendation,
    ) -> None:
        self.calculator = calculator
        self.meta = meta
        self.state = state
        self.beliefs = beliefs
        self.recommendation = recommendation
        self._candidates = {
            f"candidate-{rank:02d}": candidate
            for rank, candidate in enumerate(recommendation.candidate_catalog, start=1)
        }

    def manifest(self) -> dict[str, Any]:
        response_model = self.recommendation.response_model
        return {
            "mode": "read_only_verified_battle_knowledge",
            "generation": 9,
            "candidate_ids": list(self._candidates),
            "search_space": response_model.get("search_space", {}),
            "mechanics": self.recommendation.calculator,
            "meta": self.meta.status(),
            "active_player": [
                self.state.player.roster[pokemon_id].name
                for pokemon_id in self.state.player.active
            ],
            "active_opponent": [
                self.state.opponent.roster[pokemon_id].name
                for pokemon_id in self.state.opponent.active
            ],
            "authority_boundary": {
                "mechanics": "pinned @pkmn/dex, @pkmn/data, and @smogon/calc",
                "strategy_priors": "dated community meta snapshot",
                "hidden_information": "belief distribution plus explicit unknown branches",
            },
        }

    def definitions(self) -> list[dict[str, Any]]:
        no_arguments = {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        }
        nullable_string = {"type": ["string", "null"]}
        return [
            self._definition(
                "inspect_position",
                "Inspect canonical battle state, beliefs, source versions, and exhaustive-search coverage.",
                no_arguments,
            ),
            self._definition(
                "inspect_candidate",
                "Inspect one verified legal joint action with its score and worst counter-lines.",
                {
                    "type": "object",
                    "properties": {"candidate_id": {"type": "string"}},
                    "required": ["candidate_id"],
                    "additionalProperties": False,
                },
            ),
            self._definition(
                "inspect_damage_evidence",
                "Read exact precomputed outgoing and incoming damage evidence for a candidate; optional filters narrow the matrix.",
                {
                    "type": "object",
                    "properties": {
                        "candidate_id": {"type": "string"},
                        "actor": nullable_string,
                        "move": nullable_string,
                        "target": nullable_string,
                    },
                    "required": ["candidate_id", "actor", "move", "target"],
                    "additionalProperties": False,
                },
            ),
            self._definition(
                "calculate_verified_damage",
                "Test any active actor's Gen 9-learnable move against a current target using canonical state and pinned damage rolls.",
                {
                    "type": "object",
                    "properties": {
                        "side": {"type": "string", "enum": ["player", "opponent"]},
                        "actor": {"type": "string"},
                        "move": {"type": "string"},
                        "target": {"type": "string"},
                    },
                    "required": ["side", "actor", "move", "target"],
                    "additionalProperties": False,
                },
            ),
            self._definition(
                "lookup_battle_entity",
                "Query pinned Gen 9 data for a species, move, item, ability, nature, or type.",
                {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string", "enum": list(self.ENTITY_KINDS)},
                        "name": {"type": "string"},
                    },
                    "required": ["kind", "name"],
                    "additionalProperties": False,
                },
            ),
            self._definition(
                "lookup_learnset",
                "Query the complete generation-compatible learnset for a species.",
                {
                    "type": "object",
                    "properties": {
                        "species": {"type": "string"},
                        "restriction": nullable_string,
                    },
                    "required": ["species", "restriction"],
                    "additionalProperties": False,
                },
            ),
            self._definition(
                "lookup_type_matchup",
                "Query the pinned type chart for one attack type against a defending species.",
                {
                    "type": "object",
                    "properties": {
                        "attack_type": {"type": "string"},
                        "defender": {"type": "string"},
                    },
                    "required": ["attack_type", "defender"],
                    "additionalProperties": False,
                },
            ),
            self._definition(
                "lookup_meta",
                "Read the dated strategy-prior snapshot for a species; it is not a mechanics authority.",
                {
                    "type": "object",
                    "properties": {"species": {"type": "string"}},
                    "required": ["species"],
                    "additionalProperties": False,
                },
            ),
        ]

    def execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
            "inspect_position": self._inspect_position,
            "inspect_candidate": self._inspect_candidate,
            "inspect_damage_evidence": self._inspect_damage_evidence,
            "calculate_verified_damage": self._calculate_verified_damage,
            "lookup_battle_entity": self._lookup_battle_entity,
            "lookup_learnset": self._lookup_learnset,
            "lookup_type_matchup": self._lookup_type_matchup,
            "lookup_meta": self._lookup_meta,
        }
        if name not in handlers:
            raise ValueError(f"unknown battle knowledge tool: {name}")
        try:
            return handlers[name](arguments)
        except (KeyError, TypeError, ValueError, ShowdownCalculationError, ShowdownUnavailable) as exc:
            return {
                "ok": False,
                "tool": name,
                "error_type": type(exc).__name__,
                "message": str(exc),
                "fabricated": False,
            }

    @staticmethod
    def _definition(name: str, description: str, parameters: dict[str, Any]) -> dict[str, Any]:
        return {
            "type": "function",
            "name": name,
            "description": description,
            "strict": True,
            "parameters": parameters,
        }

    @staticmethod
    def _required_text(arguments: dict[str, Any], field: str) -> str:
        value = arguments.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} must be a non-empty string")
        if len(value) > 100:
            raise ValueError(f"{field} is too long")
        return value.strip()

    def _candidate(self, arguments: dict[str, Any]) -> tuple[str, RankedAction]:
        candidate_id = self._required_text(arguments, "candidate_id")
        candidate = self._candidates.get(candidate_id)
        if candidate is None:
            raise ValueError(f"candidate_id is outside the verified envelope: {candidate_id}")
        return candidate_id, candidate

    def _inspect_position(self, _: dict[str, Any]) -> dict[str, Any]:
        return {
            "ok": True,
            "source": "canonical_state_and_deterministic_search",
            "state": self.state.to_dict(),
            "beliefs": self.beliefs.to_dict(),
            "manifest": self.manifest(),
            "response_model": self.recommendation.response_model,
            "assumptions": list(self.recommendation.assumptions),
        }

    def _inspect_candidate(self, arguments: dict[str, Any]) -> dict[str, Any]:
        candidate_id, candidate = self._candidate(arguments)
        value = candidate.to_dict()
        return {
            "ok": True,
            "source": "deterministic_legal_action_ranker",
            "candidate_id": candidate_id,
            "action": value["action"],
            "label": value["label"],
            "score": value["score"],
            "covers": value["covers"],
            "principal_lines": value["principal_lines"],
            "damage_evidence_count": len(value["damage"]),
            "threat_evidence_count": len(value["threats"]),
        }

    def _inspect_damage_evidence(self, arguments: dict[str, Any]) -> dict[str, Any]:
        candidate_id, candidate = self._candidate(arguments)
        filters = {
            key: value.strip().lower()
            for key in ("actor", "move", "target")
            if isinstance((value := arguments.get(key)), str) and value.strip()
        }

        def matches(row: dict[str, Any]) -> bool:
            return all(str(row.get(key, "")).lower() == value for key, value in filters.items())

        value = candidate.to_dict()
        outgoing = [row for row in value["damage"] if matches(row)]
        incoming = [row for row in value["threats"] if matches(row)]
        return {
            "ok": True,
            "source": "pinned_@smogon/calc_precomputed_turn_matrix",
            "candidate_id": candidate_id,
            "filters": filters,
            "outgoing": outgoing,
            "incoming": incoming,
            "records": len(outgoing) + len(incoming),
            "fabricated": False,
        }

    def _lookup_battle_entity(self, arguments: dict[str, Any]) -> dict[str, Any]:
        kind = self._required_text(arguments, "kind")
        if kind not in self.ENTITY_KINDS:
            raise ValueError(f"unsupported entity kind: {kind}")
        name = self._required_text(arguments, "name")
        return {
            "ok": True,
            "source": "pinned_@pkmn/dex",
            "result": self.calculator.lookup(kind, name, generation=9),
        }

    def _resolve_pokemon_id(self, side: str, value: str) -> str:
        side_state = self.state.side(side)
        normalized = "".join(character for character in value.lower() if character.isalnum())
        for pokemon_id, pokemon in side_state.roster.items():
            candidates = (pokemon_id, pokemon.name)
            if normalized in {
                "".join(character for character in candidate.lower() if character.isalnum())
                for candidate in candidates
            }:
                return pokemon_id
        raise ValueError(f"Pokémon is not present on the {side} roster: {value}")

    def _calculate_verified_damage(self, arguments: dict[str, Any]) -> dict[str, Any]:
        side = self._required_text(arguments, "side")
        if side not in {"player", "opponent"}:
            raise ValueError("side must be player or opponent")
        actor = self._resolve_pokemon_id(side, self._required_text(arguments, "actor"))
        target_side = "opponent" if side == "player" else "player"
        target = self._resolve_pokemon_id(
            target_side, self._required_text(arguments, "target")
        )
        move = self._required_text(arguments, "move")
        return {
            "ok": True,
            "source": "canonical_state_plus_pinned_learnset_and_@smogon/calc",
            "result": calculate_canonical_damage(
                self.calculator,
                self.state,
                side=side,
                actor_id=actor,
                move=move,
                target_id=target,
            ),
        }

    def _lookup_learnset(self, arguments: dict[str, Any]) -> dict[str, Any]:
        species = self._required_text(arguments, "species")
        restriction_value = arguments.get("restriction")
        if restriction_value is not None and not isinstance(restriction_value, str):
            raise TypeError("restriction must be a string or null")
        restriction = restriction_value.strip() if restriction_value else None
        return {
            "ok": True,
            "source": "pinned_@pkmn/data_and_@pkmn/dex",
            "result": self.calculator.learnset(
                species, generation=9, restriction=restriction
            ),
        }

    def _lookup_type_matchup(self, arguments: dict[str, Any]) -> dict[str, Any]:
        attack_type = self._required_text(arguments, "attack_type")
        defender = self._required_text(arguments, "defender")
        return {
            "ok": True,
            "source": "pinned_@pkmn/dex_type_chart",
            "result": self.calculator.type_matchup(attack_type, defender, generation=9),
        }

    def _lookup_meta(self, arguments: dict[str, Any]) -> dict[str, Any]:
        species = self._required_text(arguments, "species")
        return {
            "ok": True,
            "source": "versioned_strategy_prior_not_mechanics",
            "result": self.meta.get(species),
        }
