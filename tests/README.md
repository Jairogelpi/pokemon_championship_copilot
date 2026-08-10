# Tests

The current suite covers deterministic replay, event invariants, belief
normalization, paired-action legality, recommendation structure, mechanics,
service workflows, local interpretation, export, the real HTTP boundary, and
the multi-ply search kernel and its live verified-sampled battle adapter. Search
tests cover depth reversals,
lower-tail risk, transposition reuse, deterministic budget degradation, and
invalid probability rejection. Transition tests cover exact sampled rolls,
deterministic replay, final speed, Focus Sash, Protect against spread damage,
friendly fire, depth-two completion, non-exhaustive labelling, and service-to-
Codex evidence flow.

Codex-brain tests verify schema-constrained candidate selection, deterministic
fallback without credentials, rejection of invented candidate IDs, rejection
of incoherent opponent probability mass, request privacy, and reasoning-setting
validation.

Run all checks with:

```bash
make check
```
