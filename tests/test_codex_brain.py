from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "battle-engine" / "src"))
sys.path.insert(0, str(ROOT / "services" / "api" / "src"))

from champions_api.codex_brain import CodexBattleBrain  # noqa: E402
from champions_api.battle_tools import BattleKnowledgeTools  # noqa: E402
from champions_api.meta import MetaRepository  # noqa: E402
from champions_api.showdown import ShowdownCalculator  # noqa: E402
from champions_copilot.beliefs import BeliefState  # noqa: E402
from champions_copilot.decision import recommend_actions, recommend_team_preview  # noqa: E402
from champions_copilot.team import create_match  # noqa: E402


OPPONENT = ["Charizard", "Garchomp", "Kingambit", "Aerodactyl", "Sylveon", "Farigiraf"]


def fixture():
    preview = recommend_team_preview(OPPONENT)
    state = create_match(
        OPPONENT,
        preview["selected"],
        preview["lead"],
        match_id="codex-brain-test-match",
    )
    beliefs = BeliefState.from_battle(state)
    recommendation = recommend_actions(state, beliefs)
    return state, beliefs, recommendation


def response_for(selected: str = "candidate-02") -> dict[str, Any]:
    decision = {
        "selected_candidate_id": selected,
        "alternative_candidate_ids": ["candidate-01", "candidate-03"],
        "opponent_plan": [
            {
                "hypothesis": "Protect plus speed control",
                "probability": 0.55,
                "evidence": "The response model preserves both branches.",
                "selected_counter": "The selected line keeps pressure into either slot.",
            },
            {
                "hypothesis": "Residual unmodelled response",
                "probability": 0.2,
                "evidence": "The belief state retains an other bucket.",
                "selected_counter": "The line keeps the safer lower tail.",
            },
        ],
        "win_condition": "Preserve speed control and the late-game cleaner.",
        "rationale": "Choose the second verified candidate for its safer conversion path.",
        "main_failure_mode": "An unobserved priority attack can still break the setup.",
        "risk": "medium",
        "confidence": 0.72,
        "assumptions": ["The opponent has not revealed its complete set."],
    }
    return {
        "output": [
            {
                "type": "message",
                "content": [
                    {"type": "output_text", "text": json.dumps(decision)}
                ],
            }
        ]
    }


class CodexBattleBrainTests(unittest.TestCase):
    def test_codex_selects_only_inside_the_verified_candidate_catalog(self) -> None:
        state, beliefs, recommendation = fixture()
        requests: list[dict[str, Any]] = []

        def transport(body: dict[str, Any]) -> dict[str, Any]:
            requests.append(body)
            return response_for()

        brain = CodexBattleBrain(
            api_key="test-key",
            model="gpt-5.6-sol",
            reasoning_effort="high",
            transport=transport,
        )
        result = brain.decide(
            state=state,
            beliefs=beliefs,
            recommendation=recommendation,
            events=[],
        )

        self.assertEqual(
            recommendation.candidate_catalog[1].label,
            result["primary"]["label"],
        )
        self.assertEqual("codex", result["brain"]["decision_source"])
        self.assertTrue(result["brain"]["overrode_deterministic_anchor"])
        self.assertEqual("codex-strategist-0.7", result["policy_version"])
        self.assertEqual(
            "CODEX_SELECTED_FROM_VERIFIED_CANDIDATES",
            result["validation_status"],
        )
        self.assertEqual("gpt-5.6-sol", requests[0]["model"])
        self.assertEqual({"effort": "high"}, requests[0]["reasoning"])
        self.assertFalse(requests[0]["store"])
        self.assertNotIn(state.match_id, requests[0]["safety_identifier"])
        candidate_enum = requests[0]["text"]["format"]["schema"]["properties"][
            "selected_candidate_id"
        ]["enum"]
        self.assertEqual(
            [row["id"] for row in result["candidate_catalog"]],
            candidate_enum,
        )

    def test_codex_can_query_verified_knowledge_before_selecting(self) -> None:
        state, beliefs, recommendation = fixture()
        calculator = ShowdownCalculator(ROOT)
        tools = BattleKnowledgeTools(
            calculator=calculator,
            meta=MetaRepository(ROOT),
            state=state,
            beliefs=beliefs,
            recommendation=recommendation,
        )
        requests: list[dict[str, Any]] = []

        def transport(body: dict[str, Any]) -> dict[str, Any]:
            requests.append(body)
            if len(requests) == 1:
                return {
                    "output": [
                        {"type": "reasoning", "id": "reasoning-1", "summary": []},
                        {
                            "type": "function_call",
                            "call_id": "call-1",
                            "name": "inspect_candidate",
                            "arguments": json.dumps({"candidate_id": "candidate-02"}),
                        },
                    ]
                }
            return response_for()

        brain = CodexBattleBrain(api_key="test-key", transport=transport)
        try:
            result = brain.decide(
                state=state,
                beliefs=beliefs,
                recommendation=recommendation,
                events=[],
                knowledge_tools=tools,
            )
        finally:
            calculator.close()

        self.assertEqual("required", requests[0]["tool_choice"])
        self.assertEqual(8, len(requests[0]["tools"]))
        self.assertEqual("auto", requests[1]["tool_choice"])
        follow_up_items = requests[1]["input"]
        self.assertTrue(any(item.get("type") == "reasoning" for item in follow_up_items))
        tool_outputs = [
            item for item in follow_up_items if item.get("type") == "function_call_output"
        ]
        self.assertEqual(1, len(tool_outputs))
        self.assertTrue(json.loads(tool_outputs[0]["output"])["ok"])
        self.assertEqual(1, result["brain"]["tool_calls_completed"])
        self.assertEqual("inspect_candidate", result["brain"]["tool_calls"][0]["name"])
        self.assertEqual(
            "read_only_verified_battle_knowledge",
            result["brain"]["knowledge_manifest"]["mode"],
        )

    def test_battle_tools_expose_mechanics_learnsets_matchups_and_meta(self) -> None:
        state, beliefs, recommendation = fixture()
        calculator = ShowdownCalculator(ROOT)
        tools = BattleKnowledgeTools(
            calculator=calculator,
            meta=MetaRepository(ROOT),
            state=state,
            beliefs=beliefs,
            recommendation=recommendation,
        )
        try:
            move = tools.execute(
                "lookup_battle_entity", {"kind": "move", "name": "Fake Out"}
            )
            learnset = tools.execute(
                "lookup_learnset", {"species": "Garchomp", "restriction": None}
            )
            matchup = tools.execute(
                "lookup_type_matchup",
                {"attack_type": "Ground", "defender": "Charizard"},
            )
            damage = tools.execute(
                "calculate_verified_damage",
                {
                    "side": "opponent",
                    "actor": "Garchomp",
                    "move": "Earthquake",
                    "target": "Sneasler",
                },
            )
            impossible_damage = tools.execute(
                "calculate_verified_damage",
                {
                    "side": "opponent",
                    "actor": "Garchomp",
                    "move": "Spore",
                    "target": "Sneasler",
                },
            )
            meta = tools.execute("lookup_meta", {"species": "Garchomp"})
        finally:
            calculator.close()

        self.assertEqual(3, move["result"]["entry"]["priority"])
        self.assertIn(
            "Earthquake", {entry["name"] for entry in learnset["result"]["moves"]}
        )
        self.assertEqual(0, matchup["result"]["multiplier"])
        self.assertTrue(damage["result"]["learnset_verified"])
        self.assertEqual("Earthquake", damage["result"]["estimate"]["move"])
        self.assertEqual(
            16,
            sum(
                roll["weight"]
                for roll in damage["result"]["estimate"]["scenarios"][0]["rolls_percent"]
            ),
        )
        self.assertFalse(impossible_damage["ok"])
        self.assertFalse(impossible_damage["fabricated"])
        self.assertTrue(meta["result"]["found"])
        self.assertFalse(meta["result"]["mechanics_authority"])

    def test_unknown_tool_request_fails_closed(self) -> None:
        state, beliefs, recommendation = fixture()
        calculator = ShowdownCalculator(ROOT)
        tools = BattleKnowledgeTools(
            calculator=calculator,
            meta=MetaRepository(ROOT),
            state=state,
            beliefs=beliefs,
            recommendation=recommendation,
        )
        unknown_call = {
            "output": [
                {
                    "type": "function_call",
                    "call_id": "call-unknown",
                    "name": "invent_damage",
                    "arguments": "{}",
                }
            ]
        }
        brain = CodexBattleBrain(api_key="test-key", transport=lambda _: unknown_call)
        try:
            result = brain.decide(
                state=state,
                beliefs=beliefs,
                recommendation=recommendation,
                events=[],
                knowledge_tools=tools,
            )
        finally:
            calculator.close()

        self.assertEqual("fallback", result["brain"]["status"])
        self.assertEqual("ValueError", result["brain"]["reason"])
        self.assertEqual("deterministic", result["brain"]["decision_source"])

    def test_unconfigured_brain_preserves_deterministic_recommendation(self) -> None:
        state, beliefs, recommendation = fixture()
        brain = CodexBattleBrain(api_key="", model="gpt-5.6-sol")

        result = brain.decide(
            state=state,
            beliefs=beliefs,
            recommendation=recommendation,
            events=[],
        )

        self.assertEqual(recommendation.primary.label, result["primary"]["label"])
        self.assertEqual("fallback", result["brain"]["status"])
        self.assertEqual("not_configured", result["brain"]["reason"])
        self.assertEqual("deterministic", result["brain"]["decision_source"])

    def test_unknown_candidate_from_model_fails_closed(self) -> None:
        state, beliefs, recommendation = fixture()
        brain = CodexBattleBrain(
            api_key="test-key",
            transport=lambda _: response_for("invented-action"),
        )

        result = brain.decide(
            state=state,
            beliefs=beliefs,
            recommendation=recommendation,
            events=[],
        )

        self.assertEqual(recommendation.primary.label, result["primary"]["label"])
        self.assertEqual("fallback", result["brain"]["status"])
        self.assertEqual("ValueError", result["brain"]["reason"])

    def test_overallocated_opponent_probabilities_fail_closed(self) -> None:
        state, beliefs, recommendation = fixture()
        payload = response_for()
        decision = json.loads(payload["output"][0]["content"][0]["text"])
        decision["opponent_plan"][0]["probability"] = 0.9
        decision["opponent_plan"][1]["probability"] = 0.9
        payload["output"][0]["content"][0]["text"] = json.dumps(decision)
        brain = CodexBattleBrain(api_key="test-key", transport=lambda _: payload)

        result = brain.decide(
            state=state,
            beliefs=beliefs,
            recommendation=recommendation,
            events=[],
        )

        self.assertEqual("fallback", result["brain"]["status"])
        self.assertEqual("deterministic", result["brain"]["decision_source"])

    def test_model_refusal_fails_closed(self) -> None:
        state, beliefs, recommendation = fixture()
        refusal = {
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "refusal", "refusal": "cannot comply"}],
                }
            ]
        }
        brain = CodexBattleBrain(api_key="test-key", transport=lambda _: refusal)

        result = brain.decide(
            state=state,
            beliefs=beliefs,
            recommendation=recommendation,
            events=[],
        )

        self.assertEqual(recommendation.primary.label, result["primary"]["label"])
        self.assertEqual("fallback", result["brain"]["status"])
        self.assertEqual("ValueError", result["brain"]["reason"])

    def test_invalid_reasoning_effort_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "OPENAI_REASONING_EFFORT"):
            CodexBattleBrain(api_key="test", reasoning_effort="infinite")


if __name__ == "__main__":
    unittest.main()
