from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .regulation import CurrentChampionsRegulation


def normalize_id(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


class MetaRepository:
    """Versioned strategy priors; never treated as a mechanics authority."""

    POSITION_SCORES = (1.0, 0.82, 0.67, 0.54, 0.43, 0.34, 0.27, 0.21, 0.16, 0.12)

    def __init__(
        self,
        repo_root: Path | None = None,
        regulation: CurrentChampionsRegulation | None = None,
    ) -> None:
        root = repo_root or Path(__file__).resolve().parents[4]
        self.path = root / "data" / "meta" / "regulation-m-b-current.json"
        with self.path.open(encoding="utf-8") as source:
            self.snapshot: dict[str, Any] = json.load(source)
        self.regulation = regulation or CurrentChampionsRegulation(root)
        if self.snapshot.get("regulation_snapshot_id") != self.regulation.snapshot.get(
            "snapshot_id"
        ):
            raise ValueError("meta snapshot does not match the current legality snapshot")
        self._by_id = {
            normalize_id(entry["name"]): entry for entry in self.snapshot["pokemon"]
        }

    def status(self) -> dict[str, Any]:
        active_to = datetime.fromisoformat(
            str(self.snapshot["active_to"]).replace("Z", "+00:00")
        ).astimezone(UTC)
        current = self.regulation.is_active() and datetime.now(UTC) <= active_to
        rejected_values = sum(
            len(values)
            for entry in self.snapshot["pokemon"]
            for values in entry.get("rejected_source_values", {}).values()
        )
        return {
            "snapshot_id": self.snapshot["snapshot_id"],
            "format": self.snapshot["format"],
            "regulation": self.snapshot["regulation"],
            "season": self.snapshot["season"],
            "retrieved_at": self.snapshot["retrieved_at"],
            "active_to": self.snapshot["active_to"],
            "source": self.snapshot["source"],
            "pokemon_count": len(self._by_id),
            "current": current,
            "fail_closed": True,
            "rejected_non_champions_source_values": rejected_values,
            "regulation_snapshot_id": self.snapshot["regulation_snapshot_id"],
            "mechanics_authority": False,
        }

    def _entry(self, species: str) -> dict[str, Any] | None:
        legal_species = self.regulation.species(species)
        if legal_species is None:
            return None
        return self._by_id.get(normalize_id(str(legal_species["name"])))

    def get(self, species: str) -> dict[str, Any]:
        legal = self.regulation.is_species_legal(species)
        entry = self._entry(species)
        return {
            **self.status(),
            "legal": legal,
            "found": legal and entry is not None,
            "pokemon": entry,
            "methodology": self.snapshot["methodology"],
        }

    def move_candidates(
        self, species: str, revealed_moves: list[str] | tuple[str, ...], *, limit: int = 6
    ) -> list[dict[str, Any]]:
        entry = self._entry(species)
        ordered = [
            move
            for move in (list(entry.get("moves", [])) if entry else [])
            if self.regulation.is_move_legal(species, move)
        ]
        revealed_ids = {normalize_id(move) for move in revealed_moves}
        names: list[str] = []
        for move in [*revealed_moves, *ordered]:
            if (
                move
                and self.regulation.is_move_legal(species, move)
                and normalize_id(move) not in {normalize_id(name) for name in names}
            ):
                names.append(move)
        result: list[dict[str, Any]] = []
        for name in names[:limit]:
            move_id = normalize_id(name)
            if move_id in revealed_ids:
                score = 1.6
                source = "revealed"
                position = ordered.index(name) + 1 if name in ordered else None
            else:
                position = ordered.index(name) + 1
                usage = float(entry.get("move_usage", {}).get(name, 0)) if entry else 0
                score = (
                    usage / 100
                    if usage > 0
                    else self.POSITION_SCORES[
                        min(position - 1, len(self.POSITION_SCORES) - 1)
                    ]
                )
                source = (
                    "daily_meta_usage_heuristic" if usage > 0 else "meta_order_heuristic"
                )
            result.append(
                {
                    "move": name,
                    "score": score,
                    "source": source,
                    "meta_position": position,
                }
            )
        return result
