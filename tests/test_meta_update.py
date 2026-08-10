from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from update_meta import comparable, parse_meta_page, validate_snapshot  # noqa: E402


def row(rank: int, name: str, usage: float, moves: str, item: str) -> str:
    return f"""
    <tr>
      <td>{rank}</td><td></td><td>{name}</td><td>types</td>
      <td>{usage}%</td><td>51.2%</td><td>{moves}</td><td>{item}</td>
    </tr>
    """


class MetaUpdaterTests(unittest.TestCase):
    def test_current_page_is_filtered_through_champions_movepools(self) -> None:
        legality = json.loads(
            (ROOT / "data" / "champions" / "current.json").read_text(encoding="utf-8")
        )
        page = (
            "<h2>Usage Rankings</h2><table>"
            + row(1, "Garchomp", 40.4, "Earthquake, Protect, Spore", "Life Orb")
            + row(2, "Sneasler", 39.1, "Dire Claw, Fake Out", "White Herb")
            + "</table>"
        )
        parsed = parse_meta_page(page, legality=legality, limit=2)
        self.assertEqual(["Earthquake", "Protect"], parsed[0]["moves"])
        self.assertEqual(["Spore"], parsed[0]["rejected_source_values"]["moves"])
        self.assertEqual("Sneasler", parsed[1]["name"])

    def test_current_snapshot_passes_fail_closed_schema_validation(self) -> None:
        path = ROOT / "data" / "meta" / "regulation-m-b-current.json"
        snapshot = json.loads(path.read_text(encoding="utf-8"))
        validate_snapshot(snapshot)
        self.assertEqual("M-B", snapshot["regulation"])
        self.assertEqual("M-5", snapshot["season"])
        self.assertEqual(
            "champions-reg-m-b-m5-2026-08-10",
            snapshot["regulation_snapshot_id"],
        )

    def test_retrieval_metadata_alone_is_not_a_material_change(self) -> None:
        first = {
            "snapshot_id": "one",
            "retrieved_at": "2026-08-09",
            "source": {"sha256": "one"},
            "pokemon": [],
        }
        second = {
            "snapshot_id": "two",
            "retrieved_at": "2026-08-10",
            "source": {"sha256": "two"},
            "pokemon": [],
        }
        self.assertEqual(comparable(first), comparable(second))


if __name__ == "__main__":
    unittest.main()
