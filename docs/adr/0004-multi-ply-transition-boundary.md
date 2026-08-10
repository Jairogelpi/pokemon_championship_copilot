# ADR-0004: Multi-ply search requires verified state transitions

- Status: Accepted
- Date: 2026-08-10
- Implemented in part by: ADR-0007

## Context

The live 0.3 policy evaluates complete bounded opponent responses for one turn.
Extending it to multiple turns requires a next battle state for every joint
action and stochastic outcome.

Using expected damage as if it were an actual roll would collapse misses,
knockouts, speed-order changes, switches, status, and field duration into a
fictional state. Such a policy could be labelled multi-turn while searching
positions that cannot occur in the game.

## Decision

The project separates the generic search kernel from the Pokémon battle
transition adapter.

The generic kernel owns:

- iterative deepening;
- expected and lower-tail value aggregation;
- catastrophic-loss penalties;
- transposition caching;
- deterministic node budgets and optional wall-clock budgets;
- return of the last fully completed depth;
- inspectable principal lines and search statistics.

The future battle adapter must own:

- legal resolution order for switches, priority, speed, and Trick Room;
- accuracy, damage-roll, critical-hit, and secondary-effect branches;
- Protect, Fake Out, spread damage, field effects, and fainting;
- legal replacement choices and terminal-state detection;
- belief updates caused by observable outcomes;
- a stable state key containing every value that can change continuation value.

The live recommendation service remains on the one-turn policy until a bounded
adapter passes its declared mechanics and replay fixtures. The existence of the
generic kernel alone must not be described as live multi-turn play.

## Consequences

- Search infrastructure can be tested without coupling it to incomplete game
  mechanics.
- The engine fails honestly instead of manufacturing future states.
- State-transition correctness becomes the next critical implementation slice.
- Multi-ply promotion requires both transition fixtures and live integration
  tests, not only synthetic search-tree tests.

ADR-0007 satisfies this boundary for a declared compatibility subset and keeps
unsupported continuations as explicit uncertainty leaves. It does not claim
complete mechanics coverage or exhaustive future search.
