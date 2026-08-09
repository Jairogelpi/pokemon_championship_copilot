# Pokémon Champions Battle Copilot

An unofficial, stateful battle assistant for Pokémon Champions doubles.

The project is built around one core rule: language models may interpret and
explain a battle, but they are not the source of truth for battle state,
mechanics, legality, damage, or speed.

## North-star goal

Build a decision system whose play quality can be demonstrated at the level of
a 2,000-point Master player for its supported team and regulation.

This is not a promise to predict every move. Pokémon contains hidden
information, simultaneous choices, novel sets, and randomness. The system must
instead maintain calibrated beliefs about the opponent, search multiple turns,
and select actions that remain strong across plausible responses—including
when its most likely prediction is wrong.

The copilot must:

- recommend the four Pokémon and lead from team preview;
- preserve the complete state of a match without conversational memory drift;
- calculate damage, speed, priority, and field interactions deterministically;
- track revealed opponent moves, items, abilities, and possible Mega Evolution;
- infer opponent sets, leads, targets, switches, Protect usage, and Mega choice
  as probability distributions rather than unsupported facts;
- generate legal joint actions and opponent response sets;
- search tactical lines and rank actions by expected value, worst-case safety,
  information value, and preservation of the match win condition;
- adapt its priors from versioned meta snapshots and the user's match history;
- explain recommendations in concise competitive language;
- replay a battle from its event log for review and debugging.

The first supported player team is the washy Ranked Season M-4 replica team
`GMKXPHAS7D`. Froslass uses Blizzard / Shadow Ball / Aurora Veil / Protect.

## Repository shape

```text
apps/web/                 Battle UI and team preview workflow
services/api/             Application API and AI orchestration
packages/battle-engine/   Deterministic state and decision engine
data/                     Versioned Champions-specific datasets
docs/                     Product scope and architecture decisions
tests/                    Cross-layer scenarios and battle fixtures
```

## System boundaries

The deterministic engine owns:

- active Pokémon, bench, fainted Pokémon, HP, status, boosts, and volatile state;
- weather, terrain, Trick Room, Tailwind, hazards, and turn counters;
- legal actions, targeting, priority, speed order, and damage ranges;
- the append-only battle event log and state reconstruction.

The AI layer is limited to:

- interpreting text, voice, or screenshots as proposed structured events;
- modelling likely opponent intent and incomplete information;
- explaining ranked recommendations.

Every AI-proposed event must be validated before it can mutate battle state.

The Master Decision Engine is divided into five independently testable layers:

1. Exact mechanics and legal action generation.
2. Belief state over hidden opponent information.
3. Tactical search over simultaneous actions and uncertain responses.
4. Strategic evaluation of board control, resources, and win conditions.
5. Explanation and learning from completed matches.

## Initial milestone

The first milestone is a complete manually operated match:

1. Load the fixed player team.
2. Enter the opponent's six Pokémon.
3. Receive a recommended four and lead.
4. Record every turn through structured controls or text.
5. Receive a primary action, alternatives, risk, and explanation.
6. Export and replay the battle log without state divergence.

See:

- [V1 scope](docs/v1-scope.md)
- [Master Decision Engine specification](docs/master-decision-engine.md)
- [Evaluation protocol](docs/evaluation-protocol.md)
- [Roadmap and quality gates](docs/roadmap.md)
- [ADR-0001: deterministic core](docs/adr/0001-deterministic-core.md)
- [ADR-0002: belief-state planning](docs/adr/0002-belief-state-planning.md)

## Status

Product scope and validation contract defined. Implementation has not started.

## Disclaimer

This is an unofficial fan project and is not affiliated with or endorsed by
Nintendo, The Pokémon Company, or Game Freak. Users are responsible for
complying with the rules of any ladder, event, or tournament in which they
participate.
