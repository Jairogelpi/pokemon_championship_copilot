# Battle engine

The deterministic domain core. Its first implementation slice will define:

- versioned battle events;
- immutable battle-state models;
- event validation and reduction;
- replay and correction semantics;
- invariants and property tests.

It must remain usable without network access or an AI model.
