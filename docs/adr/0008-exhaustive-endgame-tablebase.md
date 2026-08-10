# ADR-0008: fail-closed exhaustive endgame tablebase

## Status

Accepted for release 0.9.

## Decision

The service attempts a terminal tablebase before bounded sampled multi-turn
search. It activates only when the current Champions regulation snapshot is
active, every living Pokémon is already active, both sides have at most two
living Pokémon, all sets and moves are fully known, and every legal move belongs
to the monotone transition subset.

The solver enumerates every legal paired player action, every legal paired
opponent response, and every supported stochastic outcome. Opponent responses
are minimax choices; chance outcomes use their exact probability. A result is
published only after every reachable branch terminates. Transpositions are
cached, while a reachable cycle, unresolved mechanic, missing probability mass,
state/branch limit, or time limit invalidates the entire tablebase.

## Exact chance boundary

The turn resolver replays discrete decisions rather than drawing samples. A
symbolic uniform variate splits threshold and cumulative-weight choices into
conditional branches, covering speed ties, accuracy, hit count, criticals,
damage rolls, sleep duration, effects, and randomized replacements supported by
the transition adapter.

## Deliberate exclusions

Living reserves, incomplete opponent sets, inaccurate or healing moves,
statuses, PP exhaustion, and state-changing multi-hit contact/secondary effects
are not currently eligible. Beat Up team callbacks and Dragon Darts target
allocation remain unsupported. These restrictions prevent a finite-horizon or
partially resolved result from being presented as an exhaustive endgame.

## Consequences

Eligible output carries `EXHAUSTIVE_CURRENT_CHAMPIONS_ENDGAME`, terminal
win/draw/loss probabilities, the adversarial reply, principal line, exact branch
telemetry, and `exhaustive_claim: true`. The terminal optimum is the only action
left in Codex's selectable candidate envelope; inferior exact alternatives stay
visible for audit but cannot override the tablebase. Every other position reports why the
tablebase was ineligible or unavailable and retains the sampled planner's
non-exhaustive label.
