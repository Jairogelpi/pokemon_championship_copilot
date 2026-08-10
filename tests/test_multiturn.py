from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "battle-engine" / "src"))
sys.path.insert(0, str(ROOT / "services" / "api" / "src"))

from champions_api.meta import MetaRepository  # noqa: E402
from champions_api.codex_brain import CodexBattleBrain  # noqa: E402
from champions_api.multiturn import (  # noqa: E402
    MultiTurnConfig,
    MultiTurnPlanner,
    PlanningState,
    VerifiedTurnResolver,
)
from champions_api.opponent import build_response_model  # noqa: E402
from champions_api.showdown import ShowdownCalculator  # noqa: E402
from champions_api.showdown_planner import calculate_turn_damage  # noqa: E402
from champions_api.service import AppService  # noqa: E402
from champions_copilot.actions import JointAction, SingleAction  # noqa: E402
from champions_copilot.beliefs import BeliefState  # noqa: E402
from champions_copilot.decision import recommend_actions, recommend_team_preview  # noqa: E402
from champions_copilot.team import create_match  # noqa: E402


OPPONENT = ["Charizard", "Garchomp", "Kingambit", "Aerodactyl", "Sylveon", "Farigiraf"]


class MultiTurnTransitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.calculator = ShowdownCalculator(ROOT)
        cls.meta = MetaRepository(ROOT)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.calculator.close()

    def position(self):
        preview = recommend_team_preview(OPPONENT)
        state = create_match(
            OPPONENT,
            preview["selected"],
            preview["lead"],
            match_id="verified-multiturn-fixture",
        )
        return state, BeliefState.from_battle(state)

    def test_sampled_turns_are_deterministic_reachable_damage_states(self) -> None:
        state, _ = self.position()
        left, right = state.player.active
        self.assertEqual("Sneasler", state.player.roster[left].name)
        self.assertEqual("Kingambit", state.player.roster[right].name)
        charizard = state.opponent.active[0]
        action = JointAction(
            (
                SingleAction(left, "move", "Close Combat", charizard),
                SingleAction(right, "move", "Protect", right),
            )
        )
        response = {"label": "opponents do not damage", "probability": 1.0, "actions": []}
        config = MultiTurnConfig(samples_per_response=4)
        resolver = VerifiedTurnResolver(self.calculator, config)
        first = resolver.resolve_samples(
            PlanningState.initial(state), action, response
        )
        second = VerifiedTurnResolver(self.calculator, config).resolve_samples(
            PlanningState.initial(state), action, response
        )

        self.assertEqual(
            [outcome.next_state.key() for outcome in first],
            [outcome.next_state.key() for outcome in second],
        )
        self.assertAlmostEqual(1.0, sum(outcome.probability for outcome in first))
        for outcome in first:
            planned = outcome.next_state
            self.assertIsNone(planned.uncertainty)
            self.assertEqual(2, planned.battle.turn)
            self.assertLess(planned.battle.opponent.roster[charizard].hp, 100)
            self.assertEqual(-1, planned.battle.player.roster[left].boosts["def"])
            self.assertEqual(-1, planned.battle.player.roster[left].boosts["spd"])
            trace = " | ".join(planned.trace)
            self.assertRegex(trace, r"Close Combat→Charizard: [0-9.]+%")
            self.assertIn(
                f"opponent:{charizard}",
                dict(planned.hidden_profiles),
            )
            profile = json.loads(
                dict(planned.hidden_profiles)[f"opponent:{charizard}"]
            )
            self.assertTrue(profile["ability"])
            self.assertIn("evs", profile)

        locked_state = first[0].next_state
        locked_profile = dict(locked_state.hidden_profiles)
        continuations = resolver.resolve_samples(locked_state, action, response)
        self.assertTrue(continuations)
        self.assertTrue(
            all(
                dict(outcome.next_state.hidden_profiles) == locked_profile
                for outcome in continuations
            )
        )

    def test_focus_sash_survival_and_spread_protect_are_resolved(self) -> None:
        state, _ = self.position()
        sneasler, kingambit = state.player.active
        charizard, garchomp = state.opponent.active
        action = JointAction(
            (
                SingleAction(sneasler, "move", "Fake Out", charizard),
                SingleAction(kingambit, "move", "Protect", kingambit),
            )
        )
        response = {
            "label": "Garchomp Earthquake",
            "probability": 1.0,
            "actions": [
                {
                    "actor": garchomp,
                    "kind": "move",
                    "move": "Earthquake",
                    "target": "players",
                    "category": "attack",
                    "move_category": "Physical",
                }
            ],
        }
        outcomes = VerifiedTurnResolver(
            self.calculator, MultiTurnConfig(samples_per_response=8)
        ).resolve_samples(PlanningState.initial(state), action, response)

        sash_outcomes = [
            outcome
            for outcome in outcomes
            if "Focus Sash" in " | ".join(outcome.next_state.trace)
        ]
        self.assertTrue(sash_outcomes)
        for outcome in sash_outcomes:
            planned = outcome.next_state.battle
            self.assertGreater(planned.player.roster[sneasler].hp, 0)
            self.assertIsNone(planned.player.roster[sneasler].item)
            self.assertEqual(100, planned.player.roster[kingambit].hp)

    def test_earthquake_resolves_friendly_fire_against_the_actual_partner(self) -> None:
        opponent = [
            "Charizard",
            "Garchomp",
            "Kingambit",
            "Aerodactyl",
            "Sylveon",
            "Rotom-Wash",
        ]
        preview = recommend_team_preview(opponent)
        state = create_match(opponent, preview["selected"], preview["lead"])
        sneasler, garchomp = state.player.active
        charizard = state.opponent.active[0]
        action = JointAction(
            (
                SingleAction(sneasler, "move", "Fake Out", charizard),
                SingleAction(garchomp, "move", "Earthquake", "opponents"),
            )
        )
        outcomes = VerifiedTurnResolver(
            self.calculator, MultiTurnConfig(samples_per_response=2)
        ).resolve_samples(
            PlanningState.initial(state),
            action,
            {"label": "no opposing attacks", "probability": 1.0, "actions": []},
        )

        self.assertTrue(
            all(
                "Earthquake→Sneasler" in " | ".join(outcome.next_state.trace)
                for outcome in outcomes
            )
        )
        self.assertTrue(
            all(
                outcome.next_state.battle.player.roster[sneasler].hp < 100
                for outcome in outcomes
            )
        )

    def test_depth_two_search_is_live_bounded_and_never_called_exhaustive(self) -> None:
        state, beliefs = self.position()
        response_model = build_response_model(
            self.calculator, self.meta, state, beliefs
        )
        damage, threats, status = calculate_turn_damage(
            self.calculator,
            state,
            opponent_moves=response_model["damage_moves"],
        )
        baseline = recommend_actions(
            state,
            beliefs,
            damage_estimates=damage,
            incoming_threats=threats,
            calculator_status=status,
            concrete_response_model=response_model,
        )
        planner = MultiTurnPlanner(
            self.calculator,
            self.meta,
            MultiTurnConfig(
                enabled=True,
                depth=2,
                root_action_limit=2,
                future_action_limit=1,
                response_limit=2,
                samples_per_response=1,
                node_budget=300,
                time_budget_ms=30_000,
            ),
        )
        result = planner.plan(
            state=state,
            beliefs=beliefs,
            recommendation=baseline,
            response_model=response_model,
        )

        analysis = result.multi_turn
        self.assertEqual("ok", analysis["status"])
        self.assertEqual(2, analysis["completed_depth"])
        self.assertFalse(analysis["exhaustive_claim"])
        self.assertGreater(analysis["search"]["nodes_expanded"], 1)
        self.assertGreater(
            analysis["transition_telemetry"]["sampled_outcomes"], 0
        )
        self.assertIn("promotion_eligible", analysis)

    def test_service_carries_completed_multiturn_evidence_into_fallback_output(self) -> None:
        calculator = ShowdownCalculator(ROOT)
        meta = MetaRepository(ROOT)
        planner = MultiTurnPlanner(
            calculator,
            meta,
            MultiTurnConfig(
                enabled=True,
                depth=2,
                root_action_limit=1,
                future_action_limit=1,
                response_limit=1,
                samples_per_response=1,
                node_budget=200,
                time_budget_ms=30_000,
            ),
        )
        service = AppService(
            calculator=calculator,
            brain=CodexBattleBrain(api_key=""),
            multiturn=planner,
        )
        try:
            created = service.create_match({"opponent_team": OPPONENT})
        finally:
            service.close()

        recommendation = created["recommendation"]
        self.assertEqual(2, recommendation["multi_turn"]["completed_depth"])
        self.assertEqual("deterministic", recommendation["brain"]["decision_source"])
        self.assertFalse(recommendation["multi_turn"]["exhaustive_claim"])


if __name__ == "__main__":
    unittest.main()
