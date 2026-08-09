from __future__ import annotations

import re
import unicodedata
from typing import Any

from champions_copilot.models import BattleState


def plain(value: str) -> str:
    return "".join(
        character
        for character in unicodedata.normalize("NFD", value.lower())
        if unicodedata.category(character) != "Mn"
    )


def pokemon_index(state: BattleState) -> list[tuple[str, str, str]]:
    index: list[tuple[str, str, str]] = []
    for side_name, side in (("player", state.player), ("opponent", state.opponent)):
        for pokemon in side.roster.values():
            index.append((plain(pokemon.name), side_name, pokemon.id))
    return sorted(index, key=lambda item: -len(item[0]))


def find_pokemon(state: BattleState, text: str) -> tuple[str, str] | None:
    normalized = plain(text)
    side_hint = None
    if re.search(r"\b(?:rival|opponent|enemigo|enemy|opp)\b", normalized):
        side_hint = "opponent"
    elif re.search(r"\b(?:mi|my|our|nuestro|nuestra)\b", normalized):
        side_hint = "player"
    for name, side, pokemon_id in pokemon_index(state):
        if side_hint is not None and side != side_hint:
            continue
        if re.search(rf"\b{re.escape(name)}\b", normalized):
            return side, pokemon_id
    return None


def proposal(event: dict[str, Any], confidence: float, explanation: str) -> dict[str, Any]:
    return {
        "event": event,
        "confidence": round(max(0.0, min(1.0, confidence)), 3),
        "explanation": explanation,
        "requires_confirmation": confidence < 0.9,
        "source": "local-parser",
    }


def interpret_locally(text: str, state: BattleState) -> dict[str, Any]:
    normalized = plain(text.strip())
    if not normalized:
        raise ValueError("text is required")

    turn_match = re.search(r"\b(?:turno|turn)\s+(\d+)\b", normalized)
    if turn_match:
        turn = int(turn_match.group(1))
        return proposal(
            {"type": "turn_started", "payload": {"turn": turn}},
            0.98,
            f"Advance the battle to turn {turn}.",
        )

    identified = find_pokemon(state, normalized)
    if identified is None:
        return proposal(
            {"type": "note", "payload": {"text": text}},
            0.25,
            "No configured Pokémon name was recognized; keep this as a note.",
        )
    side, pokemon_id = identified

    if re.search(r"\b(?:se debilita|debilitado|fainted|faints|ko)\b", normalized):
        return proposal(
            {"type": "faint", "payload": {"side": side, "pokemon": pokemon_id}},
            0.97,
            "A faint event was recognized.",
        )

    hp_exact = re.search(r"(?:queda(?:\s+al?)?|at|hp)\s+(\d{1,3})\s*%", normalized)
    if hp_exact:
        hp = max(0, min(100, int(hp_exact.group(1))))
        return proposal(
            {"type": "hp_changed", "payload": {"side": side, "pokemon": pokemon_id, "hp": hp}},
            0.96,
            f"Set the recognized Pokémon to {hp}% HP.",
        )

    hp_loss = re.search(r"(?:pierde|lost|damage|dano)\s+(\d{1,3})\s*%?", normalized)
    if hp_loss:
        delta = -int(hp_loss.group(1))
        return proposal(
            {
                "type": "hp_changed",
                "payload": {"side": side, "pokemon": pokemon_id, "delta": delta},
            },
            0.9,
            f"Apply an HP change of {delta} percentage points.",
        )

    status_words = {
        "quemado": "burn",
        "burned": "burn",
        "burnt": "burn",
        "envenenado": "poison",
        "poisoned": "poison",
        "paralizado": "paralysis",
        "paralyzed": "paralysis",
        "dormido": "sleep",
        "asleep": "sleep",
        "congelado": "freeze",
        "frozen": "freeze",
    }
    for word, status in status_words.items():
        if re.search(rf"\b{word}\b", normalized):
            return proposal(
                {
                    "type": "status_set",
                    "payload": {"side": side, "pokemon": pokemon_id, "status": status},
                },
                0.93,
                f"Apply status {status}.",
            )

    if re.search(r"\b(?:megaevoluciona|mega evolves|mega)\b", normalized):
        return proposal(
            {"type": "mega_evolved", "payload": {"side": side, "pokemon": pokemon_id}},
            0.94,
            "A Mega Evolution event was recognized.",
        )

    move_match = re.search(r"\b(?:usa|uses|used)\s+(.+?)(?:\s+(?:contra|on|a)\s+|$)", text, re.I)
    if move_match:
        move = move_match.group(1).strip(" .")
        return proposal(
            {
                "type": "move_used",
                "payload": {"side": side, "pokemon": pokemon_id, "move": move},
            },
            0.88,
            f"Record the revealed move {move}.",
        )

    return proposal(
        {"type": "note", "payload": {"text": text}},
        0.4,
        "A Pokémon was recognized, but the battle event was ambiguous.",
    )
