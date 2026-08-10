from __future__ import annotations

import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from champions_copilot.models import BattleState


EVENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["event", "confidence", "explanation", "requires_confirmation"],
    "properties": {
        "event": {
            "type": "object",
            "additionalProperties": False,
            "required": ["type", "payload"],
            "properties": {
                "type": {
                    "type": "string",
                    "enum": [
                        "turn_started",
                        "hp_changed",
                        "status_set",
                        "boost_changed",
                        "switch",
                        "faint",
                        "move_used",
                        "mega_evolved",
                        "field_set",
                        "fact_revealed",
                        "match_finished",
                        "note",
                    ],
                },
                "payload": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "side": {"type": ["string", "null"], "enum": ["player", "opponent", None]},
                        "pokemon": {"type": ["string", "null"]},
                        "out": {"type": ["string", "null"]},
                        "in": {"type": ["string", "null"]},
                        "move": {"type": ["string", "null"]},
                        "targets": {"type": "array", "items": {"type": "string"}},
                        "hp": {"type": ["integer", "null"], "minimum": 0, "maximum": 100},
                        "delta": {"type": ["integer", "null"], "minimum": -100, "maximum": 100},
                        "status": {"type": ["string", "null"]},
                        "stat": {"type": ["string", "null"]},
                        "turn": {"type": ["integer", "null"]},
                        "turns": {"type": ["integer", "null"]},
                        "field": {"type": ["string", "null"]},
                        "key": {"type": ["string", "null"]},
                        "value": {"type": ["string", "number", "boolean", "null"]},
                        "winner": {"type": ["string", "null"]},
                        "text": {"type": ["string", "null"]},
                    },
                    "required": [
                        "side",
                        "pokemon",
                        "out",
                        "in",
                        "move",
                        "targets",
                        "hp",
                        "delta",
                        "status",
                        "stat",
                        "turn",
                        "turns",
                        "field",
                        "key",
                        "value",
                        "winner",
                        "text",
                    ],
                },
            },
        },
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "explanation": {"type": "string"},
        "requires_confirmation": {"type": "boolean"},
    },
}


class OpenAIEventInterpreter:
    def __init__(self) -> None:
        self.api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        self.model = os.environ.get("OPENAI_MODEL", "gpt-5.6-sol").strip()

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.model)

    def interpret(self, text: str, state: BattleState) -> dict[str, Any] | None:
        if not self.configured:
            return None
        roster = {
            "player": {id: pokemon.name for id, pokemon in state.player.roster.items()},
            "opponent": {id: pokemon.name for id, pokemon in state.opponent.roster.items()},
        }
        body = {
            "model": self.model,
            "instructions": (
                "Extract exactly one proposed Pokémon Champions battle event. Use roster IDs, not display "
                "names, in side/pokemon/out/in fields. Do not infer damage, boosts, targets, or effects that "
                "the user did not state. Low confidence or ambiguity must require confirmation."
            ),
            "input": json.dumps(
                {"utterance": text, "turn": state.turn, "roster": roster}, ensure_ascii=False
            ),
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "battle_event_proposal",
                    "strict": True,
                    "schema": EVENT_SCHEMA,
                }
            },
            "store": False,
        }
        request = Request(
            "https://api.openai.com/v1/responses",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=25) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError):
            return None
        for output in payload.get("output", []):
            for content in output.get("content", []):
                if content.get("type") == "output_text":
                    try:
                        result = json.loads(content["text"])
                    except (KeyError, json.JSONDecodeError):
                        return None
                    result["source"] = "openai"
                    return result
        return None
