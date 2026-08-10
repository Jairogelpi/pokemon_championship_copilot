from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping

from .actions import JointAction, SingleAction, generate_legal_joint_actions
from .beliefs import BeliefState
from .models import BattleState


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
    expected_damage_percent: float
    knockout_probability: float
    incoming_damage_percent: float
    incoming_knockout_probability: float
    final_score: float

    def to_dict(self) -> dict[str, float]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class RankedAction:
    action: JointAction
    label: str
    score: ScoreBreakdown
    covers: tuple[str, ...]
    damage: tuple[dict[str, Any], ...] = ()
    threats: tuple[dict[str, Any], ...] = ()
    principal_lines: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action.to_dict(),
            "label": self.label,
            "score": self.score.to_dict(),
            "covers": list(self.covers),
            "damage": list(self.damage),
            "threats": list(self.threats),
            "principal_lines": list(self.principal_lines),
        }


@dataclass(frozen=True, slots=True)
class DamageEstimate:
    actor: str
    move: str
    target: str
    source: str
    source_version: str
    generation: int
    move_category: str
    move_type: str
    move_priority: int
    move_target: str
    attacker_speed: int
    defender_speed: int
    spread_move: bool
    minimum_percent: float
    maximum_percent: float
    expected_percent: float
    knockout_probability_min: float
    knockout_probability_max: float
    knockout_probability_weighted: float
    base_accuracy_probability: float
    scenario_count: int
    scenarios: tuple[dict[str, Any], ...]
    assumptions: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["scenarios"] = list(self.scenarios)
        value["assumptions"] = list(self.assumptions)
        return value


@dataclass(frozen=True, slots=True)
class Recommendation:
    primary: RankedAction
    alternatives: tuple[RankedAction, ...]
    rationale: str
    risk: str
    assumptions: tuple[str, ...]
    calculator: dict[str, Any]
    response_model: dict[str, Any]
    candidate_catalog: tuple[RankedAction, ...] = ()
    multi_turn: dict[str, Any] = field(default_factory=dict)
    policy_version: str = "adversarial-search-0.7"
    validation_status: str = "ADVERSARIAL_SHOWDOWN_MODEL"

    def to_dict(self) -> dict[str, Any]:
        return {
            "primary": self.primary.to_dict(),
            "alternatives": [alternative.to_dict() for alternative in self.alternatives],
            "rationale": self.rationale,
            "risk": self.risk,
            "assumptions": list(self.assumptions),
            "calculator": self.calculator,
            "response_model": self.response_model,
            "candidate_catalog": [
                {
                    "id": f"candidate-{rank:02d}",
                    "rank": rank,
                    "label": candidate.label,
                    "action": candidate.action.to_dict(),
                    "score": candidate.score.to_dict(),
                    "covers": list(candidate.covers),
                    "principal_lines": list(candidate.principal_lines),
                }
                for rank, candidate in enumerate(self.candidate_catalog, start=1)
            ],
            "multi_turn": self.multi_turn or {
                "status": "not_run",
                "completed_depth": 0,
            },
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


def action_damage(
    state: BattleState,
    action: SingleAction,
    damage_estimates: Mapping[tuple[str, str, str], DamageEstimate],
) -> list[DamageEstimate]:
    if action.kind != "move" or not action.move:
        return []
    targets = (
        [target for target in state.opponent.active if not state.opponent.roster[target].fainted]
        if action.target == "opponents"
        else [action.target]
    )
    return [
        damage_estimates[(action.actor, action.move, str(target))]
        for target in targets
        if (action.actor, action.move, str(target)) in damage_estimates
    ]


def base_action_value(
    state: BattleState,
    action: SingleAction,
    damage_estimates: Mapping[tuple[str, str, str], DamageEstimate],
) -> float:
    actor = state.player.roster[action.actor]
    if action.kind == "switch":
        incoming = state.player.roster[action.switch_to or ""]
        return 7 + (100 - actor.hp) / 15 + incoming.hp / 50
    estimates = action_damage(state, action, damage_estimates)
    if estimates:
        value = 4.0 + sum(
            0.42 * estimate.expected_percent
            + 32 * estimate.knockout_probability_weighted
            for estimate in estimates
        )
    else:
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
    categories = [part.split(":", 1)[-1] for part in scenario.split(" + ")]
    value = 0.0
    for category in categories:
        if category == "attack":
            value += 6 if "Protect" in moves else -2
        elif category == "protect":
            value += 5 if "Feint" in moves else -5
        elif category == "switch":
            value += 3 if any(single.target == "opponents" for single in action.actions) else 0
        elif category == "speed_control":
            value += 5 if moves & {"Tailwind", "Fake Out"} else -4
        elif category == "setup_or_control":
            value += 4 if moves & {"Fake Out", "Taunt", "Feint"} else -2
        else:
            value += -1 + switches
    if len(categories) == 2 and categories.count("attack") == 2 and "Protect" not in moves:
        value -= 3
    return value / max(1, len(categories))


def weighted_lower_tail(outcomes: list[tuple[str, float, float]], mass: float = 0.2) -> float:
    remaining = mass
    weighted = 0.0
    for _, probability, value in sorted(outcomes, key=lambda item: item[2]):
        included = min(remaining, probability)
        weighted += included * value
        remaining -= included
        if remaining <= 1e-12:
            break
    used = mass - remaining
    return weighted / used if used else 0.0


def _convolve_probabilities(
    left: dict[float, float], right: dict[float, float]
) -> dict[float, float]:
    result: dict[float, float] = {}
    for left_damage, left_probability in left.items():
        for right_damage, right_probability in right.items():
            total = round(left_damage + right_damage, 6)
            result[total] = result.get(total, 0.0) + left_probability * right_probability
    return result


def _scenario_distribution(scenario: dict[str, Any]) -> dict[float, float]:
    accuracy = float(scenario.get("base_accuracy_probability", 1.0))
    rolls = list(scenario.get("rolls_percent", []))
    total_weight = sum(float(roll.get("weight", 0)) for roll in rolls)
    distribution = {0.0: max(0.0, 1 - accuracy)} if accuracy < 1 else {}
    if total_weight <= 0:
        return distribution or {0.0: 1.0}
    for roll in rolls:
        damage = float(roll["percent"])
        probability = accuracy * float(roll["weight"]) / total_weight
        distribution[damage] = distribution.get(damage, 0.0) + probability
    return distribution


def combined_knockout_probability(
    state: BattleState,
    estimates: list[DamageEstimate],
    *,
    target_side: str = "opponent",
) -> float:
    by_target: dict[str, list[DamageEstimate]] = {}
    for estimate in estimates:
        by_target.setdefault(estimate.target, []).append(estimate)
    no_knockout_probability = 1.0
    roster = state.side(target_side).roster
    for target_id, target_estimates in by_target.items():
        target_hp = roster[target_id].hp
        scenarios = {
            str(scenario["name"]): scenario
            for scenario in target_estimates[0].scenarios
        }
        target_ko = 0.0
        total_scenario_weight = 0.0
        for scenario_name, first_scenario in scenarios.items():
            scenario_weight = float(first_scenario.get("weight", 0.0))
            distributions: list[dict[float, float]] = []
            for estimate in target_estimates:
                match = next(
                    (
                        candidate
                        for candidate in estimate.scenarios
                        if candidate.get("name") == scenario_name
                    ),
                    None,
                )
                if match is not None:
                    distributions.append(_scenario_distribution(match))
            if not distributions:
                continue
            combined = {0.0: 1.0}
            for distribution in distributions:
                combined = _convolve_probabilities(combined, distribution)
            ko_probability = sum(
                probability
                for damage, probability in combined.items()
                if damage + 1e-6 >= target_hp
            )
            target_ko += scenario_weight * ko_probability
            total_scenario_weight += scenario_weight
        if total_scenario_weight:
            target_ko /= total_scenario_weight
        no_knockout_probability *= 1 - target_ko
    return 1 - no_knockout_probability


def independent_knockout_probability(
    state: BattleState,
    estimates: list[DamageEstimate],
    *,
    target_side: str,
) -> float:
    by_target: dict[str, list[DamageEstimate]] = {}
    for estimate in estimates:
        by_target.setdefault(estimate.target, []).append(estimate)
    roster = state.side(target_side).roster
    no_knockout_probability = 1.0
    for target_id, target_estimates in by_target.items():
        combined = {0.0: 1.0}
        for estimate in target_estimates:
            mixture: dict[float, float] = {}
            total_weight = sum(
                float(scenario.get("weight", 0.0)) for scenario in estimate.scenarios
            )
            for scenario in estimate.scenarios:
                scenario_weight = float(scenario.get("weight", 0.0)) / max(
                    total_weight, 1e-12
                )
                for damage, probability in _scenario_distribution(scenario).items():
                    mixture[damage] = mixture.get(damage, 0.0) + scenario_weight * probability
            combined = _convolve_probabilities(combined, mixture)
        target_ko = sum(
            probability
            for damage, probability in combined.items()
            if damage + 1e-6 >= roster[target_id].hp
        )
        no_knockout_probability *= 1 - target_ko
    return 1 - no_knockout_probability


def select_incoming_threats(
    action: JointAction,
    incoming_threats: Mapping[tuple[str, str, str], DamageEstimate],
) -> list[DamageEstimate]:
    protected = {
        single.actor
        for single in action.actions
        if single.kind == "move" and single.move == "Protect"
    }
    occupants = {
        single.switch_to if single.kind == "switch" else single.actor
        for single in action.actions
    }
    candidates = [
        estimate
        for estimate in incoming_threats.values()
        if estimate.target in occupants and estimate.target not in protected
    ]
    by_actor: dict[str, list[DamageEstimate]] = {}
    for estimate in candidates:
        by_actor.setdefault(estimate.actor, []).append(estimate)
    selected: list[DamageEstimate] = []
    for _, estimates in sorted(by_actor.items()):
        plans: list[list[DamageEstimate]] = []
        spread_moves = sorted({estimate.move for estimate in estimates if estimate.spread_move})
        plans.extend(
            [[estimate for estimate in estimates if estimate.move == move] for move in spread_moves]
        )
        plans.extend([[estimate] for estimate in estimates if not estimate.spread_move])
        if plans:
            selected.extend(
                max(
                    plans,
                    key=lambda plan: (
                        sum(
                            estimate.expected_percent
                            + 50 * estimate.knockout_probability_weighted
                            for estimate in plan
                        ),
                        tuple((estimate.move, estimate.target) for estimate in plan),
                    ),
                )
            )
    return selected


def _acts_before(
    state: BattleState, first: DamageEstimate, second: DamageEstimate
) -> bool:
    if first.move_priority != second.move_priority:
        return first.move_priority > second.move_priority
    if first.attacker_speed == second.attacker_speed:
        return False
    if state.field.trick_room_turns > 0:
        return first.attacker_speed < second.attacker_speed
    return first.attacker_speed > second.attacker_speed


def _response_threats(
    action: JointAction,
    response: Mapping[str, Any],
    incoming_threats: Mapping[tuple[str, str, str], DamageEstimate],
) -> list[DamageEstimate]:
    occupants = {
        single.actor: single.switch_to if single.kind == "switch" else single.actor
        for single in action.actions
    }
    protected = {
        single.actor
        for single in action.actions
        if single.kind == "move" and single.move == "Protect"
    }
    threats: list[DamageEstimate] = []
    for reply in response.get("actions", []):
        if reply.get("kind") != "move" or reply.get("move_category") == "Status":
            continue
        target = reply.get("target")
        original_targets = list(occupants) if target == "players" else [target]
        for original_target in original_targets:
            if original_target not in occupants or original_target in protected:
                continue
            actual_target = occupants[original_target]
            estimate = incoming_threats.get(
                (str(reply.get("actor")), str(reply.get("move")), str(actual_target))
            )
            if estimate is not None:
                threats.append(estimate)
    return threats


def _unblocked_outgoing(
    estimates: list[DamageEstimate], response: Mapping[str, Any]
) -> list[DamageEstimate]:
    denied = {
        str(reply.get("actor"))
        for reply in response.get("actions", [])
        if reply.get("kind") == "switch"
        or (reply.get("kind") == "move" and reply.get("category") == "protect")
    }
    return [estimate for estimate in estimates if estimate.target not in denied]


def _incoming_after_speed_race(
    state: BattleState,
    outgoing: list[DamageEstimate],
    threats: list[DamageEstimate],
) -> tuple[float, float, list[dict[str, Any]]]:
    incoming_damage = 0.0
    no_ko = 1.0
    speed_notes: list[dict[str, Any]] = []
    for threat in threats:
        faster_attacks = [
            attack
            for attack in outgoing
            if attack.target == threat.actor and _acts_before(state, attack, threat)
        ]
        suppression = combined_knockout_probability(state, faster_attacks)
        fake_out = next(
            (attack for attack in faster_attacks if attack.move == "Fake Out"), None
        )
        if fake_out is not None:
            suppression = max(suppression, fake_out.base_accuracy_probability)
        faster_move = "+".join(attack.move for attack in faster_attacks) or None
        survival = 1.0 - suppression
        incoming_damage += threat.expected_percent * survival
        no_ko *= 1.0 - threat.knockout_probability_weighted * survival
        speed_notes.append(
            {
                "opponent": threat.actor,
                "move": threat.move,
                "priority": threat.move_priority,
                "speed": threat.attacker_speed,
                "suppressed_probability": round(suppression, 4),
                "faster_player_move": faster_move,
            }
        )
    return incoming_damage, 1.0 - no_ko, speed_notes


def _concrete_outcomes(
    state: BattleState,
    action: JointAction,
    estimates: list[DamageEstimate],
    incoming_threats: Mapping[tuple[str, str, str], DamageEstimate],
    responses: list[dict[str, Any]],
    strategic: float,
) -> list[dict[str, Any]]:
    action_base = sum(base_action_value(state, single, {}) for single in action.actions)
    outcomes: list[dict[str, Any]] = []
    for response in responses:
        outgoing = _unblocked_outgoing(estimates, response)
        outgoing_damage = sum(estimate.expected_percent for estimate in outgoing)
        outgoing_ko = combined_knockout_probability(state, outgoing)
        threats = _response_threats(action, response, incoming_threats)
        incoming_damage, incoming_ko, speed_notes = _incoming_after_speed_race(
            state, outgoing, threats
        )
        categories = " + ".join(
            f"{reply.get('actor')}:{reply.get('category', 'other')}"
            for reply in response.get("actions", [])
        )
        offensive = action_base + 0.42 * outgoing_damage + 38 * outgoing_ko
        value = (
            offensive
            + strategic
            + scenario_adjustment(action, categories or "other")
            - 0.22 * incoming_damage
            - 30 * incoming_ko
        )
        outcomes.append(
            {
                "label": str(response.get("label", "other")),
                "probability": float(response.get("probability", 0.0)),
                "utility": value,
                "outgoing_damage_percent": outgoing_damage,
                "outgoing_knockout_probability": outgoing_ko,
                "incoming_damage_percent": incoming_damage,
                "incoming_knockout_probability": incoming_ko,
                "actions": response.get("actions", []),
                "threats": threats,
                "speed_order": speed_notes,
            }
        )
    return outcomes


def score_action(
    state: BattleState,
    action: JointAction,
    damage_estimates: Mapping[tuple[str, str, str], DamageEstimate],
    incoming_threats: Mapping[tuple[str, str, str], DamageEstimate],
    response_distribution: Mapping[str, float],
    concrete_responses: list[dict[str, Any]] | None = None,
) -> RankedAction:
    estimates = [
        estimate
        for single in action.actions
        for estimate in action_damage(state, single, damage_estimates)
    ]
    strategic = synergy_value(state, action)
    principal_lines: tuple[dict[str, Any], ...] = ()
    if concrete_responses:
        concrete = _concrete_outcomes(
            state,
            action,
            estimates,
            incoming_threats,
            concrete_responses,
            strategic,
        )
        outcomes = [
            (row["label"], row["probability"], row["utility"]) for row in concrete
        ]
        expected_damage = sum(
            row["probability"] * row["outgoing_damage_percent"] for row in concrete
        )
        knockout_probability = sum(
            row["probability"] * row["outgoing_knockout_probability"] for row in concrete
        )
        incoming_damage = sum(
            row["probability"] * row["incoming_damage_percent"] for row in concrete
        )
        incoming_knockout = sum(
            row["probability"] * row["incoming_knockout_probability"] for row in concrete
        )
        counter_lines = sorted(
            concrete,
            key=lambda row: (
                row["utility"],
                -row["probability"],
                row["label"],
            ),
        )[:5]
        principal_lines = tuple(
            {
                "response": row["label"],
                "probability": round(row["probability"], 4),
                "utility": round(row["utility"], 3),
                "outgoing_damage_percent": round(row["outgoing_damage_percent"], 3),
                "outgoing_knockout_probability": round(
                    row["outgoing_knockout_probability"], 4
                ),
                "incoming_damage_percent": round(row["incoming_damage_percent"], 3),
                "incoming_knockout_probability": round(
                    row["incoming_knockout_probability"], 4
                ),
                "speed_order": row["speed_order"],
            }
            for row in counter_lines
        )
        threats = counter_lines[0]["threats"] if counter_lines else []
    else:
        expected_damage = sum(estimate.expected_percent for estimate in estimates)
        knockout_probability = combined_knockout_probability(state, estimates)
        threats = select_incoming_threats(action, incoming_threats)
        incoming_damage = sum(estimate.expected_percent for estimate in threats)
        incoming_knockout = independent_knockout_probability(
            state, threats, target_side="player"
        )
        base = (
            sum(base_action_value(state, single, damage_estimates) for single in action.actions)
            + 38 * knockout_probability
            - 0.22 * incoming_damage
            - 30 * incoming_knockout
        )
        outcomes = [
            (scenario, probability, base + strategic + scenario_adjustment(action, scenario))
            for scenario, probability in response_distribution.items()
        ]
    expected = sum(probability * value for _, probability, value in outcomes)
    lower_tail = weighted_lower_tail(outcomes) if outcomes else expected
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
        expected_damage_percent=round(expected_damage, 3),
        knockout_probability=round(knockout_probability, 4),
        incoming_damage_percent=round(incoming_damage, 3),
        incoming_knockout_probability=round(incoming_knockout, 4),
        final_score=round(final, 3),
    )
    covered = tuple(
        scenario for scenario, probability, _ in sorted(outcomes, key=lambda item: -item[1])[:3]
    )
    return RankedAction(
        action=action,
        label=action.label(state),
        score=breakdown,
        covers=covered,
        damage=tuple(estimate.to_dict() for estimate in estimates),
        threats=tuple(estimate.to_dict() for estimate in threats),
        principal_lines=principal_lines,
    )


def recommend_actions(
    state: BattleState,
    beliefs: BeliefState,
    damage_estimates: Mapping[tuple[str, str, str], DamageEstimate] | None = None,
    incoming_threats: Mapping[tuple[str, str, str], DamageEstimate] | None = None,
    calculator_status: dict[str, Any] | None = None,
    concrete_response_model: dict[str, Any] | None = None,
) -> Recommendation:
    damage_estimates = damage_estimates or {}
    incoming_threats = incoming_threats or {}
    candidates = generate_legal_joint_actions(state)
    if not candidates:
        raise ValueError("no legal paired actions are available")
    response_distribution = beliefs.active_joint_response_distribution(state)
    concrete_responses = (
        list(concrete_response_model.get("responses", []))
        if concrete_response_model
        else None
    )
    ranked = sorted(
        (
            score_action(
                state,
                candidate,
                damage_estimates,
                incoming_threats,
                response_distribution,
                concrete_responses,
            )
            for candidate in candidates
        ),
        key=lambda candidate: (-candidate.score.final_score, candidate.label),
    )
    primary = ranked[0]
    alternatives: list[RankedAction] = []
    for target_id in state.opponent.active:
        candidate = next(
            (
                row
                for row in ranked[1:]
                if row not in alternatives
                and any(estimate.get("target") == target_id for estimate in row.damage)
            ),
            None,
        )
        if candidate is not None:
            alternatives.append(candidate)
    for candidate in ranked[1:]:
        if candidate not in alternatives:
            alternatives.append(candidate)
        if len(alternatives) >= 3:
            break
    risk = "high" if primary.score.catastrophic_loss_probability > 0.2 else "medium"
    if primary.score.catastrophic_loss_probability <= 0.08:
        risk = "low"
    active_opponents = [state.opponent.roster[id].name for id in state.opponent.active]
    has_showdown = bool(damage_estimates)
    rationale = (
        f"This line has the best risk-adjusted score against the current response mixture for "
        f"{' and '.join(active_opponents)} across "
        f"{len(concrete_responses) if concrete_responses else len(response_distribution)} simultaneous response "
        f"scenarios. It covers {', '.join(primary.covers)} and "
        + (
            "uses pinned Showdown damage rolls across the displayed opponent-set scenarios."
            if has_showdown
            else "does not include damage numbers because the Showdown calculator is unavailable."
        )
    )
    assumptions = [
        "Opponent sets are incomplete and retain an 'other' probability bucket.",
        "Opponent bulk is evaluated as explicit no-bulk, HP-invested, and maximum relevant-bulk scenarios.",
        "This policy has not passed the frozen Master 2000 benchmark and must not be represented as proven at that level.",
        "Stock Showdown Gen 9 data does not yet model Champions-only Mega stats or custom items.",
    ]
    if has_showdown:
        assumptions.insert(
            0,
            "Damage rolls are exact under each listed @smogon/calc scenario; uncertainty comes from hidden sets and Champions compatibility.",
        )
    else:
        assumptions.insert(0, "No damage values were synthesized while @smogon/calc was unavailable.")
    status = calculator_status or {
        "available": has_showdown,
        "engine": "@smogon/calc",
        "status": "ok" if has_showdown else "unavailable",
    }
    return Recommendation(
        primary=primary,
        alternatives=tuple(alternatives[:3]),
        rationale=rationale,
        risk=risk,
        assumptions=tuple(assumptions),
        calculator=status,
        candidate_catalog=tuple(ranked[:12]),
        response_model={
            "scenarios_evaluated": len(concrete_responses or response_distribution),
            "concrete": bool(concrete_responses),
            "residual_other_preserved": (
                bool(concrete_response_model and concrete_response_model.get("residual_mass", 0) > 0)
                or any("other" in scenario for scenario in response_distribution)
            ),
            "coverage_mass": (
                concrete_response_model.get("coverage_mass")
                if concrete_response_model
                else 1.0
            ),
            "residual_mass": (
                concrete_response_model.get("residual_mass")
                if concrete_response_model
                else 0.0
            ),
            "probability_semantics": (
                concrete_response_model.get("probability_semantics")
                if concrete_response_model
                else "belief-category distribution"
            ),
            "meta": (
                concrete_response_model.get("meta") if concrete_response_model else None
            ),
            "candidate_actions": (
                concrete_response_model.get("candidate_actions", {})
                if concrete_response_model
                else {}
            ),
            "top_scenarios": [
                {
                    "scenario": row.get("label"),
                    "probability": round(float(row.get("probability", 0)), 4),
                }
                for row in sorted(
                    concrete_responses or [
                        {"label": scenario, "probability": probability}
                        for scenario, probability in response_distribution.items()
                    ],
                    key=lambda item: (-float(item.get("probability", 0)), str(item.get("label"))),
                )[:5]
            ],
            "lower_tail_mass": 0.2,
            "search_space": {
                "horizon_turns": 1,
                "legal_player_joint_actions": len(ranked),
                "modelled_opponent_joint_responses": len(
                    concrete_responses or response_distribution
                ),
                "evaluated_action_response_pairs": len(ranked)
                * len(concrete_responses or response_distribution),
                "expanded_legal_opponent_joint_responses": (
                    concrete_response_model.get("expanded_legal_joint_responses")
                    if concrete_response_model
                    else len(response_distribution)
                ),
                "truncated_opponent_joint_responses": (
                    concrete_response_model.get("truncated_joint_responses", 0)
                    if concrete_response_model
                    else 0
                ),
                "exhaustive_within_configured_horizon": bool(
                    concrete_responses
                    and concrete_response_model
                    and concrete_response_model.get(
                        "exhaustive_within_response_horizon", False
                    )
                ),
                "candidate_selection_envelope": min(12, len(ranked)),
                "hidden_information_policy": (
                    "unknown actions remain explicit residual branches; they are never "
                    "silently assigned certainty"
                ),
            },
        },
        validation_status=(
            "ADVERSARIAL_SHOWDOWN_MODEL" if has_showdown and concrete_responses
            else "SHOWDOWN_SCENARIO_MODEL" if has_showdown
            else "SHOWDOWN_UNAVAILABLE"
        ),
    )
