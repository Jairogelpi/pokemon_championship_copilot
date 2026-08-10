# ADR-0005: Codex selects only from verified legal candidates

- Status: Accepted
- Date: 2026-08-10

## Context

The deterministic 0.3 policy can enumerate legal paired actions, calculate
damage and incoming threats, expand a bounded opponent response model, and
rank lines by expected value and lower-tail risk. It cannot provide the same
contextual strategic judgment as Codex about an opponent's likely plan, the
player's evolving win condition, or when a lower-ranked line is positionally
preferable.

Allowing a language model to emit arbitrary moves or simulated battle states
would violate the project's source-of-truth boundary. It could invent a legal
target, damage roll, speed order, hidden set, or multi-turn continuation.

## Decision

Codex becomes the final strategic selector, while the deterministic engine
remains the mechanics and evidence authority.

For every live recommendation:

1. The battle engine creates and ranks all legal paired player actions.
2. The service exposes the top eight as an immutable candidate catalog with
   stable IDs, decomposed scores, covered responses, and principal lines.
3. Codex receives canonical state, belief state, recent events, the candidate
   catalog, calculator status, response coverage, and current assumptions.
4. The Responses API enforces a strict JSON Schema whose candidate fields use
   a dynamic enum containing only the supplied IDs.
5. The service validates the returned IDs and opponent probability mass, then
   resolves the chosen ID back to the original engine-owned action object.
6. Any configuration, network, refusal, schema, parsing, ID, or probability
   failure preserves the deterministic anchor.

The default model is `gpt-5.6-sol` with `high` reasoning effort. Both are
explicit configuration and must be evaluated before a frozen policy is
promoted. Requests set `store: false` and send a hashed match identifier as the
safety identifier.

This design follows the official OpenAI guidance to use the Responses API for
reasoning workflows and Structured Outputs for schema-adherent results:

- <https://developers.openai.com/api/docs/guides/latest-model>
- <https://developers.openai.com/api/docs/guides/structured-outputs>

## Consequences

- Codex owns the final strategic judgment when configured, rather than merely
  rewriting a deterministic explanation.
- Illegal or fabricated model actions are rejected structurally and again at
  the application boundary.
- Every model override records the deterministic anchor, selected ID,
  confidence, win condition, opponent hypotheses, and main failure mode.
- The service remains usable without OpenAI credentials and degrades
  deterministically.
- Codex can reason about future intent, but this does not turn the one-turn
  mechanics model into verified multi-ply simulation. ADR-0004 still gates
  that claim on a correct battle transition adapter.
- Master 2000 performance remains an empirical evaluation claim, not a model
  branding claim.
