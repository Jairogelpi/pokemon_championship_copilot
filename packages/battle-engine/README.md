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
decision.py    Risk-aware one-turn adversarial search and principal lines
search.py      Budgeted multi-ply expectiminimax kernel and transposition cache
```

The engine is usable without a network, server, browser, or language model.
Given the same initial state, effective event log, policy version, and belief
state, it returns the same result.

The current decision policy is labelled `ADVERSARIAL_SHOWDOWN_MODEL`. It
enumerates all replies inside a bounded hypothesis set, but it is not evidence
of Master 2000 performance.

The generic multi-ply kernel supports iterative deepening, chance branches,
probability normalization, expected and lower-tail value, catastrophic-loss
penalties, deterministic node budgets, optional time budgets, transposition
caching, and inspectable principal lines. It is tested independently but is not
yet connected to the live policy: a verified battle transition adapter is
required before future turns can be searched without inventing state.
