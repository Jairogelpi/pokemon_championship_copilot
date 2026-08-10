from __future__ import annotations

import sys
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "battle-engine" / "src"))

from champions_copilot.search import (  # noqa: E402
    ChanceOutcome,
    EndgameCycleDetected,
    ExhaustiveEndgameSolver,
    RiskAwareExpectiminimax,
    SearchBudgetExhausted,
    SearchConfig,
    WeightedResponse,
)


@dataclass(frozen=True)
class Edge:
    action: str
    response: str
    probability: float
    outcome: str
    outcome_probability: float
    next_state: str
    reward: float


class TreeGame:
    def __init__(
        self,
        edges: dict[str, list[Edge]],
        evaluations: dict[str, float],
        terminals: dict[str, float] | None = None,
    ) -> None:
        self.edges = edges
        self.evaluations = evaluations
        self.terminals = terminals or {}

    def state_key(self, state: Any) -> str:
        return str(state)

    def terminal_value(self, state: Any) -> float | None:
        return self.terminals.get(str(state))

    def evaluate(self, state: Any) -> float:
        return self.evaluations[str(state)]

    def player_actions(self, state: Any) -> Sequence[Any]:
        return sorted({edge.action for edge in self.edges.get(str(state), [])})

    def action_label(self, state: Any, action: Any) -> str:
        return str(action)

    def opponent_responses(
        self, state: Any, action: Any
    ) -> Sequence[WeightedResponse]:
        matching = [
            edge
            for edge in self.edges.get(str(state), [])
            if edge.action == action
        ]
        responses: dict[str, float] = {}
        for edge in matching:
            responses[edge.response] = edge.probability
        return [
            WeightedResponse(response, probability)
            for response, probability in sorted(responses.items())
        ]

    def chance_outcomes(
        self,
        state: Any,
        action: Any,
        response: WeightedResponse,
    ) -> Sequence[ChanceOutcome]:
        return [
            ChanceOutcome(
                id=edge.outcome,
                probability=edge.outcome_probability,
                next_state=edge.next_state,
                immediate_reward=edge.reward,
            )
            for edge in self.edges.get(str(state), [])
            if edge.action == action and edge.response == response.id
        ]


class RiskAwareSearchTests(unittest.TestCase):
    def test_zero_cost_replacement_transition_does_not_consume_turn_depth(self) -> None:
        class ReplacementGame(TreeGame):
            def transition_depth_cost(
                self,
                state: Any,
                action: Any,
                response: WeightedResponse,
                outcome: ChanceOutcome,
            ) -> int:
                del action, response, outcome
                return 0 if state == "replacement" else 1

        game = ReplacementGame(
            edges={
                "replacement": [
                    Edge("send reserve", "reply", 1, "ready", 1, "turn", 0)
                ],
                "turn": [Edge("attack", "reply", 1, "ko", 1, "win", 10)],
            },
            evaluations={"turn": 0},
            terminals={"win": 100},
        )
        result = RiskAwareExpectiminimax(
            SearchConfig(max_depth=1, node_budget=20, discount=1)
        ).search(game, "replacement")

        self.assertEqual("send reserve", result.best.action)
        self.assertEqual(2, len(result.best.principal_line))
        self.assertEqual("attack", result.best.principal_line[1].action)

    def test_depth_two_can_reverse_a_greedy_choice(self) -> None:
        game = TreeGame(
            edges={
                "root": [
                    Edge("greedy", "reply", 1, "hit", 1, "bad-future", 9),
                    Edge("setup", "reply", 1, "position", 1, "good-future", 2),
                ],
                "bad-future": [
                    Edge("only", "reply", 1, "resolved", 1, "loss", -20),
                ],
                "good-future": [
                    Edge("convert", "reply", 1, "resolved", 1, "win", 20),
                ],
            },
            evaluations={"bad-future": 0, "good-future": 0},
            terminals={"loss": -100, "win": 100},
        )
        shallow = RiskAwareExpectiminimax(
            SearchConfig(max_depth=1, node_budget=100, discount=1)
        ).search(game, "root")
        deep = RiskAwareExpectiminimax(
            SearchConfig(max_depth=2, node_budget=100, discount=1)
        ).search(game, "root")

        self.assertEqual("greedy", shallow.best.action)
        self.assertEqual("setup", deep.best.action)
        self.assertEqual(2, deep.stats.completed_depth)
        self.assertEqual(2, len(deep.best.principal_line))

    def test_lower_tail_prefers_the_safer_equal_mean_action(self) -> None:
        game = TreeGame(
            edges={
                "root": [
                    Edge("risky", "good", 0.8, "good", 1, "good", 25),
                    Edge("risky", "bad", 0.2, "bad", 1, "bad", -100),
                    Edge("safe", "steady", 1.0, "steady", 1, "steady", 0),
                ]
            },
            evaluations={"good": 0, "bad": 0, "steady": 0},
        )
        result = RiskAwareExpectiminimax(
            SearchConfig(
                max_depth=1,
                node_budget=100,
                discount=1,
                expected_weight=0.5,
                lower_tail_weight=0.5,
                lower_tail_mass=0.2,
            )
        ).search(game, "root")

        self.assertEqual("safe", result.best.action)
        risky = next(row for row in result.alternatives if row.action == "risky")
        self.assertEqual(0, risky.expected_value)
        self.assertEqual(-100, risky.lower_tail_value)

    def test_transposition_cache_reuses_equal_future_states(self) -> None:
        game = TreeGame(
            edges={
                "root": [
                    Edge("left", "reply", 1, "same", 1, "shared", 0),
                    Edge("right", "reply", 1, "same", 1, "shared", 0),
                ]
            },
            evaluations={"shared": 7},
        )
        result = RiskAwareExpectiminimax(
            SearchConfig(max_depth=1, node_budget=10)
        ).search(game, "root")

        self.assertGreaterEqual(result.stats.cache_hits, 1)
        self.assertEqual("left", result.best.action)

    def test_catastrophic_chance_branch_is_not_hidden_by_response_average(self) -> None:
        game = TreeGame(
            edges={
                "root": [
                    Edge("gamble", "attack", 1, "normal", 0.9, "normal", 20),
                    Edge("gamble", "attack", 1, "critical", 0.1, "critical", -100),
                    Edge("safe", "attack", 1, "steady", 1, "steady", 0),
                ]
            },
            evaluations={"normal": 0, "critical": 0, "steady": 0},
        )
        result = RiskAwareExpectiminimax(
            SearchConfig(
                max_depth=1,
                node_budget=100,
                discount=1,
                expected_weight=0.5,
                lower_tail_weight=0.5,
                lower_tail_mass=0.1,
                catastrophic_threshold=-50,
            )
        ).search(game, "root")

        gamble = next(row for row in result.alternatives if row.action == "gamble")
        self.assertEqual("safe", result.best.action)
        self.assertAlmostEqual(0.1, gamble.catastrophic_probability)
        self.assertEqual(-100, gamble.lower_tail_value)

    def test_incomplete_deeper_iteration_returns_last_complete_depth(self) -> None:
        game = TreeGame(
            edges={
                "root": [Edge("only", "reply", 1, "next", 1, "middle", 1)],
                "middle": [Edge("only", "reply", 1, "next", 1, "leaf", 1)],
            },
            evaluations={"middle": 5, "leaf": 10},
        )
        result = RiskAwareExpectiminimax(
            SearchConfig(max_depth=2, node_budget=3)
        ).search(game, "root")

        self.assertEqual(1, result.stats.completed_depth)
        self.assertEqual("node_budget", result.stats.cutoff_reason)
        self.assertEqual("only", result.best.action)

    def test_budget_too_small_for_depth_one_fails_closed(self) -> None:
        game = TreeGame(
            edges={
                "root": [Edge("only", "reply", 1, "next", 1, "leaf", 0)],
            },
            evaluations={"leaf": 1},
        )
        with self.assertRaises(SearchBudgetExhausted):
            RiskAwareExpectiminimax(
                SearchConfig(max_depth=1, node_budget=1)
            ).search(game, "root")

    def test_negative_response_probability_is_rejected(self) -> None:
        game = TreeGame(
            edges={
                "root": [Edge("only", "invalid", -1, "next", 1, "leaf", 0)],
            },
            evaluations={"leaf": 1},
        )
        with self.assertRaisesRegex(ValueError, "negative probability"):
            RiskAwareExpectiminimax(SearchConfig()).search(game, "root")


class ExhaustiveEndgameTests(unittest.TestCase):
    def test_closes_every_chance_branch_to_terminal_states(self) -> None:
        game = TreeGame(
            edges={
                "root": [
                    Edge("attack", "best defence", 1, "hit", 0.75, "win", 0),
                    Edge("attack", "best defence", 1, "miss", 0.25, "loss", 0),
                    Edge("wait", "best defence", 1, "draw", 1, "draw", 0),
                ],
            },
            evaluations={},
            terminals={"win": 100, "draw": 0, "loss": -100},
        )

        result = ExhaustiveEndgameSolver(time_budget_ms=None).solve(game, "root")

        self.assertTrue(result.to_dict()["exhaustive_claim"])
        self.assertEqual("attack", result.best.action)
        self.assertEqual(50, result.best.value.expected_utility)
        self.assertEqual(0.75, result.best.value.win_probability)
        self.assertEqual(0.25, result.best.value.loss_probability)
        self.assertEqual(3, result.stats.chance_branches)

    def test_opponent_responses_are_adversarial_not_probability_averaged(self) -> None:
        game = TreeGame(
            edges={
                "root": [
                    Edge("greedy", "allow", 0.99, "win", 1, "win", 0),
                    Edge("greedy", "counter", 0.01, "loss", 1, "loss", 0),
                    Edge("forced", "only", 1, "draw", 1, "draw", 0),
                ]
            },
            evaluations={},
            terminals={"win": 100, "draw": 0, "loss": -100},
        )

        result = ExhaustiveEndgameSolver(time_budget_ms=None).solve(game, "root")

        self.assertEqual("forced", result.best.action)
        greedy = next(row for row in result.alternatives if row.action == "greedy")
        self.assertEqual("counter", greedy.worst_response)
        self.assertEqual(-100, greedy.value.expected_utility)

    def test_reachable_cycle_fails_closed(self) -> None:
        game = TreeGame(
            edges={
                "root": [Edge("protect", "protect", 1, "repeat", 1, "root", 0)]
            },
            evaluations={},
        )

        with self.assertRaises(EndgameCycleDetected):
            ExhaustiveEndgameSolver(time_budget_ms=None).solve(game, "root")


if __name__ == "__main__":
    unittest.main()
