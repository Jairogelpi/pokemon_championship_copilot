from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "battle-engine" / "src"))

from champions_copilot.actions import generate_legal_joint_actions  # noqa: E402
from champions_copilot.beliefs import BeliefState  # noqa: E402
from champions_copilot.decision import recommend_actions, recommend_team_preview  # noqa: E402
from champions_copilot.events import (  # noqa: E402
    BattleEvent,
    EventValidationError,
    apply_event,
    replay,
)
from champions_copilot.mechanics import calculate_damage_range, effective_speed  # noqa: E402
from champions_copilot.team import create_match  # noqa: E402


OPPONENT = ["Charizard", "Garchomp", "Kingambit", "Aerodactyl", "Sylveon", "Farigiraf"]


def battle():
    preview = recommend_team_preview(OPPONENT)
    return create_match(OPPONENT, preview["selected"], preview["lead"], match_id="test-match")


class EventEngineTests(unittest.TestCase):
    def test_replay_is_byte_equivalent(self) -> None:
        initial = battle()
        events = [
            BattleEvent("event-1", "hp_changed", {"side": "opponent", "pokemon": "charizard", "hp": 61}, "t1"),
            BattleEvent("event-2", "move_used", {"side": "opponent", "pokemon": "charizard", "move": "Heat Wave"}, "t2"),
            BattleEvent("event-3", "turn_started", {"turn": 2}, "t3"),
        ]
        incremental = initial
        for event in events:
            incremental = apply_event(incremental, event)
        self.assertEqual(incremental.to_dict(), replay(initial, events).to_dict())

    def test_fainted_pokemon_cannot_switch_in(self) -> None:
        state = battle()
        bench_id = state.player.bench[0]
        state = apply_event(
            state,
            BattleEvent("faint", "faint", {"side": "player", "pokemon": bench_id}, "t1"),
        )
        with self.assertRaises(EventValidationError):
            apply_event(
                state,
                BattleEvent(
                    "switch",
                    "switch",
                    {"side": "player", "out": state.player.active[0], "in": bench_id},
                    "t2",
                ),
            )

    def test_mega_is_unique_per_side(self) -> None:
        state = battle()
        first, second = state.player.active
        state = apply_event(
            state,
            BattleEvent("mega-1", "mega_evolved", {"side": "player", "pokemon": first}, "t1"),
        )
        with self.assertRaises(EventValidationError):
            apply_event(
                state,
                BattleEvent("mega-2", "mega_evolved", {"side": "player", "pokemon": second}, "t2"),
            )

    def test_turn_resets_protect_and_decrements_conditions(self) -> None:
        state = battle()
        active = state.player.active[0]
        state.player.roster[active].protected = True
        state.player.side_conditions["tailwind"] = 3
        state = apply_event(state, BattleEvent("turn", "turn_started", {"turn": 2}, "t1"))
        self.assertFalse(state.player.roster[active].protected)
        self.assertEqual(2, state.player.side_conditions["tailwind"])


class BeliefAndDecisionTests(unittest.TestCase):
    def test_beliefs_remain_normalized_after_observation(self) -> None:
        state = battle()
        beliefs = BeliefState.from_battle(state)
        event = BattleEvent(
            "move",
            "move_used",
            {"side": "opponent", "pokemon": "charizard", "move": "Protect"},
            "t1",
        )
        next_state = apply_event(state, event)
        beliefs.observe(next_state, event)
        for belief in beliefs.opponent.values():
            self.assertAlmostEqual(1.0, sum(belief.action_categories.values()))
        self.assertAlmostEqual(
            1.0, sum(belief.mega_probability for belief in beliefs.opponent.values())
        )

    def test_joint_actions_never_double_switch_to_same_slot(self) -> None:
        state = battle()
        for action in generate_legal_joint_actions(state):
            switches = [single.switch_to for single in action.actions if single.kind == "switch"]
            self.assertEqual(len(switches), len(set(switches)))

    def test_mega_evolution_is_a_legal_once_per_turn_action_branch(self) -> None:
        state = create_match(
            OPPONENT,
            ["froslass", "sneasler", "dragonite", "garchomp"],
            ["froslass", "sneasler"],
            match_id="mega-action-fixture",
        )
        actions = generate_legal_joint_actions(state)
        self.assertTrue(
            any(
                single.actor == "froslass" and single.mega
                for action in actions
                for single in action.actions
            )
        )
        self.assertTrue(
            all(sum(single.mega for single in action.actions) <= 1 for action in actions)
        )

    def test_joint_opponent_responses_cover_both_active_slots(self) -> None:
        state = battle()
        beliefs = BeliefState.from_battle(state)
        responses = beliefs.active_joint_response_distribution(state)
        self.assertEqual(36, len(responses))
        self.assertAlmostEqual(1.0, sum(responses.values()))
        self.assertTrue(any("other" in scenario for scenario in responses))

    def test_recommendation_is_explainable_and_deterministic(self) -> None:
        state = battle()
        beliefs = BeliefState.from_battle(state)
        first = recommend_actions(state, beliefs).to_dict()
        second = recommend_actions(state, beliefs).to_dict()
        self.assertEqual(first, second)
        self.assertTrue(first["primary"]["label"])
        self.assertEqual(3, len(first["alternatives"]))
        self.assertIn("lower_tail_utility", first["primary"]["score"])
        self.assertEqual("SHOWDOWN_UNAVAILABLE", first["validation_status"])
        self.assertEqual(False, first["calculator"]["available"])
        self.assertEqual(36, first["response_model"]["scenarios_evaluated"])


class MechanicsTests(unittest.TestCase):
    def test_damage_range_has_sixteen_ordered_rolls(self) -> None:
        result = calculate_damage_range(level=50, power=100, attack=150, defense=120, modifier=1.5)
        self.assertEqual(16, len(result.rolls))
        self.assertEqual(result.minimum, result.rolls[0])
        self.assertEqual(result.maximum, result.rolls[-1])

    def test_effective_speed_applies_stage_tailwind_and_paralysis(self) -> None:
        self.assertEqual(
            150,
            effective_speed(raw_speed=100, stage=1, tailwind=True, paralysis=True),
        )


if __name__ == "__main__":
    unittest.main()
