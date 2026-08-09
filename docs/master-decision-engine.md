# Master Decision Engine specification

## 1. Product contract

The engine targets decision quality equivalent to or better than a 2,000-point
Master player for a declared regulation, data version, and supported player
team.

It is not an oracle. It cannot know a hidden set or guarantee the opponent's
next action. Its contract is to choose the strongest risk-aware action given
the information available at decision time and to expose the assumptions and
uncertainty behind that choice.

The first supported policy is specialized for replica team `GMKXPHAS7D`.
Universal team support is a later generalization milestone.

## 2. Decision stack

### 2.1 Mechanics engine

The mechanics engine provides canonical, versioned answers for:

- legal moves, switches, targets, and paired actions;
- type, ability, item, weather, terrain, status, and field interactions;
- priority brackets, effective speed, ties, and Trick Room;
- deterministic damage inputs and complete random damage ranges;
- remaining turn counters and once-per-battle resources;
- terminal states and legal endgame transitions.

No learned model or language model may override this layer.

### 2.2 Belief engine

The belief engine represents hidden information as distributions:

- opponent bring-four and lead;
- exact moves, item, ability, nature, investment, and speed tier;
- Mega candidate and timing;
- likely target, Protect, switch, setup, speed control, or positional action;
- opponent archetype and current win condition.

Each hypothesis stores its probability, evidence, provenance, data-version, and
last update. Confirmed facts never share a field with inferred facts.

Observations update beliefs through explicit likelihood rules or calibrated
models. A residual `other` bucket prevents the engine from assigning zero
probability to legal anti-meta choices merely because they are absent from a
usage snapshot.

### 2.3 Tactical search

For each turn the search layer:

1. Generates every legal paired player action.
2. Removes only actions proven dominated by mechanics or a conservative bound.
3. Generates likely opponent paired responses plus a legal unknown-response
   bucket.
4. Expands simultaneous-action outcomes, including speed order and stochastic
   branches.
5. Searches a configurable horizon and evaluates resulting belief states.
6. Returns the best action, robust alternatives, counterfactuals, and principal
   lines.

The implementation may combine expectiminimax, beam search, cached rollouts,
and Monte Carlo sampling. Algorithm choice is an evaluated implementation
detail; the public contract is deterministic reproducibility under a recorded
seed, time budget, data version, and configuration.

### 2.4 Strategic evaluator

State value is decomposed rather than hidden behind one unexplained score.
Components include:

- terminal win or loss;
- immediate knockout and survival probabilities;
- preservation of the player's active win condition;
- removal or suppression of the opponent's win condition;
- board control and future move freedom;
- speed-control advantage and remaining duration;
- HP, status, items, Mega, Focus Sash, Multiscale, and other resource value;
- switch safety and exposure to spread or priority moves;
- information gain from forcing the opponent to reveal a set component;
- volatility, catastrophic-loss probability, and endgame convertibility.

Weights are versioned configuration. They must be fitted or adjusted only from
training data and frozen before evaluation. A weight change creates a new
policy version.

### 2.5 Explanation layer

The explanation is generated from the recorded search result. It must state:

- the recommended paired action and targets;
- the main opponent responses considered;
- why the action advances the current win condition;
- the most important failure mode;
- one lower-risk and one situational alternative when they exist;
- which claims are facts and which are inferences.

The language model may compress this evidence but may not invent a line that
the search did not evaluate.

## 3. Decision policy

The engine is risk-aware, not prediction-only. A candidate that wins against
one likely response but loses immediately against several nearby responses can
rank below a slightly lower-value action with a much safer floor.

The default comparison record contains:

```text
candidate action
expected utility
lower-tail utility
catastrophic-loss probability
information gain
win-condition preservation
opponent coverage
principal lines
assumptions
```

Risk appetite may change with the match state. When behind, the policy can
accept greater variance; when ahead, it should prefer conversion and deny the
opponent's narrow comeback lines. This adjustment must be explicit and tested.

## 4. Match planning capabilities

The completed engine must reason about:

- team preview, bring-four selection, and lead mixtures;
- early-game information gathering and positional setup;
- double targets, split targets, redirection, Fake Out, Protect, and switches;
- speed control, priority, weather, terrain, Trick Room, and denial;
- Mega selection and the value of delaying or revealing Mega Evolution;
- preserving or intentionally sacrificing resources for a winning endgame;
- damage-range uncertainty and speed-tier inference from observed order;
- Protect cycles, revealed move constraints, and remaining PP when relevant;
- forced lines and exact small endgames;
- opponent adaptation within a match and across an identified series;
- anti-meta and previously unseen legal options through the residual hypothesis
  bucket and worst-case checks.

## 5. Learning boundaries

The system maintains three separate memories:

- **Match memory:** immutable events and beliefs for one battle.
- **Player/session memory:** outcomes and tendencies across the user's matches.
- **Meta memory:** versioned aggregate priors for the active regulation.

Completed matches may produce training examples and proposed prior updates.
They cannot silently change the live policy. Promotion requires offline
evaluation, a versioned artifact, and regression approval.

Training, validation, and test splits must be separated by match and time.
Near-duplicate positions from one match may not cross splits.

## 6. Operational requirements

- A recommendation is reproducible from match log, policy version, mechanics
  version, meta snapshot, configuration, and random seed.
- Search supports a strict latency budget and returns the best fully evaluated
  result if interrupted.
- Cached calculations are keyed by every input that changes their result.
- The UI shows stale-state and low-confidence warnings before advice.
- Manual correction is always available and is recorded as an event.
- A failure in AI interpretation cannot mutate canonical state.
- Unknown data degrades confidence; it never produces fabricated certainty.

## 7. Definition of done

Implementation completeness and decision-quality validation are separate.

The product is not allowed to claim Master 2000 performance merely because all
features exist. That claim requires the independent evaluation gate in
[`evaluation-protocol.md`](evaluation-protocol.md).
