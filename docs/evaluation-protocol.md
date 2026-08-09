# Master 2000 evaluation protocol

## 1. Purpose

This protocol decides whether a policy version has demonstrated decision
quality consistent with the project's 2,000-point Master target. It prevents a
feature-complete engine from being presented as competitively validated.

The exact ladder label or rating system may change. Every report must name the
regulation, season, rating definition, supported team, mechanics version, meta
snapshot, and policy version used.

## 2. Evaluation data

The candidate benchmark should contain at least:

- 1,000 decision points;
- 200 complete matches;
- common, uncommon, anti-meta, and deliberately adversarial sets;
- team preview, early game, midgame, forced endgame, and behind/ahead states;
- positions where experts disagree as well as positions with strong consensus;
- complete information provenance and no training-set overlap.

The benchmark is frozen before final tuning. A hidden holdout set is maintained
by an evaluator who did not choose the final policy weights.

## 3. Expert reference

At least three independently acting players who have achieved the declared
Master standard review each eligible position without seeing the engine's
answer. They provide:

- preferred paired action;
- acceptable alternatives;
- actions considered materially losing;
- principal opponent responses;
- confidence and short reasoning.

Expert disagreement is retained. The benchmark must not manufacture a single
ground truth when several lines are competitively defensible.

## 4. Baselines

Every candidate is compared against:

1. Random legal paired actions.
2. A mechanics-aware greedy damage policy.
3. A hand-written tactical heuristic policy.
4. A language-model-only policy with the same visible information.
5. The previous released decision policy.

The candidate must improve decision quality, not merely explanation quality.

## 5. Required metrics

### Correctness gates

- 100% deterministic replay on the frozen corpus.
- 100% legality of emitted actions.
- 100% pass rate on verified mechanics fixtures.
- Zero leakage of hidden benchmark facts into decision inputs.

Any correctness-gate failure blocks competitive validation.

### Decision quality

- Expert top-three acceptance of at least 80% on consensus-eligible positions.
- Expert top-one agreement of at least 55% on consensus-eligible positions.
- Materially losing or catastrophic action rate no greater than 2%.
- Statistically significant improvement over every non-expert baseline.
- No major archetype segment with catastrophic-action rate above 5%.

Agreement metrics are supporting evidence, not a substitute for match-level
results. Thresholds may only be revised before the holdout is opened, with a
documented reason.

### Prediction and calibration

- Brier score and log loss for opponent action categories.
- Calibration error for set, Mega, Protect, switch, and target probabilities.
- Coverage of the residual `other` bucket.
- Performance against unseen and anti-meta sets.
- Improvement over unconditioned meta priors.

Probability quality is reported by segment. A confident wrong prediction is
penalized more heavily than an explicitly uncertain one.

### Match-level performance

- Full-match win rate with confidence intervals against frozen baselines.
- Conversion rate from advantaged positions.
- Recovery rate from disadvantaged but non-losing positions.
- Decision latency and timeout rate.
- Rating or equivalent performance in an evaluation environment permitted by
  the applicable platform and competition rules.

Live ladder use is not required when external assistance is prohibited. In
that case, prospective decisions are frozen before revealing recorded turns,
or the engine is evaluated in an approved simulator.

## 6. Ablations

The final report disables one component at a time:

- belief updates;
- multi-turn search;
- lower-tail risk term;
- information-gain term;
- endgame solver;
- match-history adaptation;
- meta priors.

A component that does not improve a relevant metric must not be described as a
proven source of strength.

## 7. Quality gates

```text
STATE_ENGINE_VERIFIED
MECHANICS_ENGINE_VERIFIED
BELIEF_ENGINE_CALIBRATED
TACTICAL_SEARCH_VERIFIED
MASTER_2000_CANDIDATE
MASTER_2000_VALIDATED
```

`MASTER_2000_VALIDATED` requires all preceding gates, the frozen holdout report,
expert review, match-level evidence, reproducibility artifacts, and no open
critical defect.

Validation applies only to the declared team, regulation, season context, and
policy version. A material mechanics, roster, search, data, or weight change
returns the system to `MASTER_2000_CANDIDATE` until regression evaluation
passes.

## 8. Prohibited claims

Before the final gate, documentation may say the engine **targets** Master 2000
decision quality. It may not say that it has achieved, proven, guarantees, or
surpasses that level.
