# Battle engine

The dependency-free deterministic domain core.

## Modules

```text
models.py      Canonical battle, side, field, and Pokémon state
events.py      Versioned events, validation, reduction, and replay
team.py        Player roster and opponent preview construction
beliefs.py     Normalized opponent hypothesis distributions
mechanics.py   Damage-range and effective-speed primitives
actions.py     Legal single and paired action generation
decision.py    Team-preview and risk-aware action baseline
```

The engine is usable without a network, server, browser, or language model.
Given the same initial state, effective event log, policy version, and belief
state, it returns the same result.

The current decision policy is deliberately labelled `UNVALIDATED_BASELINE`.
It supplies the interfaces and observability required for later search and
calibration work; it is not evidence of Master 2000 performance.
