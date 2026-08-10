from __future__ import annotations

import hashlib
import json
import os
import random
from dataclasses import dataclass, replace
from itertools import combinations, permutations
from typing import Any

from champions_copilot.actions import (
    JointAction,
    SingleAction,
    generate_legal_joint_actions,
)
from champions_copilot.beliefs import BeliefState
from champions_copilot.decision import (
    Recommendation,
    base_action_value,
    synergy_value,
)
from champions_copilot.models import BattleState
from champions_copilot.search import (
    ChanceOutcome,
    RiskAwareExpectiminimax,
    SearchBudgetExhausted,
    SearchConfig,
    WeightedResponse,
)

from .meta import MetaRepository
from .opponent import build_response_model
from .regulation import CurrentChampionsRegulation
from .showdown import ShowdownCalculationError, ShowdownCalculator, ShowdownUnavailable
from .showdown_planner import calculate_canonical_damage, calculate_canonical_speed


STATUS_IDS = {
    "brn": "burn",
    "psn": "poison",
    "tox": "toxic",
    "par": "paralysis",
    "slp": "sleep",
    "frz": "freeze",
}


class UnexpandedOpponentAction(ValueError):
    """The explicit residual-other hypothesis was selected."""

HIDDEN_SET_PROFILES = (
    {"name": "no_bulk", "weight": 0.15, "evs": {}, "nature": None},
    {"name": "hp_invested", "weight": 0.20, "evs": {"hp": 252}, "nature": None},
    {
        "name": "fast_physical",
        "weight": 0.20,
        "evs": {"atk": 252, "spe": 252},
        "nature": "Adamant",
    },
    {
        "name": "fast_special",
        "weight": 0.20,
        "evs": {"spa": 252, "spe": 252},
        "nature": "Modest",
    },
    {
        "name": "max_defense",
        "weight": 0.125,
        "evs": {"hp": 252, "def": 252},
        "nature": "Bold",
    },
    {
        "name": "max_special_defense",
        "weight": 0.125,
        "evs": {"hp": 252, "spd": 252},
        "nature": "Calm",
    },
)


def replacement_required(battle: BattleState, side_name: str) -> bool:
    side = battle.side(side_name)
    return bool(
        any(side.roster[pokemon_id].fainted for pokemon_id in side.active)
        and any(not side.roster[pokemon_id].fainted for pokemon_id in side.bench)
    )


def replacement_assignments(
    battle: BattleState, side_name: str
) -> tuple[tuple[tuple[str, str], ...], ...]:
    """Enumerate every legal simultaneous forced-replacement assignment."""

    side = battle.side(side_name)
    open_slots = [
        pokemon_id
        for pokemon_id in side.active
        if side.roster[pokemon_id].fainted
    ]
    available = [
        pokemon_id
        for pokemon_id in side.bench
        if not side.roster[pokemon_id].fainted
    ]
    replacement_count = min(len(open_slots), len(available))
    if replacement_count == 0:
        return ((),)
    assignments: list[tuple[tuple[str, str], ...]] = []
    for slots in combinations(open_slots, replacement_count):
        for incoming in permutations(available, replacement_count):
            assignments.append(tuple(zip(slots, incoming, strict=True)))
    return tuple(assignments)


def player_replacement_actions(battle: BattleState) -> tuple[JointAction, ...]:
    return tuple(
        JointAction(
            tuple(
                SingleAction(
                    actor=outgoing,
                    kind="switch",
                    switch_to=incoming,
                )
                for outgoing, incoming in assignment
            )
        )
        for assignment in replacement_assignments(battle, "player")
    )


@dataclass(frozen=True, slots=True)
class MultiTurnConfig:
    enabled: bool = True
    depth: int = 2
    root_action_limit: int = 3
    future_action_limit: int = 2
    response_limit: int = 2
    samples_per_response: int = 2
    node_budget: int = 900
    time_budget_ms: int = 8_000
    uncertainty_penalty: float = 14.0
    minimum_verified_frontier_fraction: float = 0.90

    def __post_init__(self) -> None:
        positive_fields = {
            "depth": self.depth,
            "root_action_limit": self.root_action_limit,
            "future_action_limit": self.future_action_limit,
            "response_limit": self.response_limit,
            "samples_per_response": self.samples_per_response,
            "node_budget": self.node_budget,
            "time_budget_ms": self.time_budget_ms,
        }
        invalid = [name for name, value in positive_fields.items() if value < 1]
        if invalid:
            raise ValueError(f"multi-turn limits must be positive: {', '.join(invalid)}")
        if not 0 <= self.minimum_verified_frontier_fraction <= 1:
            raise ValueError("minimum_verified_frontier_fraction must be between 0 and 1")

    @classmethod
    def from_environment(cls, *, default_enabled: bool) -> MultiTurnConfig:
        value = os.environ.get("MULTITURN_ENABLED")
        enabled = default_enabled if value is None else value.strip().lower() not in {
            "0",
            "false",
            "no",
            "off",
        }
        return cls(
            enabled=enabled,
            depth=max(1, min(3, int(os.environ.get("MULTITURN_DEPTH", "2")))),
            root_action_limit=max(
                1, int(os.environ.get("MULTITURN_ROOT_ACTIONS", "3"))
            ),
            future_action_limit=max(
                1, int(os.environ.get("MULTITURN_FUTURE_ACTIONS", "2"))
            ),
            response_limit=max(
                1, int(os.environ.get("MULTITURN_RESPONSE_SAMPLES", "2"))
            ),
            samples_per_response=max(
                1, int(os.environ.get("MULTITURN_CHANCE_SAMPLES", "2"))
            ),
            node_budget=max(50, int(os.environ.get("MULTITURN_NODE_BUDGET", "900"))),
            time_budget_ms=max(
                500, int(os.environ.get("MULTITURN_TIME_BUDGET_MS", "8000"))
            ),
            minimum_verified_frontier_fraction=max(
                0.0,
                min(
                    1.0,
                    float(
                        os.environ.get(
                            "MULTITURN_VERIFIED_FRONTIER", "0.90"
                        )
                    ),
                ),
            ),
        )


@dataclass(frozen=True, slots=True)
class PlanningState:
    battle: BattleState
    uncertainty: str | None = None
    trace: tuple[str, ...] = ()
    protect_chain: tuple[tuple[str, int], ...] = ()
    active_turns: tuple[tuple[str, int], ...] = ()
    hidden_profiles: tuple[tuple[str, str], ...] = ()
    replacement_phase: bool = False

    @classmethod
    def initial(cls, battle: BattleState) -> PlanningState:
        turns = 0 if battle.turn == 1 else 1
        active_turns = tuple(
            sorted(
                (f"{side_name}:{pokemon_id}", turns)
                for side_name in ("player", "opponent")
                for pokemon_id in battle.side(side_name).active
            )
        )
        return cls(
            battle=BattleState.from_dict(battle.to_dict()),
            active_turns=active_turns,
            replacement_phase=any(
                replacement_required(battle, side_name)
                for side_name in ("player", "opponent")
            ),
        )

    def key(self) -> str:
        value = {
            "battle": self.battle.to_dict(),
            "uncertainty": self.uncertainty,
            "protect_chain": list(self.protect_chain),
            "active_turns": list(self.active_turns),
            "hidden_profiles": list(self.hidden_profiles),
            "replacement_phase": self.replacement_phase,
        }
        return json.dumps(value, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True, slots=True)
class MoveIntent:
    side: str
    actor: str
    move: str
    target: str | None
    priority: int
    speed: int
    category: str
    move_data: dict[str, Any]


class VerifiedTurnResolver:
    """Resolve sampled, reachable Pokémon turns without expected-state shortcuts."""

    def __init__(
        self,
        calculator: ShowdownCalculator,
        config: MultiTurnConfig,
        regulation: CurrentChampionsRegulation | None = None,
    ) -> None:
        self.calculator = calculator
        self.config = config
        self.regulation = regulation
        self._lookup_cache: dict[tuple[str, str], dict[str, Any]] = {}
        self._speed_cache: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        self._damage_cache: dict[tuple[str, ...], dict[str, Any]] = {}
        self.telemetry = {
            "turns_resolved": 0,
            "replacement_transitions": 0,
            "sampled_outcomes": 0,
            "uncertainty_leaves": 0,
            "uncertainty_reasons": {},
            "damage_cache_hits": 0,
            "speed_cache_hits": 0,
            "hidden_profiles_sampled": 0,
            "mega_evolutions_resolved": 0,
            "independent_per_hit_critical_checks": 0,
        }

    def resolve_samples(
        self,
        state: PlanningState,
        action: JointAction,
        response: dict[str, Any],
    ) -> list[ChanceOutcome]:
        if response.get("residual"):
            residual = replace(
                state,
                uncertainty=str(response.get("reason", "unexpanded_opponent_response")),
                trace=(*state.trace, "opponent residual response"),
            )
            self._record_uncertainty(residual.uncertainty or "unknown")
            return [ChanceOutcome("residual-response", 1.0, residual)]

        outcomes: dict[str, tuple[PlanningState, int, float]] = {}
        for sample_index in range(self.config.samples_per_response):
            seed_material = (
                f"{state.key()}|{action.to_dict()}|{response}|{sample_index}"
            ).encode("utf-8")
            seed = int.from_bytes(hashlib.sha256(seed_material).digest()[:8], "big")
            rng = random.Random(seed)
            next_state = self._resolve_one(state, action, response, rng)
            reward = round(material_value(next_state.battle) - material_value(state.battle), 6)
            key = next_state.key()
            if key in outcomes:
                previous, count, previous_reward = outcomes[key]
                outcomes[key] = (previous, count + 1, previous_reward)
            else:
                outcomes[key] = (next_state, 1, reward)

        if state.replacement_phase:
            self.telemetry["replacement_transitions"] += 1
        else:
            self.telemetry["turns_resolved"] += 1
        self.telemetry["sampled_outcomes"] += self.config.samples_per_response
        total = float(self.config.samples_per_response)
        return [
            ChanceOutcome(
                id=f"sample-{index + 1}:{outcome.trace[-1] if outcome.trace else 'turn'}",
                probability=count / total,
                next_state=outcome,
                immediate_reward=reward,
            )
            for index, (outcome, count, reward) in enumerate(
                sorted(outcomes.values(), key=lambda row: row[0].key())
            )
        ]

    def _resolve_one(
        self,
        state: PlanningState,
        action: JointAction,
        response: dict[str, Any],
        rng: random.Random,
    ) -> PlanningState:
        battle = BattleState.from_dict(state.battle.to_dict())
        trace = list(state.trace)
        protect_chain = dict(state.protect_chain)
        active_turns = dict(state.active_turns)
        hidden_profiles = dict(state.hidden_profiles)
        player_switches = self._apply_player_switches(
            battle, action, trace, active_turns
        )
        opponent_switches = self._apply_opponent_switches(
            battle, response, trace, active_turns
        )
        for pokemon_id in battle.opponent.active:
            self._hidden_profile(battle, pokemon_id, hidden_profiles, rng)
        switch_in_boundary = self._switch_in_boundary(
            battle, player_switches, opponent_switches, hidden_profiles, trace
        )
        if switch_in_boundary:
            self._record_uncertainty(switch_in_boundary)
            return PlanningState(
                battle=battle,
                uncertainty=switch_in_boundary,
                trace=(*trace, switch_in_boundary),
                protect_chain=tuple(sorted(protect_chain.items())),
                active_turns=tuple(sorted(active_turns.items())),
                hidden_profiles=tuple(sorted(hidden_profiles.items())),
                replacement_phase=state.replacement_phase,
            )
        mega_boundary = self._apply_player_mega(
            battle, action, hidden_profiles, trace
        )
        if not mega_boundary:
            mega_boundary = self._apply_opponent_mega(
                battle, response, hidden_profiles, trace
            )
        if not mega_boundary:
            player_megas = {
                str(single.actor): str(single.actor)
                for single in action.actions
                if single.mega
            }
            opponent_megas = {
                str(reply["actor"]): str(reply["actor"])
                for reply in response.get("actions", [])
                if reply.get("mega")
            }
            if player_megas or opponent_megas:
                mega_boundary = self._switch_in_boundary(
                    battle,
                    player_megas,
                    opponent_megas,
                    hidden_profiles,
                    trace,
                )
        if mega_boundary:
            self._record_uncertainty(mega_boundary)
            return PlanningState(
                battle=battle,
                uncertainty=mega_boundary,
                trace=(*trace, mega_boundary),
                protect_chain=tuple(sorted(protect_chain.items())),
                active_turns=tuple(sorted(active_turns.items())),
                hidden_profiles=tuple(sorted(hidden_profiles.items())),
                replacement_phase=state.replacement_phase,
            )
        if state.replacement_phase:
            if any(
                replacement_required(battle, side_name)
                for side_name in ("player", "opponent")
            ):
                reason = "incomplete_forced_replacement"
                self._record_uncertainty(reason)
                return PlanningState(
                    battle=battle,
                    uncertainty=reason,
                    trace=(*trace, reason),
                    protect_chain=tuple(sorted(protect_chain.items())),
                    active_turns=tuple(sorted(active_turns.items())),
                    hidden_profiles=tuple(sorted(hidden_profiles.items())),
                    replacement_phase=True,
                )
            self._remove_unfillable_fainted_slots(battle)
            trace.append("forced replacements resolved")
            return PlanningState(
                battle=battle,
                trace=tuple(trace),
                protect_chain=tuple(sorted(protect_chain.items())),
                active_turns=tuple(sorted(active_turns.items())),
                hidden_profiles=tuple(sorted(hidden_profiles.items())),
                replacement_phase=False,
            )
        try:
            intents = self._move_intents(
                battle,
                action,
                response,
                player_switches,
                opponent_switches,
                hidden_profiles,
            )
        except UnexpandedOpponentAction:
            reason = "opponent_response_outside_declared_model"
            self._record_uncertainty(reason)
            return PlanningState(
                battle=battle,
                uncertainty=reason,
                trace=(*trace, "opponent response outside declared model"),
                protect_chain=tuple(sorted(protect_chain.items())),
                active_turns=tuple(sorted(active_turns.items())),
                hidden_profiles=tuple(sorted(hidden_profiles.items())),
                replacement_phase=state.replacement_phase,
            )
        except (KeyError, ValueError, ShowdownCalculationError, ShowdownUnavailable) as exc:
            self._record_uncertainty(
                f"move_order_unresolved:{type(exc).__name__}"
            )
            return PlanningState(
                battle=battle,
                uncertainty=f"move_order_unresolved:{type(exc).__name__}",
                trace=(*trace, "move order unresolved"),
                protect_chain=tuple(sorted(protect_chain.items())),
                active_turns=tuple(sorted(active_turns.items())),
                hidden_profiles=tuple(sorted(hidden_profiles.items())),
                replacement_phase=state.replacement_phase,
            )

        flinched: set[str] = set()
        acted: set[str] = set()
        used_protect: set[str] = set()
        redirection: dict[str, tuple[str, str]] = {}
        life_orb_recoil_applied: set[str] = set()
        intent_by_actor = {f"{intent.side}:{intent.actor}": intent for intent in intents}
        uncertainty: str | None = None
        pending = list(intents)
        while pending:
            available: list[MoveIntent] = []
            for queued in pending:
                queued_actor = battle.side(queued.side).roster[queued.actor]
                if (
                    queued_actor.fainted
                    or queued.actor not in battle.side(queued.side).active
                ):
                    trace.append(f"{queued_actor.name} cannot act")
                    acted.add(f"{queued.side}:{queued.actor}")
                else:
                    available.append(queued)
            pending = available
            if not pending:
                break
            try:
                pending = [
                    replace(
                        intent,
                        speed=self._speed(
                            battle,
                            intent.side,
                            intent.actor,
                            hidden_profiles,
                        ),
                    )
                    for intent in pending
                ]
            except (ValueError, ShowdownCalculationError, ShowdownUnavailable) as exc:
                uncertainty = f"dynamic_speed_unresolved:{type(exc).__name__}"
                trace.append("dynamic speed order unresolved")
                break
            if battle.field.trick_room_turns > 0:
                pending.sort(
                    key=lambda row: (-row.priority, row.speed, row.side, row.actor)
                )
            else:
                pending.sort(
                    key=lambda row: (-row.priority, -row.speed, row.side, row.actor)
                )
            tied = 1
            while tied < len(pending) and (
                pending[tied].priority,
                pending[tied].speed,
            ) == (pending[0].priority, pending[0].speed):
                tied += 1
            intent = pending.pop(rng.randrange(tied))
            actor_key = f"{intent.side}:{intent.actor}"
            actor = battle.side(intent.side).roster[intent.actor]
            if actor.fainted or actor_key in flinched:
                trace.append(f"{actor.name} cannot act")
                acted.add(actor_key)
                continue
            if actor.status == "sleep":
                if actor.status_counter <= 0:
                    actor.status_counter = rng.randint(1, 3)
                actor.status_counter -= 1
                if actor.status_counter == 0:
                    actor.status = None
                    trace.append(f"{actor.name} will wake after this turn")
                else:
                    trace.append(
                        f"{actor.name} remains asleep ({actor.status_counter} turn(s))"
                    )
                acted.add(actor_key)
                continue
            if actor.status == "freeze":
                if rng.random() < 0.20:
                    actor.status = None
                    actor.status_counter = 0
                    trace.append(f"{actor.name} thawed out")
                else:
                    trace.append(f"{actor.name} is frozen solid")
                    acted.add(actor_key)
                    continue
            if actor.status == "paralysis" and rng.random() < 0.25:
                trace.append(f"{actor.name} was fully paralysed")
                acted.add(actor_key)
                continue
            move_id = str(intent.move_data.get("id", ""))
            target_switches = (
                opponent_switches if intent.side == "player" else player_switches
            )
            if str(intent.target) in target_switches:
                intent = replace(
                    intent, target=target_switches[str(intent.target)]
                )
            if intent.category == "Status" and actor.volatile_conditions.get("taunt", 0):
                trace.append(f"{actor.name} could not use {intent.move} while taunted")
                acted.add(actor_key)
                continue
            opposing_side = "opponent" if intent.side == "player" else "player"
            imprisoned = any(
                opponent.volatile_conditions.get("imprison", 0)
                and intent.move in set(opponent.moves) | set(opponent.revealed_moves)
                for opponent_id in battle.side(opposing_side).active
                for opponent in [battle.side(opposing_side).roster[opponent_id]]
                if not opponent.fainted
            )
            if imprisoned:
                trace.append(f"{actor.name}'s {intent.move} was sealed by Imprison")
                acted.add(actor_key)
                continue
            if move_id in {"protect", "detect"}:
                streak = protect_chain.get(actor_key, 0)
                probability = 1.0 / (3**streak)
                if rng.random() <= probability:
                    actor.protected = True
                    used_protect.add(actor_key)
                    trace.append(f"{actor.name} protected")
                else:
                    trace.append(f"{actor.name} Protect failed")
                acted.add(actor_key)
                continue
            if move_id == "tailwind":
                battle.side(intent.side).side_conditions["tailwind"] = 4
                trace.append(f"{actor.name} set Tailwind")
                acted.add(actor_key)
                continue
            if move_id == "auroraveil":
                if str(battle.field.weather).lower() in {"snow", "hail"}:
                    battle.side(intent.side).side_conditions["aurora_veil"] = 5
                    trace.append(f"{actor.name} set Aurora Veil")
                else:
                    trace.append(f"{actor.name} Aurora Veil failed without snow")
                acted.add(actor_key)
                continue
            if intent.category == "Status":
                try:
                    handled, status_uncertainty = self._apply_status_move(
                        battle,
                        intent,
                        rng,
                        trace,
                        hidden_profiles,
                        redirection,
                        player_switches,
                        opponent_switches,
                    )
                except (
                    KeyError,
                    ValueError,
                    ShowdownCalculationError,
                    ShowdownUnavailable,
                ) as exc:
                    handled = False
                    status_uncertainty = (
                        f"status_move_unresolved:{type(exc).__name__}"
                    )
                if status_uncertainty:
                    uncertainty = status_uncertainty
                    break
                if handled:
                    acted.add(actor_key)
                    continue
                uncertainty = f"unsupported_status_move:{intent.move}"
                trace.append(f"unresolved status move: {intent.move}")
                break

            if move_id == "fakeout" and active_turns.get(actor_key, 1) > 0:
                trace.append(f"{actor.name} Fake Out failed after its first active turn")
                acted.add(actor_key)
                continue

            targets = self._targets(battle, intent)
            if len(targets) == 1 and targets[0][0] != intent.side:
                redirect = redirection.get(targets[0][0])
                actor_ability = self._ability_for(
                    battle, intent.side, intent.actor, hidden_profiles
                )
                if redirect and actor_ability not in {"Stalwart", "Propeller Tail"}:
                    redirect_id, redirect_move = redirect
                    redirector = battle.side(targets[0][0]).roster[redirect_id]
                    powder_immune = False
                    if redirect_move == "ragepowder":
                        species = self._lookup("species", actor.name)
                        powder_immune = bool(
                            "Grass" in set(species.get("types", []))
                            or actor_ability == "Overcoat"
                            or self._item_for(
                                battle, intent.side, intent.actor
                            ) == "Safety Goggles"
                        )
                    if not redirector.fainted and not powder_immune:
                        targets = [(targets[0][0], redirect_id)]
                        trace.append(f"{intent.move} redirected to {redirector.name}")
            if move_id == "suckerpunch" and not self._sucker_punch_succeeds(
                intent, targets, intent_by_actor, acted
            ):
                trace.append(f"{actor.name} Sucker Punch failed")
                acted.add(actor_key)
                continue
            for target_side, target_id in targets:
                target = battle.side(target_side).roster[target_id]
                if target.fainted:
                    continue
                if move_id == "feint" and target.protected:
                    target.protected = False
                    trace.append(f"{target.name}'s protection was broken")
                if target.protected:
                    trace.append(f"{target.name} blocked {intent.move}")
                    continue
                move_target = str(intent.move_data.get("target", ""))
                if (
                    battle.side(target_side).side_conditions.get("wide_guard", 0)
                    and move_target in {"allAdjacent", "allAdjacentFoes"}
                ):
                    trace.append(f"{target.name}'s side blocked {intent.move} with Wide Guard")
                    continue
                if (
                    battle.side(target_side).side_conditions.get("quick_guard", 0)
                    and intent.priority > 0
                    and move_id != "feint"
                ):
                    trace.append(f"{target.name}'s side blocked {intent.move} with Quick Guard")
                    continue
                try:
                    damage, hit, scenario, damage_units, target_max_hp = self._sample_damage(
                        battle,
                        intent.side,
                        intent.actor,
                        intent.move,
                        target_side,
                        target_id,
                        rng,
                        hidden_profiles,
                        intent.move_data,
                    )
                except (ValueError, ShowdownCalculationError, ShowdownUnavailable) as exc:
                    uncertainty = f"damage_unresolved:{type(exc).__name__}"
                    trace.append(f"damage unresolved for {intent.move}")
                    break
                if not hit:
                    trace.append(f"{actor.name}'s {intent.move} missed {target.name}")
                    continue
                if damage <= 1e-9:
                    trace.append(
                        f"{actor.name}'s {intent.move} had no effect on {target.name}"
                    )
                    continue
                previous_hp = float(target.hp)
                target.hp = round(max(0.0, previous_hp - damage), 6)
                target.fainted = target.hp <= 1e-9
                survival = self._survival_at_one_hp(
                    battle,
                    target_side,
                    target_id,
                    intent.move_data,
                    previous_hp,
                    target.fainted,
                    target_max_hp,
                    hidden_profiles,
                )
                if survival:
                    target.hp = round(100.0 / target_max_hp, 6)
                    target.fainted = False
                    trace.append(f"{target.name} survived through {survival}")
                trace.append(
                    f"{actor.name} {intent.move}→{target.name}: {damage:.3f}% ({scenario})"
                )
                if target.fainted:
                    target.hp = 0
                    target.protected = False
                    trace.append(f"{target.name} fainted")
                self._apply_post_hit_items(
                    battle,
                    intent,
                    target_side,
                    target_id,
                    life_orb_recoil_applied,
                    trace,
                    hidden_profiles,
                )
                contact_boundary = self._contact_boundary(
                    battle,
                    intent,
                    target_side,
                    target_id,
                    hidden_profiles,
                    rng,
                    trace,
                )
                if contact_boundary:
                    uncertainty = contact_boundary
                    trace.append(contact_boundary)
                    break
                self._apply_recoil_or_drain(
                    battle,
                    intent.side,
                    intent.actor,
                    actor,
                    intent.move_data,
                    damage_units,
                    trace,
                    hidden_profiles,
                )
                secondary_uncertainty = self._apply_secondary(
                    battle,
                    intent,
                    target_side,
                    target_id,
                    flinched,
                    acted,
                    rng,
                    trace,
                    hidden_profiles,
                )
                if secondary_uncertainty:
                    uncertainty = secondary_uncertainty
                    break
                if intent.move_data.get("forceSwitch") and not target.fainted:
                    target_side_state = battle.side(target_side)
                    target_ability = self._ability_for(
                        battle, target_side, target_id, hidden_profiles
                    )
                    available_switches = [
                        pokemon_id
                        for pokemon_id in target_side_state.bench
                        if not target_side_state.roster[pokemon_id].fainted
                    ]
                    if target_ability != "Suction Cups" and available_switches:
                        incoming = available_switches[
                            rng.randrange(len(available_switches))
                        ]
                        self._switch(target_side_state, target_id, incoming)
                        switch_table = (
                            player_switches
                            if target_side == "player"
                            else opponent_switches
                        )
                        switch_table[target_id] = incoming
                        active_turns[f"{target_side}:{incoming}"] = 0
                        if target_side == "opponent":
                            self._hidden_profile(
                                battle, incoming, hidden_profiles, rng
                            )
                        boundary = self._switch_in_boundary(
                            battle,
                            {target_id: incoming}
                            if target_side == "player"
                            else {},
                            {target_id: incoming}
                            if target_side == "opponent"
                            else {},
                            hidden_profiles,
                            trace,
                        )
                        if boundary:
                            uncertainty = boundary
                            break
                        trace.append(
                            f"{target.name} was forced out for {incoming}"
                        )
            acted.add(actor_key)
            if uncertainty:
                break

        for key in set(protect_chain) | used_protect:
            protect_chain[key] = protect_chain.get(key, 0) + 1 if key in used_protect else 0
        if not uncertainty:
            uncertainty = self._finish_turn(
                battle,
                trace,
                active_turns,
                player_switches,
                opponent_switches,
                hidden_profiles,
                rng,
            )
        if uncertainty:
            self._record_uncertainty(uncertainty)
        else:
            self._remove_unfillable_fainted_slots(battle)
        return PlanningState(
            battle=battle,
            uncertainty=uncertainty,
            trace=tuple(trace),
            protect_chain=tuple(sorted(protect_chain.items())),
            active_turns=tuple(sorted(active_turns.items())),
            hidden_profiles=tuple(sorted(hidden_profiles.items())),
            replacement_phase=(
                uncertainty is None
                and any(
                    replacement_required(battle, side_name)
                    for side_name in ("player", "opponent")
                )
            ),
        )

    def _record_uncertainty(self, reason: str) -> None:
        self.telemetry["uncertainty_leaves"] += 1
        reasons = self.telemetry["uncertainty_reasons"]
        reasons[reason] = int(reasons.get(reason, 0)) + 1

    @staticmethod
    def _remove_unfillable_fainted_slots(battle: BattleState) -> None:
        for side_name in ("player", "opponent"):
            side = battle.side(side_name)
            if any(not side.roster[pokemon_id].fainted for pokemon_id in side.bench):
                continue
            side.active[:] = [
                pokemon_id
                for pokemon_id in side.active
                if not side.roster[pokemon_id].fainted
            ]

    @staticmethod
    def _switch(side: Any, outgoing: str, incoming: str) -> None:
        if outgoing not in side.active or incoming not in side.bench:
            raise ValueError("simulated switch is no longer legal")
        position = side.active.index(outgoing)
        side.active[position] = incoming
        side.bench.remove(incoming)
        if not side.roster[outgoing].fainted:
            side.bench.append(outgoing)
        if side.roster[outgoing].status == "toxic":
            side.roster[outgoing].status_counter = 0
        side.roster[outgoing].protected = False
        side.roster[outgoing].volatile_conditions.clear()

    def _apply_player_switches(
        self,
        battle: BattleState,
        action: JointAction,
        trace: list[str],
        active_turns: dict[str, int],
    ) -> dict[str, str]:
        switches: dict[str, str] = {}
        for single in action.actions:
            if single.kind != "switch" or not single.switch_to:
                continue
            incoming = single.switch_to
            self._switch(battle.player, single.actor, incoming)
            switches[single.actor] = incoming
            active_turns[f"player:{incoming}"] = 0
            trace.append(
                f"player switch {single.actor}→{incoming}"
            )
        return switches

    def _apply_opponent_switches(
        self,
        battle: BattleState,
        response: dict[str, Any],
        trace: list[str],
        active_turns: dict[str, int],
    ) -> dict[str, str]:
        switches: dict[str, str] = {}
        for reply in response.get("actions", []):
            if reply.get("kind") != "switch":
                continue
            outgoing = str(reply["actor"])
            incoming = str(reply["switch_to"])
            self._switch(battle.opponent, outgoing, incoming)
            switches[outgoing] = incoming
            active_turns[f"opponent:{incoming}"] = 0
            trace.append(f"opponent switch {outgoing}→{incoming}")
        return switches

    def _apply_player_mega(
        self,
        battle: BattleState,
        action: JointAction,
        hidden_profiles: dict[str, str],
        trace: list[str],
    ) -> str | None:
        requests = [single for single in action.actions if single.mega]
        if not requests:
            return None
        if len(requests) != 1:
            return "illegal_multiple_mega_evolutions"
        single = requests[0]
        pokemon = battle.player.roster.get(single.actor)
        if (
            pokemon is None
            or pokemon.fainted
            or pokemon.id not in battle.player.active
            or not pokemon.can_mega_evolve
            or pokemon.mega_evolved
        ):
            return "illegal_mega_evolution_state"
        if any(
            member.mega_evolved
            for member in battle.player.roster.values()
            if member.id != pokemon.id
        ):
            return "mega_evolution_already_used"
        if self.regulation is None:
            return "mega_mechanics_snapshot_unavailable"
        try:
            resolved = self.regulation.mega_evolution(pokemon.name, item=pokemon.item)
        except ValueError:
            return "mega_form_not_legal_in_current_champions"
        pokemon.mega_evolved = True
        pokemon.can_mega_evolve = False
        pokemon.battle_form = str(resolved["battle_form"])
        pokemon.mechanics_override = dict(resolved["mechanics_override"])
        pokemon.ability = str(resolved["ability"])
        self.telemetry["mega_evolutions_resolved"] += 1
        trace.append(
            f"{pokemon.name} Mega Evolved into {pokemon.battle_form} "
            f"({pokemon.ability})"
        )
        return None

    def _apply_opponent_mega(
        self,
        battle: BattleState,
        response: dict[str, Any],
        hidden_profiles: dict[str, str],
        trace: list[str],
    ) -> str | None:
        requests = [reply for reply in response.get("actions", []) if reply.get("mega")]
        if not requests:
            return None
        if len(requests) != 1:
            return "illegal_multiple_opponent_mega_evolutions"
        reply = requests[0]
        actor_id = str(reply.get("actor", ""))
        pokemon = battle.opponent.roster.get(actor_id)
        if (
            pokemon is None
            or pokemon.fainted
            or actor_id not in battle.opponent.active
            or pokemon.mega_evolved
            or any(
                member.mega_evolved
                for member in battle.opponent.roster.values()
                if member.id != actor_id
            )
        ):
            return "illegal_opponent_mega_evolution_state"
        if self.regulation is None:
            return "mega_mechanics_snapshot_unavailable"
        stone = str(reply.get("mega_stone", "")) or None
        form = str(reply.get("mega_form", "")) or None
        try:
            resolved = self.regulation.mega_evolution(
                pokemon.name,
                item=stone,
                form=form,
            )
        except ValueError:
            return "opponent_mega_form_not_legal_in_current_champions"
        pokemon.item = str(resolved["mega_stone"])
        pokemon.mega_evolved = True
        pokemon.can_mega_evolve = False
        pokemon.battle_form = str(resolved["battle_form"])
        pokemon.mechanics_override = dict(resolved["mechanics_override"])
        pokemon.ability = str(resolved["ability"])
        battle.opponent.known_facts.setdefault(actor_id, {})["item"] = pokemon.item
        battle.opponent.known_facts[actor_id]["ability"] = pokemon.ability
        profile_key = f"opponent:{actor_id}"
        if profile_key in hidden_profiles:
            profile = json.loads(hidden_profiles[profile_key])
            profile["item"] = pokemon.item
            profile["ability"] = pokemon.ability
            hidden_profiles[profile_key] = json.dumps(profile, sort_keys=True)
        self.telemetry["mega_evolutions_resolved"] += 1
        trace.append(
            f"opponent {pokemon.name} Mega Evolved into {pokemon.battle_form} "
            f"({pokemon.ability})"
        )
        return None

    def _move_intents(
        self,
        battle: BattleState,
        action: JointAction,
        response: dict[str, Any],
        player_switches: dict[str, str],
        opponent_switches: dict[str, str],
        hidden_profiles: dict[str, str],
    ) -> list[MoveIntent]:
        intents: list[MoveIntent] = []
        for single in action.actions:
            if single.kind != "move" or not single.move or single.actor in player_switches:
                continue
            target = opponent_switches.get(str(single.target), single.target)
            intents.append(
                self._intent(
                    battle,
                    "player",
                    single.actor,
                    single.move,
                    target,
                    hidden_profiles,
                )
            )
        for reply in response.get("actions", []):
            if reply.get("kind") == "other":
                raise UnexpandedOpponentAction("unresolved opponent action")
            if reply.get("kind") != "move" or str(reply.get("actor")) in opponent_switches:
                continue
            target_value = reply.get("target")
            target = player_switches.get(str(target_value), target_value)
            intents.append(
                self._intent(
                    battle,
                    "opponent",
                    str(reply["actor"]),
                    str(reply["move"]),
                    str(target) if target is not None else None,
                    hidden_profiles,
                )
            )
        return intents

    def _intent(
        self,
        battle: BattleState,
        side: str,
        actor: str,
        move: str,
        target: str | None,
        hidden_profiles: dict[str, str],
    ) -> MoveIntent:
        move_data = self._lookup("move", move)
        return MoveIntent(
            side=side,
            actor=actor,
            move=move,
            target=target,
            priority=int(move_data.get("priority", 0)),
            speed=self._speed(battle, side, actor, hidden_profiles),
            category=str(move_data.get("category", "Status")),
            move_data=move_data,
        )

    def _lookup(self, kind: str, name: str) -> dict[str, Any]:
        key = (kind, name.lower())
        if key not in self._lookup_cache:
            self._lookup_cache[key] = self.calculator.lookup(kind, name)["entry"]
        return self._lookup_cache[key]

    def _speed(
        self,
        battle: BattleState,
        side: str,
        actor: str,
        hidden_profiles: dict[str, str],
    ) -> int:
        profile = (
            dict(json.loads(hidden_profiles[f"opponent:{actor}"]))
            if side == "opponent" and f"opponent:{actor}" in hidden_profiles
            else None
        )
        profile_key = json.dumps(profile, sort_keys=True) if profile else "*"
        key = (self._structural_key(battle), side, actor, profile_key)
        if key in self._speed_cache:
            self.telemetry["speed_cache_hits"] += 1
            return int(self._speed_cache[key]["finalSpeed"])
        result = calculate_canonical_speed(
            self.calculator,
            battle,
            side=side,
            actor_id=actor,
            profile=profile,
        )
        self._speed_cache[key] = result
        return int(result["finalSpeed"])

    def _max_hp(
        self,
        battle: BattleState,
        side: str,
        actor: str,
        hidden_profiles: dict[str, str],
    ) -> int:
        stored_profile = (
            hidden_profiles.get(f"opponent:{actor}")
            if side == "opponent"
            else None
        )
        profile_key = (
            json.dumps(json.loads(stored_profile), sort_keys=True)
            if stored_profile
            else "*"
        )
        key = (self._structural_key(battle), side, actor, profile_key)
        if key not in self._speed_cache:
            self._speed(battle, side, actor, hidden_profiles)
        return int(self._speed_cache[key]["maxHP"])

    def _sample_damage(
        self,
        battle: BattleState,
        side: str,
        actor: str,
        move: str,
        target_side: str,
        target: str,
        rng: random.Random,
        hidden_profiles: dict[str, str],
        move_data: dict[str, Any],
    ) -> tuple[float, bool, str, float, int]:
        attacker_profile = (
            self._hidden_profile(battle, actor, hidden_profiles, rng)
            if side == "opponent"
            else None
        )
        defender_profile = (
            self._hidden_profile(battle, target, hidden_profiles, rng)
            if target_side == "opponent"
            else None
        )
        attacker_profile_key = (
            json.dumps(attacker_profile, sort_keys=True) if attacker_profile else "*"
        )
        defender_profile_key = (
            json.dumps(defender_profile, sort_keys=True) if defender_profile else "*"
        )
        attacker_ability = self._ability_for(battle, side, actor, hidden_profiles)
        attacker_item = self._item_for(battle, side, actor)
        defender_ability = self._ability_for(
            battle, target_side, target, hidden_profiles
        )
        crit_stage = max(0, int(move_data.get("critRatio", 1) or 1) - 1)
        if attacker_ability == "Super Luck":
            crit_stage += 1
        if attacker_item in {"Scope Lens", "Razor Claw"}:
            crit_stage += 1
        target_status = battle.side(target_side).roster[target].status
        forced_crit = bool(move_data.get("willCrit")) or (
            attacker_ability == "Merciless"
            and target_status in {"poison", "toxic"}
        )
        crit_rates = (1 / 24, 1 / 8, 1 / 2, 1.0)
        critical = bool(
            defender_ability not in {"Battle Armor", "Shell Armor"}
            and (
                forced_crit
                or rng.random() < crit_rates[min(crit_stage, len(crit_rates) - 1)]
            )
        )
        hits: int | None = None
        multihit = move_data.get("multihit")
        if isinstance(multihit, int):
            hits = multihit
        elif isinstance(multihit, list) and len(multihit) == 2:
            low, high = int(multihit[0]), int(multihit[1])
            if attacker_ability == "Skill Link":
                hits = high
            elif attacker_item == "Loaded Dice" and (low, high) == (2, 5):
                hits = 4 if rng.random() < 0.5 else 5
            elif (low, high) == (2, 5):
                hits = self._weighted_choice(
                    [
                        {"hits": 2, "weight": 3},
                        {"hits": 3, "weight": 3},
                        {"hits": 4, "weight": 1},
                        {"hits": 5, "weight": 1},
                    ],
                    "weight",
                    rng,
                )["hits"]
            else:
                hits = rng.randint(low, high)
        if hits and hits > 1:
            move_id = str(move_data.get("id", "")).lower()
            if move_id in {"beatup", "dragondarts", "populationbomb", "tripleaxel", "triplekick"}:
                raise ValueError(f"per-hit callback is not verified for {move}")
            criticals = [critical]
            for _ in range(hits - 1):
                criticals.append(
                    bool(
                        defender_ability not in {"Battle Armor", "Shell Armor"}
                        and (
                            forced_crit
                            or rng.random()
                            < crit_rates[min(crit_stage, len(crit_rates) - 1)]
                        )
                    )
                )
            self.telemetry["independent_per_hit_critical_checks"] += hits
            return self._sample_equal_power_multi_hit_damage(
                battle=battle,
                side=side,
                actor=actor,
                move=move,
                target_side=target_side,
                target=target,
                rng=rng,
                attacker_profile=attacker_profile,
                defender_profile=defender_profile,
                attacker_profile_key=attacker_profile_key,
                defender_profile_key=defender_profile_key,
                criticals=criticals,
            )
        key = (
            self._structural_key(battle),
            side,
            actor,
            move.lower(),
            target_side,
            target,
            attacker_profile_key,
            defender_profile_key,
            "critical" if critical else "normal",
            str(hits or 1),
        )
        if key in self._damage_cache:
            self.telemetry["damage_cache_hits"] += 1
            result = self._damage_cache[key]
        else:
            result = calculate_canonical_damage(
                self.calculator,
                battle,
                side=side,
                actor_id=actor,
                move=move,
                target_id=target,
                target_side=target_side,
                attacker_profile=attacker_profile,
                defender_profile=defender_profile,
                critical=critical,
                hits=hits,
            )
            self._damage_cache[key] = result
        if not result.get("damage_applicable"):
            raise ValueError("status move reached damage sampler")
        estimate = result["estimate"]
        scenario = self._weighted_choice(
            list(estimate["scenarios"]), "weight", rng
        )
        scenario_name = str(scenario["name"])
        if critical:
            scenario_name += ":critical"
        if hits and hits > 1:
            scenario_name += f":{hits}-hits"
        accuracy = float(scenario.get("base_accuracy_probability", 1.0))
        if rng.random() > accuracy:
            return 0.0, False, scenario_name, 0.0, int(
                scenario["defender_max_hp"]
            )
        roll = self._weighted_choice(list(scenario["rolls_percent"]), "weight", rng)
        return (
            float(roll["percent"]),
            True,
            scenario_name,
            float(roll["damage"]),
            int(scenario["defender_max_hp"]),
        )

    def _sample_equal_power_multi_hit_damage(
        self,
        *,
        battle: BattleState,
        side: str,
        actor: str,
        move: str,
        target_side: str,
        target: str,
        rng: random.Random,
        attacker_profile: dict[str, Any] | None,
        defender_profile: dict[str, Any] | None,
        attacker_profile_key: str,
        defender_profile_key: str,
        criticals: list[bool],
    ) -> tuple[float, bool, str, float, int]:
        results: dict[bool, dict[str, Any]] = {}
        for critical in sorted(set(criticals)):
            key = (
                self._structural_key(battle),
                side,
                actor,
                move.lower(),
                target_side,
                target,
                attacker_profile_key,
                defender_profile_key,
                "critical" if critical else "normal",
                "per-hit",
            )
            if key in self._damage_cache:
                self.telemetry["damage_cache_hits"] += 1
                result = self._damage_cache[key]
            else:
                result = calculate_canonical_damage(
                    self.calculator,
                    battle,
                    side=side,
                    actor_id=actor,
                    move=move,
                    target_id=target,
                    target_side=target_side,
                    attacker_profile=attacker_profile,
                    defender_profile=defender_profile,
                    critical=critical,
                    hits=1,
                )
                self._damage_cache[key] = result
            if not result.get("damage_applicable"):
                raise ValueError("status move reached multi-hit damage sampler")
            results[critical] = result

        first_estimate = results[criticals[0]]["estimate"]
        first_scenario = self._weighted_choice(
            list(first_estimate["scenarios"]), "weight", rng
        )
        scenario_name = str(first_scenario["name"])
        accuracy = float(first_scenario.get("base_accuracy_probability", 1.0))
        if rng.random() > accuracy:
            return 0.0, False, f"{scenario_name}:{len(criticals)}-hits", 0.0, int(
                first_scenario["defender_max_hp"]
            )

        percent = 0.0
        damage_units = 0.0
        for critical in criticals:
            estimate = results[critical]["estimate"]
            scenario = next(
                (
                    row
                    for row in estimate["scenarios"]
                    if row["name"] == first_scenario["name"]
                ),
                estimate["scenarios"][0],
            )
            roll = self._weighted_choice(
                list(scenario["rolls_percent"]), "weight", rng
            )
            percent += float(roll["percent"])
            damage_units += float(roll["damage"])
        critical_hits = sum(criticals)
        return (
            percent,
            True,
            f"{scenario_name}:{len(criticals)}-hits:{critical_hits}-critical-hits",
            damage_units,
            int(first_scenario["defender_max_hp"]),
        )

    def _hidden_profile(
        self,
        battle: BattleState,
        pokemon_id: str,
        hidden_profiles: dict[str, str],
        rng: random.Random,
    ) -> dict[str, Any]:
        key = f"opponent:{pokemon_id}"
        if key in hidden_profiles:
            return dict(json.loads(hidden_profiles[key]))
        facts = battle.opponent.known_facts.get(pokemon_id, {})
        if "evs" in facts:
            profile = {
                "name": "confirmed_set",
                "evs": dict(facts["evs"]),
                "nature": facts.get("nature"),
            }
        else:
            sampled = self._weighted_choice(list(HIDDEN_SET_PROFILES), "weight", rng)
            profile = {
                "name": str(sampled["name"]),
                "evs": dict(sampled.get("evs", {})),
                "nature": facts.get("nature", sampled.get("nature")),
            }
        known_ability = facts.get(
            "ability", battle.opponent.roster[pokemon_id].ability
        )
        if known_ability:
            profile["ability"] = str(known_ability)
        else:
            species = self._lookup(
                "species", battle.opponent.roster[pokemon_id].name
            )
            raw_abilities = species.get("abilities", {})
            abilities = (
                sorted({str(value) for value in raw_abilities.values()})
                if isinstance(raw_abilities, dict)
                else []
            )
            if not abilities:
                raise ValueError(f"no ability profile for opponent {pokemon_id}")
            profile["ability"] = abilities[rng.randrange(len(abilities))]
        hidden_profiles[key] = json.dumps(
            profile, sort_keys=True, separators=(",", ":")
        )
        self.telemetry["hidden_profiles_sampled"] += 1
        return profile

    @staticmethod
    def _weighted_choice(
        rows: list[dict[str, Any]], field: str, rng: random.Random
    ) -> dict[str, Any]:
        total = sum(float(row.get(field, 0)) for row in rows)
        if total <= 0:
            raise ValueError("sample distribution has no probability mass")
        selected = rng.random() * total
        cumulative = 0.0
        for row in rows:
            cumulative += float(row.get(field, 0))
            if selected <= cumulative:
                return row
        return rows[-1]

    @staticmethod
    def _targets(battle: BattleState, intent: MoveIntent) -> list[tuple[str, str]]:
        target_side = "opponent" if intent.side == "player" else "player"
        move_target = intent.move_data.get("target")
        if move_target == "self":
            return [(intent.side, intent.actor)]
        if move_target in {"adjacentAlly", "adjacentAllyOrSelf"}:
            if intent.target in battle.side(intent.side).roster:
                return [(intent.side, str(intent.target))]
            allies = [
                pokemon_id
                for pokemon_id in battle.side(intent.side).active
                if pokemon_id != intent.actor
                and not battle.side(intent.side).roster[pokemon_id].fainted
            ]
            if allies:
                return [(intent.side, allies[0])]
            if move_target == "adjacentAllyOrSelf":
                return [(intent.side, intent.actor)]
            return []
        if move_target == "allAdjacent":
            opposing = [
                (target_side, pokemon_id)
                for pokemon_id in battle.side(target_side).active
                if not battle.side(target_side).roster[pokemon_id].fainted
            ]
            partner = [
                (intent.side, pokemon_id)
                for pokemon_id in battle.side(intent.side).active
                if pokemon_id != intent.actor
                and not battle.side(intent.side).roster[pokemon_id].fainted
            ]
            return [*opposing, *partner]
        if intent.target in {"opponents", "players", "both"} or intent.move_data.get(
            "target"
        ) == "allAdjacentFoes":
            return [
                (target_side, pokemon_id)
                for pokemon_id in battle.side(target_side).active
                if not battle.side(target_side).roster[pokemon_id].fainted
            ]
        if intent.target in battle.side(target_side).active:
            return [(target_side, str(intent.target))]
        return []

    @staticmethod
    def _sucker_punch_succeeds(
        intent: MoveIntent,
        targets: list[tuple[str, str]],
        intent_by_actor: dict[str, MoveIntent],
        acted: set[str],
    ) -> bool:
        if len(targets) != 1:
            return False
        target_side, target = targets[0]
        key = f"{target_side}:{target}"
        target_intent = intent_by_actor.get(key)
        return bool(
            target_intent
            and target_intent.category != "Status"
            and key not in acted
            and intent.side != target_side
        )

    def _apply_status_move(
        self,
        battle: BattleState,
        intent: MoveIntent,
        rng: random.Random,
        trace: list[str],
        hidden_profiles: dict[str, str],
        redirection: dict[str, tuple[str, str]],
        player_switches: dict[str, str],
        opponent_switches: dict[str, str],
    ) -> tuple[bool, str | None]:
        actor = battle.side(intent.side).roster[intent.actor]
        move_data = intent.move_data
        move_id = str(move_data.get("id", ""))
        accuracy = move_data.get("accuracy", True)
        probability = 1.0 if accuracy is True else float(accuracy or 0) / 100.0
        if rng.random() > probability:
            trace.append(f"{actor.name}'s {intent.move} missed")
            return True, None

        if move_data.get("pseudoWeather") == "trickroom":
            battle.field.trick_room_turns = (
                0 if battle.field.trick_room_turns > 0 else 5
            )
            trace.append(f"{actor.name} toggled Trick Room")
            return True, None

        weather = move_data.get("weather")
        if weather:
            weather_id = str(weather).lower()
            battle.field.weather = {
                "raindance": "rain",
                "sunnyday": "sun",
                "sandstorm": "sand",
                "snowscape": "snow",
                "hail": "hail",
            }.get(weather_id, weather_id)
            battle.field.weather_turns = 5
            trace.append(f"{actor.name} set {battle.field.weather}")
            return True, None

        terrain = move_data.get("terrain")
        if terrain:
            terrain_id = str(terrain).lower().removesuffix("terrain")
            battle.field.terrain = terrain_id
            battle.field.terrain_turns = 5
            trace.append(f"{actor.name} set {terrain_id} terrain")
            return True, None

        side_condition = move_data.get("sideCondition")
        if side_condition:
            condition = str(side_condition).lower()
            durations = {
                "tailwind": 4,
                "auroraveil": 5,
                "reflect": 5,
                "lightscreen": 5,
                "safeguard": 5,
                "wideguard": 1,
                "quickguard": 1,
            }
            if condition in durations:
                if condition == "auroraveil" and str(battle.field.weather).lower() not in {
                    "snow",
                    "hail",
                }:
                    trace.append(f"{actor.name} Aurora Veil failed without snow")
                    return True, None
                key = {
                    "auroraveil": "aurora_veil",
                    "lightscreen": "light_screen",
                    "wideguard": "wide_guard",
                    "quickguard": "quick_guard",
                }.get(condition, condition)
                battle.side(intent.side).side_conditions[key] = durations[condition]
                trace.append(f"{actor.name} set {condition}")
                return True, None

        volatile = str(move_data.get("volatileStatus") or "").lower()
        if volatile in {"yawn", "taunt"}:
            targets = self._targets(battle, intent)
            for target_side, target_id in targets:
                target = battle.side(target_side).roster[target_id]
                if target.protected:
                    trace.append(f"{target.name} blocked {intent.move}")
                    continue
                target_ability = self._ability_for(
                    battle, target_side, target_id, hidden_profiles
                )
                if volatile == "taunt" and target_ability in {"Oblivious", "Aroma Veil"}:
                    trace.append(f"{target.name} is immune to Taunt")
                    continue
                if volatile == "yawn":
                    if target.status is not None:
                        trace.append(f"{target.name} already has a status")
                        continue
                    can_sleep = self._status_can_apply(
                        battle, target_side, target_id, "sleep", hidden_profiles
                    )
                    if can_sleep is None:
                        return False, "hidden_ability_status_immunity:sleep"
                    if not can_sleep:
                        trace.append(f"{target.name} is immune to Yawn")
                        continue
                    target.volatile_conditions["yawn"] = 2
                else:
                    target.volatile_conditions["taunt"] = 3
                trace.append(f"{target.name} gained {volatile}")
                if (
                    volatile == "taunt"
                    and self._item_for(battle, target_side, target_id)
                    == "Mental Herb"
                ):
                    del target.volatile_conditions["taunt"]
                    self._consume_item(battle, target_side, target_id)
                    trace.append(f"{target.name} consumed Mental Herb to cure Taunt")
            return True, None
        if volatile == "imprison":
            actor.volatile_conditions["imprison"] = 1
            trace.append(f"{actor.name} sealed shared moves with Imprison")
            return True, None
        if volatile in {"followme", "ragepowder"}:
            redirection[intent.side] = (intent.actor, volatile)
            trace.append(f"{actor.name} used {intent.move} for redirection")
            return True, None

        if move_id in {"roar", "whirlwind"}:
            targets = self._targets(battle, intent)
            for target_side, target_id in targets:
                target_side_state = battle.side(target_side)
                target = target_side_state.roster[target_id]
                target_ability = self._ability_for(
                    battle, target_side, target_id, hidden_profiles
                )
                available = [
                    pokemon_id
                    for pokemon_id in target_side_state.bench
                    if not target_side_state.roster[pokemon_id].fainted
                ]
                if target.protected:
                    trace.append(f"{target.name} blocked {intent.move}")
                    continue
                if target_ability == "Suction Cups" or not available:
                    trace.append(f"{intent.move} failed against {target.name}")
                    continue
                incoming = available[rng.randrange(len(available))]
                self._switch(target_side_state, target_id, incoming)
                if target_side == "opponent":
                    self._hidden_profile(battle, incoming, hidden_profiles, rng)
                switch_table = (
                    player_switches if target_side == "player" else opponent_switches
                )
                switch_table[target_id] = incoming
                boundary = self._switch_in_boundary(
                    battle,
                    {target_id: incoming} if target_side == "player" else {},
                    {target_id: incoming} if target_side == "opponent" else {},
                    hidden_profiles,
                    trace,
                )
                if boundary:
                    return False, boundary
                trace.append(f"{target.name} was forced out for {incoming}")
            return True, None
        if volatile == "helpinghand" or move_id == "helpinghand":
            battle.side(intent.side).side_conditions["helping_hand"] = 1
            trace.append(f"{actor.name} used Helping Hand")
            return True, None

        targets = self._targets(battle, intent)
        direct_status = move_data.get("status")
        boosts = move_data.get("boosts")
        heal = move_data.get("heal")
        if not targets and any(value for value in (direct_status, boosts, heal)):
            trace.append(f"{actor.name}'s {intent.move} had no legal target")
            return True, None
        for target_side, target_id in targets:
            target = battle.side(target_side).roster[target_id]
            if target.protected and target_side != intent.side:
                trace.append(f"{target.name} blocked {intent.move}")
                continue
            if direct_status and target.status is None:
                normalized = STATUS_IDS.get(str(direct_status), str(direct_status))
                flags = move_data.get("flags", [])
                if isinstance(flags, list) and "powder" in flags:
                    species = self._lookup("species", target.name)
                    if (
                        "Grass" in set(species.get("types", []))
                        or self._ability_for(
                            battle, target_side, target_id, hidden_profiles
                        ) == "Overcoat"
                        or self._item_for(battle, target_side, target_id)
                        == "Safety Goggles"
                    ):
                        trace.append(f"{target.name} is immune to powder moves")
                        continue
                if battle.side(target_side).side_conditions.get("safeguard", 0):
                    trace.append(f"{target.name} was protected by Safeguard")
                    continue
                can_apply = self._status_can_apply(
                    battle, target_side, target_id, normalized, hidden_profiles
                )
                if can_apply is None:
                    return False, f"hidden_ability_status_immunity:{normalized}"
                if can_apply:
                    target.status = normalized
                    target.status_counter = (
                        rng.randint(1, 3) if normalized == "sleep" else 0
                    )
                    trace.append(f"{target.name} gained {normalized}")
                    self._consume_status_cure_item(
                        battle, target_side, target_id, trace
                    )
                else:
                    trace.append(f"{target.name} is immune to {normalized}")
            if isinstance(boosts, dict):
                for stat, delta in boosts.items():
                    if stat in target.boosts:
                        target.boosts[stat] = max(
                            -6, min(6, target.boosts[stat] + int(delta))
                        )
                trace.append(f"{target.name} stat change from {intent.move}")
            if isinstance(heal, list) and len(heal) == 2 and heal[1]:
                amount = 100.0 * float(heal[0]) / float(heal[1])
                target.hp = round(min(100.0, float(target.hp) + amount), 6)
                trace.append(f"{target.name} recovered {amount:.3f}%")
        if direct_status or isinstance(boosts, dict) or isinstance(heal, list):
            return True, None
        return False, None

    def _apply_secondary(
        self,
        battle: BattleState,
        intent: MoveIntent,
        target_side: str,
        target_id: str,
        flinched: set[str],
        acted: set[str],
        rng: random.Random,
        trace: list[str],
        hidden_profiles: dict[str, str],
    ) -> str | None:
        target = battle.side(target_side).roster[target_id]
        if target.fainted:
            return None
        ability = self._ability_for(
            battle, target_side, target_id, hidden_profiles
        )
        item = self._item_for(battle, target_side, target_id)
        effects: list[dict[str, Any]] = []
        secondary = intent.move_data.get("secondary")
        secondaries = intent.move_data.get("secondaries")
        if isinstance(secondaries, list):
            effects.extend(row for row in secondaries if isinstance(row, dict))
        elif isinstance(secondary, dict):
            effects.append(secondary)
        if effects and (ability == "Shield Dust" or item == "Covert Cloak"):
            trace.append(f"{target.name} blocked secondary effects")
            effects = []
        for effect in effects:
            if rng.random() * 100 > float(effect.get("chance", 100)):
                continue
            status = effect.get("status")
            if status:
                if target.status is not None:
                    continue
                normalized_status = STATUS_IDS.get(str(status), str(status))
                can_apply = self._status_can_apply(
                    battle,
                    target_side,
                    target_id,
                    normalized_status,
                    hidden_profiles,
                )
                if can_apply is None:
                    return f"hidden_ability_status_immunity:{normalized_status}"
                if not can_apply:
                    trace.append(f"{target.name} is immune to {normalized_status}")
                    continue
                target.status = normalized_status
                target.status_counter = (
                    rng.randint(1, 3) if normalized_status == "sleep" else 0
                )
                trace.append(f"{target.name} gained {target.status}")
                self._consume_status_cure_item(
                    battle, target_side, target_id, trace
                )
                continue
            boosts = effect.get("boosts")
            if isinstance(boosts, dict):
                for stat, delta in boosts.items():
                    if stat in target.boosts:
                        target.boosts[stat] = max(
                            -6, min(6, target.boosts[stat] + int(delta))
                        )
                trace.append(f"{target.name} stat change from {intent.move}")
                continue
            self_payload = effect.get("self")
            if isinstance(self_payload, dict) and isinstance(
                self_payload.get("boosts"), dict
            ):
                actor = battle.side(intent.side).roster[intent.actor]
                for stat, delta in self_payload["boosts"].items():
                    if stat in actor.boosts:
                        actor.boosts[stat] = max(
                            -6, min(6, actor.boosts[stat] + int(delta))
                        )
                trace.append(f"{actor.name} self stat change from {intent.move}")
                continue
            if effect.get("volatileStatus") == "flinch":
                if ability == "Inner Focus":
                    trace.append(
                        f"{target.name} blocked flinching through Inner Focus"
                    )
                    continue
                key = f"{target_side}:{target_id}"
                if key not in acted:
                    flinched.add(key)
                    trace.append(f"{target.name} flinched")
                continue
            if str(intent.move_data.get("id")) == "direclaw":
                if target.status is not None:
                    continue
                normalized_status = rng.choice(
                    ("sleep", "poison", "paralysis")
                )
                can_apply = self._status_can_apply(
                    battle,
                    target_side,
                    target_id,
                    normalized_status,
                    hidden_profiles,
                )
                if can_apply is None:
                    return f"hidden_ability_status_immunity:{normalized_status}"
                if can_apply:
                    target.status = normalized_status
                    target.status_counter = (
                        rng.randint(1, 3)
                        if normalized_status == "sleep"
                        else 0
                    )
                    trace.append(
                        f"{target.name} gained {normalized_status} from Dire Claw"
                    )
                    self._consume_status_cure_item(
                        battle, target_side, target_id, trace
                    )
                else:
                    trace.append(f"{target.name} is immune to {normalized_status}")
                continue
            return f"unsupported_secondary:{intent.move}"

        self_effect = intent.move_data.get("selfEffect")
        if isinstance(self_effect, dict) and isinstance(self_effect.get("boosts"), dict):
            actor = battle.side(intent.side).roster[intent.actor]
            for stat, delta in self_effect["boosts"].items():
                if stat in actor.boosts:
                    actor.boosts[stat] = max(-6, min(6, actor.boosts[stat] + int(delta)))
            trace.append(f"{actor.name} self stat change from {intent.move}")
            if (
                self._item_for(battle, intent.side, intent.actor) == "White Herb"
                and any(stage < 0 for stage in actor.boosts.values())
            ):
                for stat, stage in actor.boosts.items():
                    actor.boosts[stat] = max(0, stage)
                self._consume_item(battle, intent.side, intent.actor)
                trace.append(f"{actor.name} consumed White Herb")
        return None

    def _status_can_apply(
        self,
        battle: BattleState,
        side: str,
        target_id: str,
        status: str,
        hidden_profiles: dict[str, str],
    ) -> bool | None:
        side_state = battle.side(side)
        target = side_state.roster[target_id]
        species = self._lookup("species", target.name)
        types = set(species.get("types", []))
        type_immunities = {
            "burn": {"Fire"},
            "poison": {"Poison", "Steel"},
            "toxic": {"Poison", "Steel"},
            "paralysis": {"Electric"},
            "freeze": {"Ice"},
        }
        if types & type_immunities.get(status, set()):
            return False
        immunity_abilities = {
            "burn": {"Water Veil", "Water Bubble", "Thermal Exchange", "Purifying Salt"},
            "poison": {"Immunity", "Pastel Veil", "Purifying Salt"},
            "toxic": {"Immunity", "Pastel Veil", "Purifying Salt"},
            "paralysis": {"Limber", "Purifying Salt"},
            "sleep": {"Insomnia", "Vital Spirit", "Sweet Veil", "Purifying Salt"},
            "freeze": {"Magma Armor", "Purifying Salt"},
        }.get(status, set())
        known_ability = self._ability_for(
            battle, side, target_id, hidden_profiles
        )
        if known_ability:
            return str(known_ability) not in immunity_abilities
        possible = set(species.get("abilities", {}).values())
        if possible & immunity_abilities:
            return None
        return True

    @staticmethod
    def _consume_item(
        battle: BattleState,
        side: str,
        pokemon_id: str,
    ) -> None:
        pokemon = battle.side(side).roster[pokemon_id]
        pokemon.item = None
        battle.side(side).known_facts.setdefault(pokemon_id, {})["item"] = None

    def _consume_status_cure_item(
        self,
        battle: BattleState,
        side: str,
        pokemon_id: str,
        trace: list[str],
    ) -> None:
        pokemon = battle.side(side).roster[pokemon_id]
        item = self._item_for(battle, side, pokemon_id)
        cures = {
            "Cheri Berry": "paralysis",
            "Chesto Berry": "sleep",
            "Pecha Berry": "poison",
            "Rawst Berry": "burn",
            "Aspear Berry": "freeze",
        }
        status_matches = bool(
            item == "Lum Berry"
            or cures.get(str(item)) == pokemon.status
            or (item == "Pecha Berry" and pokemon.status == "toxic")
        )
        if not status_matches:
            return
        cured = pokemon.status
        pokemon.status = None
        pokemon.status_counter = 0
        self._consume_item(battle, side, pokemon_id)
        trace.append(f"{pokemon.name} consumed {item} to cure {cured}")

    def _apply_post_hit_items(
        self,
        battle: BattleState,
        intent: MoveIntent,
        target_side: str,
        target_id: str,
        life_orb_recoil_applied: set[str],
        trace: list[str],
        hidden_profiles: dict[str, str],
    ) -> None:
        target = battle.side(target_side).roster[target_id]
        target_item = self._item_for(battle, target_side, target_id)
        if not target.fainted:
            recovery = 0.0
            if target_item == "Sitrus Berry" and target.hp <= 50:
                recovery = 25.0
            elif target_item == "Oran Berry" and target.hp <= 50:
                recovery = 10.0
            elif target_item in {
                "Figy Berry",
                "Wiki Berry",
                "Mago Berry",
                "Aguav Berry",
                "Iapapa Berry",
            } and target.hp <= 25:
                recovery = 100.0 / 3.0
            if recovery:
                target.hp = round(min(100.0, float(target.hp) + recovery), 6)
                self._consume_item(battle, target_side, target_id)
                trace.append(
                    f"{target.name} consumed {target_item} and recovered {recovery:.3f}%"
                )
                target_item = None

        move_type = str(intent.move_data.get("type", ""))
        resist_berries = {
            "Occa Berry": "Fire",
            "Passho Berry": "Water",
            "Wacan Berry": "Electric",
            "Rindo Berry": "Grass",
            "Yache Berry": "Ice",
            "Chople Berry": "Fighting",
            "Kebia Berry": "Poison",
            "Shuca Berry": "Ground",
            "Coba Berry": "Flying",
            "Payapa Berry": "Psychic",
            "Tanga Berry": "Bug",
            "Charti Berry": "Rock",
            "Kasib Berry": "Ghost",
            "Haban Berry": "Dragon",
            "Colbur Berry": "Dark",
            "Babiri Berry": "Steel",
            "Roseli Berry": "Fairy",
        }
        super_effective = False
        if target_item in resist_berries and resist_berries[target_item] == move_type:
            matchup = self.calculator.type_matchup(move_type, target.name)
            super_effective = float(matchup.get("multiplier", 1.0)) > 1.0
            if super_effective:
                self._consume_item(battle, target_side, target_id)
                trace.append(f"{target.name} consumed {target_item}")
                target_item = None
        if target_item == "Weakness Policy":
            matchup = self.calculator.type_matchup(move_type, target.name)
            super_effective = float(matchup.get("multiplier", 1.0)) > 1.0
            if super_effective and not target.fainted:
                target.boosts["atk"] = min(6, target.boosts["atk"] + 2)
                target.boosts["spa"] = min(6, target.boosts["spa"] + 2)
                self._consume_item(battle, target_side, target_id)
                trace.append(f"{target.name} activated Weakness Policy")
                target_item = None
        if target_item == "Air Balloon":
            self._consume_item(battle, target_side, target_id)
            trace.append(f"{target.name}'s Air Balloon popped")

        actor_key = f"{intent.side}:{intent.actor}"
        actor = battle.side(intent.side).roster[intent.actor]
        if (
            actor_key not in life_orb_recoil_applied
            and self._item_for(battle, intent.side, intent.actor) == "Life Orb"
            and self._ability_for(
                battle, intent.side, intent.actor, hidden_profiles
            ) != "Magic Guard"
        ):
            actor.hp = round(max(0.0, float(actor.hp) - 10.0), 6)
            actor.fainted = actor.hp <= 1e-9
            if actor.fainted:
                actor.hp = 0
            life_orb_recoil_applied.add(actor_key)
            trace.append(f"{actor.name} Life Orb recoil 10.000%")

    def _apply_recoil_or_drain(
        self,
        battle: BattleState,
        side: str,
        actor_id: str,
        actor: Any,
        move_data: dict[str, Any],
        damage_units: float,
        trace: list[str],
        hidden_profiles: dict[str, str],
    ) -> None:
        actor_max_hp = self._max_hp(
            battle, side, actor_id, hidden_profiles
        )
        recoil = move_data.get("recoil")
        drain = move_data.get("drain")
        ability = self._ability_for(battle, side, actor_id, hidden_profiles)
        if isinstance(recoil, list) and len(recoil) == 2 and recoil[1]:
            if ability in {"Rock Head", "Magic Guard"}:
                trace.append(f"{actor.name} avoided recoil through {ability}")
                recoil = None
        if isinstance(recoil, list) and len(recoil) == 2 and recoil[1]:
            amount = (
                damage_units
                * float(recoil[0])
                / float(recoil[1])
                * 100.0
                / actor_max_hp
            )
            actor.hp = round(max(0.0, float(actor.hp) - amount), 6)
            actor.fainted = actor.hp <= 1e-9
            if actor.fainted:
                actor.hp = 0
            trace.append(f"{actor.name} recoil {amount:.3f}%")
        if isinstance(drain, list) and len(drain) == 2 and drain[1]:
            amount = (
                damage_units
                * float(drain[0])
                / float(drain[1])
                * 100.0
                / actor_max_hp
            )
            actor.hp = round(min(100.0, float(actor.hp) + amount), 6)
            trace.append(f"{actor.name} drain {amount:.3f}%")

    @staticmethod
    def _survival_at_one_hp(
        battle: BattleState,
        side: str,
        target_id: str,
        move_data: dict[str, Any],
        previous_hp: float,
        would_faint: bool,
        target_max_hp: int,
        hidden_profiles: dict[str, str],
    ) -> str | None:
        if not would_faint or previous_hp < 99.999:
            return None
        target_side = battle.side(side)
        target = target_side.roster[target_id]
        item = VerifiedTurnResolver._item_for(battle, side, target_id)
        ability = VerifiedTurnResolver._ability_for(
            battle, side, target_id, hidden_profiles
        )
        if move_data.get("multihit") and (item == "Focus Sash" or ability == "Sturdy"):
            return None
        if item == "Focus Sash":
            target.item = None
            target_side.known_facts.setdefault(target_id, {})["item"] = None
            return "Focus Sash"
        if ability == "Sturdy":
            return "Sturdy"
        return None

    @staticmethod
    def _ability_for(
        battle: BattleState,
        side: str,
        pokemon_id: str,
        hidden_profiles: dict[str, str],
    ) -> str | None:
        pokemon = battle.side(side).roster[pokemon_id]
        facts = battle.side(side).known_facts.get(pokemon_id, {})
        profiled_ability = None
        profile_key = f"opponent:{pokemon_id}"
        if side == "opponent" and profile_key in hidden_profiles:
            profiled_ability = json.loads(hidden_profiles[profile_key]).get("ability")
        value = facts.get("ability", pokemon.ability or profiled_ability)
        return str(value) if value else None

    @staticmethod
    def _item_for(battle: BattleState, side: str, pokemon_id: str) -> str | None:
        pokemon = battle.side(side).roster[pokemon_id]
        facts = battle.side(side).known_facts.get(pokemon_id, {})
        value = facts.get("item", pokemon.item)
        return str(value) if value else None

    def _contact_boundary(
        self,
        battle: BattleState,
        intent: MoveIntent,
        target_side: str,
        target_id: str,
        hidden_profiles: dict[str, str],
        rng: random.Random,
        trace: list[str],
    ) -> str | None:
        flags = intent.move_data.get("flags", [])
        if not isinstance(flags, list) or "contact" not in flags:
            return None
        actor = battle.side(intent.side).roster[intent.actor]
        actor_ability = self._ability_for(
            battle, intent.side, intent.actor, hidden_profiles
        )
        actor_item = self._item_for(battle, intent.side, intent.actor)
        if actor_ability == "Long Reach" or actor_item == "Protective Pads":
            return None
        ability = self._ability_for(
            battle, target_side, target_id, hidden_profiles
        )
        item = self._item_for(battle, target_side, target_id)
        if actor_ability != "Magic Guard":
            if ability in {"Rough Skin", "Iron Barbs"}:
                actor.hp = round(max(0.0, float(actor.hp) - 12.5), 6)
                trace.append(f"{actor.name} took 12.500% from {ability}")
            if item == "Rocky Helmet":
                amount = 100.0 / 6.0
                actor.hp = round(max(0.0, float(actor.hp) - amount), 6)
                trace.append(f"{actor.name} took {amount:.3f}% from Rocky Helmet")

        status: str | None = None
        if ability == "Static" and rng.random() < 0.30:
            status = "paralysis"
        elif ability == "Flame Body" and rng.random() < 0.30:
            status = "burn"
        elif ability == "Poison Point" and rng.random() < 0.30:
            status = "poison"
        elif ability == "Effect Spore":
            species = self._lookup("species", actor.name)
            powder_immune = bool(
                "Grass" in set(species.get("types", []))
                or actor_ability == "Overcoat"
                or actor_item == "Safety Goggles"
            )
            if not powder_immune:
                roll = rng.random()
                if roll < 0.09:
                    status = "poison"
                elif roll < 0.20:
                    status = "sleep"
                elif roll < 0.30:
                    status = "paralysis"
        if status and actor.status is None:
            can_apply = self._status_can_apply(
                battle,
                intent.side,
                intent.actor,
                status,
                hidden_profiles,
            )
            if can_apply is None:
                return f"hidden_ability_status_immunity:{status}"
            if can_apply:
                actor.status = status
                actor.status_counter = rng.randint(1, 3) if status == "sleep" else 0
                trace.append(f"{actor.name} gained {status} from {ability}")
                self._consume_status_cure_item(
                    battle, intent.side, intent.actor, trace
                )

        if ability in {"Gooey", "Tangling Hair"}:
            if actor_ability not in {"Clear Body", "White Smoke", "Full Metal Body"}:
                actor.boosts["spe"] = max(-6, actor.boosts["spe"] - 1)
                trace.append(f"{actor.name} lost Speed from {ability}")
        elif ability == "Mummy" and actor_ability not in {"Mummy", "Lingering Aroma"}:
            actor.ability = "Mummy"
            battle.side(intent.side).known_facts.setdefault(intent.actor, {})[
                "ability"
            ] = "Mummy"
            trace.append(f"{actor.name}'s Ability became Mummy")
        elif ability == "Wandering Spirit" and actor_ability:
            target = battle.side(target_side).roster[target_id]
            target.ability = actor_ability
            actor.ability = "Wandering Spirit"
            battle.side(target_side).known_facts.setdefault(target_id, {})[
                "ability"
            ] = actor_ability
            battle.side(intent.side).known_facts.setdefault(intent.actor, {})[
                "ability"
            ] = "Wandering Spirit"
            trace.append(f"{actor.name} swapped Abilities through Wandering Spirit")

        actor.fainted = actor.hp <= 1e-9
        if actor.fainted:
            actor.hp = 0
            trace.append(f"{actor.name} fainted from contact effects")
        return None

    def _switch_in_boundary(
        self,
        battle: BattleState,
        player_switches: dict[str, str],
        opponent_switches: dict[str, str],
        hidden_profiles: dict[str, str],
        trace: list[str],
    ) -> str | None:
        incoming = [
            *(("player", pokemon_id) for pokemon_id in player_switches.values()),
            *(("opponent", pokemon_id) for pokemon_id in opponent_switches.values()),
        ]
        try:
            incoming.sort(
                key=lambda row: (
                    -self._speed(battle, row[0], row[1], hidden_profiles),
                    row[0],
                    row[1],
                )
            )
        except (ValueError, ShowdownCalculationError, ShowdownUnavailable) as exc:
            return f"switch_in_order_unresolved:{type(exc).__name__}"
        weather_setters = {
            "Drizzle": "rain",
            "Drought": "sun",
            "Sand Stream": "sand",
            "Snow Warning": "snow",
        }
        terrain_setters = {
            "Electric Surge": "electric",
            "Grassy Surge": "grassy",
            "Misty Surge": "misty",
            "Psychic Surge": "psychic",
        }
        for side, pokemon_id in incoming:
            ability = self._ability_for(
                battle, side, pokemon_id, hidden_profiles
            )
            pokemon = battle.side(side).roster[pokemon_id]
            if ability in weather_setters:
                battle.field.weather = weather_setters[ability]
                battle.field.weather_turns = 5
                trace.append(f"{pokemon.name} set {battle.field.weather} through {ability}")
            elif ability in terrain_setters:
                battle.field.terrain = terrain_setters[ability]
                battle.field.terrain_turns = 5
                trace.append(f"{pokemon.name} set {battle.field.terrain} through {ability}")
            elif ability == "Intimidate":
                target_side = "opponent" if side == "player" else "player"
                for target_id in battle.side(target_side).active:
                    target = battle.side(target_side).roster[target_id]
                    if target.fainted:
                        continue
                    target_ability = self._ability_for(
                        battle, target_side, target_id, hidden_profiles
                    )
                    if self._item_for(
                        battle, target_side, target_id
                    ) == "Clear Amulet":
                        continue
                    if target_ability in {
                        "Clear Body",
                        "White Smoke",
                        "Hyper Cutter",
                        "Full Metal Body",
                        "Inner Focus",
                        "Oblivious",
                        "Own Tempo",
                        "Scrappy",
                    }:
                        continue
                    if target_ability == "Guard Dog":
                        target.boosts["atk"] = min(6, target.boosts["atk"] + 1)
                        continue
                    target.boosts["atk"] = max(-6, target.boosts["atk"] - 1)
                    if target_ability == "Defiant":
                        target.boosts["atk"] = min(6, target.boosts["atk"] + 2)
                    elif target_ability == "Competitive":
                        target.boosts["spa"] = min(6, target.boosts["spa"] + 2)
                    elif target_ability == "Rattled":
                        target.boosts["spe"] = min(6, target.boosts["spe"] + 1)
                trace.append(f"{pokemon.name} applied Intimidate")
            elif ability in {"Download", "Trace"}:
                return f"switch_in_ability_not_verified:{ability}"
        return None

    def _finish_turn(
        self,
        battle: BattleState,
        trace: list[str],
        active_turns: dict[str, int],
        player_switches: dict[str, str],
        opponent_switches: dict[str, str],
        hidden_profiles: dict[str, str],
        rng: random.Random,
    ) -> str | None:
        weather = str(battle.field.weather or "").lower()
        for side_name in ("player", "opponent"):
            side = battle.side(side_name)
            for pokemon_id in side.active:
                pokemon = side.roster[pokemon_id]
                if pokemon.fainted:
                    continue
                ability = self._ability_for(
                    battle, side_name, pokemon_id, hidden_profiles
                )
                item = self._item_for(battle, side_name, pokemon_id)
                species = self._lookup("species", pokemon.name)
                types = set(species.get("types", []))
                indirect_immune = ability == "Magic Guard"

                for condition in ("taunt", "yawn"):
                    if condition not in pokemon.volatile_conditions:
                        continue
                    pokemon.volatile_conditions[condition] = max(
                        0, pokemon.volatile_conditions[condition] - 1
                    )
                    if pokemon.volatile_conditions[condition] > 0:
                        continue
                    del pokemon.volatile_conditions[condition]
                    if condition == "yawn" and pokemon.status is None:
                        can_sleep = self._status_can_apply(
                            battle,
                            side_name,
                            pokemon_id,
                            "sleep",
                            hidden_profiles,
                        )
                        if can_sleep is None:
                            return "hidden_ability_status_immunity:sleep"
                        if can_sleep:
                            pokemon.status = "sleep"
                            pokemon.status_counter = rng.randint(1, 3)
                            trace.append(f"{pokemon.name} fell asleep from Yawn")
                            self._consume_status_cure_item(
                                battle, side_name, pokemon_id, trace
                            )

                weather_damage = 0.0
                if weather in {"sand", "sandstorm"}:
                    immune = bool(
                        types & {"Rock", "Ground", "Steel"}
                        or ability
                        in {
                            "Sand Force",
                            "Sand Rush",
                            "Sand Veil",
                            "Overcoat",
                            "Magic Guard",
                        }
                        or item == "Safety Goggles"
                    )
                    weather_damage = 0.0 if immune else 6.25
                elif weather == "hail":
                    immune = bool(
                        "Ice" in types
                        or ability in {"Ice Body", "Snow Cloak", "Overcoat", "Magic Guard"}
                        or item == "Safety Goggles"
                    )
                    weather_damage = 0.0 if immune else 6.25
                if weather_damage:
                    pokemon.hp = round(max(0.0, float(pokemon.hp) - weather_damage), 6)
                    trace.append(f"{pokemon.name} weather damage {weather_damage:.3f}%")

                if pokemon.hp > 1e-9 and not indirect_immune:
                    if pokemon.status == "burn":
                        pokemon.hp = round(max(0.0, float(pokemon.hp) - 6.25), 6)
                        trace.append(f"{pokemon.name} burn damage 6.250%")
                    elif pokemon.status == "poison":
                        if ability == "Poison Heal":
                            pokemon.hp = round(min(100.0, float(pokemon.hp) + 12.5), 6)
                            trace.append(f"{pokemon.name} Poison Heal 12.500%")
                        else:
                            pokemon.hp = round(max(0.0, float(pokemon.hp) - 12.5), 6)
                            trace.append(f"{pokemon.name} poison damage 12.500%")
                    elif pokemon.status == "toxic":
                        if ability == "Poison Heal":
                            pokemon.hp = round(min(100.0, float(pokemon.hp) + 12.5), 6)
                            trace.append(f"{pokemon.name} Poison Heal 12.500%")
                        else:
                            pokemon.status_counter += 1
                            amount = 6.25 * pokemon.status_counter
                            pokemon.hp = round(max(0.0, float(pokemon.hp) - amount), 6)
                            trace.append(
                                f"{pokemon.name} toxic damage {amount:.3f}% "
                                f"(counter {pokemon.status_counter})"
                            )

                if pokemon.hp > 1e-9:
                    if weather in {"sun", "sunny"} and ability in {"Solar Power", "Dry Skin"}:
                        pokemon.hp = round(max(0.0, float(pokemon.hp) - 12.5), 6)
                        trace.append(f"{pokemon.name} {ability} damage 12.500%")
                    elif weather == "rain" and ability == "Dry Skin":
                        pokemon.hp = round(min(100.0, float(pokemon.hp) + 12.5), 6)
                        trace.append(f"{pokemon.name} Dry Skin recovery 12.500%")
                    elif (
                        weather == "rain" and ability == "Rain Dish"
                    ) or (weather in {"hail", "snow"} and ability == "Ice Body"):
                        pokemon.hp = round(min(100.0, float(pokemon.hp) + 6.25), 6)
                        trace.append(f"{pokemon.name} {ability} recovery 6.250%")
                    if weather == "rain" and ability == "Hydration" and pokemon.status:
                        pokemon.status = None
                        pokemon.status_counter = 0
                        trace.append(f"{pokemon.name} was cured by Hydration")

                if pokemon.hp > 1e-9:
                    if item == "Leftovers":
                        pokemon.hp = round(min(100.0, float(pokemon.hp) + 6.25), 6)
                        trace.append(f"{pokemon.name} Leftovers recovery 6.250%")
                    elif item == "Black Sludge":
                        amount = 6.25 if "Poison" in types else -12.5
                        pokemon.hp = round(
                            max(0.0, min(100.0, float(pokemon.hp) + amount)), 6
                        )
                        trace.append(f"{pokemon.name} Black Sludge {amount:+.3f}%")
                    elif item in {"Flame Orb", "Toxic Orb"} and pokemon.status is None:
                        inflicted = "burn" if item == "Flame Orb" else "toxic"
                        if self._status_can_apply(
                            battle,
                            side_name,
                            pokemon_id,
                            inflicted,
                            hidden_profiles,
                        ):
                            pokemon.status = inflicted
                            pokemon.status_counter = 0
                            trace.append(f"{pokemon.name} {item} inflicted {inflicted}")

                if pokemon.hp > 1e-9 and ability == "Speed Boost":
                    pokemon.boosts["spe"] = min(6, pokemon.boosts["spe"] + 1)
                    trace.append(f"{pokemon.name} gained Speed from Speed Boost")
                elif pokemon.hp > 1e-9 and ability == "Moody":
                    return "end_turn_ability_not_verified:Moody"
                if pokemon.hp > 1e-9 and ability == "Shed Skin" and pokemon.status:
                    if rng.random() < 1 / 3:
                        pokemon.status = None
                        pokemon.status_counter = 0
                        trace.append(f"{pokemon.name} was cured by Shed Skin")
                pokemon.fainted = pokemon.hp <= 1e-9
                if pokemon.fainted:
                    pokemon.hp = 0
                    pokemon.protected = False
                    trace.append(f"{pokemon.name} fainted from residual damage")
            for pokemon in side.roster.values():
                pokemon.protected = False
            for key in list(side.side_conditions):
                side.side_conditions[key] = max(0, side.side_conditions[key] - 1)

        battle.field.weather_turns = max(0, battle.field.weather_turns - 1)
        battle.field.terrain_turns = max(0, battle.field.terrain_turns - 1)
        battle.field.trick_room_turns = max(0, battle.field.trick_room_turns - 1)
        if battle.field.weather_turns == 0:
            battle.field.weather = None
        if battle.field.terrain_turns == 0:
            battle.field.terrain = None
        battle.turn += 1
        battle.revision += 1

        switched = {
            *(f"player:{pokemon_id}" for pokemon_id in player_switches.values()),
            *(f"opponent:{pokemon_id}" for pokemon_id in opponent_switches.values()),
        }
        for side_name in ("player", "opponent"):
            for pokemon_id in battle.side(side_name).active:
                key = f"{side_name}:{pokemon_id}"
                active_turns[key] = 0 if key in switched else active_turns.get(key, 0) + 1

        for side_name in ("player", "opponent"):
            side = battle.side(side_name)
            if any(side.roster[pokemon_id].fainted for pokemon_id in side.active) and any(
                not side.roster[pokemon_id].fainted for pokemon_id in side.bench
            ):
                trace.append(f"{side_name} forced replacement required")
        trace.append(f"turn {battle.turn - 1} resolved")
        return None

    @staticmethod
    def _structural_key(battle: BattleState) -> str:
        return json.dumps(battle.to_dict(), sort_keys=True, separators=(",", ":"))


def material_value(battle: BattleState) -> float:
    def side_value(side_name: str) -> tuple[float, float]:
        side = battle.side(side_name)
        ids = side.selected or list(side.roster)
        hp = sum(float(side.roster[pokemon_id].hp) for pokemon_id in ids) / max(1, len(ids))
        fainted = sum(side.roster[pokemon_id].fainted for pokemon_id in ids) / max(1, len(ids))
        return hp, fainted

    player_hp, player_fainted = side_value("player")
    opponent_hp, opponent_fainted = side_value("opponent")
    field_value = 0.0
    field_value += 4.0 if battle.player.side_conditions.get("tailwind", 0) else 0.0
    field_value -= 4.0 if battle.opponent.side_conditions.get("tailwind", 0) else 0.0
    field_value += 3.0 if battle.player.side_conditions.get("aurora_veil", 0) else 0.0
    field_value -= 3.0 if battle.opponent.side_conditions.get("aurora_veil", 0) else 0.0
    return round(
        (player_hp - opponent_hp) + 30.0 * (opponent_fainted - player_fainted) + field_value,
        6,
    )


class VerifiedBattleGame:
    def __init__(
        self,
        *,
        calculator: ShowdownCalculator,
        meta: MetaRepository,
        beliefs: BeliefState,
        recommendation: Recommendation,
        response_model: dict[str, Any],
        config: MultiTurnConfig,
    ) -> None:
        self.calculator = calculator
        self.meta = meta
        self.beliefs = beliefs
        self.recommendation = recommendation
        self.response_model = response_model
        self.config = config
        self.resolver = VerifiedTurnResolver(calculator, config, meta.regulation)
        self.root_key = ""
        self.root_actions = tuple(
            candidate.action
            for candidate in recommendation.candidate_catalog[: config.root_action_limit]
        )
        self.telemetry = {
            "response_models_built": 0,
            "response_sample_calls": 0,
            "response_distribution_samples": 0,
            "sampled_source_probability_mass_total": 0.0,
        }

    def bind_root(self, state: PlanningState) -> None:
        self.root_key = state.key()

    def state_key(self, state: PlanningState) -> str:
        return state.key()

    def terminal_value(self, state: PlanningState) -> float | None:
        if state.uncertainty:
            return material_value(state.battle) - self.config.uncertainty_penalty
        player_ids = state.battle.player.selected
        opponent_ids = state.battle.opponent.selected
        player_out = player_ids and all(
            state.battle.player.roster[pokemon_id].fainted for pokemon_id in player_ids
        )
        opponent_out = opponent_ids and all(
            state.battle.opponent.roster[pokemon_id].fainted for pokemon_id in opponent_ids
        )
        if player_out and opponent_out:
            return 0.0
        if player_out:
            return -100.0
        if opponent_out:
            return 100.0
        return None

    def evaluate(self, state: PlanningState) -> float:
        return material_value(state.battle)

    def player_actions(self, state: PlanningState) -> tuple[JointAction, ...]:
        if state.replacement_phase:
            return player_replacement_actions(state.battle)
        if state.key() == self.root_key:
            return self.root_actions
        candidates = generate_legal_joint_actions(state.battle)
        ranked = sorted(
            candidates,
            key=lambda action: (
                -self._cheap_action_value(state.battle, action),
                action.label(state.battle),
            ),
        )
        return tuple(ranked[: self.config.future_action_limit])

    @staticmethod
    def _cheap_action_value(state: BattleState, action: JointAction) -> float:
        action_value = sum(
            base_action_value(state, single, {}) for single in action.actions
        )
        return action_value + synergy_value(state, action)

    def action_label(self, state: PlanningState, action: JointAction) -> str:
        return action.label(state.battle)

    def opponent_responses(
        self, state: PlanningState, action: JointAction
    ) -> tuple[WeightedResponse, ...]:
        if state.replacement_phase:
            assignments = replacement_assignments(state.battle, "opponent")
            probability = 1.0 / len(assignments)
            return tuple(
                WeightedResponse(
                    id=(
                        "forced replacement: "
                        + ", ".join(
                            f"{outgoing}→{incoming}"
                            for outgoing, incoming in assignment
                        )
                        if assignment
                        else "forced replacement: hold"
                    ),
                    probability=probability,
                    payload={
                        "replacement": True,
                        "actions": [
                            {
                                "actor": outgoing,
                                "kind": "switch",
                                "switch_to": incoming,
                            }
                            for outgoing, incoming in assignment
                        ],
                    },
                )
                for assignment in assignments
            )
        if state.key() == self.root_key:
            model = self.response_model
        else:
            model = build_response_model(
                self.calculator,
                self.meta,
                state.battle,
                self.beliefs,
                maximum_joint_responses=1024,
            )
            self.telemetry["response_models_built"] += 1
        responses = list(model.get("responses", []))
        total = sum(float(row.get("probability", 0)) for row in responses)
        if total <= 0:
            return (
                WeightedResponse(
                    id="residual: missing response probability",
                    probability=1.0,
                    payload={"residual": True, "reason": "missing_response_probability"},
                ),
            )
        selected: dict[str, tuple[dict[str, Any], int]] = {}
        cumulative_rows: list[tuple[float, dict[str, Any]]] = []
        cumulative = 0.0
        for row in responses:
            cumulative += float(row.get("probability", 0)) / total
            cumulative_rows.append((cumulative, row))
        seed_material = f"{state.key()}|{action.to_dict()}".encode("utf-8")
        start = (
            int.from_bytes(hashlib.sha256(seed_material).digest()[:8], "big")
            / float(2**64)
            / self.config.response_limit
        )
        for index in range(self.config.response_limit):
            quantile = start + index / self.config.response_limit
            row = next(row for cumulative, row in cumulative_rows if quantile <= cumulative)
            label = str(row.get("label", f"response-{index + 1}"))
            previous = selected.get(label)
            selected[label] = (row, (previous[1] if previous else 0) + 1)
        self.telemetry["response_distribution_samples"] += self.config.response_limit
        self.telemetry["response_sample_calls"] += 1
        self.telemetry["sampled_source_probability_mass_total"] += sum(
            float(row.get("probability", 0)) for row, _ in selected.values()
        )
        return tuple(
            WeightedResponse(
                id=label,
                probability=count / self.config.response_limit,
                payload=row,
            )
            for label, (row, count) in sorted(selected.items())
        )

    def chance_outcomes(
        self,
        state: PlanningState,
        action: JointAction,
        response: WeightedResponse,
    ) -> tuple[ChanceOutcome, ...]:
        return tuple(
            self.resolver.resolve_samples(state, action, dict(response.payload or {}))
        )

    @staticmethod
    def transition_depth_cost(
        state: PlanningState,
        action: JointAction,
        response: WeightedResponse,
        outcome: ChanceOutcome,
    ) -> int:
        del action, response, outcome
        return 0 if state.replacement_phase else 1


class MultiTurnPlanner:
    def __init__(
        self,
        calculator: ShowdownCalculator,
        meta: MetaRepository,
        config: MultiTurnConfig,
    ) -> None:
        self.calculator = calculator
        self.meta = meta
        self.config = config

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "mode": "verified_transition_deterministic_sampling",
            "requested_depth": self.config.depth,
            "root_action_limit": self.config.root_action_limit,
            "future_action_limit": self.config.future_action_limit,
            "response_limit": self.config.response_limit,
            "samples_per_response": self.config.samples_per_response,
            "node_budget": self.config.node_budget,
            "time_budget_ms": self.config.time_budget_ms,
            "minimum_verified_frontier_fraction": (
                self.config.minimum_verified_frontier_fraction
            ),
        }

    def plan(
        self,
        *,
        state: BattleState,
        beliefs: BeliefState,
        recommendation: Recommendation,
        response_model: dict[str, Any],
    ) -> Recommendation:
        if not self.enabled:
            return replace(recommendation, multi_turn={**self.status(), "status": "disabled"})
        initial = PlanningState.initial(state)
        try:
            game = VerifiedBattleGame(
                calculator=self.calculator,
                meta=self.meta,
                beliefs=beliefs,
                recommendation=recommendation,
                response_model=response_model,
                config=self.config,
            )
            game.bind_root(initial)
            search = RiskAwareExpectiminimax(
                SearchConfig(
                    max_depth=self.config.depth,
                    node_budget=self.config.node_budget,
                    time_budget_ms=self.config.time_budget_ms,
                    discount=0.97,
                    lower_tail_mass=0.20,
                    expected_weight=0.72,
                    lower_tail_weight=0.28,
                    catastrophic_threshold=-55,
                    catastrophic_penalty=12,
                )
            ).search(game, initial)
        except (
            SearchBudgetExhausted,
            ShowdownCalculationError,
            ShowdownUnavailable,
            KeyError,
            TypeError,
            ValueError,
            RuntimeError,
        ) as exc:
            return replace(
                recommendation,
                multi_turn={
                    **self.status(),
                    "status": "fallback",
                    "reason": type(exc).__name__,
                    "completed_depth": 0,
                },
            )

        result = search.to_dict()
        response_calls = int(game.telemetry["response_sample_calls"])
        source_mass_total = float(
            game.telemetry.pop("sampled_source_probability_mass_total")
        )
        game.telemetry["mean_unique_source_probability_mass_per_call"] = round(
            source_mass_total / response_calls if response_calls else 0.0,
            4,
        )
        analysis = {
            **self.status(),
            "status": "ok" if search.stats.completed_depth >= 2 else "partial",
            "completed_depth": search.stats.completed_depth,
            "sampling_semantics": (
                "Every sampled next state is reachable under one exact hidden-set scenario, "
                "accuracy result, and weighted damage roll; samples approximate the chance "
                "distribution."
            ),
            "exhaustive_claim": False,
            "best": result["best"],
            "alternatives": result["alternatives"],
            "search": result["stats"],
            "transition_telemetry": game.resolver.telemetry,
            "beam_telemetry": game.telemetry,
            "unsupported_boundaries": [
                "the explicit residual-other opponent hypothesis remains an uncertainty leaf",
                "unimplemented volatile, move-specific, item, and ability effects remain "
                "named uncertainty leaves",
                "equal-power multi-hit moves sample critical hits independently per hit; "
                "Beat Up, Dragon Darts, Population Bomb, Triple Axel, and Triple Kick "
                "remain named per-hit callback boundaries",
                "legacy Champions species use their latest official species and learnset data "
                "inside the generation-9 calculator",
                "opponent responses use deterministic seeded systematic samples of the full "
                "declared distribution",
                "belief priors are not observation-updated inside sampled futures",
                "legal abilities and complete EV profiles are sampled compatibility "
                "scenarios, not calibrated set probabilities",
                "an unknown held item uses a reachable no-item scenario until revealed",
            ],
        }
        sampled = int(game.resolver.telemetry["sampled_outcomes"])
        uncertain = int(game.resolver.telemetry["uncertainty_leaves"])
        resolved_fraction = max(0.0, (sampled - uncertain) / sampled) if sampled else 0.0
        reasons = game.resolver.telemetry["uncertainty_reasons"]
        outside_model = int(
            reasons.get("opponent_response_outside_declared_model", 0)
        )
        declared_samples = max(0, sampled - outside_model)
        mechanics_uncertainty = max(0, uncertain - outside_model)
        declared_mechanics_fraction = (
            max(0.0, (declared_samples - mechanics_uncertainty) / declared_samples)
            if declared_samples
            else 0.0
        )
        modeled_response_mass = float(
            response_model.get(
                "modeled_response_mass",
                max(0.0, 1.0 - float(response_model.get("residual_mass", 1.0))),
            )
        )
        verified_frontier_fraction = (
            modeled_response_mass * declared_mechanics_fraction
        )
        promotion_eligible = bool(
            search.stats.completed_depth >= 2
            and verified_frontier_fraction
            >= self.config.minimum_verified_frontier_fraction
        )
        analysis["resolved_sample_fraction"] = round(resolved_fraction, 4)
        analysis["declared_mechanics_resolved_fraction"] = round(
            declared_mechanics_fraction, 4
        )
        analysis["modeled_response_probability_mass"] = round(
            modeled_response_mass, 4
        )
        analysis["verified_frontier_fraction"] = round(
            verified_frontier_fraction, 4
        )
        analysis["promotion_threshold"] = (
            self.config.minimum_verified_frontier_fraction
        )
        analysis["promotion_eligible"] = promotion_eligible
        if not promotion_eligible:
            return replace(recommendation, multi_turn=analysis)

        by_label = {candidate.label: candidate for candidate in recommendation.candidate_catalog}
        ordered_labels = [result["best"]["action"], *[
            row["action"] for row in result["alternatives"]
        ]]
        ordered = [by_label[label] for label in ordered_labels if label in by_label]
        ordered.extend(
            candidate
            for candidate in recommendation.candidate_catalog
            if candidate not in ordered
        )
        if not ordered:
            return replace(recommendation, multi_turn=analysis)
        return replace(
            recommendation,
            primary=ordered[0],
            alternatives=tuple(ordered[1:4]),
            candidate_catalog=tuple(ordered),
            rationale=(
                "Verified sampled two-turn search promoted this line before Codex selection. "
                + recommendation.rationale
            ),
            assumptions=(
                *recommendation.assumptions,
                "Multi-turn chance branches use deterministic weighted samples; exhaustive "
                "coverage remains one-turn only.",
            ),
            multi_turn=analysis,
            policy_version="verified-sampled-multiturn-0.8",
            validation_status="VERIFIED_SAMPLED_MULTITURN_SEARCH",
        )
