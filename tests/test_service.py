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

    def test_create_match_returns_preview_beliefs_and_recommendation(self) -> None:
        self.assertEqual(1, self.created["state"]["turn"])
        self.assertEqual(6, len(self.created["beliefs"]["opponent"]))
        self.assertIsNotNone(self.created["recommendation"])

    def test_record_event_updates_state_and_export_replays(self) -> None:
        changed = self.service.record_event(
            self.match_id,
            {"type": "hp_changed", "payload": {"side": "opponent", "pokemon": "charizard", "hp": 44}},
        )
        self.assertEqual(44, changed["state"]["opponent"]["roster"]["charizard"]["hp"])
        exported = self.service.export_match(self.match_id)
        self.assertEqual(exported["final_state"], changed["state"])
        self.assertEqual(1, len(exported["events"]))

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


if __name__ == "__main__":
    unittest.main()
