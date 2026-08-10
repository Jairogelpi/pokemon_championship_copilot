# Tests

The current suite covers deterministic replay, event invariants, belief
normalization, paired-action legality, recommendation structure, mechanics,
service workflows, local interpretation, export, the real HTTP boundary, and
the multi-ply search kernel, its live verified-sampled battle adapter, exact
chance replay, and the fail-closed terminal endgame tablebase. Search tests cover
depth reversals, lower-tail risk, transposition reuse, deterministic budget
degradation, invalid probability rejection, adversarial terminal replies,
win/draw/loss mass, and cycle rejection. Transition tests cover exact sampled
rolls, exact Protect probability, per-hit critical/accuracy/power rules,
Loaded Dice Population Bomb, deterministic replay, final speed, Focus Sash,
Protect against spread damage, friendly fire, depth-two completion, exact
endgame promotion, non-exhaustive general-search labelling, and service-to-Codex
evidence flow.

Codex-brain tests verify schema-constrained candidate selection, deterministic
fallback without credentials, rejection of invented candidate IDs, rejection
of incoherent opponent probability mass, request privacy, and reasoning-setting
validation.

Run all checks with:

```bash
make check
```
