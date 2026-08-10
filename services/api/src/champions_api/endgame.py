from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from typing import Any

from champions_copilot.actions import JointAction, SingleAction
from champions_copilot.models import BattleState, PokemonState
from champions_copilot.search import (
    ChanceOutcome,
    ExhaustiveEndgameResult,
    ExhaustiveEndgameSolver,
    ExhaustiveEndgameUnavailable,
    WeightedResponse,
)

from .multiturn import (
    ExactResolutionUnavailable,
    MultiTurnConfig,
    PlanningState,
    VerifiedTurnResolver,
)
from .regulation import CurrentChampionsRegulation
from .showdown import ShowdownCalculator


SINGLE_FOE_TARGETS = {"normal", "any", "adjacentFoe"}
SPREAD_TARGETS = {"allAdjacent", "allAdjacentFoes", "all"}
UNVERIFIED_PER_HIT_MOVES = {
    "beatup",
    "dragondarts",
    "populationbomb",
    "tripleaxel",
    "triplekick",
}


@dataclass(frozen=True, slots=True)
class EndgameEligibility:
    eligible: bool
    reasons: tuple[str, ...]
    player_living: int
    opponent_living: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "eligible": self.eligible,
            "reasons": list(self.reasons),
            "player_living": self.player_living,
            "opponent_living": self.opponent_living,
            "scope": "closed active-only Champions doubles endgames",
        }


def _living_ids(state: BattleState, side_name: str) -> list[str]:
    side = state.side(side_name)
    selected = side.selected or list(side.roster)
    return [pokemon_id for pokemon_id in selected if not side.roster[pokemon_id].fainted]


def _exact_moves(state: BattleState, side_name: str, pokemon_id: str) -> tuple[str, ...]:
    side = state.side(side_name)
    pokemon = side.roster[pokemon_id]
    if side_name == "player":
        return tuple(pokemon.moves)
    if pokemon.set_verified and pokemon.moves:
        return tuple(pokemon.moves)
    return tuple(pokemon.revealed_moves)


def assess_endgame_eligibility(
    state: BattleState,
    calculator: ShowdownCalculator,
    regulation: CurrentChampionsRegulation,
) -> EndgameEligibility:
    reasons: list[str] = []
    player_living = _living_ids(state, "player")
    opponent_living = _living_ids(state, "opponent")
    if state.phase != "battle":
        reasons.append("battle_not_active")
    try:
        regulation.require_active()
    except RuntimeError:
        reasons.append("current_regulation_snapshot_inactive")
    for side_name, living in (
        ("player", player_living),
        ("opponent", opponent_living),
    ):
        side = state.side(side_name)
        living_bench = [pokemon_id for pokemon_id in side.bench if pokemon_id in living]
        if living_bench:
            reasons.append(f"{side_name}_has_living_reserves")
        if not living:
            reasons.append(f"{side_name}_already_terminal")
        if len(living) > 2:
            reasons.append(f"{side_name}_more_than_two_living_pokemon")
        for pokemon_id in living:
            pokemon = side.roster[pokemon_id]
            if pokemon_id not in side.active:
                reasons.append(f"{side_name}:{pokemon_id}:not_active")
            if pokemon.status is not None:
                reasons.append(f"{side_name}:{pokemon_id}:status_cycle_not_closed")
            if side_name == "player":
                if not pokemon.set_verified:
                    reasons.append(f"player:{pokemon_id}:set_not_verified")
                if not pokemon.ability or pokemon.nature is None:
                    reasons.append(f"player:{pokemon_id}:set_facts_incomplete")
            else:
                facts = side.known_facts.get(pokemon_id, {})
                required = {"ability", "item", "nature", "evs", "ivs"}
                missing = sorted(required - set(facts))
                if missing:
                    reasons.append(
                        f"opponent:{pokemon_id}:missing_set_facts:{','.join(missing)}"
                    )
                if not pokemon.ability:
                    reasons.append(f"opponent:{pokemon_id}:ability_unknown")
            moves = _exact_moves(state, side_name, pokemon_id)
            if not 1 <= len(moves) <= 4:
                reasons.append(f"{side_name}:{pokemon_id}:complete_moveset_required")
                continue
            for move in moves:
                try:
                    regulation.assert_move(pokemon.name, move)
                    move_data = calculator.lookup("move", move)["entry"]
                except (KeyError, ValueError, RuntimeError):
                    reasons.append(f"{side_name}:{pokemon_id}:{move}:move_not_verified")
                    continue
                move_id = str(move_data.get("id", "")).lower()
                accuracy = move_data.get("accuracy")
                target = str(move_data.get("target", ""))
                if move_data.get("category") == "Status":
                    reasons.append(f"{side_name}:{pokemon_id}:{move}:status_move_cycle")
                if accuracy is not True and float(accuracy or 0) < 100:
                    reasons.append(f"{side_name}:{pokemon_id}:{move}:miss_cycle")
                if target not in SINGLE_FOE_TARGETS | SPREAD_TARGETS:
                    reasons.append(f"{side_name}:{pokemon_id}:{move}:target_not_closed")
                if move_data.get("heal") or move_data.get("drain"):
                    reasons.append(f"{side_name}:{pokemon_id}:{move}:healing_cycle")
                if move_data.get("forceSwitch"):
                    reasons.append(f"{side_name}:{pokemon_id}:{move}:forced_switch")
                if move_data.get("multihit"):
                    reasons.append(
                        f"{side_name}:{pokemon_id}:{move}:per_hit_state_callbacks"
                    )
                secondary_rows = [
                    row
                    for row in (
                        [move_data.get("secondary")]
                        + list(move_data.get("secondaries") or [])
                    )
                    if isinstance(row, dict)
                ]
                if move_data.get("status") or any(
                    row.get("status") or row.get("volatileStatus")
                    for row in secondary_rows
                ):
                    reasons.append(f"{side_name}:{pokemon_id}:{move}:status_secondary")
                if move_id in UNVERIFIED_PER_HIT_MOVES:
                    reasons.append(f"{side_name}:{pokemon_id}:{move}:per_hit_callback")
    return EndgameEligibility(
        eligible=not reasons,
        reasons=tuple(sorted(set(reasons))),
        player_living=len(player_living),
        opponent_living=len(opponent_living),
    )


class ExactChampionsEndgame:
    def __init__(
        self,
        *,
        calculator: ShowdownCalculator,
        regulation: CurrentChampionsRegulation,
        config: MultiTurnConfig,
        max_paths_per_transition: int,
    ) -> None:
        self.calculator = calculator
        self.regulation = regulation
        self.resolver = VerifiedTurnResolver(calculator, config, regulation)
        self.max_paths_per_transition = max_paths_per_transition
        self.root_actions: dict[str, JointAction] = {}

    def state_key(self, state: PlanningState) -> str:
        return state.key()

    @staticmethod
    def terminal_value(state: PlanningState) -> float | None:
        player_ids = state.battle.player.selected or list(state.battle.player.roster)
        opponent_ids = state.battle.opponent.selected or list(
            state.battle.opponent.roster
        )
        player_out = all(
            state.battle.player.roster[pokemon_id].fainted
            for pokemon_id in player_ids
        )
        opponent_out = all(
            state.battle.opponent.roster[pokemon_id].fainted
            for pokemon_id in opponent_ids
        )
        if player_out and opponent_out:
            return 0.0
        if player_out:
            return -100.0
        if opponent_out:
            return 100.0
        return None

    def evaluate(self, state: PlanningState) -> float:
        del state
        raise ExhaustiveEndgameUnavailable("heuristic evaluation is forbidden")

    def _mega_options(
        self, state: BattleState, side_name: str, pokemon: PokemonState
    ) -> tuple[bool, ...]:
        side = state.side(side_name)
        if pokemon.mega_evolved or any(
            member.mega_evolved
            for member in side.roster.values()
            if member.id != pokemon.id
        ):
            return (False,)
        if side_name == "player":
            return (False, True) if pokemon.can_mega_evolve else (False,)
        try:
            self.regulation.mega_evolution(pokemon.name, item=pokemon.item)
        except ValueError:
            return (False,)
        return (False, True)

    def _actor_actions(
        self, state: BattleState, side_name: str, pokemon_id: str
    ) -> list[SingleAction | dict[str, Any]]:
        side = state.side(side_name)
        opposing_name = "opponent" if side_name == "player" else "player"
        opposing = state.side(opposing_name)
        pokemon = side.roster[pokemon_id]
        targets = [
            target_id
            for target_id in opposing.active
            if not opposing.roster[target_id].fainted
        ]
        actions: list[SingleAction | dict[str, Any]] = []
        for move in _exact_moves(state, side_name, pokemon_id):
            move_data = self.calculator.lookup("move", move)["entry"]
            move_target = str(move_data.get("target", ""))
            move_targets = (
                ["opponents" if side_name == "player" else "players"]
                if move_target in SPREAD_TARGETS
                else targets
            )
            for target in move_targets:
                for mega in self._mega_options(state, side_name, pokemon):
                    if side_name == "player":
                        actions.append(
                            SingleAction(
                                actor=pokemon_id,
                                kind="move",
                                move=move,
                                target=target,
                                mega=mega,
                            )
                        )
                    else:
                        payload: dict[str, Any] = {
                            "actor": pokemon_id,
                            "kind": "move",
                            "move": move,
                            "target": target,
                        }
                        if mega:
                            resolved = self.regulation.mega_evolution(
                                pokemon.name, item=pokemon.item
                            )
                            payload.update(
                                {
                                    "mega": True,
                                    "mega_stone": resolved["mega_stone"],
                                    "mega_form": resolved["battle_form"],
                                }
                            )
                        actions.append(payload)
        return actions

    def player_actions(self, state: PlanningState) -> tuple[JointAction, ...]:
        pools = [
            self._actor_actions(state.battle, "player", pokemon_id)
            for pokemon_id in state.battle.player.active
            if not state.battle.player.roster[pokemon_id].fainted
        ]
        actions = tuple(
            JointAction(tuple(choice for choice in choices if isinstance(choice, SingleAction)))
            for choices in product(*pools)
            if sum(bool(getattr(choice, "mega", False)) for choice in choices) <= 1
        )
        for action in actions:
            self.root_actions.setdefault(action.label(state.battle), action)
        return actions

    def action_label(self, state: PlanningState, action: JointAction) -> str:
        return action.label(state.battle)

    def opponent_responses(
        self, state: PlanningState, action: JointAction
    ) -> tuple[WeightedResponse, ...]:
        del action
        pools = [
            self._actor_actions(state.battle, "opponent", pokemon_id)
            for pokemon_id in state.battle.opponent.active
            if not state.battle.opponent.roster[pokemon_id].fainted
        ]
        responses: list[WeightedResponse] = []
        for choices in product(*pools):
            payloads = [dict(choice) for choice in choices if isinstance(choice, dict)]
            if sum(bool(choice.get("mega")) for choice in payloads) > 1:
                continue
            label = " + ".join(
                f"{state.battle.opponent.roster[choice['actor']].name}: "
                f"{'Mega + ' if choice.get('mega') else ''}{choice['move']}→{choice['target']}"
                for choice in payloads
            )
            responses.append(
                WeightedResponse(
                    id=label,
                    probability=1.0,
                    payload={"label": label, "actions": payloads},
                )
            )
        return tuple(responses)

    def chance_outcomes(
        self,
        state: PlanningState,
        action: JointAction,
        response: WeightedResponse,
    ) -> tuple[ChanceOutcome, ...]:
        outcomes = self.resolver.resolve_exact(
            state,
            action,
            dict(response.payload or {}),
            max_paths=self.max_paths_per_transition,
        )
        return tuple(
            ChanceOutcome(
                id=outcome.id,
                probability=outcome.probability,
                next_state=outcome.next_state,
                immediate_reward=0.0,
            )
            for outcome in outcomes
        )


def solve_current_endgame(
    *,
    state: BattleState,
    calculator: ShowdownCalculator,
    regulation: CurrentChampionsRegulation,
    config: MultiTurnConfig,
    max_states: int,
    max_chance_branches: int,
    max_paths_per_transition: int,
    time_budget_ms: int,
) -> tuple[EndgameEligibility, ExhaustiveEndgameResult | None, ExactChampionsEndgame | None, str | None]:
    eligibility = assess_endgame_eligibility(state, calculator, regulation)
    if not eligibility.eligible:
        return eligibility, None, None, None
    game = ExactChampionsEndgame(
        calculator=calculator,
        regulation=regulation,
        config=config,
        max_paths_per_transition=max_paths_per_transition,
    )
    try:
        result = ExhaustiveEndgameSolver(
            max_states=max_states,
            max_chance_branches=max_chance_branches,
            time_budget_ms=time_budget_ms,
        ).solve(game, PlanningState.initial(state))
    except (ExhaustiveEndgameUnavailable, ExactResolutionUnavailable) as exc:
        return eligibility, None, game, str(exc)
    return eligibility, result, game, None
