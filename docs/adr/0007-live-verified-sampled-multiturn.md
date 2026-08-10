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

Opponent responses are selected by deterministic systematic quantiles of the
complete bounded response distribution. Root and future action beams, response
samples, chance samples, node budget, wall-clock budget, and depth are explicit
configuration values.

The adapter resolves a declared compatibility subset:

- simultaneous switches and retargeting to incoming Pokémon;
- priority, recalculated final speed, speed ties, Tailwind, and Trick Room;
- Protect/Detect chains, Feint, Fake Out, flinching, and Sucker Punch;
- single, spread, and all-adjacent targets, including friendly fire;
- exact sampled damage, accuracy, Focus Sash, Sturdy, recoil, and drain;
- simple status/stat secondaries, paralysis, burn/poison residuals, and field
  duration decrement.

Forced replacements, sleep/freeze/toxic counters, complex status or secondary
effects, sand/hail and known held-item residuals, and other undeclared mechanics
terminate the branch as an explicit uncertainty state with a risk penalty.
Unverified contact, switch-in, and end-turn ability effects use the same
boundary.
Legal abilities are sampled uniformly and an unknown held item uses a reachable
no-item scenario; neither is a calibrated set probability. Beliefs are not
observation-updated inside sampled futures.

A multi-turn result may reorder the live candidate catalog only when search
completes at least depth two and the resolved-sample fraction meets the declared
threshold. Otherwise the exhaustive one-turn recommendation is preserved.

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
