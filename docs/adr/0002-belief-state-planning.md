# ADR-0002: Risk-sensitive planning over a belief state

- Status: Accepted
- Date: 2026-08-10

## Context

Pokémon doubles is a simultaneous-action game with hidden sets, incomplete team
selection information, stochastic damage, and opponent adaptation. Selecting a
move against one predicted opponent action creates brittle play and encourages
false confidence.

The product target is not merely legal or plausible recommendations. It is
measurable decision quality at the level of a 2,000-point Master player for a
supported team and regulation.

## Decision

The decision engine will plan over a belief state: a probability distribution
over opponent hidden information and plausible paired actions.

Candidate player actions will be compared across multiple opponent responses
and stochastic outcomes. Ranking will combine expected utility with lower-tail
safety, catastrophic-loss probability, information gain, and preservation of
the current win condition.

The engine will retain an `other` hypothesis for legal, low-frequency choices.
No legal action receives zero probability solely because it is missing from a
meta dataset.

Every recommendation will persist its policy version, inputs, candidates,
beliefs, search budget, random seed, score decomposition, and principal lines.

## Consequences

- Recommendations remain useful when the most likely opponent prediction is
  wrong.
- Prediction calibration and decision quality can be measured independently.
- Search is more expensive than a single-response policy and needs explicit
  latency budgets, caching, and interruption behavior.
- Meta statistics are priors, not truth.
- Learned updates require offline promotion and regression validation.
- Master-level claims require the independent evaluation protocol rather than
  feature completion or anecdotal ladder results.
