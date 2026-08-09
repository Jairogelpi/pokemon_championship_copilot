from __future__ import annotations

from dataclasses import asdict, dataclass
from itertools import combinations
from typing import Any

from .actions import JointAction, SingleAction, generate_legal_joint_actions
from .beliefs import BeliefState
from .models import BattleState
from .team import PLAYER_TEAM


MOVE_VALUE = {
    "Blizzard": 18,
    "Shadow Ball": 14,
    "Aurora Veil": 19,
    "Fake Out": 20,
    "Close Combat": 18,
    "Dire Claw": 16,
    "Feint": 12,
    "Wave Crash": 20,
    "Aqua Jet": 14,
    "Last Respects": 17,
    "Dragon Pulse": 14,
    "Heat Wave": 17,
    "Tailwind": 19,
    "Earthquake": 19,
    "Rock Slide": 17,
    "Dragon Claw": 15,
    "Kowtow Cleave": 17,
    "Sucker Punch": 16,
    "Iron Head": 16,
    "Protect": 9,
}


@dataclass(frozen=True, slots=True)
class ScoreBreakdown:
    expected_utility: float
    lower_tail_utility: float
    strategic_value: float
    information_value: float
    catastrophic_loss_probability: float
    final_score: float

    def to_dict(self) -> dict[str, float]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class RankedAction:
    action: JointAction
    label: str
    score: ScoreBreakdown
    covers: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action.to_dict(),
            "label": self.label,
            "score": self.score.to_dict(),
            "covers": list(self.covers),
        }


@dataclass(frozen=True, slots=True)
class Recommendation:
    primary: RankedAction
    alternatives: tuple[RankedAction, ...]
    rationale: str
    risk: str
    assumptions: tuple[str, ...]
    policy_version: str = "baseline-0.1"
    validation_status: str = "UNVALIDATED_BASELINE"

    def to_dict(self) -> dict[str, Any]:
        return {
            "primary": self.primary.to_dict(),
            "alternatives": [alternative.to_dict() for alternative in self.alternatives],
            "rationale": self.rationale,
            "risk": self.risk,
            "assumptions": list(self.assumptions),
            "policy_version": self.policy_version,
            "validation_status": self.validation_status,
        }


def recommend_team_preview(opponent_names: list[str]) -> dict[str, Any]:
    lower = {name.lower() for name in opponent_names}
    selected = ["froslass", "sneasler", "basculegion", "dragonite"]
    lead = ["froslass", "sneasler"]
    reasons = [
        "Froslass and Sneasler create immediate speed and disruption pressure.",
        "Basculegion and Dragonite preserve two independent late-game routes.",
    ]
    if lower & {"incineroar", "kingambit", "archaludon"}:
        selected = ["sneasler", "garchomp", "basculegion", "dragonite"]
        lead = ["sneasler", "garchomp"]
        reasons = [
            "The opposing preview contains physical targets that increase Garchomp's immediate value.",
            "Sneasler protects the opening with Fake Out while preserving Basculegion for cleanup.",
        ]
    if lower & {"farigiraf", "toxapex"}:
        selected = ["sneasler", "kingambit", "basculegion", "dragonite"]
        lead = ["sneasler", "kingambit"]
        reasons.append("Kingambit is retained as the slower positional anchor into control structures.")
    return {
        "selected": selected,
        "lead": lead,
        "back": [id for id in selected if id not in lead],
        "reasons": reasons,
        "validation_status": "UNVALIDATED_BASELINE",
    }


def target_pressure(state: BattleState, action: SingleAction) -> float:
    if action.target not in state.opponent.roster:
        return 0.0
    target = state.opponent.roster[action.target]
    pressure = (100 - target.hp) / 8
    if target.role in {"speed-control", "trick-room", "setup", "endgame-anchor"}:
        pressure += 5
    return pressure


def base_action_value(state: BattleState, action: SingleAction) -> float:
    actor = state.player.roster[action.actor]
    if action.kind == "switch":
        incoming = state.player.roster[action.switch_to or ""]
        return 7 + (100 - actor.hp) / 15 + incoming.hp / 50
    value = float(MOVE_VALUE.get(action.move or "", 10))
    value += target_pressure(state, action)
    if action.move == "Protect":
        value += (100 - actor.hp) / 6
    if action.move == "Tailwind" and state.player.side_conditions.get("tailwind", 0) > 0:
        value -= 12
    if action.move == "Aurora Veil" and state.player.side_conditions.get("aurora_veil", 0) > 0:
        value -= 12
    if action.move == "Last Respects":
        fainted_allies = sum(member.fainted for member in state.player.roster.values())
        value += 3 * fainted_allies
    return value


def synergy_value(state: BattleState, action: JointAction) -> float:
    moves = {single.move for single in action.actions}
    targets = [single.target for single in action.actions if single.target in state.opponent.roster]
    value = 0.0
    if "Fake Out" in moves and moves & {"Tailwind", "Aurora Veil"}:
        value += 11
    if len(targets) == 2 and targets[0] == targets[1]:
        target = state.opponent.roster[targets[0]]
        value += 8 if target.hp <= 65 else 3
    if moves == {"Protect"}:
        value -= 14
    if "Earthquake" in moves:
        partner = next(
            state.player.roster[single.actor]
            for single in action.actions
            if single.move != "Earthquake"
        )
        if partner.name not in {"Dragonite", "Froslass"} and not any(
            single.move == "Protect" for single in action.actions
        ):
            value -= 16
    return value


def scenario_adjustment(action: JointAction, scenario: str) -> float:
    moves = {single.move for single in action.actions}
    switches = sum(single.kind == "switch" for single in action.actions)
    if scenario == "attack":
        return 6 if "Protect" in moves else -2
    if scenario == "protect":
        return 5 if "Feint" in moves else -5
    if scenario == "switch":
        return 3 if any(single.target == "opponents" for single in action.actions) else 0
    if scenario == "speed_control":
        return 5 if moves & {"Tailwind", "Fake Out"} else -4
    if scenario == "setup_or_control":
        return 4 if moves & {"Fake Out", "Taunt", "Feint"} else -2
    return -1 + switches


def score_action(state: BattleState, beliefs: BeliefState, action: JointAction) -> RankedAction:
    base = sum(base_action_value(state, single) for single in action.actions)
    strategic = synergy_value(state, action)
    distribution = beliefs.active_action_distribution(state)
    outcomes = [
        (scenario, probability, base + strategic + scenario_adjustment(action, scenario))
        for scenario, probability in distribution.items()
    ]
    expected = sum(probability * value for _, probability, value in outcomes)
    ordered = sorted(value for _, _, value in outcomes)
    lower_tail = ordered[max(0, len(ordered) // 4 - 1)] if ordered else expected
    information = 4.0 if any(single.kind == "move" for single in action.actions) else 1.0
    catastrophic = sum(probability for _, probability, value in outcomes if value < 12)
    final = (
        0.62 * expected
        + 0.20 * lower_tail
        + 0.13 * strategic
        + 0.05 * information
        - 12 * catastrophic
    )
    breakdown = ScoreBreakdown(
        expected_utility=round(expected, 3),
        lower_tail_utility=round(lower_tail, 3),
        strategic_value=round(strategic, 3),
        information_value=information,
        catastrophic_loss_probability=round(catastrophic, 3),
        final_score=round(final, 3),
    )
    covered = tuple(
        scenario for scenario, probability, _ in sorted(outcomes, key=lambda item: -item[1])[:3]
    )
    return RankedAction(action=action, label=action.label(state), score=breakdown, covers=covered)


def recommend_actions(state: BattleState, beliefs: BeliefState) -> Recommendation:
    candidates = generate_legal_joint_actions(state)
    if not candidates:
        raise ValueError("no legal paired actions are available")
    ranked = sorted(
        (score_action(state, beliefs, candidate) for candidate in candidates),
        key=lambda candidate: (-candidate.score.final_score, candidate.label),
    )
    primary = ranked[0]
    risk = "high" if primary.score.catastrophic_loss_probability > 0.2 else "medium"
    if primary.score.catastrophic_loss_probability <= 0.08:
        risk = "low"
    active_opponents = [state.opponent.roster[id].name for id in state.opponent.active]
    rationale = (
        f"This line has the best risk-adjusted score against the current response mixture for "
        f"{' and '.join(active_opponents)}. It covers {', '.join(primary.covers)} while retaining "
        "an explicit lower-tail penalty for nearby counterplay."
    )
    assumptions = (
        "Opponent sets are incomplete and retain an 'other' probability bucket.",
        "The baseline policy is deterministic but has not passed the Master 2000 benchmark.",
        "Damage-specific scoring is conservative until exact stats are verified.",
    )
    return Recommendation(
        primary=primary,
        alternatives=tuple(ranked[1:4]),
        rationale=rationale,
        risk=risk,
        assumptions=assumptions,
    )
