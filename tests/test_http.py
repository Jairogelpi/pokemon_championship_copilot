from __future__ import annotations

import json
import sys
import threading
import unittest
from pathlib import Path
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "battle-engine" / "src"))
sys.path.insert(0, str(ROOT / "services" / "api" / "src"))

from champions_api.codex_brain import CodexBattleBrain  # noqa: E402
from champions_api.server import create_server  # noqa: E402
from champions_api.service import AppService  # noqa: E402


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        service = AppService(brain=CodexBattleBrain(api_key=""))
        cls.server = create_server(ROOT, "127.0.0.1", 0, service=service)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)

    def get(self, path: str) -> tuple[int, bytes, str]:
        with urlopen(f"{self.base}{path}", timeout=3) as response:
            return response.status, response.read(), response.headers.get_content_type()

    def post(self, path: str, payload: dict[str, object]) -> tuple[int, dict[str, object]]:
        request = Request(
            f"{self.base}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def test_health_and_static_app(self) -> None:
        status, body, content_type = self.get("/api/health")
        self.assertEqual(200, status)
        self.assertEqual("ok", json.loads(body)["status"])
        status, body, content_type = self.get("/")
        self.assertEqual(200, status)
        self.assertEqual("text/html", content_type)
        self.assertIn(b"Champions Battle Copilot", body)

    def test_real_match_http_workflow(self) -> None:
        status, created = self.post(
            "/api/matches",
            {
                "opponent_team": [
                    "Charizard",
                    "Garchomp",
                    "Kingambit",
                    "Aerodactyl",
                    "Sylveon",
                    "Farigiraf",
                ]
            },
        )
        self.assertEqual(201, status)
        match_id = created["state"]["match_id"]
        status, changed = self.post(
            f"/api/matches/{match_id}/events",
            {"type": "move_used", "payload": {"side": "opponent", "pokemon": "charizard", "move": "Protect"}},
        )
        self.assertEqual(201, status)
        self.assertIn("Protect", changed["state"]["opponent"]["roster"]["charizard"]["revealed_moves"])
        status, exported, _ = self.get(f"/api/matches/{match_id}/export")
        self.assertEqual(200, status)
        self.assertEqual(1, len(json.loads(exported)["events"]))

    def test_official_showdown_calculation_endpoint(self) -> None:
        status, result = self.post(
            "/api/calculate/showdown",
            {
                "generation": 9,
                "attacker": {
                    "name": "Garchomp",
                    "level": 50,
                    "nature": "Jolly",
                    "evs": {"atk": 252},
                },
                "defender": {
                    "name": "Kingambit",
                    "level": 50,
                    "nature": "Adamant",
                    "evs": {"hp": 252},
                },
                "move": {"name": "Earthquake"},
                "field": {"gameType": "Doubles"},
            },
        )
        self.assertEqual(200, status)
        self.assertEqual("@smogon/calc", result["source"])
        self.assertEqual("0.11.0", result["sourceVersion"])
        self.assertEqual(16, sum(roll["weight"] for roll in result["rolls"]))
        self.assertLessEqual(result["minimumPercent"], result["maximumPercent"])

    def test_knowledge_and_meta_endpoints(self) -> None:
        status, move = self.post(
            "/api/knowledge/lookup", {"kind": "move", "name": "Fake Out"}
        )
        self.assertEqual(200, status)
        self.assertEqual(3, move["entry"]["priority"])
        status, learnset = self.post(
            "/api/knowledge/learnset", {"species": "Garchomp"}
        )
        self.assertEqual(200, status)
        self.assertGreater(learnset["moveCount"], 50)
        self.assertEqual("Champions", learnset["generation"])
        current_species = self.server.service.meta.snapshot["pokemon"][0]["name"]
        status, meta = self.post("/api/meta/species", {"species": current_species})
        self.assertEqual(200, status)
        self.assertTrue(meta["found"])

    def test_current_regulation_endpoints_expose_and_enforce_snapshot(self) -> None:
        status, body, _ = self.get("/api/regulation/current")
        current = json.loads(body)
        self.assertEqual(200, status)
        self.assertEqual("M-B", current["regulation"])
        self.assertEqual("M-5", current["season"])
        self.assertTrue(current["active"])
        status, validation = self.post(
            "/api/regulation/validate",
            {
                "kind": "preview",
                "species": [
                    "Charizard",
                    "Garchomp",
                    "Kingambit",
                    "Aerodactyl",
                    "Sylveon",
                    "Farigiraf",
                ],
            },
        )
        self.assertEqual(200, status)
        self.assertTrue(validation["legal"])


if __name__ == "__main__":
    unittest.main()
