from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "battle-engine" / "src"))
sys.path.insert(0, str(ROOT / "services" / "api" / "src"))

from champions_copilot.decision import recommend_team_preview  # noqa: E402
from champions_copilot.team import create_match  # noqa: E402
from champions_api.showdown import ShowdownCalculator  # noqa: E402
from champions_api.showdown_planner import calculate_turn_damage  # noqa: E402


OPPONENT = ["Charizard", "Garchomp", "Kingambit", "Aerodactyl", "Sylveon", "Farigiraf"]


class ShowdownCalculatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.calculator = ShowdownCalculator(ROOT)

    def tearDown(self) -> None:
        self.calculator.close()

    def test_health_reports_official_engine_version(self) -> None:
        health = self.calculator.health()
        self.assertTrue(health["available"])
        self.assertEqual("@smogon/calc", health["engine"])
        self.assertEqual("0.11.0", health["version"])
        self.assertEqual("0.10.11", health["knowledge"]["dexVersion"])
        self.assertGreater(health["knowledge"]["catalog"]["moves"], 600)

    def test_pokedex_lookup_learnset_and_type_chart_are_queryable(self) -> None:
        move = self.calculator.lookup("move", "Fake Out")
        self.assertEqual(3, move["entry"]["priority"])
        self.assertEqual("Physical", move["entry"]["category"])
        learnset = self.calculator.learnset("Garchomp")
        move_names = {entry["name"] for entry in learnset["moves"]}
        self.assertIn("Earthquake", move_names)
        self.assertIn("Protect", move_names)
        matchup = self.calculator.type_matchup("Ground", "Charizard")
        self.assertEqual(0, matchup["multiplier"])

    def test_batch_isolates_an_invalid_matchup(self) -> None:
        valid = {
            "generation": 9,
            "attacker": {"name": "Garchomp", "level": 50},
            "defender": {"name": "Kingambit", "level": 50},
            "move": {"name": "Earthquake"},
            "field": {"gameType": "Doubles"},
        }
        invalid = {**valid, "defender": {"name": "Definitely Not A Pokemon"}}
        results = self.calculator.batch([valid, invalid])
        self.assertTrue(results[0]["ok"])
        self.assertFalse(results[1]["ok"])

    def test_final_speed_uses_pinned_item_status_and_tailwind_mechanics(self) -> None:
        speed = self.calculator.speed(
            {
                "generation": 9,
                "pokemon": {
                    "name": "Garchomp",
                    "level": 50,
                    "nature": "Jolly",
                    "evs": {"spe": 252},
                    "item": "Choice Scarf",
                },
                "field": {
                    "gameType": "Doubles",
                    "attackerSide": {"isTailwind": True},
                },
            }
        )
        self.assertEqual(169, speed["rawSpeed"])
        self.assertEqual(507, speed["finalSpeed"])
        self.assertEqual(183, speed["maxHP"])

    def test_turn_matrix_covers_every_player_move_target_pair(self) -> None:
        preview = recommend_team_preview(OPPONENT)
        state = create_match(OPPONENT, preview["selected"], preview["lead"])
        estimates, threats, status = calculate_turn_damage(self.calculator, state)
        self.assertEqual(1.0, status["coverage"])
        self.assertEqual(status["requested_matchups"], len(estimates))
        self.assertEqual({}, threats)
        first = next(iter(estimates.values()))
        self.assertEqual(3, first.scenario_count)
        self.assertTrue(all(scenario["rolls_percent"] for scenario in first.scenarios))

    def test_missing_node_is_visible_and_never_fabricates_health(self) -> None:
        calculator = ShowdownCalculator(ROOT, node_binary="/missing/node")
        health = calculator.health()
        self.assertFalse(health["available"])
        self.assertEqual("unavailable", health["status"])


if __name__ == "__main__":
    unittest.main()
