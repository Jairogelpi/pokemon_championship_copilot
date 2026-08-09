from __future__ import annotations

import re
from uuid import uuid4

from .models import BattleState, PokemonState, SideState


def slug(name: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return value or f"pokemon-{uuid4().hex[:8]}"


def pokemon(
    id: str,
    name: str,
    moves: tuple[str, ...],
    item: str | None,
    ability: str | None,
    nature: str | None,
    role: str,
    verified: bool = True,
) -> PokemonState:
    return PokemonState(
        id=id,
        name=name,
        moves=moves,
        item=item,
        ability=ability,
        nature=nature,
        role=role,
        set_verified=verified,
    )


PLAYER_TEAM: tuple[PokemonState, ...] = (
    pokemon(
        "froslass",
        "Froslass",
        ("Blizzard", "Shadow Ball", "Aurora Veil", "Protect"),
        "Froslassite",
        "Cursed Body",
        "Modest",
        "speed-control",
    ),
    pokemon(
        "sneasler",
        "Sneasler",
        ("Fake Out", "Close Combat", "Dire Claw", "Feint"),
        "Focus Sash",
        "Unburden",
        "Jolly",
        "disruption",
    ),
    pokemon(
        "basculegion",
        "Basculegion",
        ("Wave Crash", "Aqua Jet", "Last Respects", "Protect"),
        "Mystic Water",
        "Adaptability",
        "Adamant",
        "late-game-cleaner",
    ),
    pokemon(
        "dragonite",
        "Dragonite",
        ("Dragon Pulse", "Heat Wave", "Tailwind", "Protect"),
        "Dragoninite",
        "Multiscale",
        "Modest",
        "speed-control",
    ),
    pokemon(
        "garchomp",
        "Garchomp",
        ("Earthquake", "Rock Slide", "Dragon Claw", "Protect"),
        "Roseli Berry",
        "Rough Skin",
        "Jolly",
        "physical-pressure",
        verified=False,
    ),
    pokemon(
        "kingambit",
        "Kingambit",
        ("Kowtow Cleave", "Sucker Punch", "Iron Head", "Protect"),
        None,
        None,
        None,
        "endgame-anchor",
        verified=False,
    ),
)


ROLE_HINTS = {
    "incineroar": "pivot",
    "farigiraf": "trick-room",
    "aerodactyl": "speed-control",
    "toxapex": "control",
    "charizard": "special-pressure",
    "garchomp": "physical-pressure",
    "kingambit": "endgame-anchor",
    "sylveon": "spread-pressure",
    "archaludon": "bulky-pressure",
    "volcarona": "setup",
}


def opponent_pokemon(name: str, used_ids: set[str]) -> PokemonState:
    base = slug(name)
    id = base
    suffix = 2
    while id in used_ids:
        id = f"{base}-{suffix}"
        suffix += 1
    used_ids.add(id)
    return PokemonState(
        id=id,
        name=name.strip(),
        role=ROLE_HINTS.get(base, "unknown"),
        set_verified=False,
    )


def clone_player_team() -> dict[str, PokemonState]:
    return {member.id: PokemonState.from_dict(member.to_dict()) for member in PLAYER_TEAM}


def create_match(
    opponent_names: list[str],
    selected_player: list[str],
    player_lead: list[str],
    opponent_lead: list[str] | None = None,
    match_id: str | None = None,
) -> BattleState:
    if len(opponent_names) != 6:
        raise ValueError("team preview requires exactly six opponent Pokémon")
    if len(selected_player) != 4 or len(set(selected_player)) != 4:
        raise ValueError("exactly four unique player Pokémon must be selected")
    if len(player_lead) != 2 or not set(player_lead).issubset(selected_player):
        raise ValueError("player lead must contain two selected Pokémon")

    player_roster = clone_player_team()
    if not set(selected_player).issubset(player_roster):
        raise ValueError("selected player Pokémon must belong to the configured team")

    used_ids: set[str] = set()
    opponent_members = [opponent_pokemon(name, used_ids) for name in opponent_names]
    opponent_roster = {member.id: member for member in opponent_members}
    requested_lead = opponent_lead or [member.id for member in opponent_members[:2]]
    if len(requested_lead) != 2 or not set(requested_lead).issubset(opponent_roster):
        raise ValueError("opponent lead must contain two preview Pokémon")

    state = BattleState(
        match_id=match_id or uuid4().hex,
        turn=1,
        phase="battle",
        player=SideState(
            roster=player_roster,
            active=list(player_lead),
            bench=[id for id in selected_player if id not in player_lead],
            selected=list(selected_player),
        ),
        opponent=SideState(
            roster=opponent_roster,
            active=list(requested_lead),
            bench=[id for id in opponent_roster if id not in requested_lead],
            selected=list(opponent_roster),
        ),
    )
    state.validate()
    return state
