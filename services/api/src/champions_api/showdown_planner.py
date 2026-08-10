from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from champions_copilot.decision import DamageEstimate
from champions_copilot.models import BattleState, PokemonState, SideState

from .regulation import CurrentChampionsRegulation
from .showdown import ShowdownCalculator, ShowdownUnavailable


NON_DAMAGE_MOVES = {"Aurora Veil", "Protect", "Tailwind"}
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
    ability: str | None = None,
) -> dict[str, Any]:
    facts = _known(side, pokemon.id)
    item = facts.get("item", pokemon.item)
    if pokemon.can_mega_evolve or pokemon.mega_evolved:
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
    resolved_ability = facts.get("ability", ability or pokemon.ability)
    tera_type = facts.get("tera_type", pokemon.tera_type)
    if item:
        spec["item"] = item
    if resolved_ability:
        spec["ability"] = resolved_ability
    if tera_type:
        spec["teraType"] = tera_type
    if pokemon.mechanics_override:
        spec["overrides"] = dict(pokemon.mechanics_override)
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
    selected = (
        rows
        if len(rows) == 1
        else [(scenario, result) for scenario, result in rows if scenario.name != excluded]
    )
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
    selected = (
        rows
        if len(rows) == 1
        else [(scenario, result) for scenario, result in rows if scenario.name != excluded]
    )
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
    actor: str | None = None,
) -> DamageEstimate:
    category = str(rows[0][1].get("moveCategory", "Physical"))
    relevant = (
        _relevant_offense_scenarios(rows, category)
        if offense
        else _relevant_scenarios(rows, category)
    )
    key_actor, move, target_id = key
    actor_id = actor or key_actor
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
            "defender_max_hp": result["defenderMaxHP"],
            "rolls_percent": [
                {
                    "damage": roll["damage"],
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
    regulation: CurrentChampionsRegulation | None = None,
) -> tuple[
    dict[tuple[str, str, str], DamageEstimate],
    dict[tuple[str, str, str], DamageEstimate],
    dict[str, Any],
]:
    requests: list[dict[str, Any]] = []
    metadata: list[tuple[str, str, str, BulkScenario]] = []
    display_actors: dict[str, str] = {}
    mega_requests = 0
    for actor_id in state.player.active:
        actor = state.player.roster[actor_id]
        if actor.fainted:
            continue
        attacker_variants: list[tuple[str, dict[str, Any], dict[str, str]]] = [
            (actor_id, _pokemon_spec(state.player, actor), {})
        ]
        if regulation is not None and actor.can_mega_evolve and not actor.mega_evolved:
            resolved = regulation.mega_evolution(actor.name, item=actor.item)
            mega_spec = _pokemon_spec(state.player, actor)
            mega_spec["ability"] = resolved["ability"]
            mega_spec["overrides"] = resolved["mechanics_override"]
            mega_spec.pop("item", None)
            ability = str(resolved["ability"])
            environment: dict[str, str] = {}
            if ability in {
                "Drizzle": "Rain",
                "Drought": "Sun",
                "Sand Stream": "Sand",
                "Snow Warning": "Snow",
            }:
                environment["weather"] = {
                    "Drizzle": "Rain",
                    "Drought": "Sun",
                    "Sand Stream": "Sand",
                    "Snow Warning": "Snow",
                }[ability]
            if ability in {
                "Electric Surge": "Electric",
                "Grassy Surge": "Grassy",
                "Misty Surge": "Misty",
                "Psychic Surge": "Psychic",
            }:
                environment["terrain"] = {
                    "Electric Surge": "Electric",
                    "Grassy Surge": "Grassy",
                    "Misty Surge": "Misty",
                    "Psychic Surge": "Psychic",
                }[ability]
            key_actor = f"{actor_id}#mega"
            display_actors[key_actor] = actor_id
            attacker_variants.append((key_actor, mega_spec, environment))
        for key_actor, attacker_spec, environment in attacker_variants:
            for move in actor.moves:
                if move in NON_DAMAGE_MOVES:
                    continue
                for target_id in state.opponent.active:
                    target = state.opponent.roster[target_id]
                    if target.fainted:
                        continue
                    for scenario in _defender_scenarios(state.opponent, target):
                        field = _field(state, target)
                        field.update(environment)
                        requests.append(
                            {
                                "generation": 9,
                                "scenario": scenario.name,
                                "attacker": attacker_spec,
                                "defender": _pokemon_spec(
                                    state.opponent,
                                    target,
                                    evs=scenario.evs,
                                    nature=scenario.nature,
                                ),
                                "move": {"name": move},
                                "field": field,
                            }
                        )
                        metadata.append((key_actor, move, target_id, scenario))
                        if key_actor.endswith("#mega"):
                            mega_requests += 1

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
        estimates[key] = _estimate_from_rows(
            key,
            rows,
            offense=False,
            actor=display_actors.get(key[0]),
        )

    threats, threat_errors = calculate_revealed_threats(
        calculator,
        state,
        opponent_moves=opponent_moves,
        regulation=regulation,
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
        "mega_matchup_requests": mega_requests,
        "champions_mega_overrides": bool(mega_requests),
        "compatibility": (
            "Showdown Gen 9 mechanics with pinned current-Champions Mega stat/type/ability overrides; "
            "unverified Champions-only effects remain explicit boundaries"
        ),
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
    regulation: CurrentChampionsRegulation | None = None,
) -> tuple[dict[tuple[str, str, str], DamageEstimate], list[dict[str, str]]]:
    requests: list[dict[str, Any]] = []
    metadata: list[tuple[str, str, str, BulkScenario]] = []
    display_actors: dict[str, str] = {}
    living_players = [
        pokemon_id
        for pokemon_id in state.player.selected
        if not state.player.roster[pokemon_id].fainted
    ]
    for actor_id in state.opponent.active:
        actor = state.opponent.roster[actor_id]
        attacker_variants: list[tuple[str, dict[str, Any], dict[str, str]]] = [
            (actor_id, _pokemon_spec(state.opponent, actor), {})
        ]
        if (
            regulation is not None
            and not actor.mega_evolved
            and not any(member.mega_evolved for member in state.opponent.roster.values())
        ):
            for entry in regulation.mega_options(actor.name):
                resolved = regulation.mega_evolution(
                    actor.name, item=str(entry["mega_stone"])
                )
                spec = _pokemon_spec(state.opponent, actor)
                spec["ability"] = resolved["ability"]
                spec["overrides"] = resolved["mechanics_override"]
                spec.pop("item", None)
                ability = str(resolved["ability"])
                environment: dict[str, str] = {}
                weather = {
                    "Drizzle": "Rain",
                    "Drought": "Sun",
                    "Sand Stream": "Sand",
                    "Snow Warning": "Snow",
                }.get(ability)
                terrain = {
                    "Electric Surge": "Electric",
                    "Grassy Surge": "Grassy",
                    "Misty Surge": "Misty",
                    "Psychic Surge": "Psychic",
                }.get(ability)
                if weather:
                    environment["weather"] = weather
                if terrain:
                    environment["terrain"] = terrain
                key_actor = f"{actor_id}#mega:{resolved['battle_form']}"
                display_actors[key_actor] = actor_id
                attacker_variants.append((key_actor, spec, environment))
        moves = list(actor.revealed_moves)
        for candidate in (opponent_moves or {}).get(actor_id, []):
            if candidate not in moves:
                moves.append(candidate)
        for key_actor, base_spec, environment in attacker_variants:
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
                        attacker = dict(base_spec)
                        attacker["evs"] = scenario.evs
                        attacker["nature"] = scenario.nature or str(
                            base_spec.get("nature", "Serious")
                        )
                        field = _opponent_field(state, target)
                        field.update(environment)
                        requests.append(
                            {
                                "generation": 9,
                                "scenario": scenario.name,
                                "attacker": attacker,
                                "defender": _pokemon_spec(state.player, target),
                                "move": {"name": move},
                                "field": field,
                            }
                        )
                        metadata.append((key_actor, move, target_id, scenario))
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
        {
            key: _estimate_from_rows(
                key,
                rows,
                offense=True,
                actor=display_actors.get(key[0]),
            )
            for key, rows in grouped.items()
        },
        errors,
    )


def calculate_canonical_damage(
    calculator: ShowdownCalculator,
    state: BattleState,
    *,
    side: str,
    actor_id: str,
    move: str,
    target_id: str,
    target_side: str | None = None,
    attacker_profile: dict[str, Any] | None = None,
    defender_profile: dict[str, Any] | None = None,
    critical: bool = False,
    hits: int | None = None,
    move_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Calculate a current-position matchup after verifying Gen 9 learnability.

    This is intentionally read-only and separate from legal action generation.
    It lets the strategist test a hidden-but-learnable move without silently
    promoting that hypothesis to a revealed fact or legal recommendation.
    """

    if side not in {"player", "opponent"}:
        raise ValueError("side must be player or opponent")
    attacker_side = state.side(side)
    resolved_target_side = target_side or ("opponent" if side == "player" else "player")
    if resolved_target_side not in {"player", "opponent"}:
        raise ValueError("target_side must be player or opponent")
    defender_side = state.side(resolved_target_side)
    friendly_fire = resolved_target_side == side
    if actor_id not in attacker_side.active:
        raise ValueError("actor must be active in the canonical position")
    if actor_id not in attacker_side.roster or attacker_side.roster[actor_id].fainted:
        raise ValueError("actor is unavailable")
    if target_id not in defender_side.roster or defender_side.roster[target_id].fainted:
        raise ValueError("target is unavailable")

    actor = attacker_side.roster[actor_id]
    target = defender_side.roster[target_id]
    move_entry = calculator.lookup("move", move, generation=9)["entry"]
    learnset = calculator.learnset(actor.name, generation=9)
    learnable = {str(entry["id"]) for entry in learnset["moves"]}
    if str(move_entry["id"]) not in learnable:
        raise ValueError(f"{move_entry['name']} is not in {actor.name}'s Gen 9 learnset")
    if move_entry.get("category") == "Status":
        return {
            "damage_applicable": False,
            "actor": actor_id,
            "actor_species": actor.name,
            "move": move_entry,
            "target": target_id,
            "target_species": target.name,
            "learnset_verified": True,
            "source": "pinned @pkmn/data + @pkmn/dex",
        }

    offense = side == "opponent" and not friendly_fire
    if offense:
        facts = _known(attacker_side, actor_id)
        scenarios = (
            (BulkScenario("confirmed_set", 1.0, dict(facts["evs"])),)
            if "evs" in facts
            else OFFENSE_SCENARIOS
        )
    else:
        scenarios = (
            (
                BulkScenario(
                    "confirmed_friendly_set",
                    1.0,
                    dict(target.evs),
                    target.nature,
                ),
            )
            if friendly_fire and side == "player"
            else _defender_scenarios(defender_side, target)
        )
    selected_profile = attacker_profile if offense else defender_profile
    if selected_profile is not None:
        scenarios = (
            BulkScenario(
                str(selected_profile["name"]),
                1.0,
                dict(selected_profile.get("evs", {})),
                selected_profile.get("nature"),
            ),
        )

    requests: list[dict[str, Any]] = []
    rows: list[tuple[BulkScenario, dict[str, Any]]] = []
    for scenario in scenarios:
        if offense:
            attacker = _pokemon_spec(
                attacker_side, actor, evs=scenario.evs, nature=scenario.nature
            )
            defender = _pokemon_spec(defender_side, target)
            field = _opponent_field(state, target)
        else:
            attacker = _pokemon_spec(attacker_side, actor)
            defender = _pokemon_spec(
                defender_side, target, evs=scenario.evs, nature=scenario.nature
            )
            if friendly_fire:
                weather = (
                    WEATHER.get(str(state.field.weather).lower())
                    if state.field.weather
                    else None
                )
                terrain = (
                    TERRAIN.get(str(state.field.terrain).lower())
                    if state.field.terrain
                    else None
                )
                field = {
                    "gameType": "Doubles",
                    "attackerSide": {
                        "isHelpingHand": bool(
                            attacker_side.side_conditions.get("helping_hand", 0)
                        ),
                    },
                    "defenderSide": {
                        "isProtected": target.protected,
                    },
                }
                if weather:
                    field["weather"] = weather
                if terrain:
                    field["terrain"] = terrain
            else:
                field = _field(state, target)
        if attacker_profile is not None:
            attacker = _pokemon_spec(
                attacker_side,
                actor,
                evs=dict(attacker_profile.get("evs", {})),
                nature=attacker_profile.get("nature"),
                ability=attacker_profile.get("ability"),
            )
        if defender_profile is not None:
            defender = _pokemon_spec(
                defender_side,
                target,
                evs=dict(defender_profile.get("evs", {})),
                nature=defender_profile.get("nature"),
                ability=defender_profile.get("ability"),
            )
        move_spec: dict[str, Any] = {
            "name": move_entry["name"],
            "isCrit": critical,
        }
        if hits is not None:
            move_spec["hits"] = hits
        if move_overrides:
            move_spec["overrides"] = dict(move_overrides)
        requests.append(
            {
                "generation": 9,
                "scenario": scenario.name,
                "attacker": attacker,
                "defender": defender,
                "move": move_spec,
                "field": field,
            }
        )

    for scenario, response in zip(scenarios, calculator.batch(requests), strict=True):
        if not response.get("ok") or not isinstance(response.get("result"), dict):
            error = response.get("error") or {}
            raise ShowdownUnavailable(str(error.get("message", "calculation failed")))
        rows.append((scenario, response["result"]))
    estimate = _estimate_from_rows(
        (actor_id, str(move_entry["name"]), target_id), rows, offense=offense
    )
    return {
        "damage_applicable": True,
        "actor_species": actor.name,
        "target_species": target.name,
        "target_side": resolved_target_side,
        "friendly_fire": friendly_fire,
        "learnset_verified": True,
        "learnset_source": "pinned @pkmn/data + @pkmn/dex",
        "estimate": estimate.to_dict(),
    }


def calculate_canonical_speed(
    calculator: ShowdownCalculator,
    state: BattleState,
    *,
    side: str,
    actor_id: str,
    profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if side not in {"player", "opponent"}:
        raise ValueError("side must be player or opponent")
    side_state = state.side(side)
    if actor_id not in side_state.roster or side_state.roster[actor_id].fainted:
        raise ValueError("speed actor is unavailable")
    weather = WEATHER.get(str(state.field.weather).lower()) if state.field.weather else None
    terrain = TERRAIN.get(str(state.field.terrain).lower()) if state.field.terrain else None
    field: dict[str, Any] = {
        "gameType": "Doubles",
        "attackerSide": {
            "isTailwind": bool(side_state.side_conditions.get("tailwind", 0)),
        },
    }
    if weather:
        field["weather"] = weather
    if terrain:
        field["terrain"] = terrain
    return calculator.speed(
        {
            "generation": 9,
            "pokemon": _pokemon_spec(
                side_state,
                side_state.roster[actor_id],
                evs=dict(profile.get("evs", {})) if profile else None,
                nature=profile.get("nature") if profile else None,
                ability=profile.get("ability") if profile else None,
            ),
            "field": field,
            "trickRoom": state.field.trick_room_turns > 0,
        }
    )
