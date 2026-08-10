# Tests

The current suite covers deterministic replay, event invariants, belief
normalization, paired-action legality, recommendation structure, mechanics,
service workflows, local interpretation, export, the real HTTP boundary, and
the independent multi-ply search kernel. Search tests cover depth reversals,
lower-tail risk, transposition reuse, deterministic budget degradation, and
invalid probability rejection.

Codex-brain tests verify schema-constrained candidate selection, deterministic
fallback without credentials, rejection of invented candidate IDs, rejection
of incoherent opponent probability mass, request privacy, and reasoning-setting
validation.

Run all checks with:

```bash
make check
```
