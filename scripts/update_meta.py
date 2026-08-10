from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = "https://rotompicks.com/en/meta/"
DEFAULT_OUTPUT = ROOT / "data" / "meta" / "regulation-m-b-current.json"
DEFAULT_LEGALITY = ROOT / "data" / "champions" / "current.json"
USER_AGENT = "pokemon-champions-copilot-meta-updater/0.8 (+GitHub Actions)"


def normalize_id(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def visible_text(value: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", value)).split())


def fetch(url: str, *, attempts: int = 3) -> str:
    request = Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
    )
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            with urlopen(request, timeout=30) as response:  # noqa: S310 - explicit HTTPS input
                charset = response.headers.get_content_charset() or "utf-8"
                return response.read().decode(charset, errors="replace")
        except (HTTPError, URLError, TimeoutError) as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(2**attempt)
    raise RuntimeError(f"could not fetch {url}: {last_error}")


def _legality_indexes(legality: dict[str, Any]) -> dict[str, Any]:
    aliases = {
        normalize_id(alias): normalize_id(canonical)
        for alias, canonical in legality.get("aliases", {}).items()
    }
    return {
        "species": {normalize_id(entry["name"]) for entry in legality["species"]},
        "items": {normalize_id(item) for item in legality["items"]},
        "moves": {normalize_id(move) for move in legality["moves"]},
        "learnsets": {
            normalize_id(species): {normalize_id(move) for move in moves}
            for species, moves in legality["learnsets"].items()
        },
        "aliases": aliases,
    }


def parse_meta_page(
    document: str,
    *,
    legality: dict[str, Any],
    previous: dict[str, Any] | None = None,
    limit: int = 45,
) -> list[dict[str, Any]]:
    table = re.search(r"Usage Rankings.*?</table>", document, re.S | re.I)
    if table is None:
        raise ValueError("current meta page does not contain a usage table")
    indexes = _legality_indexes(legality)
    previous_by_id = {
        indexes["aliases"].get(
            normalize_id(entry["name"]), normalize_id(entry["name"])
        ): entry
        for entry in (previous or {}).get("pokemon", [])
    }
    entries: list[dict[str, Any]] = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", table.group(), re.S | re.I):
        cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S | re.I)
        if len(cells) < 8:
            continue
        rank_text = visible_text(cells[0])
        if not rank_text.isdigit():
            continue
        name = visible_text(cells[2])
        requested_id = normalize_id(name)
        species_id = indexes["aliases"].get(requested_id, requested_id)
        if species_id not in indexes["species"]:
            raise ValueError(f"meta source contains a non-Champions species: {name}")
        usage_text = visible_text(cells[4]).rstrip("%")
        win_text = visible_text(cells[5]).rstrip("%")
        if not usage_text:
            raise ValueError(f"{name}: missing usage value")
        top_moves = [
            part.strip()
            for part in visible_text(cells[6]).split(",")
            if part.strip() and part.strip() != "—"
        ]
        top_item = visible_text(cells[7]).replace("—", "").strip()
        old = previous_by_id.get(species_id, {})
        rejected_moves: list[str] = []
        moves: list[str] = []
        for move in [*top_moves, *old.get("moves", [])]:
            move_id = normalize_id(move)
            if (
                move_id not in indexes["moves"]
                or move_id not in indexes["learnsets"].get(species_id, set())
            ):
                if move:
                    rejected_moves.append(move)
                continue
            if move_id not in {normalize_id(value) for value in moves}:
                moves.append(move)
        rejected_items: list[str] = []
        items: list[str] = []
        for item in [top_item, *old.get("items", [])]:
            item_id = normalize_id(item)
            if item_id not in indexes["items"]:
                if item:
                    rejected_items.append(item)
                continue
            if item_id not in {normalize_id(value) for value in items}:
                items.append(item)
        if not moves:
            raise ValueError(f"{name}: no current Champions-legal move survived filtering")
        entries.append(
            {
                "rank": int(rank_text),
                "name": name,
                "usage": float(usage_text),
                "win_rate": float(win_text) if win_text not in {"", "—"} else None,
                "abilities": list(old.get("abilities", [])),
                "items": items,
                "moves": moves,
                "top_moves_observed": top_moves,
                "top_item_observed": top_item,
                "rejected_source_values": {
                    "moves": sorted(set(rejected_moves)),
                    "items": sorted(set(rejected_items)),
                },
                "set_prior_scope": (
                    "current-meta-summary plus prior legal set details"
                    if old
                    else "current-meta-summary only"
                ),
            }
        )
        if len(entries) >= limit:
            break
    if len(entries) < min(20, limit):
        raise ValueError(f"source yielded only {len(entries)} validated current entries")
    for expected_rank, entry in enumerate(entries, start=1):
        if entry["rank"] != expected_rank:
            raise ValueError("source ranks are not contiguous")
    return entries


def validate_snapshot(snapshot: dict[str, Any], *, minimum_entries: int = 20) -> None:
    entries = snapshot.get("pokemon")
    if snapshot.get("schema_version") != 2:
        raise ValueError("current meta snapshot must use schema version 2")
    if not isinstance(entries, list) or len(entries) < minimum_entries:
        raise ValueError("snapshot does not contain enough Pokémon")
    names = [normalize_id(str(entry.get("name", ""))) for entry in entries]
    if any(not name for name in names) or len(names) != len(set(names)):
        raise ValueError("snapshot contains missing or duplicate Pokémon names")
    for expected_rank, entry in enumerate(entries, start=1):
        if entry.get("rank") != expected_rank:
            raise ValueError("snapshot ranks are not contiguous")
        if not entry.get("moves"):
            raise ValueError(f"{entry.get('name')}: no legal moves")
        usage = float(entry.get("usage", -1))
        if not 0 <= usage <= 100:
            raise ValueError(f"{entry.get('name')}: invalid usage")
        win_rate = entry.get("win_rate")
        if win_rate is not None and not 0 <= float(win_rate) <= 100:
            raise ValueError(f"{entry.get('name')}: invalid win rate")


def build_snapshot(
    source_url: str,
    *,
    top: int = 45,
    previous: dict[str, Any] | None = None,
    legality_path: Path = DEFAULT_LEGALITY,
) -> dict[str, Any]:
    legality = json.loads(legality_path.read_text(encoding="utf-8"))
    document = fetch(source_url)
    entries = parse_meta_page(
        document,
        legality=legality,
        previous=previous,
        limit=top,
    )
    today = datetime.now(UTC).date().isoformat()
    regulation = str(legality["regulation"])
    season = str(legality["season"])
    snapshot = {
        "schema_version": 2,
        "snapshot_id": f"champions-reg-{regulation.lower()}-{season.lower()}-{today}",
        "regulation_snapshot_id": legality["snapshot_id"],
        "format": f"Pokemon Champions Doubles Regulation {regulation} / Season {season}",
        "regulation": regulation,
        "season": season,
        "retrieved_at": today,
        "active_to": legality["active_to"],
        "source": {
            "name": "RotomPicks current Regulation M-B meta",
            "url": source_url,
            "kind": "community-current-meta-aggregation",
            "lineage": "Pikalytics Pokemon Showdown ranked battles and Champions Lab tournament results",
            "sha256": hashlib.sha256(document.encode()).hexdigest(),
        },
        "set_prior_source": {
            "name": "last verified current-format snapshot",
            "note": "Only values that survive the pinned Champions species/item/movepool gate are retained.",
        },
        "methodology": {
            "ordered_fields": "Usage, win rate, top moves, and top item come from the current-format page; prior legal values may enrich sparse rows.",
            "action_prior": "The planner uses current top moves first, then legal set-order heuristics, and preserves an explicit residual-other branch.",
            "mechanics_boundary": "This snapshot supplies strategy priors only. The pinned Champions snapshot is the legality authority.",
            "in_game_boundary": "The source includes Showdown and community tournaments, not the private in-game ladder. Actual in-game usage may differ.",
        },
        "pokemon": entries,
    }
    validate_snapshot(snapshot)
    return snapshot


def comparable(snapshot: dict[str, Any]) -> dict[str, Any]:
    value = json.loads(json.dumps(snapshot))
    value.pop("retrieved_at", None)
    value.pop("snapshot_id", None)
    if isinstance(value.get("source"), dict):
        value["source"].pop("sha256", None)
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Refresh the current Pokémon Champions Doubles meta")
    parser.add_argument("--source-url", default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--legality", type=Path, default=DEFAULT_LEGALITY)
    parser.add_argument("--top", type=int, default=45)
    arguments = parser.parse_args(argv)
    try:
        current = (
            json.loads(arguments.output.read_text(encoding="utf-8"))
            if arguments.output.is_file()
            else None
        )
        snapshot = build_snapshot(
            arguments.source_url,
            top=arguments.top,
            previous=current,
            legality_path=arguments.legality,
        )
        if current is not None and comparable(current) == comparable(snapshot):
            print("Current Champions meta checked: no material change.")
            return 0
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"Updated {arguments.output} with {len(snapshot['pokemon'])} legal entries.")
        return 0
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"Meta update rejected: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
