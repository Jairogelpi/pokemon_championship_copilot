# ADR-0003: official Showdown calculator boundary

## Decision

Use the official Smogon `@smogon/calc` package as a pinned, out-of-process
mechanics oracle. A persistent line-delimited JSON worker owns the JavaScript
objects; Python sends one batch per canonical battle revision.

## Why a process boundary

- The battle engine remains deterministic Python with no embedded JavaScript
  runtime.
- A worker crash, timeout, missing dependency, or rejected Pokémon is visible
  and cannot silently fall back to invented damage.
- Batch requests amortize startup and calculate every live move/target/scenario
  pair before ranking actions.
- The HTTP API can expose the same boundary for independent verification.

## Probability semantics

For one move and one set scenario, the roll probabilities come directly from
`@smogon/calc`. `koProbabilityOnHit` is conditional on connecting;
`koProbabilityWithBaseAccuracy` multiplies by the move's base accuracy. Accuracy
and evasion stages or other untracked modifiers are excluded and disclosed.

When both player moves target the same Pokémon, the engine convolves their
weighted roll distributions, includes each move's miss branch, and tests the
sum against current HP. It then averages that result over the explicit hidden
bulk scenarios.

## Compatibility boundary

The base profile is Showdown generation 9 in Doubles, behind the current
Champions legality gate. A versioned M-B/M-5 overlay supplies Champions Mega
forms, stones, stats, types and known abilities to the calculator; Mega entry
weather and terrain are applied by the transition engine. This does not prove
that every Pokémon Champions callback is identical. Any rare custom effect or
per-hit divergence without a verified fixture remains a named unsupported
boundary instead of inheriting an invented value.

## Failure behavior

If Node.js or `@smogon/calc` is missing, the health endpoint becomes degraded
and recommendations carry `SHOWDOWN_UNAVAILABLE`. The heuristic policy may
still return a legal line, but no damage values are synthesized.
