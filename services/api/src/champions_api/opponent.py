from __future__ import annotations

from itertools import product
from typing import Any

from champions_copilot.beliefs import BeliefState, normalize
from champions_copilot.models import BattleState

from .meta import MetaRepository
from .showdown import ShowdownCalculationError, ShowdownCalculator


PROTECT_MOVES = {"protect", "detect", "spikyshield", "kingsshield", "banefulbunker"}
SPEED_CONTROL_MOVES = {
    "tailwind",
    "trickroom",
    "icywind",
    "electroweb",
    "bulldoze",
    "rocktomb",
    "scaryface",
    "quash",
}
SPREAD_TARGETS = {"allAdjacent", "allAdjacentFoes", "all"}
SELF_TARGETS = {"self", "allySide", "allyTeam", "allies", "adjacentAllyOrSelf"}


def _move_category(move: dict[str, Any]) -> str:
    move_id = str(move["id"])
    if move_id in PROTECT_MOVES:
        return "protect"
    if move_id in SPEED_CONTROL_MOVES:
        return "speed_control"
    if move.get("category") == "Status":
        return "setup_or_control"
    return "attack"


def _targeted_actions(
    state: BattleState,
    actor_id: str,
    candidate: dict[str, Any],
    move: dict[str, Any],
    probability: float,
) -> list[dict[str, Any]]:
    move_target = str(move.get("target", "normal"))
    base = {
        "actor": actor_id,
        "kind": "move",
        "move": move["name"],
        "category": _move_category(move),
        "priority": int(move.get("priority", 0)),
        "move_category": move.get("category"),
        "source": candidate["source"],
        "meta_position": candidate.get("meta_position"),
    }
    if str(move["id"]) in PROTECT_MOVES or move_target in SELF_TARGETS:
        return [{**base, "target": actor_id, "probability": probability}]
    if move_target in SPREAD_TARGETS:
        return [{**base, "target": "players", "probability": probability}]
    targets = [
        pokemon_id
        for pokemon_id in state.player.active
        if not state.player.roster[pokemon_id].fainted
    ]
    if not targets:
        return [{**base, "target": "field", "probability": probability}]
    share = probability / len(targets)
    return [{**base, "target": target, "probability": share} for target in targets]


def _actions_for_actor(
    calculator: ShowdownCalculator,
    meta: MetaRepository,
    state: BattleState,
    beliefs: BeliefState,
    actor_id: str,
) -> tuple[list[dict[str, Any]], list[str], list[dict[str, str]]]:
    actor = state.opponent.roster[actor_id]
    belief = beliefs.opponent[actor_id]
    candidates = meta.move_candidates(actor.name, actor.revealed_moves, limit=6)
    move_rows: list[tuple[dict[str, Any], dict[str, Any]]] = []
    errors: list[dict[str, str]] = []
    for candidate in candidates:
        try:
            move_rows.append((candidate, calculator.lookup("move", candidate["move"])["entry"]))
        except ShowdownCalculationError as exc:
            errors.append({"actor": actor_id, "move": candidate["move"], "message": str(exc)})

    grouped: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = {}
    for candidate, move in move_rows:
        grouped.setdefault(_move_category(move), []).append((candidate, move))

    actions: list[dict[str, Any]] = []
    unassigned = float(belief.action_categories.get("other", 0.0))
    for category in ("attack", "protect", "speed_control", "setup_or_control"):
        mass = float(belief.action_categories.get(category, 0.0))
        rows = grouped.get(category, [])
        if not rows:
            unassigned += mass
            continue
        total_score = sum(float(candidate["score"]) for candidate, _ in rows)
        for candidate, move in rows:
            probability = mass * float(candidate["score"]) / total_score
            actions.extend(
                _targeted_actions(state, actor_id, candidate, move, probability)
            )

    living_bench = [
        pokemon_id
        for pokemon_id in state.opponent.bench
        if not state.opponent.roster[pokemon_id].fainted
    ]
    switch_mass = float(belief.action_categories.get("switch", 0.0))
    if living_bench:
        for pokemon_id in living_bench:
            actions.append(
                {
                    "actor": actor_id,
                    "kind": "switch",
                    "switch_to": pokemon_id,
                    "category": "switch",
                    "source": "belief_switch_prior",
                    "probability": switch_mass / len(living_bench),
                }
            )
    else:
        unassigned += switch_mass

    actions.append(
        {
            "actor": actor_id,
            "kind": "other",
            "category": "other",
            "source": "residual_unknown",
            "probability": unassigned,
        }
    )
    probabilities = normalize(
        {str(index): float(action["probability"]) for index, action in enumerate(actions)}
    )
    for index, action in enumerate(actions):
        action["probability"] = probabilities[str(index)]
    damaging = sorted(
        {
            str(move["name"])
            for _, move in move_rows
            if move.get("category") != "Status"
        }
    )
    return actions, damaging, errors


def _action_label(state: BattleState, action: dict[str, Any]) -> str:
    actor = state.opponent.roster[action["actor"]].name
    if action["kind"] == "switch":
        incoming = state.opponent.roster[action["switch_to"]].name
        return f"{actor}: switch→{incoming}"
    if action["kind"] == "other":
        return f"{actor}: other/unknown"
    target_id = action.get("target")
    if target_id == "players":
        target = "both"
    elif target_id == action["actor"]:
        target = "self"
    elif target_id in state.player.roster:
        target = state.player.roster[target_id].name
    else:
        target = "field"
    return f"{actor}: {action['move']}→{target}"


def build_response_model(
    calculator: ShowdownCalculator,
    meta: MetaRepository,
    state: BattleState,
    beliefs: BeliefState,
    *,
    maximum_joint_responses: int = 256,
) -> dict[str, Any]:
    active = [pokemon_id for pokemon_id in state.opponent.active if not state.opponent.roster[pokemon_id].fainted]
    actor_actions: dict[str, list[dict[str, Any]]] = {}
    damage_moves: dict[str, list[str]] = {}
    errors: list[dict[str, str]] = []
    for actor_id in active:
        actions, moves, actor_errors = _actions_for_actor(
            calculator, meta, state, beliefs, actor_id
        )
        actor_actions[actor_id] = actions
        damage_moves[actor_id] = moves
        errors.extend(actor_errors)

    if not active:
        return {
            "responses": [{"label": "other/unknown", "probability": 1.0, "actions": []}],
            "damage_moves": {},
            "coverage_mass": 0.0,
            "residual_mass": 1.0,
            "errors": errors,
        }

    combinations: list[dict[str, Any]] = []
    for choices in product(*(actor_actions[actor_id] for actor_id in active)):
        switch_targets = [choice.get("switch_to") for choice in choices if choice["kind"] == "switch"]
        if len(switch_targets) != len(set(switch_targets)):
            continue
        probability = 1.0
        for choice in choices:
            probability *= float(choice["probability"])
        combinations.append(
            {
                "label": " + ".join(_action_label(state, choice) for choice in choices),
                "probability": probability,
                "actions": [
                    {key: value for key, value in choice.items() if key != "probability"}
                    for choice in choices
                ],
            }
        )
    combinations.sort(key=lambda row: (-float(row["probability"]), row["label"]))
    legal_mass = sum(float(row["probability"]) for row in combinations)
    if legal_mass > 0:
        for row in combinations:
            row["probability"] = float(row["probability"]) / legal_mass
    kept = combinations[:maximum_joint_responses]
    coverage_mass = sum(float(row["probability"]) for row in kept)
    residual_mass = max(0.0, 1.0 - coverage_mass)
    explicit_joint_responses = len(kept)
    if residual_mass > 1e-12:
        kept.append(
            {
                "label": "residual: unexpanded legal/hidden response",
                "probability": residual_mass,
                "actions": [
                    {
                        "actor": actor_id,
                        "kind": "other",
                        "category": "other",
                        "source": "truncation_residual",
                    }
                    for actor_id in active
                ],
            }
        )
    return {
        "responses": kept,
        "damage_moves": damage_moves,
        "meta": meta.status(),
        "coverage_mass": round(coverage_mass, 6),
        "residual_mass": round(residual_mass, 6),
        "joint_responses_evaluated": len(kept),
        "expanded_legal_joint_responses": len(combinations),
        "explicit_joint_responses": explicit_joint_responses,
        "maximum_joint_responses": maximum_joint_responses,
        "truncated_joint_responses": max(0, len(combinations) - explicit_joint_responses),
        "exhaustive_within_response_horizon": len(combinations) <= maximum_joint_responses,
        "candidate_actions": {
            actor_id: [
                {
                    **{key: value for key, value in action.items() if key != "probability"},
                    "probability": round(float(action["probability"]), 6),
                }
                for action in actions
            ]
            for actor_id, actions in actor_actions.items()
        },
        "errors": errors[:8],
        "probability_semantics": "Bayesian action-category belief allocated by revealed/meta-order heuristic; not raw usage frequency",
    }
