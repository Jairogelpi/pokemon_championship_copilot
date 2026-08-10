from __future__ import annotations

import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "battle-engine" / "src"))
sys.path.insert(0, str(ROOT / "services" / "api" / "src"))

from champions_api.regulation import CurrentChampionsRegulation  # noqa: E402
from champions_copilot.models import PokemonState  # noqa: E402
from champions_copilot.team import PLAYER_TEAM  # noqa: E402


class CurrentChampionsRegulationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.regulation = CurrentChampionsRegulation(ROOT)

    def test_snapshot_is_complete_and_active(self) -> None:
        status = self.regulation.status(datetime(2026, 8, 10, tzinfo=UTC))
        self.assertTrue(status["active"])
        self.assertEqual("M-B", status["regulation"])
        self.assertEqual("M-5", status["season"])
        self.assertEqual(231, status["species"])
        self.assertEqual(75, status["mega_forms"])
        self.assertEqual(148, status["items"])
        self.assertEqual(486, status["moves"])

    def test_configured_team_passes_current_champions_legality(self) -> None:
        self.assertTrue(self.regulation.validate_team(PLAYER_TEAM).legal)
        dragonite = self.regulation.lookup("mega", "Mega Dragonite")
        self.assertTrue(dragonite["legal"])
        self.assertEqual("Dragoninite", dragonite["entry"]["mega_stone"])
        self.assertEqual(["multiscale"], dragonite["entry"]["abilities"])
        resolved = self.regulation.mega_evolution("Dragonite", item="Dragoninite")
        self.assertEqual("Mega Dragonite", resolved["battle_form"])
        self.assertEqual("Multiscale", resolved["ability"])
        self.assertEqual(145, resolved["mechanics_override"]["baseStats"]["spa"])

    def test_ambiguous_mega_form_fails_closed_without_stone(self) -> None:
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            self.regulation.mega_evolution("Charizard")
        resolved = self.regulation.mega_evolution(
            "Charizard", item="Charizardite X"
        )
        self.assertEqual("Mega Charizard X", resolved["battle_form"])

    def test_aliases_are_accepted_but_out_of_format_species_are_rejected(self) -> None:
        self.assertTrue(self.regulation.is_species_legal("Rotom-Wash"))
        self.assertTrue(self.regulation.is_species_legal("Ninetales-Alola"))
        self.assertFalse(self.regulation.is_species_legal("Mewtwo"))

    def test_move_item_and_mega_stone_compatibility_are_fail_closed(self) -> None:
        self.assertTrue(self.regulation.is_move_legal("Garchomp", "Earthquake"))
        self.assertFalse(self.regulation.is_move_legal("Garchomp", "Spore"))
        wrong_stone = PokemonState(
            id="dragonite",
            name="Dragonite",
            moves=("Protect",),
            item="Froslassite",
        )
        result = self.regulation.validate_set(wrong_stone)
        self.assertFalse(result.legal)
        self.assertIn("belongs to another species", result.errors[0])

    def test_expired_snapshot_stops_verified_play(self) -> None:
        with self.assertRaisesRegex(ValueError, "outside its verified window"):
            self.regulation.require_active(datetime(2026, 9, 3, tzinfo=UTC))


if __name__ == "__main__":
    unittest.main()
