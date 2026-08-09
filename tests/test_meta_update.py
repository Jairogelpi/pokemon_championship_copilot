from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from update_meta import (  # noqa: E402
    comparable,
    extract_species_slugs,
    parse_species_page,
    validate_snapshot,
)


class MetaUpdaterTests(unittest.TestCase):
    def test_index_preserves_ranked_link_order(self) -> None:
        links = "".join(
            f'<a href="/pokedex/battledataregmbs3/Pokemon-{index}">#{index}</a>'
            for index in range(1, 26)
        )
        slugs = extract_species_slugs(links, "battledataregmbs3", limit=25)
        self.assertEqual("Pokemon-1", slugs[0])
        self.assertEqual("Pokemon-25", slugs[-1])

    def test_detail_parser_requires_complete_competitive_evidence(self) -> None:
        page = """
        <meta property="og:title" content="Garchomp VGC 2026 Reg. M-B Champions">
        <main>
          Garchomp has a 51.2% winrate in Pokemon Champions, based on 1,234 wins
          and 1,111 losses with 7 ties.
          The most common abilities for Garchomp are Rough Skin (91.0%) and Sand Veil (9.0%).
          The most popular items for Garchomp are Life Orb (42.0%) and Choice Scarf (18.0%).
          The top moves for Garchomp are Earthquake (88.0%), Protect (72.0%),
          Dragon Claw (61.0%), and Rock Slide (55.0%).
        </main>
        """
        parsed = parse_species_page(page, "Garchomp", 1)
        self.assertEqual("Garchomp", parsed["name"])
        self.assertEqual(51.2, parsed["win_rate"])
        self.assertEqual("Earthquake", parsed["moves"][0])
        self.assertEqual(1234, parsed["sample"]["wins"])

    def test_current_snapshot_passes_fail_closed_schema_validation(self) -> None:
        import json

        path = ROOT / "data" / "meta" / "regulation-m-b-current.json"
        snapshot = json.loads(path.read_text(encoding="utf-8"))
        validate_snapshot(snapshot)

    def test_retrieval_timestamp_alone_is_not_a_material_change(self) -> None:
        first = {"snapshot_id": "one", "retrieved_at": "2026-08-09", "pokemon": []}
        second = {"snapshot_id": "two", "retrieved_at": "2026-08-10", "pokemon": []}
        self.assertEqual(comparable(first), comparable(second))


if __name__ == "__main__":
    unittest.main()
