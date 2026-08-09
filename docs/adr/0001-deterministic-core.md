# ADR-0001: Deterministic core with AI at the boundary

- Status: Accepted
- Date: 2026-08-09

## Context

A conversational assistant accumulates errors when HP, boosts, fainted Pokémon,
field conditions, and revealed information exist only in natural-language
history. Competitive recommendations are not trustworthy when their inputs can
drift silently.

## Decision

The application will use an append-only event log and a deterministic reducer
as the sole authority for battle state.

AI components may propose structured events, opponent hypotheses, and natural
language explanations. They cannot directly mutate state or perform canonical
mechanics calculations.

Known facts, inferred facts, and priors will use separate data structures. Each
inference will retain provenance and confidence.

## Consequences

- Matches can be replayed, inspected, corrected, and tested.
- Voice and vision failures become rejectable proposals instead of silent state
  corruption.
- Mechanics changes are versioned independently from prompts and models.
- The initial implementation requires more domain modelling than a chatbot.
- Decision quality can be evaluated separately from state accuracy.
