# Implementation status

## Current release boundary

Showdown scenario model 0.2 is a complete vertical slice. A user can start the server, enter a
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
| Official Showdown damage | Implemented for Gen 9 compatibility | Worker smoke, API, batch-isolation, and matrix tests |
| Hidden bulk scenarios | Implemented | Scenario aggregation and confirmed-EV tests |
| Combined double-target KO | Implemented | Weighted roll convolution in decision engine |
| Revealed incoming threats | Implemented | Reverse matrix includes active and switch-in targets |
| Speed primitive | Implemented baseline | Mechanics unit tests |
| Decision ranking | Uses Showdown damage and KO | Determinism, explanation, and service tests |
| HTTP API | Implemented | Real socket integration tests |
| Battle console | Implemented | Served by the tested HTTP boundary |
| Local text interpretation | Implemented | Spanish HP proposal test |
| OpenAI structured proposals | Implemented, optional | Requires user API credentials |
| Match export | Implemented | Export replays against canonical state |
| Continuous integration | Implemented | Pinned npm install plus Python/Node checks |

## Not yet complete

- Authoritative Champions mechanics and regulation dataset.
- Exact verification of Garchomp's fourth move and Kingambit's complete set for
  replica team `GMKXPHAS7D`.
- Authoritative Champions divergences from Showdown Gen 9, especially custom
  Mega forms and items. Stock Showdown mechanics are integrated and labelled as
  a compatibility profile rather than silently assumed to be Champions truth.
- Opponent set priors from a licensed and reproducible meta source.
- Multi-ply expectiminimax, beam search, Monte Carlo rollouts, and endgame solver.
- Calibrated win-value model and policy weights.
- Durable server-side match storage and accounts.
- Screenshot, continuous capture, OCR, and HP-bar measurement.
- Voice capture; pasted voice transcripts already work through interpretation.
- Frozen 1,000-position benchmark, expert panel, ablations, and match evaluation.

## Honest product label

The current product is:

> A runnable, state-correct Battle Copilot with official Showdown damage rolls,
> explicit set uncertainty, exact double-target KO convolution, and inspectable
> risk-aware recommendations.

It is not yet:

> A validated 2,000-point Master decision policy.

That label remains blocked by the gates in
[`evaluation-protocol.md`](evaluation-protocol.md).
