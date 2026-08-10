# Data

Versioned, source-attributed Pokémon Champions mechanics and regulation data.

`champions/current.json` is the pinned, fail-closed Regulation M-B/Ranked Season
M-5 legality contract. It contains 231 legal base species/forms, 75 Mega forms,
148 items, 486 moves, and the per-species Champions learnsets. It records its
verified activity window, source URLs and source hashes. The API refuses to
recommend after that window expires.

`meta/regulation-m-b-current.json` is a dated, source-attributed snapshot of the
current public M-B/M-5 doubles usage proxy. It is used only to form strategy
priors. Every species, move and item is filtered through the pinned Champions
registry, and rejected source values remain recorded for audit. An explicit
residual-unknown branch is always retained. Public community data is not
represented as official in-game ladder telemetry.

`.github/workflows/update-meta.yml` checks the source once per day. The updater
requires a complete ranked index plus at least 20 individually valid Pokémon
pages and runs the full regression suite before committing a material change.
Failures preserve the last valid file. Git history is the snapshot archive.

Legality comes only from the current Champions snapshot. General mechanics are
loaded from pinned `@pkmn/dex` and `@pkmn/data`; damage comes from pinned
`@smogon/calc`. Champions Mega stats, types and known abilities are injected as
overrides. Rare unpublished or unverified Champions-specific effect constants
remain explicit compatibility gaps.
