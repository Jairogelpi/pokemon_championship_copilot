from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "battle-engine" / "src"))
sys.path.insert(0, str(ROOT / "services" / "api" / "src"))

from champions_api.service import AppService  # noqa: E402


OPPONENT = ["Charizard", "Garchomp", "Kingambit", "Aerodactyl", "Sylveon", "Farigiraf"]


class ServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = AppService()
        self.created = self.service.create_match({"opponent_team": OPPONENT})
        self.match_id = self.created["state"]["match_id"]

    def tearDown(self) -> None:
        self.service.close()

    def test_create_match_returns_preview_beliefs_and_recommendation(self) -> None:
        self.assertEqual(1, self.created["state"]["turn"])
        self.assertEqual(6, len(self.created["beliefs"]["opponent"]))
        self.assertIsNotNone(self.created["recommendation"])
        calculator = self.created["recommendation"]["calculator"]
        self.assertEqual("@smogon/calc", calculator["engine"])
        self.assertEqual(1.0, calculator["coverage"])
        self.assertTrue(self.created["recommendation"]["primary"]["damage"])
        response_model = self.created["recommendation"]["response_model"]
        self.assertTrue(response_model["concrete"])
        self.assertEqual(1.0, response_model["coverage_mass"])
        self.assertGreater(response_model["scenarios_evaluated"], 100)
        self.assertTrue(self.created["recommendation"]["primary"]["principal_lines"])
        self.assertGreater(calculator["modelled_opponent_moves"], 0)

    def test_meta_snapshot_is_versioned_and_order_is_not_called_frequency(self) -> None:
        meta = self.service.meta_lookup({"species": "Charizard"})
        self.assertTrue(meta["found"])
        self.assertEqual("2026-08-10", meta["retrieved_at"])
        self.assertEqual("Protect", meta["pokemon"]["moves"][0])
        self.assertIn("not a usage percentage", meta["methodology"]["ordered_fields"])

    def test_record_event_updates_state_and_export_replays(self) -> None:
        changed = self.service.record_event(
            self.match_id,
            {"type": "hp_changed", "payload": {"side": "opponent", "pokemon": "charizard", "hp": 44}},
        )
        self.assertEqual(44, changed["state"]["opponent"]["roster"]["charizard"]["hp"])
        exported = self.service.export_match(self.match_id)
        self.assertEqual(exported["final_state"], changed["state"])
        self.assertEqual(1, len(exported["events"]))

    def test_revealed_opponent_attack_is_calculated_as_incoming_threat(self) -> None:
        changed = self.service.record_event(
            self.match_id,
            {
                "type": "move_used",
                "payload": {
                    "side": "opponent",
                    "pokemon": "charizard",
                    "move": "Heat Wave",
                },
            },
        )
        recommendation = changed["recommendation"]
        self.assertGreater(recommendation["calculator"]["revealed_threat_matchups"], 0)
        ranked = [recommendation["primary"], *recommendation["alternatives"]]
        self.assertTrue(any(candidate["threats"] for candidate in ranked))
        self.assertTrue(
            any(candidate["score"]["incoming_damage_percent"] > 0 for candidate in ranked)
        )

    def test_correction_is_append_only_and_rebuilds_state(self) -> None:
        changed = self.service.record_event(
            self.match_id,
            {"type": "hp_changed", "payload": {"side": "opponent", "pokemon": "charizard", "hp": 44}},
        )
        event_id = changed["events"][0]["id"]
        corrected = self.service.correct_event(
            self.match_id,
            {"target_event_id": event_id, "replacement": None},
        )
        self.assertEqual(100, corrected["state"]["opponent"]["roster"]["charizard"]["hp"])
        self.assertEqual(2, len(corrected["events"]))
        self.assertEqual("correction", corrected["events"][-1]["type"])
        exported = self.service.export_match(self.match_id)
        self.assertEqual(corrected["state"], exported["final_state"])
        self.assertEqual(0, exported["initial_state"]["revision"])
        self.assertEqual(2, exported["final_state"]["revision"])

    def test_local_interpreter_proposes_confirmable_event(self) -> None:
        result = self.service.interpret(self.match_id, {"text": "Charizard queda al 62%"})
        self.assertEqual("hp_changed", result["event"]["type"])
        self.assertEqual(62, result["event"]["payload"]["hp"])
        self.assertEqual("opponent", result["event"]["payload"]["side"])

    def test_damage_and_speed_endpoints(self) -> None:
        damage = self.service.damage(
            {"level": 50, "power": 80, "attack": 140, "defense": 100, "modifier": 1.5}
        )
        speed = self.service.speed({"raw_speed": 100, "stage": -1, "tailwind": True})
        self.assertLessEqual(damage["minimum"], damage["maximum"])
        self.assertEqual(133, speed["effective_speed"])

    def test_confirmed_evs_collapse_hidden_bulk_to_one_scenario(self) -> None:
        changed = self.service.record_event(
            self.match_id,
            {
                "type": "fact_revealed",
                "payload": {
                    "side": "opponent",
                    "pokemon": "charizard",
                    "key": "evs",
                    "value": {"hp": 252, "def": 252},
                },
            },
        )
        estimates = [
            estimate
            for alternative in [changed["recommendation"]["primary"]]
            + changed["recommendation"]["alternatives"]
            for estimate in alternative["damage"]
            if estimate["target"] == "charizard"
        ]
        self.assertTrue(estimates)
        self.assertTrue(all(estimate["scenario_count"] == 1 for estimate in estimates))


if __name__ == "__main__":
    unittest.main()
