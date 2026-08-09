from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import unquote
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = "https://www.pikalytics.com/pokedex/battledataregmbs3"
DEFAULT_OUTPUT = ROOT / "data" / "meta" / "regulation-m-b-current.json"
USER_AGENT = "pokemon-champions-copilot-meta-updater/0.3 (+GitHub Actions)"


class VisibleText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.parts.append(data.strip())

    def text(self) -> str:
        return re.sub(r"\s+", " ", " ".join(self.parts)).strip()


def fetch(url: str, *, attempts: int = 3) -> str:
    request = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            with urlopen(request, timeout=30) as response:  # noqa: S310 - fixed HTTPS source
                charset = response.headers.get_content_charset() or "utf-8"
                return response.read().decode(charset, errors="replace")
        except (HTTPError, URLError, TimeoutError) as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(2**attempt)
    raise RuntimeError(f"could not fetch {url}: {last_error}")


def extract_species_slugs(document: str, format_id: str, *, limit: int) -> list[str]:
    pattern = re.compile(
        rf"href=[\"'](?:https://www\.pikalytics\.com)?/pokedex/{re.escape(format_id)}/([^\"'/?#]+)",
        re.IGNORECASE,
    )
    slugs: list[str] = []
    for match in pattern.finditer(document):
        slug = unquote(match.group(1)).strip()
        if slug and slug.lower() not in {value.lower() for value in slugs}:
            slugs.append(slug)
        if len(slugs) >= limit:
            break
    if len(slugs) < min(20, limit):
        raise ValueError(
            f"source index yielded only {len(slugs)} unique Pokémon; refusing a partial update"
        )
    return slugs


def visible_text(document: str) -> str:
    parser = VisibleText()
    parser.feed(document)
    return html.unescape(parser.text()).replace(r"\u0026", "&")


def _name(document: str, text: str, slug: str) -> str:
    metadata = re.search(
        r"<meta[^>]+(?:property|name)=[\"']og:title[\"'][^>]+content=[\"']([^\"']+)",
        document,
        re.IGNORECASE,
    )
    if metadata:
        title = html.unescape(metadata.group(1))
        return re.split(r"\s+(?:VGC|Pokémon Champions|Pokemon Champions|[-|])\s*", title)[0].strip()
    question = re.search(r"best moves for (.+?)(?:\?| in Pokemon Champions)", text, re.IGNORECASE)
    if question:
        return question.group(1).strip()
    return unquote(slug).replace("-", " ").strip().title()


def _ranked_pairs(text: str, labels: tuple[str, ...]) -> list[tuple[str, float]]:
    for label in labels:
        match = re.search(label + r".{0,180}?\bare\s+(.{1,700})", text, re.IGNORECASE)
        if not match:
            continue
        segment = re.split(
            r"(?=\s+(?:The\s+(?:most common|top)|What\b|Is\b|Support Us\b))",
            match.group(1),
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0]
        pairs = re.findall(
            r"([A-Za-z0-9][A-Za-z0-9 .:'’/&\-]+?)\s*\((\d+(?:\.\d+)?)%\)",
            segment,
        )
        cleaned: list[tuple[str, float]] = []
        for raw_name, raw_usage in pairs:
            name = re.sub(r"^(?:and|or)\s+", "", raw_name.strip(" ,"), flags=re.IGNORECASE)
            if name and name not in {entry[0] for entry in cleaned}:
                cleaned.append((name, float(raw_usage)))
        if cleaned:
            return cleaned
    return []


def parse_species_page(document: str, slug: str, rank: int) -> dict[str, Any]:
    text = visible_text(document)
    name = _name(document, text, slug)
    moves = _ranked_pairs(text, (r"top moves for", r"most common moves for"))
    abilities = _ranked_pairs(text, (r"most common abilities for", r"top abilities for"))
    items = _ranked_pairs(
        text,
        (
            r"most popular items for",
            r"most common items for",
            r"top items for",
            r"most used items for",
        ),
    )
    winrate_match = re.search(r"has a (\d+(?:\.\d+)?)% winrate", text, re.IGNORECASE)
    sample_match = re.search(
        r"based on ([\d,]+) wins and ([\d,]+) losses(?: with ([\d,]+) ties)?",
        text,
        re.IGNORECASE,
    )
    if len(moves) < 3 or not abilities or not items or not winrate_match:
        raise ValueError(
            f"{name}: incomplete source page (moves={len(moves)}, abilities={len(abilities)}, "
            f"items={len(items)}, winrate={bool(winrate_match)})"
        )
    result: dict[str, Any] = {
        "rank": rank,
        "name": name,
        "win_rate": float(winrate_match.group(1)),
        "abilities": [entry[0] for entry in abilities],
        "ability_usage": {entry[0]: entry[1] for entry in abilities},
        "items": [entry[0] for entry in items],
        "item_usage": {entry[0]: entry[1] for entry in items},
        "moves": [entry[0] for entry in moves],
        "move_usage": {entry[0]: entry[1] for entry in moves},
    }
    if sample_match:
        result["sample"] = {
            "wins": int(sample_match.group(1).replace(",", "")),
            "losses": int(sample_match.group(2).replace(",", "")),
            "ties": int((sample_match.group(3) or "0").replace(",", "")),
            "window": "source-reported rolling window",
        }
    return result


def validate_snapshot(snapshot: dict[str, Any], *, minimum_entries: int = 20) -> None:
    entries = snapshot.get("pokemon")
    if not isinstance(entries, list) or len(entries) < minimum_entries:
        raise ValueError("snapshot does not contain enough Pokémon")
    names = [str(entry.get("name", "")).lower() for entry in entries]
    if any(not name for name in names) or len(names) != len(set(names)):
        raise ValueError("snapshot contains missing or duplicate Pokémon names")
    for expected_rank, entry in enumerate(entries, start=1):
        if entry.get("rank") != expected_rank:
            raise ValueError("snapshot ranks are not contiguous")
        if len(entry.get("moves", [])) < 3:
            raise ValueError(f"{entry.get('name')}: fewer than three moves")
        if not entry.get("abilities") or not entry.get("items"):
            raise ValueError(f"{entry.get('name')}: missing ability or item data")
        win_rate = float(entry.get("win_rate", -1))
        if not 0 <= win_rate <= 100:
            raise ValueError(f"{entry.get('name')}: invalid win rate")


def build_snapshot(source_url: str, *, top: int = 25) -> dict[str, Any]:
    format_id = source_url.rstrip("/").rsplit("/", 1)[-1]
    index = fetch(source_url)
    slugs = extract_species_slugs(index, format_id, limit=top)
    entries = [
        parse_species_page(fetch(f"{source_url.rstrip('/')}/{slug}"), slug, rank)
        for rank, slug in enumerate(slugs, start=1)
    ]
    today = datetime.now(UTC).date().isoformat()
    snapshot = {
        "schema_version": 1,
        "snapshot_id": f"champions-reg-m-b-current-{today}",
        "format": "Pokemon Champions VGC 2026 Regulation M-B S3 Ranked Battles",
        "retrieved_at": today,
        "source": {
            "name": "Pikalytics",
            "url": source_url,
            "kind": "community-ranked-battle-aggregation",
        },
        "methodology": {
            "ordered_fields": "Usage percentages and ordering are extracted from the source pages; action choice remains a separate model prior.",
            "action_prior": "The planner combines source move ordering with revealed evidence and action-category beliefs, then preserves an explicit residual-other branch.",
            "mechanics_boundary": "This snapshot supplies strategy priors only. @pkmn/dex and @smogon/calc remain the mechanics sources.",
            "automation": "Refreshed at most once per day by GitHub Actions; invalid or partial source data is rejected before replacing the last valid snapshot.",
        },
        "pokemon": entries,
    }
    validate_snapshot(snapshot)
    return snapshot


def comparable(snapshot: dict[str, Any]) -> dict[str, Any]:
    value = dict(snapshot)
    value.pop("retrieved_at", None)
    value.pop("snapshot_id", None)
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Refresh the Pokémon Champions doubles meta")
    parser.add_argument("--source-url", default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--top", type=int, default=25)
    arguments = parser.parse_args(argv)
    try:
        snapshot = build_snapshot(arguments.source_url, top=arguments.top)
        current = (
            json.loads(arguments.output.read_text(encoding="utf-8"))
            if arguments.output.is_file()
            else None
        )
        if current is not None and comparable(current) == comparable(snapshot):
            print("Meta source checked: no material change.")
            return 0
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"Updated {arguments.output} with {len(snapshot['pokemon'])} validated entries.")
        return 0
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"Meta update rejected: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
