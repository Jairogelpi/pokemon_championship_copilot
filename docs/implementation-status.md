# Implementation status

## Current release boundary

Baseline 0.1 is a complete vertical slice. A user can start the server, enter a
team preview, receive a bring-four and lead baseline, record a match, inspect
beliefs, request paired actions, correct history, and export a replayable log.

## Working now

| Area | Status | Evidence |
| --- | --- | --- |
| Canonical state models | Implemented | Unit and HTTP workflow tests |
| Immutable event log | Implemented | Replay equality tests |
| Append-only corrections | Implemented | Correction and export tests |
| Belief normalization | Implemented | Probability invariant tests |
| Legal paired actions | Implemented baseline | Duplicate-switch invariant test |
| Damage and speed primitives | Implemented baseline | Mechanics unit tests |
| Decision ranking | Implemented baseline | Determinism and explanation test |
| HTTP API | Implemented | Real socket integration tests |
| Battle console | Implemented | Served by the tested HTTP boundary |
| Local text interpretation | Implemented | Spanish HP proposal test |
| OpenAI structured proposals | Implemented, optional | Requires user API credentials |
| Match export | Implemented | Export replays against canonical state |
| Continuous integration | Implemented | Dependency-free GitHub Actions workflow |

## Not yet complete

- Authoritative Champions mechanics and regulation dataset.
- Exact verification of Garchomp's fourth move and Kingambit's complete set for
  replica team `GMKXPHAS7D`.
- Full type, ability, item, target, weather, terrain, Mega, and damage mechanics.
- Opponent set priors from a licensed and reproducible meta source.
- Multi-ply expectiminimax, beam search, Monte Carlo rollouts, and endgame solver.
- Calibrated win-value model and policy weights.
- Durable server-side match storage and accounts.
- Screenshot, continuous capture, OCR, and HP-bar measurement.
- Voice capture; pasted voice transcripts already work through interpretation.
- Frozen 1,000-position benchmark, expert panel, ablations, and match evaluation.

## Honest product label

The current product is:

> A runnable, state-correct Battle Copilot baseline with explicit uncertainty
> and inspectable risk-aware recommendations.

It is not yet:

> A validated 2,000-point Master decision policy.

That label remains blocked by the gates in
[`evaluation-protocol.md`](evaluation-protocol.md).
