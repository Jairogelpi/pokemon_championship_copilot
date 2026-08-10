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
from champions_api.regulation import CurrentChampionsRegulation  # noqa: E402
from champions_api.showdown import ShowdownCalculator  # noqa: E402
from champions_api.showdown_planner import (  # noqa: E402
    calculate_canonical_damage,
    calculate_turn_damage,
)
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

    def test_exact_turn_enumerates_protect_success_and_failure_probability(self) -> None:
        state, _ = self.position()
        for pokemon_id in state.opponent.active:
            pokemon = state.opponent.roster[pokemon_id]
            abilities = self.calculator.lookup("species", pokemon.name)["entry"][
                "abilities"
            ]
            state.opponent.known_facts[pokemon_id] = {
                "evs": dict(pokemon.evs),
                "nature": pokemon.nature,
                "ability": pokemon.ability or next(iter(abilities.values())),
            }
        actor = state.player.active[0]
        planning = PlanningState.initial(state)
        planning = planning.__class__(
            battle=planning.battle,
            uncertainty=planning.uncertainty,
            trace=planning.trace,
            protect_chain=((f"player:{actor}", 1),),
            active_turns=planning.active_turns,
            hidden_profiles=planning.hidden_profiles,
            replacement_phase=planning.replacement_phase,
        )
        outcomes = VerifiedTurnResolver(
            self.calculator,
            MultiTurnConfig(samples_per_response=1),
        ).resolve_exact(
            planning,
            JointAction((SingleAction(actor, "move", "Protect", actor),)),
            {"label": "hold", "probability": 1.0, "actions": []},
        )

        self.assertEqual(2, len(outcomes))
        self.assertAlmostEqual(1.0, sum(row.probability for row in outcomes))
        successful = next(
            row for row in outcomes if f"{state.player.roster[actor].name} protected" in row.next_state.trace
        )
        failed = next(
            row for row in outcomes if "Protect failed" in " | ".join(row.next_state.trace)
        )
        self.assertAlmostEqual(1 / 3, successful.probability)
        self.assertAlmostEqual(2 / 3, failed.probability)

    def test_closed_current_endgame_promotes_terminal_tablebase_result(self) -> None:
        state, _ = self.position()
        player_id = "sneasler"
        opponent_id = "garchomp"
        for side_name, survivor in (("player", player_id), ("opponent", opponent_id)):
            side = state.side(side_name)
            for pokemon in side.roster.values():
                pokemon.fainted = pokemon.id != survivor
                pokemon.hp = 0 if pokemon.fainted else 1
            side.active = [survivor]
            side.bench = []
            side.selected = [survivor]
        player = state.player.roster[player_id]
        player.moves = ("Close Combat",)
        player.set_verified = True
        opponent = state.opponent.roster[opponent_id]
        opponent.moves = ("Dragon Claw",)
        opponent.revealed_moves = ["Dragon Claw"]
        opponent.set_verified = True
        opponent.item = None
        opponent.ability = "Sand Veil"
        opponent.nature = "Jolly"
        opponent.evs = {"atk": 252, "spe": 252, "spd": 4}
        opponent.ivs = {stat: 31 for stat in ("hp", "atk", "def", "spa", "spd", "spe")}
        state.opponent.known_facts[opponent_id] = {
            "item": None,
            "ability": opponent.ability,
            "nature": opponent.nature,
            "evs": dict(opponent.evs),
            "ivs": dict(opponent.ivs),
        }
        beliefs = BeliefState.from_battle(state)
        baseline = recommend_actions(state, beliefs)

        solved = MultiTurnPlanner(
            self.calculator,
            self.meta,
            MultiTurnConfig(
                enabled=False,
                endgame_time_budget_ms=5_000,
                endgame_max_states=1_000,
                endgame_max_chance_branches=10_000,
                endgame_max_paths_per_transition=10_000,
            ),
        ).plan(
            state=state,
            beliefs=beliefs,
            recommendation=baseline,
            response_model={"responses": []},
        )

        self.assertEqual(
            "EXHAUSTIVE_CURRENT_CHAMPIONS_ENDGAME", solved.validation_status
        )
        self.assertTrue(solved.multi_turn["exhaustive_claim"])
        self.assertEqual("solved", solved.multi_turn["status"])
        self.assertEqual(1.0, solved.multi_turn["best"]["win_probability"])
        self.assertIn("Close Combat", solved.primary.label)
        self.assertEqual((solved.primary,), solved.candidate_catalog)

    def test_mega_branch_applies_current_form_stats_ability_and_weather(self) -> None:
        state = create_match(
            OPPONENT,
            ["froslass", "sneasler", "dragonite", "garchomp"],
            ["froslass", "sneasler"],
            match_id="verified-mega-fixture",
        )
        charizard = state.opponent.active[0]
        action = JointAction(
            (
                SingleAction("froslass", "move", "Shadow Ball", charizard, mega=True),
                SingleAction("sneasler", "move", "Protect", "sneasler"),
            )
        )
        response = {"label": "no response", "probability": 1.0, "actions": []}
        resolver = VerifiedTurnResolver(
            self.calculator,
            MultiTurnConfig(samples_per_response=1),
            CurrentChampionsRegulation(ROOT),
        )
        outcome = resolver.resolve_samples(
            PlanningState.initial(state), action, response
        )[0].next_state
        froslass = outcome.battle.player.roster["froslass"]
        self.assertTrue(froslass.mega_evolved)
        self.assertEqual("Mega Froslass", froslass.battle_form)
        self.assertEqual("Snow Warning", froslass.ability)
        self.assertEqual(140, froslass.mechanics_override["baseStats"]["spa"])
        self.assertEqual("snow", outcome.battle.field.weather)

    def test_current_response_model_branches_and_resolves_opponent_mega(self) -> None:
        state, beliefs = self.position()
        model = build_response_model(self.calculator, self.meta, state, beliefs)
        charizard = state.opponent.active[0]
        garchomp = state.opponent.active[1]
        response = next(
            row
            for row in model["responses"]
            if any(
                reply.get("actor") == charizard
                and reply.get("mega_form") == "Mega Charizard Y"
                and reply.get("move") == "Protect"
                for reply in row.get("actions", [])
            )
            and any(
                reply.get("actor") == garchomp and reply.get("move") == "Protect"
                for reply in row.get("actions", [])
            )
        )
        left, right = state.player.active
        action = JointAction(
            (
                SingleAction(left, "move", "Protect", left),
                SingleAction(right, "move", "Protect", right),
            )
        )
        resolver = VerifiedTurnResolver(
            self.calculator,
            MultiTurnConfig(samples_per_response=1),
            self.meta.regulation,
        )
        outcome = resolver.resolve_samples(
            PlanningState.initial(state), action, response
        )[0].next_state
        evolved = outcome.battle.opponent.roster[charizard]
        self.assertEqual("Mega Charizard Y", evolved.battle_form)
        self.assertEqual("Drought", evolved.ability)
        self.assertEqual("sun", outcome.battle.field.weather)
        self.assertEqual(159, evolved.mechanics_override["baseStats"]["spa"])

    def test_equal_power_multi_hit_moves_roll_critical_checks_per_hit(self) -> None:
        preview = recommend_team_preview(OPPONENT)
        state = create_match(
            OPPONENT,
            preview["selected"],
            preview["lead"],
            opponent_lead=["aerodactyl", "charizard"],
            match_id="per-hit-critical-fixture",
        )
        left, right = state.player.active
        incoming = state.player.bench[0]
        action = JointAction(
            (
                SingleAction(left, "switch", switch_to=incoming),
                SingleAction(right, "move", "Protect", right),
            )
        )
        response = {
            "label": "Dual Wingbeat plus Protect",
            "probability": 1.0,
            "actions": [
                {
                    "actor": "aerodactyl",
                    "kind": "move",
                    "move": "Dual Wingbeat",
                    "target": left,
                },
                {
                    "actor": "charizard",
                    "kind": "move",
                    "move": "Protect",
                    "target": "charizard",
                },
            ],
        }
        resolver = VerifiedTurnResolver(
            self.calculator,
            MultiTurnConfig(samples_per_response=1),
            self.meta.regulation,
        )
        resolver.resolve_samples(PlanningState.initial(state), action, response)
        self.assertEqual(2, resolver.telemetry["independent_per_hit_critical_checks"])

    def test_triple_axel_uses_per_hit_accuracy_criticals_and_escalating_power(self) -> None:
        state = create_match(
            OPPONENT,
            ["froslass", "sneasler", "basculegion", "dragonite"],
            ["froslass", "sneasler"],
            match_id="triple-axel-fixture",
        )
        actor, partner = state.player.active
        target = state.opponent.active[0]
        action = JointAction(
            (
                SingleAction(actor, "move", "Triple Axel", target),
                SingleAction(partner, "move", "Protect", partner),
            )
        )
        resolver = VerifiedTurnResolver(
            self.calculator, MultiTurnConfig(samples_per_response=24)
        )
        outcomes = resolver.resolve_samples(
            PlanningState.initial(state),
            action,
            {"label": "hold", "probability": 1.0, "actions": []},
        )

        self.assertTrue(outcomes)
        self.assertTrue(all(row.next_state.uncertainty is None for row in outcomes))
        traces = [" | ".join(row.next_state.trace) for row in outcomes]
        self.assertTrue(any("escalating-power" in trace for trace in traces))
        self.assertGreaterEqual(
            resolver.telemetry["independent_per_hit_critical_checks"], 24
        )

    def test_showdown_lookup_exposes_multiaccuracy_mechanics(self) -> None:
        triple_axel = self.calculator.lookup("move", "Triple Axel")["entry"]
        population_bomb = self.calculator.lookup("move", "Population Bomb")["entry"]

        self.assertTrue(triple_axel["multiaccuracy"])
        self.assertEqual(3, triple_axel["multihit"])
        self.assertTrue(population_bomb["multiaccuracy"])
        self.assertEqual(10, population_bomb["multihit"])

    def test_loaded_dice_population_bomb_uses_four_to_ten_hit_branch(self) -> None:
        opponents = [
            "Maushold",
            "Charizard",
            "Garchomp",
            "Kingambit",
            "Sylveon",
            "Farigiraf",
        ]
        state = create_match(
            opponents,
            ["froslass", "sneasler", "basculegion", "dragonite"],
            ["froslass", "sneasler"],
            opponent_lead=["maushold", "charizard"],
            match_id="loaded-dice-population-bomb-fixture",
        )
        maushold = state.opponent.roster["maushold"]
        maushold.item = "Loaded Dice"
        maushold.ability = "Technician"
        state.opponent.known_facts["maushold"] = {
            "item": maushold.item,
            "ability": maushold.ability,
            "nature": "Jolly",
            "evs": {"atk": 252, "spe": 252},
        }
        target = state.player.active[1]
        response = {
            "label": "Loaded Dice Population Bomb",
            "probability": 1.0,
            "actions": [
                {
                    "actor": "maushold",
                    "kind": "move",
                    "move": "Population Bomb",
                    "target": target,
                }
            ],
        }
        outcomes = VerifiedTurnResolver(
            self.calculator, MultiTurnConfig(samples_per_response=16)
        ).resolve_samples(PlanningState.initial(state), JointAction(()), response)

        self.assertTrue(outcomes)
        self.assertTrue(all(row.next_state.uncertainty is None for row in outcomes))
        hit_traces = [
            " | ".join(row.next_state.trace)
            for row in outcomes
            if "Population Bomb→" in " | ".join(row.next_state.trace)
        ]
        self.assertTrue(hit_traces)
        self.assertTrue(
            all(
                any(f":{hits}-hits:" in trace for hits in range(4, 11))
                for trace in hit_traces
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

    def test_forced_replacement_is_reachable_and_does_not_advance_the_turn(self) -> None:
        state, _ = self.position()
        outgoing = state.opponent.active[0]
        incoming = next(
            pokemon_id
            for pokemon_id in state.opponent.bench
            if state.opponent.roster[pokemon_id].name == "Kingambit"
        )
        state.opponent.roster[outgoing].hp = 0
        state.opponent.roster[outgoing].fainted = True
        planning = PlanningState.initial(state)

        outcomes = VerifiedTurnResolver(
            self.calculator, MultiTurnConfig(samples_per_response=2)
        ).resolve_samples(
            planning,
            JointAction(()),
            {
                "replacement": True,
                "actions": [
                    {
                        "actor": outgoing,
                        "kind": "switch",
                        "switch_to": incoming,
                    }
                ],
            },
        )

        self.assertTrue(planning.replacement_phase)
        self.assertTrue(outcomes)
        for outcome in outcomes:
            next_state = outcome.next_state
            self.assertIsNone(next_state.uncertainty)
            self.assertFalse(next_state.replacement_phase)
            self.assertEqual(state.turn, next_state.battle.turn)
            self.assertIn(incoming, next_state.battle.opponent.active)
            self.assertNotIn(outgoing, next_state.battle.opponent.active)
            self.assertIn("forced replacements resolved", next_state.trace)

    def test_toxic_counter_and_leftovers_persist_across_future_turns(self) -> None:
        state, _ = self.position()
        pokemon_id = state.player.active[0]
        pokemon = state.player.roster[pokemon_id]
        pokemon.status = "toxic"
        pokemon.status_counter = 0
        pokemon.item = "Leftovers"
        resolver = VerifiedTurnResolver(
            self.calculator, MultiTurnConfig(samples_per_response=1)
        )
        response = {"label": "hold", "probability": 1.0, "actions": []}

        first = resolver.resolve_samples(
            PlanningState.initial(state), JointAction(()), response
        )[0].next_state
        second = resolver.resolve_samples(first, JointAction(()), response)[0].next_state

        self.assertEqual(1, first.battle.player.roster[pokemon_id].status_counter)
        self.assertEqual(100, first.battle.player.roster[pokemon_id].hp)
        self.assertEqual(2, second.battle.player.roster[pokemon_id].status_counter)
        self.assertAlmostEqual(93.75, second.battle.player.roster[pokemon_id].hp)

    def test_canonical_critical_hit_uses_the_pinned_damage_engine(self) -> None:
        state, _ = self.position()
        actor = state.player.active[0]
        target = state.opponent.active[0]
        normal = calculate_canonical_damage(
            self.calculator,
            state,
            side="player",
            actor_id=actor,
            move="Close Combat",
            target_id=target,
            target_side="opponent",
        )
        critical = calculate_canonical_damage(
            self.calculator,
            state,
            side="player",
            actor_id=actor,
            move="Close Combat",
            target_id=target,
            target_side="opponent",
            critical=True,
        )

        normal_expected = normal["estimate"]["scenarios"][0]["expected_percent"]
        critical_expected = critical["estimate"]["scenarios"][0]["expected_percent"]
        self.assertGreater(critical_expected, normal_expected)

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
        self.assertGreaterEqual(analysis["verified_frontier_fraction"], 0.90)
        self.assertEqual(1.0, analysis["declared_mechanics_resolved_fraction"])
        self.assertTrue(analysis["promotion_eligible"])

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
