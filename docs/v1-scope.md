# V1 scope

## Outcome

V1 must support a complete doubles match with the user's fixed six-Pokémon
team while retaining an exact, replayable state throughout the battle.

## Included

- Fixed player roster with exact sets.
- Manual opponent team-preview entry.
- Bring-four and lead recommendation.
- Structured battle-state schema.
- Append-only, versioned battle events.
- Reducer that derives state exclusively from events.
- Undo implemented by event correction and replay, not in-place mutation.
- Deterministic legality, type, priority, speed, and damage services.
- Known and inferred opponent information kept separately.
- Candidate action generation and explainable ranking.
- Fast battle controls for moves, switches, Mega Evolution, Protect, fainting,
  status, critical hits, misses, and manual corrections.
- Match export and deterministic replay.
- Unit, property, and scenario tests for engine invariants.

## Explicitly deferred

- Continuous screen capture.
- Automated HP-bar measurement.
- Universal support for arbitrary player teams.
- Online opponent identification.
- Self-training from private match footage.
- Claims of calibrated win probability before sufficient validation data exists.

## Acceptance criteria

V1 is complete only when:

1. A recorded match replays to the same final state byte-for-byte.
2. A fainted Pokémon cannot return through a normal switch event.
3. Unknown opponent facts are never promoted to known facts without evidence.
4. Illegal action combinations are rejected before scoring.
5. Damage and speed calculations expose their inputs and assumptions.
6. Corrections do not silently alter historical events.
7. The UI can record a normal turn in a few interactions.
8. A complete golden battle scenario passes in continuous integration.

## Validation plan

The first validation set will consist of real matches played with replica team
`GMKXPHAS7D`. Each match will be entered once live and independently reconstructed
from the recording. Differences become engine or interface defects.
