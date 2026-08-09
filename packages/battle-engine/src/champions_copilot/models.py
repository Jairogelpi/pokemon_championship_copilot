from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


STAT_NAMES = ("atk", "def", "spa", "spd", "spe", "accuracy", "evasion")
VALID_STATUSES = {None, "burn", "poison", "toxic", "paralysis", "sleep", "freeze"}
CORE_STATS = ("hp", "atk", "def", "spa", "spd", "spe")


def default_boosts() -> dict[str, int]:
    return {name: 0 for name in STAT_NAMES}


def default_ivs() -> dict[str, int]:
    return {name: 31 for name in CORE_STATS}


@dataclass(slots=True)
class PokemonState:
    id: str
    name: str
    moves: tuple[str, ...] = ()
    item: str | None = None
    ability: str | None = None
    nature: str | None = None
    level: int = 50
    evs: dict[str, int] = field(default_factory=dict)
    ivs: dict[str, int] = field(default_factory=default_ivs)
    tera_type: str | None = None
    role: str = "unknown"
    set_verified: bool = False
    hp: int = 100
    status: str | None = None
    boosts: dict[str, int] = field(default_factory=default_boosts)
    fainted: bool = False
    protected: bool = False
    mega_evolved: bool = False
    revealed_moves: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not 0 <= self.hp <= 100:
            raise ValueError("hp must be between 0 and 100")
        if self.status not in VALID_STATUSES:
            raise ValueError(f"invalid status: {self.status}")
        for stat, stage in self.boosts.items():
            if stat not in STAT_NAMES or not -6 <= stage <= 6:
                raise ValueError(f"invalid stat stage: {stat}={stage}")
        if not 1 <= self.level <= 100:
            raise ValueError("level must be between 1 and 100")
        for table_name, values, maximum in (("evs", self.evs, 252), ("ivs", self.ivs, 31)):
            for stat, value in values.items():
                if stat not in CORE_STATS or not 0 <= int(value) <= maximum:
                    raise ValueError(f"invalid {table_name} value: {stat}={value}")
        if sum(int(value) for value in self.evs.values()) > 510:
            raise ValueError("EV total cannot exceed 510")

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["moves"] = list(self.moves)
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> PokemonState:
        value = dict(value)
        value["moves"] = tuple(value.get("moves", ()))
        return cls(**value)


@dataclass(slots=True)
class SideState:
    roster: dict[str, PokemonState]
    active: list[str]
    bench: list[str]
    selected: list[str]
    side_conditions: dict[str, int] = field(default_factory=dict)
    known_facts: dict[str, dict[str, Any]] = field(default_factory=dict)

    def validate(self) -> None:
        ids = set(self.roster)
        if len(self.active) > 2:
            raise ValueError("doubles permits at most two active Pokémon")
        if not set(self.active + self.bench + self.selected).issubset(ids):
            raise ValueError("side references a Pokémon outside its roster")
        if set(self.active) & set(self.bench):
            raise ValueError("a Pokémon cannot be both active and on the bench")
        if len(set(self.active)) != len(self.active) or len(set(self.bench)) != len(self.bench):
            raise ValueError("duplicate side positions are not allowed")

    def to_dict(self) -> dict[str, Any]:
        return {
            "roster": {key: pokemon.to_dict() for key, pokemon in self.roster.items()},
            "active": list(self.active),
            "bench": list(self.bench),
            "selected": list(self.selected),
            "side_conditions": dict(self.side_conditions),
            "known_facts": self.known_facts,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> SideState:
        side = cls(
            roster={key: PokemonState.from_dict(pokemon) for key, pokemon in value["roster"].items()},
            active=list(value.get("active", [])),
            bench=list(value.get("bench", [])),
            selected=list(value.get("selected", [])),
            side_conditions=dict(value.get("side_conditions", {})),
            known_facts=dict(value.get("known_facts", {})),
        )
        side.validate()
        return side


@dataclass(slots=True)
class FieldState:
    weather: str | None = None
    weather_turns: int = 0
    terrain: str | None = None
    terrain_turns: int = 0
    trick_room_turns: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> FieldState:
        return cls(**value)


@dataclass(slots=True)
class BattleState:
    match_id: str
    turn: int
    phase: str
    player: SideState
    opponent: SideState
    field: FieldState = field(default_factory=FieldState)
    winner: str | None = None
    revision: int = 0

    def validate(self) -> None:
        if self.turn < 0:
            raise ValueError("turn cannot be negative")
        if self.phase not in {"preview", "battle", "finished"}:
            raise ValueError(f"invalid phase: {self.phase}")
        if self.winner not in {None, "player", "opponent", "draw"}:
            raise ValueError(f"invalid winner: {self.winner}")
        self.player.validate()
        self.opponent.validate()

    def side(self, name: str) -> SideState:
        if name == "player":
            return self.player
        if name == "opponent":
            return self.opponent
        raise ValueError(f"invalid side: {name}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "match_id": self.match_id,
            "turn": self.turn,
            "phase": self.phase,
            "player": self.player.to_dict(),
            "opponent": self.opponent.to_dict(),
            "field": self.field.to_dict(),
            "winner": self.winner,
            "revision": self.revision,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> BattleState:
        state = cls(
            match_id=value["match_id"],
            turn=int(value["turn"]),
            phase=value["phase"],
            player=SideState.from_dict(value["player"]),
            opponent=SideState.from_dict(value["opponent"]),
            field=FieldState.from_dict(value.get("field", {})),
            winner=value.get("winner"),
            revision=int(value.get("revision", 0)),
        )
        state.validate()
        return state
