from __future__ import annotations

import hashlib
import json
import os
import random
from dataclasses import dataclass, replace
from typing import Any

from champions_copilot.actions import JointAction, generate_legal_joint_actions
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
    minimum_resolved_sample_fraction: float = 0.35

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
        if not 0 <= self.minimum_resolved_sample_fraction <= 1:
            raise ValueError("minimum_resolved_sample_fraction must be between 0 and 1")

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
        )


@dataclass(frozen=True, slots=True)
class PlanningState:
    battle: BattleState
    uncertainty: str | None = None
    trace: tuple[str, ...] = ()
    protect_chain: tuple[tuple[str, int], ...] = ()
    active_turns: tuple[tuple[str, int], ...] = ()
    hidden_profiles: tuple[tuple[str, str], ...] = ()

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
        )

    def key(self) -> str:
        value = {
            "battle": self.battle.to_dict(),
            "uncertainty": self.uncertainty,
            "protect_chain": list(self.protect_chain),
            "active_turns": list(self.active_turns),
            "hidden_profiles": list(self.hidden_profiles),
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

    def __init__(self, calculator: ShowdownCalculator, config: MultiTurnConfig) -> None:
        self.calculator = calculator
        self.config = config
        self._lookup_cache: dict[tuple[str, str], dict[str, Any]] = {}
        self._speed_cache: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        self._damage_cache: dict[tuple[str, ...], dict[str, Any]] = {}
        self.telemetry = {
            "turns_resolved": 0,
            "sampled_outcomes": 0,
            "uncertainty_leaves": 0,
            "damage_cache_hits": 0,
            "speed_cache_hits": 0,
            "hidden_profiles_sampled": 0,
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
            self.telemetry["uncertainty_leaves"] += 1
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
            battle, player_switches, opponent_switches, hidden_profiles
        )
        if switch_in_boundary:
            self.telemetry["uncertainty_leaves"] += 1
            return PlanningState(
                battle=battle,
                uncertainty=switch_in_boundary,
                trace=(*trace, switch_in_boundary),
                protect_chain=tuple(sorted(protect_chain.items())),
                active_turns=tuple(sorted(active_turns.items())),
                hidden_profiles=tuple(sorted(hidden_profiles.items())),
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
        except (KeyError, ValueError, ShowdownCalculationError, ShowdownUnavailable) as exc:
            self.telemetry["uncertainty_leaves"] += 1
            return PlanningState(
                battle=battle,
                uncertainty=f"move_order_unresolved:{type(exc).__name__}",
                trace=(*trace, "move order unresolved"),
                protect_chain=tuple(sorted(protect_chain.items())),
                active_turns=tuple(sorted(active_turns.items())),
                hidden_profiles=tuple(sorted(hidden_profiles.items())),
            )

        flinched: set[str] = set()
        acted: set[str] = set()
        used_protect: set[str] = set()
        intent_by_actor = {f"{intent.side}:{intent.actor}": intent for intent in intents}
        uncertainty: str | None = None
        pending = list(intents)
        while pending:
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
            if actor.status in {"sleep", "freeze"}:
                uncertainty = f"status_counter_not_tracked:{actor.status}"
                trace.append(f"{actor.name} action unresolved under {actor.status}")
                break
            if actor.status == "paralysis" and rng.random() < 0.25:
                trace.append(f"{actor.name} was fully paralysed")
                acted.add(actor_key)
                continue
            move_id = str(intent.move_data.get("id", ""))
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
                uncertainty = f"unsupported_status_move:{intent.move}"
                trace.append(f"unresolved status move: {intent.move}")
                break

            if move_id == "fakeout" and active_turns.get(actor_key, 1) > 0:
                trace.append(f"{actor.name} Fake Out failed after its first active turn")
                acted.add(actor_key)
                continue

            targets = self._targets(battle, intent)
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
                contact_boundary = self._contact_boundary(
                    battle,
                    intent,
                    target_side,
                    target_id,
                    hidden_profiles,
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
            )
        if uncertainty:
            self.telemetry["uncertainty_leaves"] += 1
        return PlanningState(
            battle=battle,
            uncertainty=uncertainty,
            trace=tuple(trace),
            protect_chain=tuple(sorted(protect_chain.items())),
            active_turns=tuple(sorted(active_turns.items())),
            hidden_profiles=tuple(sorted(hidden_profiles.items())),
        )

    @staticmethod
    def _switch(side: Any, outgoing: str, incoming: str) -> None:
        if outgoing not in side.active or incoming not in side.bench:
            raise ValueError("simulated switch is no longer legal")
        position = side.active.index(outgoing)
        side.active[position] = incoming
        side.bench.remove(incoming)
        if not side.roster[outgoing].fainted:
            side.bench.append(outgoing)
        side.roster[outgoing].protected = False

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
                raise ValueError("unresolved opponent action")
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
        key = (
            self._structural_key(battle),
            side,
            actor,
            move.lower(),
            target_side,
            target,
            attacker_profile_key,
            defender_profile_key,
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
            )
            self._damage_cache[key] = result
        if not result.get("damage_applicable"):
            raise ValueError("status move reached damage sampler")
        estimate = result["estimate"]
        scenario = self._weighted_choice(
            list(estimate["scenarios"]), "weight", rng
        )
        accuracy = float(scenario.get("base_accuracy_probability", 1.0))
        if rng.random() > accuracy:
            return 0.0, False, str(scenario["name"]), 0.0, int(
                scenario["defender_max_hp"]
            )
        roll = self._weighted_choice(list(scenario["rolls_percent"]), "weight", rng)
        return (
            float(roll["percent"]),
            True,
            str(scenario["name"]),
            float(roll["damage"]),
            int(scenario["defender_max_hp"]),
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
        if intent.target in battle.side(target_side).roster:
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
                trace.append(f"{target.name} gained {target.status}")
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
            return f"unsupported_secondary:{intent.move}"

        self_effect = intent.move_data.get("selfEffect")
        if isinstance(self_effect, dict) and isinstance(self_effect.get("boosts"), dict):
            actor = battle.side(intent.side).roster[intent.actor]
            for stat, delta in self_effect["boosts"].items():
                if stat in actor.boosts:
                    actor.boosts[stat] = max(-6, min(6, actor.boosts[stat] + int(delta)))
            trace.append(f"{actor.name} self stat change from {intent.move}")
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
    ) -> str | None:
        flags = intent.move_data.get("flags", [])
        if not isinstance(flags, list) or "contact" not in flags:
            return None
        ability = self._ability_for(
            battle, target_side, target_id, hidden_profiles
        )
        item = self._item_for(battle, target_side, target_id)
        reactive_abilities = {
            "Rough Skin",
            "Iron Barbs",
            "Static",
            "Flame Body",
            "Poison Point",
            "Effect Spore",
            "Gooey",
            "Tangling Hair",
            "Wandering Spirit",
            "Mummy",
        }
        if ability in reactive_abilities:
            return f"contact_ability_not_verified:{ability}"
        if item == "Rocky Helmet":
            return "contact_item_not_verified:Rocky Helmet"
        return None

    def _switch_in_boundary(
        self,
        battle: BattleState,
        player_switches: dict[str, str],
        opponent_switches: dict[str, str],
        hidden_profiles: dict[str, str],
    ) -> str | None:
        switch_abilities = {
            "Intimidate",
            "Drizzle",
            "Drought",
            "Sand Stream",
            "Snow Warning",
            "Electric Surge",
            "Grassy Surge",
            "Misty Surge",
            "Psychic Surge",
            "Download",
            "Trace",
        }
        incoming = [
            *(("player", pokemon_id) for pokemon_id in player_switches.values()),
            *(("opponent", pokemon_id) for pokemon_id in opponent_switches.values()),
        ]
        for side, pokemon_id in incoming:
            ability = self._ability_for(
                battle, side, pokemon_id, hidden_profiles
            )
            if ability in switch_abilities:
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
    ) -> str | None:
        if str(battle.field.weather).lower() in {"sand", "sandstorm", "hail"}:
            return "weather_residual_not_verified"
        for side_name in ("player", "opponent"):
            side = battle.side(side_name)
            for pokemon_id in side.active:
                pokemon = side.roster[pokemon_id]
                if pokemon.fainted:
                    continue
                ability = self._ability_for(
                    battle, side_name, pokemon_id, hidden_profiles
                )
                if ability in {"Speed Boost", "Moody"}:
                    return f"end_turn_ability_not_verified:{ability}"
                weather = str(battle.field.weather).lower()
                weather_abilities = {
                    "sun": {"Solar Power"},
                    "sunny": {"Solar Power"},
                    "rain": {"Rain Dish", "Dry Skin", "Hydration"},
                    "hail": {"Ice Body"},
                    "snow": {"Ice Body"},
                }
                if ability in weather_abilities.get(weather, set()):
                    return f"end_turn_ability_not_verified:{ability}"
                facts = side.known_facts.get(pokemon_id, {})
                residual_item = facts.get("item", pokemon.item)
                if residual_item in {
                    "Leftovers",
                    "Black Sludge",
                    "Flame Orb",
                    "Toxic Orb",
                }:
                    return f"residual_item_not_verified:{residual_item}"
                if pokemon.status == "burn":
                    pokemon.hp = round(max(0.0, float(pokemon.hp) - 6.25), 6)
                elif pokemon.status == "poison":
                    pokemon.hp = round(max(0.0, float(pokemon.hp) - 12.5), 6)
                elif pokemon.status == "toxic":
                    return "toxic_counter_not_tracked"
                pokemon.fainted = pokemon.hp <= 1e-9
                if pokemon.fainted:
                    pokemon.hp = 0
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
                return "forced_replacement_boundary"
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
        self.resolver = VerifiedTurnResolver(calculator, config)
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
        if state.key() == self.root_key:
            model = self.response_model
        else:
            model = build_response_model(
                self.calculator,
                self.meta,
                state.battle,
                self.beliefs,
                maximum_joint_responses=64,
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
        for index in range(self.config.response_limit):
            quantile = (index + 0.5) / self.config.response_limit
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
            "minimum_resolved_sample_fraction": (
                self.config.minimum_resolved_sample_fraction
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
                "forced replacements end as explicit uncertainty leaves",
                "unsupported status and secondary effects end as uncertainty leaves",
                "weather and known held-item residual effects not yet verified end as "
                "uncertainty leaves",
                "unverified contact, switch-in, and end-turn ability effects end as "
                "uncertainty leaves",
                "opponent responses use deterministic systematic samples of the full distribution",
                "belief priors are not observation-updated inside sampled futures",
                "legal abilities and complete EV profiles are sampled compatibility "
                "scenarios, not calibrated set probabilities",
                "an unknown held item uses a reachable no-item scenario until revealed",
            ],
        }
        sampled = int(game.resolver.telemetry["sampled_outcomes"])
        uncertain = int(game.resolver.telemetry["uncertainty_leaves"])
        resolved_fraction = max(0.0, (sampled - uncertain) / sampled) if sampled else 0.0
        promotion_eligible = bool(
            search.stats.completed_depth >= 2
            and resolved_fraction >= self.config.minimum_resolved_sample_fraction
        )
        analysis["resolved_sample_fraction"] = round(resolved_fraction, 4)
        analysis["promotion_threshold"] = self.config.minimum_resolved_sample_fraction
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
            policy_version="verified-sampled-multiturn-0.6",
            validation_status="VERIFIED_SAMPLED_MULTITURN_SEARCH",
        )
