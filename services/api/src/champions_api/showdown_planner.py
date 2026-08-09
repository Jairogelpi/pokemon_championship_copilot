from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from champions_copilot.decision import DamageEstimate
from champions_copilot.models import BattleState, PokemonState, SideState

from .showdown import ShowdownCalculator, ShowdownUnavailable


NON_DAMAGE_MOVES = {"Aurora Veil", "Protect", "Tailwind"}
CHAMPIONS_ONLY_ITEMS = {"Dragoninite", "Froslassite"}
WEATHER = {
    "sun": "Sun",
    "sunny": "Sun",
    "rain": "Rain",
    "sand": "Sand",
    "sandstorm": "Sand",
    "hail": "Hail",
    "snow": "Snow",
}
TERRAIN = {
    "electric": "Electric",
    "grassy": "Grassy",
    "misty": "Misty",
    "psychic": "Psychic",
}


@dataclass(frozen=True, slots=True)
class BulkScenario:
    name: str
    weight: float
    evs: dict[str, int]
    nature: str | None = None


BULK_SCENARIOS = (
    BulkScenario("no_bulk", 0.30, {}),
    BulkScenario("hp_invested", 0.45, {"hp": 252}),
    BulkScenario("max_defense", 0.25, {"hp": 252, "def": 252}, "Bold"),
    BulkScenario("max_special_defense", 0.25, {"hp": 252, "spd": 252}, "Calm"),
)
OFFENSE_SCENARIOS = (
    BulkScenario("fast_neutral", 0.35, {"spe": 252}),
    BulkScenario("max_attack", 0.65, {"atk": 252, "spe": 252}, "Adamant"),
    BulkScenario("max_special_attack", 0.65, {"spa": 252, "spe": 252}, "Modest"),
)


def _known(side: SideState, pokemon_id: str) -> dict[str, Any]:
    return side.known_facts.get(pokemon_id, {})


def _pokemon_spec(
    side: SideState,
    pokemon: PokemonState,
    *,
    evs: dict[str, int] | None = None,
    nature: str | None = None,
) -> dict[str, Any]:
    facts = _known(side, pokemon.id)
    item = facts.get("item", pokemon.item)
    if item in CHAMPIONS_ONLY_ITEMS:
        item = None
    spec: dict[str, Any] = {
        "name": pokemon.name,
        "level": int(facts.get("level", pokemon.level)),
        "nature": facts.get("nature", nature or pokemon.nature) or "Serious",
        "ivs": facts.get("ivs", pokemon.ivs),
        "evs": facts.get("evs", evs if evs is not None else pokemon.evs),
        "boosts": {
            key: value
            for key, value in pokemon.boosts.items()
            if key in {"atk", "def", "spa", "spd", "spe"}
        },
        "hpPercent": pokemon.hp,
        "status": pokemon.status,
        "alliesFainted": sum(member.fainted for member in side.roster.values()),
    }
    ability = facts.get("ability", pokemon.ability)
    tera_type = facts.get("tera_type", pokemon.tera_type)
    if item:
        spec["item"] = item
    if ability:
        spec["ability"] = ability
    if tera_type:
        spec["teraType"] = tera_type
    return spec


def _field(state: BattleState, target: PokemonState) -> dict[str, Any]:
    weather = WEATHER.get(str(state.field.weather).lower()) if state.field.weather else None
    terrain = TERRAIN.get(str(state.field.terrain).lower()) if state.field.terrain else None
    field: dict[str, Any] = {
        "gameType": "Doubles",
        "attackerSide": {
            "isHelpingHand": bool(state.player.side_conditions.get("helping_hand", 0)),
        },
        "defenderSide": {
            "isReflect": bool(state.opponent.side_conditions.get("reflect", 0)),
            "isLightScreen": bool(state.opponent.side_conditions.get("light_screen", 0)),
            "isAuroraVeil": bool(state.opponent.side_conditions.get("aurora_veil", 0)),
            "isProtected": target.protected,
        },
    }
    if weather:
        field["weather"] = weather
    if terrain:
        field["terrain"] = terrain
    return field


def _opponent_field(state: BattleState, target: PokemonState) -> dict[str, Any]:
    field = _field(state, target)
    field["attackerSide"] = {
        "isHelpingHand": bool(state.opponent.side_conditions.get("helping_hand", 0)),
    }
    field["defenderSide"] = {
        "isReflect": bool(state.player.side_conditions.get("reflect", 0)),
        "isLightScreen": bool(state.player.side_conditions.get("light_screen", 0)),
        "isAuroraVeil": bool(state.player.side_conditions.get("aurora_veil", 0)),
        "isProtected": target.protected,
    }
    return field


def _defender_scenarios(side: SideState, pokemon: PokemonState) -> tuple[BulkScenario, ...]:
    facts = _known(side, pokemon.id)
    if "evs" in facts:
        return (BulkScenario("confirmed_set", 1.0, dict(facts["evs"])),)
    return BULK_SCENARIOS


def _relevant_scenarios(
    rows: list[tuple[BulkScenario, dict[str, Any]]], category: str
) -> list[tuple[BulkScenario, dict[str, Any]]]:
    excluded = "max_special_defense" if category == "Physical" else "max_defense"
    selected = [(scenario, result) for scenario, result in rows if scenario.name != excluded]
    total = sum(scenario.weight for scenario, _ in selected)
    return [
        (
            BulkScenario(
                scenario.name, scenario.weight / total, scenario.evs, scenario.nature
            ),
            result,
        )
        for scenario, result in selected
    ]


def _relevant_offense_scenarios(
    rows: list[tuple[BulkScenario, dict[str, Any]]], category: str
) -> list[tuple[BulkScenario, dict[str, Any]]]:
    excluded = "max_special_attack" if category == "Physical" else "max_attack"
    selected = [(scenario, result) for scenario, result in rows if scenario.name != excluded]
    total = sum(scenario.weight for scenario, _ in selected)
    return [
        (
            BulkScenario(
                scenario.name, scenario.weight / total, scenario.evs, scenario.nature
            ),
            result,
        )
        for scenario, result in selected
    ]


def _estimate_from_rows(
    key: tuple[str, str, str],
    rows: list[tuple[BulkScenario, dict[str, Any]]],
    *,
    offense: bool,
) -> DamageEstimate:
    category = str(rows[0][1].get("moveCategory", "Physical"))
    relevant = (
        _relevant_offense_scenarios(rows, category)
        if offense
        else _relevant_scenarios(rows, category)
    )
    actor_id, move, target_id = key
    scenario_payloads = tuple(
        {
            "name": scenario.name,
            "weight": round(scenario.weight, 4),
            "nature_assumption": scenario.nature or "confirmed_or_neutral",
            "minimum_percent": result["minimumPercent"],
            "maximum_percent": result["maximumPercent"],
            "expected_percent": result["expectedPercent"],
            "ko_probability": result["koProbabilityWithBaseAccuracy"],
            "ko_probability_on_hit": result["koProbabilityOnHit"],
            "base_accuracy_probability": result["baseAccuracyProbability"],
            "rolls_percent": [
                {
                    "percent": round(
                        roll["damage"] * 100 / result["defenderMaxHP"], 6
                    ),
                    "weight": roll["weight"],
                }
                for roll in result["rolls"]
            ],
            "description": result["description"],
        }
        for scenario, result in relevant
    )
    ko_values = [float(result["koProbabilityWithBaseAccuracy"]) for _, result in relevant]
    first = relevant[0][1]
    return DamageEstimate(
        actor=actor_id,
        move=move,
        target=target_id,
        source=str(first["source"]),
        source_version=str(first["sourceVersion"]),
        generation=int(first["generation"]),
        move_category=str(first["moveCategory"]),
        move_type=str(first["moveType"]),
        move_priority=int(first.get("movePriority", 0)),
        move_target=str(first.get("moveTarget", "normal")),
        attacker_speed=int(first.get("attackerSpeed", 0)),
        defender_speed=int(first.get("defenderSpeed", 0)),
        spread_move=bool(first["spreadMove"]),
        minimum_percent=round(
            min(float(result["minimumPercent"]) for _, result in relevant), 3
        ),
        maximum_percent=round(
            max(float(result["maximumPercent"]) for _, result in relevant), 3
        ),
        expected_percent=round(
            sum(
                scenario.weight * float(result["expectedPercent"])
                for scenario, result in relevant
            ),
            3,
        ),
        knockout_probability_min=round(min(ko_values), 4),
        knockout_probability_max=round(max(ko_values), 4),
        knockout_probability_weighted=round(
            sum(
                scenario.weight * float(result["koProbabilityWithBaseAccuracy"])
                for scenario, result in relevant
            ),
            4,
        ),
        base_accuracy_probability=float(first["baseAccuracyProbability"]),
        scenario_count=len(relevant),
        scenarios=scenario_payloads,
        assumptions=(
            "Showdown Gen 9 doubles compatibility profile",
            "Base move accuracy; untracked accuracy/evasion modifiers are excluded",
        ),
    )


def calculate_turn_damage(
    calculator: ShowdownCalculator,
    state: BattleState,
    opponent_moves: dict[str, list[str]] | None = None,
) -> tuple[
    dict[tuple[str, str, str], DamageEstimate],
    dict[tuple[str, str, str], DamageEstimate],
    dict[str, Any],
]:
    requests: list[dict[str, Any]] = []
    metadata: list[tuple[str, str, str, BulkScenario]] = []
    for actor_id in state.player.active:
        actor = state.player.roster[actor_id]
        if actor.fainted:
            continue
        for move in actor.moves:
            if move in NON_DAMAGE_MOVES:
                continue
            for target_id in state.opponent.active:
                target = state.opponent.roster[target_id]
                if target.fainted:
                    continue
                for scenario in _defender_scenarios(state.opponent, target):
                    requests.append(
                        {
                            "generation": 9,
                            "scenario": scenario.name,
                            "attacker": _pokemon_spec(state.player, actor),
                            "defender": _pokemon_spec(
                                state.opponent,
                                target,
                                evs=scenario.evs,
                                nature=scenario.nature,
                            ),
                            "move": {"name": move},
                            "field": _field(state, target),
                        }
                    )
                    metadata.append((actor_id, move, target_id, scenario))

    raw_results = calculator.batch(requests)
    grouped: dict[
        tuple[str, str, str], list[tuple[BulkScenario, dict[str, Any]]]
    ] = defaultdict(list)
    errors: list[dict[str, str]] = []
    for meta, response in zip(metadata, raw_results, strict=True):
        actor_id, move, target_id, scenario = meta
        if not response.get("ok"):
            error = response.get("error") or {}
            errors.append(
                {
                    "actor": actor_id,
                    "move": move,
                    "target": target_id,
                    "scenario": scenario.name,
                    "message": str(error.get("message", "calculation failed")),
                }
            )
            continue
        result = response.get("result")
        if isinstance(result, dict):
            grouped[(actor_id, move, target_id)].append((scenario, result))

    estimates: dict[tuple[str, str, str], DamageEstimate] = {}
    for key, rows in grouped.items():
        estimates[key] = _estimate_from_rows(key, rows, offense=False)

    threats, threat_errors = calculate_revealed_threats(
        calculator, state, opponent_moves=opponent_moves
    )
    errors.extend(threat_errors)

    requested_groups = len({(actor, move, target) for actor, move, target, _ in metadata})
    status = {
        "available": bool(estimates),
        "status": "ok" if len(estimates) == requested_groups else "degraded",
        "engine": "@smogon/calc",
        "version": next(iter(estimates.values())).source_version if estimates else None,
        "generation": 9,
        "game_type": "Doubles",
        "requested_matchups": requested_groups,
        "calculated_matchups": len(estimates),
        "coverage": round(len(estimates) / requested_groups, 4) if requested_groups else 1.0,
        "errors": errors[:8],
        "revealed_threat_matchups": len(threats),
        "modelled_opponent_moves": sum(
            len(moves) for moves in (opponent_moves or {}).values()
        ),
        "compatibility": "Showdown Gen 9 proxy; Champions-only mechanics are flagged as assumptions",
    }
    if requests and not estimates:
        raise ShowdownUnavailable(
            errors[0]["message"] if errors else "no matchup could be calculated"
        )
    return estimates, threats, status


def calculate_revealed_threats(
    calculator: ShowdownCalculator,
    state: BattleState,
    *,
    opponent_moves: dict[str, list[str]] | None = None,
) -> tuple[dict[tuple[str, str, str], DamageEstimate], list[dict[str, str]]]:
    requests: list[dict[str, Any]] = []
    metadata: list[tuple[str, str, str, BulkScenario]] = []
    living_players = [
        pokemon_id
        for pokemon_id in state.player.selected
        if not state.player.roster[pokemon_id].fainted
    ]
    for actor_id in state.opponent.active:
        actor = state.opponent.roster[actor_id]
        moves = list(actor.revealed_moves)
        for candidate in (opponent_moves or {}).get(actor_id, []):
            if candidate not in moves:
                moves.append(candidate)
        for move in moves:
            if move in NON_DAMAGE_MOVES:
                continue
            facts = _known(state.opponent, actor_id)
            scenarios = (
                (BulkScenario("confirmed_set", 1.0, dict(facts["evs"])),)
                if "evs" in facts
                else OFFENSE_SCENARIOS
            )
            for target_id in living_players:
                target = state.player.roster[target_id]
                for scenario in scenarios:
                    requests.append(
                        {
                            "generation": 9,
                            "scenario": scenario.name,
                            "attacker": _pokemon_spec(
                                state.opponent,
                                actor,
                                evs=scenario.evs,
                                nature=scenario.nature,
                            ),
                            "defender": _pokemon_spec(state.player, target),
                            "move": {"name": move},
                            "field": _opponent_field(state, target),
                        }
                    )
                    metadata.append((actor_id, move, target_id, scenario))
    if not requests:
        return {}, []
    responses = calculator.batch(requests)
    grouped: dict[
        tuple[str, str, str], list[tuple[BulkScenario, dict[str, Any]]]
    ] = defaultdict(list)
    errors: list[dict[str, str]] = []
    for meta, response in zip(metadata, responses, strict=True):
        actor_id, move, target_id, scenario = meta
        if response.get("ok") and isinstance(response.get("result"), dict):
            grouped[(actor_id, move, target_id)].append((scenario, response["result"]))
        else:
            error = response.get("error") or {}
            errors.append(
                {
                    "actor": actor_id,
                    "move": move,
                    "target": target_id,
                    "scenario": scenario.name,
                    "message": str(error.get("message", "calculation failed")),
                }
            )
    return (
        {key: _estimate_from_rows(key, rows, offense=True) for key, rows in grouped.items()},
        errors,
    )
