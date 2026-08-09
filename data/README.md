# Data

Versioned, source-attributed Pokémon Champions mechanics and regulation data.

`meta/regulation-m-b-current.json` is a dated, source-attributed snapshot
of ordered public Pikalytics Regulation M-B S3 fields. It is used only to form
strategy priors. Ordering is not represented as usage frequency, and an
explicit residual-unknown branch is always retained. The file records its
source, retrieval date, methodology, and the boundary between community meta
evidence and mechanics authority.

`.github/workflows/update-meta.yml` checks the source once per day. The updater
requires a complete ranked index plus at least 20 individually valid Pokémon
pages and runs the full regression suite before committing a material change.
Failures preserve the last valid file. Git history is the snapshot archive.

Mechanics and legality are loaded at runtime from pinned `@pkmn/dex` and
`@pkmn/data`; damage comes from pinned `@smogon/calc`. Pokémon Champions-only
differences remain explicit compatibility gaps until an authoritative dataset
is available.
