# Implementation status

## Current release boundary

Codex strategist 0.5 is a complete vertical slice. A user can start the server,
enter a team preview, receive a bring-four and lead baseline, record a match,
inspect beliefs, request paired actions, correct history, and export a replayable
log. With OpenAI credentials, Codex makes the final strategic selection from a
verified legal-action envelope; without them, the same flow uses the
deterministic anchor.

## Working now

| Area | Status | Evidence |
| --- | --- | --- |
| Canonical state models | Implemented | Unit and HTTP workflow tests |
| Immutable event log | Implemented | Replay equality tests |
| Append-only corrections | Implemented | Correction and export tests |
| Belief normalization | Implemented | Probability invariant tests |
| Legal paired actions | Implemented baseline | Duplicate-switch invariant test |
| Showdown damage | Implemented for Gen 9 compatibility | Worker smoke, API, batch-isolation, and matrix tests |
| Mechanics knowledge | 876 species/forms, 685 moves, learnsets, items, abilities, natures, and types | Worker and HTTP knowledge tests |
| Regulation strategy priors | Versioned Regulation M-B S3 snapshot for 25 Pokémon | Provenance, date, method, and meta API test |
| Daily meta refresh | Scheduled, manual, fail-closed GitHub Actions workflow | Parser, schema, no-change, and full regression gates |
| Hidden bulk scenarios | Implemented | Scenario aggregation and confirmed-EV tests |
| Combined double-target KO | Implemented | Weighted roll convolution in decision engine |
| Modelled incoming threats | Implemented for revealed + top meta candidates | Reverse matrix includes active and switch-in targets |
| Priority and scenario speed | Implemented in one-turn race evaluation | Damage metadata and principal-line output |
| Concrete joint responses | Exhaustive inside the bounded model | Moves, targets, Protect, legal switches, other branch, coverage test |
| Decision ranking | One-turn adversarial search with principal lines | Determinism, explanation, coverage, and service tests |
| Multi-ply search kernel | Implemented, not yet live-wired | Iterative-depth, risk-tail, cache, budget, and fail-closed tests |
| HTTP API | Implemented | Real socket integration tests |
| Battle console | Implemented | Served by the tested HTTP boundary |
| Local text interpretation | Implemented | Spanish HP proposal test |
| OpenAI structured proposals | Implemented, optional | Requires user API credentials |
| Codex strategic selector | Implemented, optional, fail-closed | Dynamic candidate enum, output validation, fallback, and privacy tests |
| Codex battle research tools | Implemented, optional, read-only | Forced function call, tool-loop continuity, mechanics, learnset, matchup, meta, damage, and unknown-tool tests |
| Codex decision evidence | Implemented | Anchor, selected ID, confidence, win condition, opponent plan, and failure mode |
| Search-space audit | Implemented for live one-turn policy | Exact action/reply product, horizon, truncation, coverage, and exhaustiveness assertions |
| Match export | Implemented | Export replays against canonical state |
| Continuous integration | Implemented | Pinned npm install plus Python/Node checks |

## Not yet complete

- Authoritative Champions mechanics and regulation dataset.
- Exact verification of Garchomp's fourth move and Kingambit's complete set for
  replica team `GMKXPHAS7D`.
- Authoritative Champions divergences from Showdown Gen 9, especially custom
  Mega forms and items. Stock Showdown mechanics are integrated and labelled as
  a compatibility profile rather than silently assumed to be Champions truth.
- Verified battle transition adapter connecting live Showdown outcomes to the
  multi-ply kernel. The kernel itself is implemented; the current live policy
  remains one-turn.
- Beam search, Monte Carlo rollouts, and endgame solver.
- Calibrated win-value model and policy weights.
- Durable server-side match storage and accounts.
- Screenshot, continuous capture, OCR, and HP-bar measurement.
- Voice capture; pasted voice transcripts already work through interpretation.
- Frozen 1,000-position benchmark, expert panel, ablations, and match evaluation.

## Honest product label

The current product is:

> A runnable, state-correct Battle Copilot with pinned Showdown damage rolls,
> queryable mechanics data, versioned meta priors, explicit hidden-information
> uncertainty, exhaustive bounded one-turn replies, inspectable principal
> counter-lines, and optional tool-grounded Codex strategic selection restricted
> to verified legal candidates.

It is not yet:

> A validated 2,000-point Master decision policy.

That label remains blocked by the gates in
[`evaluation-protocol.md`](evaluation-protocol.md).
