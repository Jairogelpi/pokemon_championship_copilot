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

    def label(self, state: BattleState) -> str:
        actor_name = state.player.roster[self.actor].name
        if self.kind == "switch":
            return f"{actor_name} → switch {state.player.roster[self.switch_to or ''].name}"
        target = ""
        if self.target == "opponents":
            target = " → both opponents"
        elif self.target in state.opponent.roster:
            target = f" → {state.opponent.roster[self.target].name}"
        return f"{actor_name} → {self.move}{target}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class JointAction:
    actions: tuple[SingleAction, SingleAction]

    def label(self, state: BattleState) -> str:
        return " + ".join(action.label(state) for action in self.actions)

    def to_dict(self) -> dict[str, Any]:
        return {"actions": [action.to_dict() for action in self.actions]}


def actions_for_pokemon(state: BattleState, pokemon: PokemonState) -> list[SingleAction]:
    if pokemon.fainted:
        return []
    actions: list[SingleAction] = []
    living_opponents = [
        id for id in state.opponent.active if not state.opponent.roster[id].fainted
    ]
    for move in pokemon.moves:
        if move in SELF_MOVES:
            actions.append(SingleAction(actor=pokemon.id, kind="move", move=move, target=pokemon.id))
        elif move in FIELD_MOVES:
            actions.append(SingleAction(actor=pokemon.id, kind="move", move=move, target="field"))
        elif move in SPREAD_MOVES:
            actions.append(SingleAction(actor=pokemon.id, kind="move", move=move, target="opponents"))
        else:
            actions.extend(
                SingleAction(actor=pokemon.id, kind="move", move=move, target=target)
                for target in living_opponents
            )
    actions.extend(
        SingleAction(actor=pokemon.id, kind="switch", switch_to=bench_id)
        for bench_id in state.player.bench
        if not state.player.roster[bench_id].fainted
    )
    return actions


def generate_legal_joint_actions(state: BattleState) -> list[JointAction]:
    if len(state.player.active) != 2:
        return []
    left = actions_for_pokemon(state, state.player.roster[state.player.active[0]])
    right = actions_for_pokemon(state, state.player.roster[state.player.active[1]])
    result: list[JointAction] = []
    for first, second in product(left, right):
        if first.kind == second.kind == "switch" and first.switch_to == second.switch_to:
            continue
        result.append(JointAction(actions=(first, second)))
    return result
