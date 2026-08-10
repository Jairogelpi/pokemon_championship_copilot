# Roadmap and exit gates

## Phase 0: Rules and data contract

Deliver versioned Champions-specific species, moves, abilities, items, Mega,
type, targeting, and regulation schemas with provenance.

Exit: every required datum has a source, version, validation rule, and update
policy.

## Phase 1: Exact match state

Deliver immutable state, versioned events, reducer, corrections, export, and
deterministic replay.

Exit: `STATE_ENGINE_VERIFIED`.

## Phase 2: Mechanics and legal actions

Deliver legality, targeting, priority, speed, damage ranges, field counters,
and complete paired-action generation.

Exit: `MECHANICS_ENGINE_VERIFIED`.

## Phase 3: Belief state and team preview

Deliver opponent bring-four, lead, set, Mega, target, switch, and Protect
distributions with evidence and calibration reports.

Exit: `BELIEF_ENGINE_CALIBRATED`.

## Phase 4: Master tactical search

Deliver simultaneous-action search, stochastic outcomes, risk-sensitive
ranking, principal lines, endgame solving, and score decomposition.

Current boundary: bounded one-turn adversarial search remains exhaustive inside
its declared response model. The live depth-two expectiminimax search consumes
deterministic samples of reachable Showdown-compatible states, models forced
replacement without spending turn depth, and covers the common critical,
multi-hit, status, contact, entry, item, weather, and residual mechanics needed
by the supported team and current meta. Its reference verified frontier is
90.18%; remaining unknown responses and unsupported callbacks fail closed.

Codex strategist 0.7 may research the canonical position with read-only
mechanics, learnset, meta, matchup, candidate, and damage tools before selecting
a strategically preferable line from the top twelve verified candidates. Codex
is not allowed to manufacture future battle states; it receives only states
produced by the transition adapter. This is a compatibility milestone, not yet
`TACTICAL_SEARCH_VERIFIED`.

Exit: `TACTICAL_SEARCH_VERIFIED`.

## Phase 5: Battle interface and interpretation

Deliver fast structured controls, text and voice event proposals, team-preview
image input, confidence warnings, and a recommendation panel. Continuous screen
capture remains optional until manual operation is reliable.

Exit: complete real matches can be recorded and advised within the latency
budget without state divergence.

## Phase 6: Competitive evaluation

Freeze policy and benchmark, run expert review, baselines, calibration,
ablations, match evaluation, and reproducibility checks.

Exit: `MASTER_2000_CANDIDATE` or `MASTER_2000_VALIDATED`, according to the
evidence. Failed thresholds produce documented work items, not a softened
definition of done.

## Phase 7: Generalization

Only after validation with `GMKXPHAS7D`, add arbitrary player teams, more
regulations, continuous vision, and controlled online adaptation.

Exit: each new supported configuration passes its own regression and decision
quality evaluation.
