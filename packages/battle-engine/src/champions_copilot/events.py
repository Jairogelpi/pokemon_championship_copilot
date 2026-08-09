from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from .models import BattleState, STAT_NAMES, VALID_STATUSES


class EventValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class BattleEvent:
    id: str
    type: str
    payload: dict[str, Any]
    created_at: str

    @classmethod
    def create(cls, type: str, payload: dict[str, Any]) -> BattleEvent:
        return cls(
            id=uuid4().hex,
            type=type,
            payload=dict(payload),
            created_at=datetime.now(UTC).isoformat(),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> BattleEvent:
        return cls(
            id=value["id"],
            type=value["type"],
            payload=dict(value.get("payload", {})),
            created_at=value["created_at"],
        )


def required(payload: dict[str, Any], key: str) -> Any:
    if key not in payload:
        raise EventValidationError(f"event payload requires '{key}'")
    return payload[key]


def apply_event(state: BattleState, event: BattleEvent) -> BattleState:
    next_state = BattleState.from_dict(state.to_dict())
    payload = event.payload

    if event.type == "turn_started":
        requested = int(payload.get("turn", next_state.turn + 1))
        if requested <= next_state.turn:
            raise EventValidationError("a new turn must advance the turn number")
        next_state.turn = requested
        for side in (next_state.player, next_state.opponent):
            for pokemon in side.roster.values():
                pokemon.protected = False
        for key in list(next_state.player.side_conditions):
            next_state.player.side_conditions[key] = max(
                0, next_state.player.side_conditions[key] - 1
            )
        for key in list(next_state.opponent.side_conditions):
            next_state.opponent.side_conditions[key] = max(
                0, next_state.opponent.side_conditions[key] - 1
            )
        next_state.field.weather_turns = max(0, next_state.field.weather_turns - 1)
        next_state.field.terrain_turns = max(0, next_state.field.terrain_turns - 1)
        next_state.field.trick_room_turns = max(0, next_state.field.trick_room_turns - 1)

    elif event.type == "hp_changed":
        side = next_state.side(required(payload, "side"))
        pokemon = side.roster.get(required(payload, "pokemon"))
        if pokemon is None:
            raise EventValidationError("unknown Pokémon")
        hp = int(payload.get("hp", pokemon.hp + int(payload.get("delta", 0))))
        pokemon.hp = min(100, max(0, hp))
        pokemon.fainted = pokemon.hp == 0

    elif event.type == "status_set":
        side = next_state.side(required(payload, "side"))
        pokemon = side.roster.get(required(payload, "pokemon"))
        status = payload.get("status")
        if pokemon is None:
            raise EventValidationError("unknown Pokémon")
        if status not in VALID_STATUSES:
            raise EventValidationError(f"invalid status: {status}")
        pokemon.status = status

    elif event.type == "boost_changed":
        side = next_state.side(required(payload, "side"))
        pokemon = side.roster.get(required(payload, "pokemon"))
        stat = required(payload, "stat")
        if pokemon is None:
            raise EventValidationError("unknown Pokémon")
        if stat not in STAT_NAMES:
            raise EventValidationError(f"invalid stat: {stat}")
        current = pokemon.boosts[stat]
        pokemon.boosts[stat] = max(-6, min(6, current + int(required(payload, "delta"))))

    elif event.type == "switch":
        side = next_state.side(required(payload, "side"))
        outgoing = required(payload, "out")
        incoming = required(payload, "in")
        if outgoing not in side.active:
            raise EventValidationError("outgoing Pokémon is not active")
        if incoming not in side.bench:
            raise EventValidationError("incoming Pokémon is not available on the bench")
        if side.roster[incoming].fainted:
            raise EventValidationError("a fainted Pokémon cannot switch in")
        position = side.active.index(outgoing)
        side.active[position] = incoming
        side.bench.remove(incoming)
        if not side.roster[outgoing].fainted:
            side.bench.append(outgoing)

    elif event.type == "faint":
        side = next_state.side(required(payload, "side"))
        pokemon = side.roster.get(required(payload, "pokemon"))
        if pokemon is None:
            raise EventValidationError("unknown Pokémon")
        pokemon.hp = 0
        pokemon.fainted = True
        pokemon.protected = False
        if pokemon.id in side.bench:
            side.bench.remove(pokemon.id)

    elif event.type == "move_used":
        side_name = required(payload, "side")
        side = next_state.side(side_name)
        pokemon = side.roster.get(required(payload, "pokemon"))
        move = str(required(payload, "move")).strip()
        if pokemon is None:
            raise EventValidationError("unknown Pokémon")
        if pokemon.fainted:
            raise EventValidationError("a fainted Pokémon cannot use a move")
        if move and move not in pokemon.revealed_moves:
            pokemon.revealed_moves.append(move)
        if move.lower() == "protect":
            pokemon.protected = True

    elif event.type == "mega_evolved":
        side = next_state.side(required(payload, "side"))
        pokemon = side.roster.get(required(payload, "pokemon"))
        if pokemon is None:
            raise EventValidationError("unknown Pokémon")
        if any(member.mega_evolved for member in side.roster.values() if member.id != pokemon.id):
            raise EventValidationError("this side has already Mega Evolved another Pokémon")
        pokemon.mega_evolved = True

    elif event.type == "field_set":
        field_name = required(payload, "field")
        turns = max(0, int(payload.get("turns", 0)))
        value = payload.get("value")
        side_name = payload.get("side")
        if field_name in {"tailwind", "aurora_veil", "reflect", "light_screen"}:
            if side_name not in {"player", "opponent"}:
                raise EventValidationError("side condition requires a side")
            next_state.side(side_name).side_conditions[field_name] = turns
        elif field_name == "weather":
            next_state.field.weather = value
            next_state.field.weather_turns = turns
        elif field_name == "terrain":
            next_state.field.terrain = value
            next_state.field.terrain_turns = turns
        elif field_name == "trick_room":
            next_state.field.trick_room_turns = turns
        else:
            raise EventValidationError(f"unsupported field condition: {field_name}")

    elif event.type == "fact_revealed":
        side = next_state.side(required(payload, "side"))
        pokemon_id = required(payload, "pokemon")
        if pokemon_id not in side.roster:
            raise EventValidationError("unknown Pokémon")
        key = str(required(payload, "key"))
        if key not in {"item", "ability", "nature", "level", "evs", "ivs", "tera_type"}:
            raise EventValidationError(f"unsupported set fact: {key}")
        value = payload.get("value")
        if key == "level":
            value = int(value)
        elif key in {"evs", "ivs"}:
            if not isinstance(value, dict):
                raise EventValidationError(f"{key} must be an object of stat values")
            value = {str(stat): int(amount) for stat, amount in value.items()}
        pokemon = side.roster[pokemon_id]
        setattr(pokemon, key, value)
        pokemon.__post_init__()
        side.known_facts.setdefault(pokemon_id, {})[key] = value

    elif event.type == "match_finished":
        winner = required(payload, "winner")
        if winner not in {"player", "opponent", "draw"}:
            raise EventValidationError("invalid match winner")
        next_state.phase = "finished"
        next_state.winner = winner

    elif event.type != "note":
        raise EventValidationError(f"unsupported event type: {event.type}")

    next_state.revision += 1
    next_state.validate()
    return next_state


def replay(initial: BattleState, events: list[BattleEvent]) -> BattleState:
    state = BattleState.from_dict(initial.to_dict())
    for event in events:
        state = apply_event(state, event)
    return state
