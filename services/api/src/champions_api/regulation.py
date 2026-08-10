from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

from champions_copilot.models import PokemonState


def normalize_id(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class LegalityResult:
    legal: bool
    errors: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"legal": self.legal, "errors": list(self.errors)}


class CurrentChampionsRegulation:
    """Fail-closed legality boundary for the currently pinned Champions format."""

    def __init__(self, repo_root: Path | None = None) -> None:
        root = repo_root or Path(__file__).resolve().parents[4]
        self.path = root / "data" / "champions" / "current.json"
        with self.path.open(encoding="utf-8") as source:
            self.snapshot: dict[str, Any] = json.load(source)

        self._species = {
            normalize_id(str(entry["name"])): entry
            for entry in self.snapshot["species"]
        }
        self._mega_forms = {
            normalize_id(str(entry["name"])): entry
            for entry in self.snapshot["mega_forms"]
        }
        self._mega_by_species: dict[str, list[dict[str, Any]]] = {}
        for entry in self.snapshot["mega_forms"]:
            self._mega_by_species.setdefault(normalize_id(str(entry["slug"])), []).append(entry)
        self._items = {normalize_id(str(name)): str(name) for name in self.snapshot["items"]}
        self._moves = {normalize_id(str(name)): str(name) for name in self.snapshot["moves"]}
        self._aliases = {
            normalize_id(alias): normalize_id(canonical)
            for alias, canonical in self.snapshot.get("aliases", {}).items()
        }
        self._learnsets = {
            normalize_id(species): {normalize_id(move) for move in moves}
            for species, moves in self.snapshot["learnsets"].items()
        }
        self._mega_stones = {
            normalize_id(stone): normalize_id(species.replace("special ", ""))
            for stone, species in self.snapshot["mega_stones"].items()
        }
        self._active_from = _utc(str(self.snapshot["active_from"]))
        self._active_to = _utc(str(self.snapshot["active_to"]))
        self._validate_snapshot()

    def _validate_snapshot(self) -> None:
        expected = {"species": 231, "mega_forms": 75, "items": 148, "moves": 486}
        actual = {
            "species": len(self._species),
            "mega_forms": len(self._mega_forms),
            "items": len(self._items),
            "moves": len(self._moves),
        }
        if actual != expected:
            raise ValueError(f"Champions legality snapshot is incomplete: {actual} != {expected}")
        if len(self._learnsets) != len(self._species):
            raise ValueError("Champions legality snapshot is missing species learnsets")
        if len(self._mega_stones) != len(self._mega_forms):
            raise ValueError("Champions legality snapshot is missing Mega Stone mappings")
        if self.snapshot.get("format") != "Doubles":
            raise ValueError("current Champions snapshot is not a Doubles format")

    @staticmethod
    def _now(at: datetime | None = None) -> datetime:
        value = at or datetime.now(UTC)
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def is_active(self, at: datetime | None = None) -> bool:
        now = self._now(at)
        return self._active_from <= now <= self._active_to

    def require_active(self, at: datetime | None = None) -> None:
        if self.is_active(at):
            return
        now = self._now(at).isoformat()
        raise ValueError(
            "Champions legality snapshot is outside its verified window "
            f"({self.snapshot['active_from']} to {self.snapshot['active_to']}); now={now}. "
            "Refresh the regulation before generating a recommendation."
        )

    def status(self, at: datetime | None = None) -> dict[str, Any]:
        active = self.is_active(at)
        return {
            "snapshot_id": self.snapshot["snapshot_id"],
            "game": self.snapshot["game"],
            "format": self.snapshot["format"],
            "regulation": self.snapshot["regulation"],
            "season": self.snapshot["season"],
            "retrieved_at": self.snapshot["retrieved_at"],
            "active_from": self.snapshot["active_from"],
            "active_to": self.snapshot["active_to"],
            "active": active,
            "fail_closed": True,
            "species": len(self._species),
            "mega_forms": len(self._mega_forms),
            "items": len(self._items),
            "moves": len(self._moves),
            "learnsets": len(self._learnsets),
            "rules": self.snapshot["rules"],
            "sources": self.snapshot["sources"],
            "stale_reason": None if active else "verified regulation window expired",
        }

    def _species_id(self, species: str) -> str:
        requested = normalize_id(species)
        return self._aliases.get(requested, requested)

    def species(self, species: str) -> dict[str, Any] | None:
        return self._species.get(self._species_id(species))

    def mega_form(self, name: str) -> dict[str, Any] | None:
        return self._mega_forms.get(normalize_id(name))

    def mega_options(self, species: str) -> tuple[dict[str, Any], ...]:
        species_id = self._species_id(species)
        return tuple(dict(entry) for entry in self._mega_by_species.get(species_id, ()))

    def mega_evolution(
        self,
        species: str,
        *,
        item: str | None = None,
        form: str | None = None,
    ) -> dict[str, Any]:
        """Resolve one legal Mega form and expose calculator-ready mechanics.

        A stone is authoritative when a species has multiple Mega forms. The
        method deliberately fails closed instead of guessing X/Y or similar
        branches.
        """

        self.require_active()
        species_id = self._species_id(species)
        if species_id not in self._species:
            raise ValueError(f"Pokemon is not legal in current Champions Doubles: {species}")
        candidates = list(self._mega_by_species.get(species_id, ()))
        if form:
            requested = self.mega_form(form)
            candidates = [requested] if requested in candidates else []
        if item:
            item_id = normalize_id(item)
            candidates = [
                entry
                for entry in candidates
                if normalize_id(str(entry["mega_stone"])) == item_id
            ]
        if not candidates:
            qualifier = f" with {item}" if item else ""
            raise ValueError(f"{species} cannot Mega Evolve{qualifier} in current Champions Doubles")
        if len(candidates) != 1:
            stones = ", ".join(str(entry["mega_stone"]) for entry in candidates)
            raise ValueError(f"Mega form is ambiguous for {species}; specify one of: {stones}")
        entry = candidates[0]
        ability = str(entry["abilities"][0]).title()
        return {
            "snapshot_id": self.snapshot["snapshot_id"],
            "species": self._species[species_id]["name"],
            "battle_form": str(entry["name"]).title(),
            "mega_stone": entry["mega_stone"],
            "ability": ability,
            "mechanics_override": {
                "types": [str(value).title() for value in entry["types"]],
                "baseStats": {key: int(value) for key, value in entry["base_stats"].items()},
            },
        }

    def is_species_legal(self, species: str) -> bool:
        return self.species(species) is not None

    def is_item_legal(self, item: str | None) -> bool:
        return item is None or normalize_id(item) in self._items

    def is_move_legal(self, species: str, move: str) -> bool:
        species_id = self._species_id(species)
        move_id = normalize_id(move)
        return move_id in self._moves and move_id in self._learnsets.get(species_id, set())

    def learnset(self, species: str) -> dict[str, Any]:
        self.require_active()
        species_id = self._species_id(species)
        entry = self._species.get(species_id)
        if entry is None:
            raise ValueError(f"Pokémon is not legal in current Champions Doubles: {species}")
        move_ids = self._learnsets[species_id]
        return {
            "snapshot_id": self.snapshot["snapshot_id"],
            "regulation": self.snapshot["regulation"],
            "season": self.snapshot["season"],
            "species": entry,
            "moves": [
                name.title() for move_id, name in self._moves.items() if move_id in move_ids
            ],
            "move_count": len(move_ids),
            "source": "Pokemon Champions current legality snapshot",
        }

    def lookup(self, kind: str, name: str) -> dict[str, Any]:
        self.require_active()
        normalized_kind = kind.lower().strip()
        if normalized_kind in {"species", "pokemon"}:
            entry = self.species(name)
            legal = entry is not None
        elif normalized_kind == "move":
            entry = self._moves.get(normalize_id(name))
            legal = entry is not None
        elif normalized_kind == "item":
            entry = self._items.get(normalize_id(name))
            legal = entry is not None
        elif normalized_kind == "mega":
            entry = self.mega_form(name)
            legal = entry is not None
        else:
            raise ValueError("kind must be species, move, item, or mega")
        return {
            "snapshot_id": self.snapshot["snapshot_id"],
            "regulation": self.snapshot["regulation"],
            "season": self.snapshot["season"],
            "kind": normalized_kind,
            "requested": name,
            "legal": legal,
            "entry": entry,
        }

    def validate_set(self, pokemon: PokemonState) -> LegalityResult:
        errors: list[str] = []
        species_id = self._species_id(pokemon.name)
        if species_id not in self._species:
            errors.append(f"{pokemon.name}: species is not legal in current Champions Doubles")
        if not self.is_item_legal(pokemon.item):
            errors.append(f"{pokemon.name}: item is not legal: {pokemon.item}")
        for move in pokemon.moves:
            if not self.is_move_legal(pokemon.name, move):
                errors.append(f"{pokemon.name}: move is not in its Champions movepool: {move}")
        if pokemon.item and normalize_id(pokemon.item) in self._mega_stones:
            holder = self._mega_stones[normalize_id(pokemon.item)]
            if holder != species_id:
                errors.append(
                    f"{pokemon.name}: {pokemon.item} belongs to another species"
                )
        return LegalityResult(not errors, tuple(errors))

    def validate_team(
        self,
        team: Iterable[PokemonState],
        *,
        require_six: bool = True,
    ) -> LegalityResult:
        errors: list[str] = []
        members = list(team)
        if require_six and len(members) != int(self.snapshot["rules"]["team_size"]):
            errors.append("current Champions Doubles requires a six-Pokémon team")
        species_ids = [self._species_id(member.name) for member in members]
        if len(species_ids) != len(set(species_ids)):
            errors.append("duplicate species are not legal")
        items = [normalize_id(member.item) for member in members if member.item]
        if len(items) != len(set(items)):
            errors.append("duplicate held items are not legal")
        for member in members:
            errors.extend(self.validate_set(member).errors)
        return LegalityResult(not errors, tuple(errors))

    def validate_preview(self, species: Iterable[str]) -> LegalityResult:
        errors: list[str] = []
        names = [str(name).strip() for name in species]
        if len(names) != int(self.snapshot["rules"]["team_size"]):
            errors.append("team preview requires exactly six Pokémon")
        ids = [self._species_id(name) for name in names]
        if len(ids) != len(set(ids)):
            errors.append("duplicate species are not legal")
        for name, species_id in zip(names, ids, strict=True):
            if species_id not in self._species:
                errors.append(f"Pokémon is not legal in current Champions Doubles: {name}")
        return LegalityResult(not errors, tuple(errors))

    def assert_preview(self, species: Iterable[str]) -> None:
        self.require_active()
        result = self.validate_preview(species)
        if not result.legal:
            raise ValueError("; ".join(result.errors))

    def assert_move(self, species: str, move: str) -> None:
        self.require_active()
        if not self.is_move_legal(species, move):
            raise ValueError(
                f"move is not legal for {species} in current Champions Doubles: {move}"
            )

    def assert_item(self, item: str | None) -> None:
        self.require_active()
        if not self.is_item_legal(item):
            raise ValueError(f"item is not legal in current Champions Doubles: {item}")

    def assert_item_for_species(self, species: str, item: str | None) -> None:
        self.assert_item(item)
        if item is None:
            return
        holder = self._mega_stones.get(normalize_id(item))
        if holder is not None and holder != self._species_id(species):
            raise ValueError(f"{item} cannot be held to Mega Evolve {species}")
