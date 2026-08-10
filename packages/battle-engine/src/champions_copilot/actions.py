from __future__ import annotations

from dataclasses import asdict, dataclass
from itertools import product
from typing import Any

from .models import BattleState, PokemonState


SELF_MOVES = {"Protect"}
FIELD_MOVES = {"Aurora Veil", "Tailwind"}
SPREAD_MOVES = {"Blizzard", "Earthquake", "Rock Slide", "Heat Wave"}


@dataclass(frozen=True, slots=True)
class SingleAction:
    actor: str
    kind: str
    move: str | None = None
    target: str | None = None
    switch_to: str | None = None
    mega: bool = False

    def label(self, state: BattleState) -> str:
        actor_name = state.player.roster[self.actor].name
        if self.kind == "switch":
            return f"{actor_name} → switch {state.player.roster[self.switch_to or ''].name}"
        prefix = "Mega Evolve + " if self.mega else ""
        target = ""
        if self.target == "opponents":
            target = " → both opponents"
        elif self.target in state.opponent.roster:
            target = f" → {state.opponent.roster[self.target].name}"
        return f"{actor_name} → {prefix}{self.move}{target}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class JointAction:
    actions: tuple[SingleAction, ...]

    def label(self, state: BattleState) -> str:
        return " + ".join(action.label(state) for action in self.actions) or "hold position"

    def to_dict(self) -> dict[str, Any]:
        return {"actions": [action.to_dict() for action in self.actions]}


def actions_for_pokemon(state: BattleState, pokemon: PokemonState) -> list[SingleAction]:
    if pokemon.fainted:
        return []
    actions: list[SingleAction] = []
    mega_available = pokemon.can_mega_evolve and not pokemon.mega_evolved and not any(
        member.mega_evolved for member in state.player.roster.values()
    )

    def add_move(move: str, target: str) -> None:
        actions.append(
            SingleAction(actor=pokemon.id, kind="move", move=move, target=target)
        )
        if mega_available:
            actions.append(
                SingleAction(
                    actor=pokemon.id,
                    kind="move",
                    move=move,
                    target=target,
                    mega=True,
                )
            )

    living_opponents = [
        id for id in state.opponent.active if not state.opponent.roster[id].fainted
    ]
    for move in pokemon.moves:
        if move in SELF_MOVES:
            add_move(move, pokemon.id)
        elif move in FIELD_MOVES:
            add_move(move, "field")
        elif move in SPREAD_MOVES:
            add_move(move, "opponents")
        else:
            for target in living_opponents:
                add_move(move, target)
    actions.extend(
        SingleAction(actor=pokemon.id, kind="switch", switch_to=bench_id)
        for bench_id in state.player.bench
        if not state.player.roster[bench_id].fainted
    )
    return actions


def generate_legal_joint_actions(state: BattleState) -> list[JointAction]:
    pools = [
        actions_for_pokemon(state, state.player.roster[pokemon_id])
        for pokemon_id in state.player.active
        if not state.player.roster[pokemon_id].fainted
    ]
    if not pools or any(not pool for pool in pools):
        return []
    result: list[JointAction] = []
    for choices in product(*pools):
        if sum(single.mega for single in choices) > 1:
            continue
        switch_targets = [
            action.switch_to for action in choices if action.kind == "switch"
        ]
        if len(switch_targets) != len(set(switch_targets)):
            continue
        result.append(JointAction(actions=tuple(choices)))
    return result
