# Implementation status

## Current release boundary

Endgame tablebase 0.9 extends the Codex strategist 0.8 vertical slice. A user can start the server,
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
| Current-format legality | Fail-closed M-B/M-5 contract | 231 base forms, 75 Megas, 148 items, 486 moves, 231 exact learnsets; active-window and rejection tests |
| Legal paired actions | Current-only, including one Mega branch per side | Duplicate-switch, Mega uniqueness, form resolution, and action-generation tests |
| Showdown damage | Implemented for Gen 9 compatibility | Worker smoke, API, batch-isolation, and matrix tests |
| Mechanics knowledge | Current Champions registry at the decision boundary; pinned upstream mechanics underneath | Regulation, worker, calculator and HTTP knowledge tests |
| Regulation strategy priors | Current M-B/M-5 snapshot for 45 legal Pokémon | Provenance, legality filter, rejected-value audit, date, method, and meta API tests |
| Daily meta refresh | Scheduled, manual, fail-closed GitHub Actions workflow | Parser, schema, no-change, and full regression gates |
| Hidden bulk scenarios | Implemented | Scenario aggregation and confirmed-EV tests |
| Combined double-target KO | Implemented | Weighted roll convolution in decision engine |
| Modelled incoming threats | Implemented for revealed + top meta candidates | Reverse matrix includes active and switch-in targets |
| Priority and scenario speed | Implemented in one-turn race evaluation | Damage metadata and principal-line output |
| Concrete joint responses | Exhaustive inside the bounded model | Moves, targets, Protect, legal switches, current Mega branches, other branch, and coverage tests |
| Decision ranking | One-turn adversarial search with principal lines | Determinism, explanation, coverage, and service tests |
| Multi-ply search kernel | Implemented and live-wired at depth two | Iterative-depth, risk-tail, cache, budget, and service integration tests |
| Sampled battle transition adapter | Implemented broad compatibility profile | Replacements, critical/multi-hit damage, status counters, switching, speed, contact, entry, residual, item, spread, and deterministic replay tests |
| Exact chance replay | Implemented, fail-closed | Protect probability, terminal mass, per-hit accuracy/critical/damage, and unsupported-branch tests |
| Exhaustive endgame tablebase | Implemented for eligible closed active-only endgames | Terminal expectiminimax, adversarial replies, transpositions, win/draw/loss mass, cycle rejection, current-regulation eligibility, and live promotion test |
| Multi-turn promotion gate | Implemented, fail-closed at 90% | Completed-depth, modeled probability mass, declared-mechanics resolution, uncertainty reasons, and fallback assertions |
| Reference verified frontier | 90.18% response probability; 100% declared mechanics | Charizard + Garchomp fixture regression |
| Champions Mega overlay | Pinned current form stats, types, abilities and Mega Stones | Dragonite/Froslass/Charizard resolution, exact override, weather activation, and calculator branch tests |
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

- Exact verification of Garchomp's fourth move and Kingambit's complete set for
  replica team `GMKXPHAS7D`.
- Complete coverage for every rare volatile, move-specific callback, ability,
  item, callback-changing multi-hit move, and Champions-specific divergence.
  Equal-power multi-hit critical checks are independent. Triple Axel/Triple
  Kick escalating power and Population Bomb/Loaded Dice hit rules are covered;
  Beat Up, Dragon Darts, and per-hit contact/secondary state callbacks remain
  named boundaries.
- Automatic promotion to the next regulation. The current snapshot stops
  recommendations at expiry until a newly verified snapshot is committed.
- Observation-driven belief updates inside future branches, calibrated set
  priors, and wider response sampling. The general multi-turn search remains
  sampled; the exhaustive claim is restricted to tablebase-eligible closed
  endgames. Extending it to living reserves, PP exhaustion, recovery and
  game-theoretic cycles remains incomplete.
- Calibrated win-value model and policy weights.
- Durable server-side match storage and accounts.
- Screenshot, continuous capture, OCR, and HP-bar measurement.
- Voice capture; pasted voice transcripts already work through interpretation.
- Frozen 1,000-position benchmark, expert panel, ablations, and match evaluation.

## Honest product label

The current product is:

> A runnable, state-correct Battle Copilot with pinned Showdown damage rolls,
> a fail-closed current-Champions legality registry, searched Mega Evolution,
> queryable mechanics data, versioned current meta priors, explicit hidden-information
> uncertainty, exhaustive bounded one-turn replies, verified-sampled two-turn
> continuations with explicit uncertainty leaves, a fail-closed terminal
> tablebase for eligible fully observed active-only endgames, inspectable principal lines,
> and optional tool-grounded Codex strategic selection restricted to verified
> legal candidates.

It is not yet:

> A validated 2,000-point Master decision policy.

That label remains blocked by the gates in
[`evaluation-protocol.md`](evaluation-protocol.md).
