# ADR-0007: Live multi-turn search uses reachable deterministic samples

- Status: Accepted
- Date: 2026-08-10

## Context

The one-turn policy exhaustively evaluates every action/response pair inside a
bounded opponent model, but it cannot reason about the next decision. The
generic expectiminimax kernel from ADR-0004 needs concrete future states.
Applying expected damage to HP would create states that no battle roll can
produce and would corrupt speed, fainting, targeting, and replacement logic.

Full branching over every opponent response, hidden set, accuracy event, damage
roll, secondary effect, and later legal action is not operationally tractable.
The system therefore needs a bounded method that preserves reachability and
reports its approximation honestly.

## Decision

The live service runs a depth-two risk-aware search when Codex is configured,
unless `MULTITURN_ENABLED` overrides it. For every selected response it uses a
stable SHA-256 seed to sample one exact hidden-bulk scenario, accuracy result,
and weighted Showdown damage roll. That exact roll mutates HP. Repeating the
same canonical position produces the same search tree. Once sampled, an
opponent EV, nature, and legal-ability compatibility profile is stored in the
search state and remains fixed across later turns in that trajectory.

Opponent responses are selected by deterministic seeded systematic quantiles of
the complete bounded response distribution. Ten ranked meta move candidates per
active Pokémon are expanded; category mass with no modeled legal action is
conditioned back onto the available declared categories while the explicit 5%
per-Pokémon residual-other prior is preserved. Root and future action beams,
response samples, chance samples, node budget, wall-clock budget, and depth are
explicit configuration values.

The adapter resolves a declared compatibility subset:

- simultaneous switches, retargeting, and forced replacements that do not spend
  a battle-turn depth unit;
- priority, recalculated final speed, speed ties, Tailwind, and Trick Room;
- Protect/Detect chains, Feint, Fake Out, flinching, Sucker Punch, redirection,
  Wide Guard, and Quick Guard;
- single, spread, and all-adjacent targets, including friendly fire;
- sampled accuracy, critical hits, multi-hit counts and damage rolls, Focus
  Sash, Sturdy, recoil, drain, contact abilities, and Rocky Helmet;
- common status/control moves and secondaries, sleep/freeze/toxic counters,
  Taunt, Yawn, Imprison, phazing, paralysis, and status immunities;
- weather/terrain setters, Intimidate, common entry/end-turn abilities,
  burn/poison/weather residuals, recovery/orb items, berries, Life Orb, and
  field-duration decrement.

The residual-other response, rare move-specific callbacks, unimplemented
volatiles, and undeclared Champions divergences terminate the branch as an
explicit uncertainty state with a risk penalty. Release 0.8 adds independent
critical checks for equal-power multi-hit moves; Beat Up, Dragon Darts,
Population Bomb, Triple Axel, and Triple Kick still fail closed at their
specialized per-hit callback boundary. Uncertainty reasons are counted
individually in transition telemetry.
Species missing from the native Gen 9 Pokédex use their latest official species
and learnset record under Gen 9 calculation mechanics, with the source
generation returned explicitly.
Legal abilities are sampled uniformly and an unknown held item uses a reachable
no-item scenario; neither is a calibrated set probability. Beliefs are not
observation-updated inside sampled futures.

A multi-turn result may reorder the live candidate catalog only when search
completes at least depth two and the verified frontier meets 90%. The frontier
multiplies declared modeled-response probability by the resolved fraction of
declared mechanic samples; the residual-other branch is not mislabeled as a
mechanics failure. Otherwise the exhaustive one-turn recommendation is
preserved.

## Consequences

- Every searched concrete state is reachable under the sampled scenario; no
  expected-damage state shortcut exists.
- Search is reproducible, budgeted, inspectable, and safe to pass to Codex.
- One-turn coverage may be called exhaustive within its bounded model. Future
  search must always be labelled sampled and non-exhaustive.
- More mechanics coverage reduces uncertainty penalties and can unlock stronger
  promotions without weakening the fail-closed boundary.
- This milestone does not establish Master 2000 performance or satisfy the
  final `TACTICAL_SEARCH_VERIFIED` quality gate.
