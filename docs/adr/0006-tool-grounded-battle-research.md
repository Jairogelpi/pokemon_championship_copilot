# ADR-0006: Codex researches battle facts through read-only verified tools

- Status: Accepted
- Date: 2026-08-10

## Context

ADR-0005 gave Codex a compact, immutable catalog of legal candidates. That
boundary prevents fabricated actions, but a fixed prompt cannot economically
contain every species, move, ability, item, learnset, type interaction, meta
entry, or full damage distribution that might matter in one position.

Putting the entire Pokédex in every request would waste context and still make
provenance difficult to inspect. Allowing arbitrary model-authored calculator
inputs would create a different problem: invented sets could be presented as
facts.

## Decision

The service exposes eight strict Responses API function tools:

1. Inspect canonical position, beliefs, sources, and search coverage.
2. Inspect a verified candidate and its principal counter-lines.
3. Inspect its precomputed outgoing and incoming damage evidence.
4. Calculate a current matchup after verifying that the move is in the active
   Pokémon's pinned Gen 9 learnset.
5. Look up species, moves, items, abilities, natures, and types.
6. Load a complete generation-compatible learnset.
7. Query a type matchup.
8. Read a dated meta entry that is explicitly not a mechanics authority.

The first Responses request uses required tool choice whenever the provider is
available. The service appends every reasoning and function-call output item,
executes the read-only local function, appends a `function_call_output`, and
continues until Codex returns the strict final decision. Calls are capped at
eight. Unknown tools, malformed arguments, budget overflow, or invalid final
output fail closed to the deterministic anchor.

All battle tools are non-mutating. Mechanics answers are sourced from pinned
`@pkmn/data`, `@pkmn/dex`, and `@smogon/calc`; meta answers come from the dated
community snapshot. A hypothetical damage query is evidence about a legal move
possibility, not proof that the opponent carries it.

The deterministic engine also records the exact one-turn search space:

- all generated legal player joint actions;
- all expanded opponent joint responses within the configured cap;
- their Cartesian product evaluated by the ranker;
- truncation count and covered probability mass;
- whether the configured horizon was exhaustive.

## Consequences

- Codex can retrieve deep mechanics evidence only when it is relevant instead
  of relying on memorized or model-generated facts.
- Every recommendation records which tools were called and which source
  answered them.
- “Exhaustive” is a machine-checkable statement about the configured one-turn
  model, not a claim to know hidden information, randomness, or every future
  turn.
- The live horizon remains one turn. ADR-0004 still blocks multi-ply promotion
  until a verified Pokémon state-transition adapter exists.
- Master 2000 strength still requires the frozen benchmark and independent
  evaluation; tool access alone is not evidence of rating.

## References

- <https://developers.openai.com/api/docs/guides/function-calling>
- <https://developers.openai.com/api/docs/guides/latest-model>
