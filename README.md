# Pokémon Champions Battle Copilot

An unofficial, stateful battle assistant for Pokémon Champions doubles.

The project is built around one core rule: language models may interpret and
explain a battle, but they are not the source of truth for battle state,
mechanics, legality, damage, or speed.

## Product goal

Build a copilot that can:

- recommend the four Pokémon and lead from team preview;
- preserve the complete state of a match without conversational memory drift;
- calculate damage, speed, priority, and field interactions deterministically;
- track revealed opponent moves, items, abilities, and possible Mega Evolution;
- generate legal candidate actions and rank them by risk and expected value;
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

## Initial milestone

The first milestone is a complete manually operated match:

1. Load the fixed player team.
2. Enter the opponent's six Pokémon.
3. Receive a recommended four and lead.
4. Record every turn through structured controls or text.
5. Receive a primary action, alternatives, risk, and explanation.
6. Export and replay the battle log without state divergence.

See [V1 scope](docs/v1-scope.md) and
[ADR-0001](docs/adr/0001-deterministic-core.md).

## Status

Repository initialized. Implementation has not started.

## Disclaimer

This is an unofficial fan project and is not affiliated with or endorsed by
Nintendo, The Pokémon Company, or Game Freak. Users are responsible for
complying with the rules of any ladder, event, or tournament in which they
participate.
