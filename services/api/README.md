# Application API

The HTTP layer uses Python's standard library and serves both JSON endpoints and
the static battle console. Canonical battle state lives in the deterministic
engine; the HTTP layer does not maintain a second representation. Exact damage
calls cross a persistent local worker running pinned `@smogon/calc`,
`@pkmn/data`, and `@pkmn/dex` packages.

## Endpoints

```text
GET  /api/health
GET  /api/team
GET  /api/matches
POST /api/matches
GET  /api/matches/{id}
POST /api/matches/{id}/events
POST /api/matches/{id}/corrections
POST /api/matches/{id}/recommend
POST /api/matches/{id}/interpret
GET  /api/matches/{id}/export
POST /api/calculate/damage
POST /api/calculate/showdown
POST /api/calculate/showdown/batch
POST /api/calculate/speed
POST /api/knowledge/lookup
POST /api/knowledge/learnset
POST /api/knowledge/type-matchup
POST /api/meta/species
```

## Create a match

```bash
curl -s http://127.0.0.1:8765/api/matches \
  -H 'Content-Type: application/json' \
  -d '{
    "opponent_team": [
      "Charizard", "Garchomp", "Kingambit",
      "Aerodactyl", "Sylveon", "Farigiraf"
    ]
  }'
```

The service currently stores matches in memory. Export the immutable match log
before stopping the process. Durable storage is a later roadmap item.

## OpenAI boundary

`openai_adapter.py` calls the Responses API directly when `OPENAI_API_KEY` is
configured and requests a JSON-schema-constrained event proposal. Network,
authentication, schema, or model failure falls back to the local parser. No
failure can mutate battle state.

`codex_brain.py` makes Codex the final strategic selector. The deterministic
engine first produces an ordered catalog of eight legal paired actions with
scores, calculator evidence, response coverage, and principal counter-lines.
Codex must return one of those candidate IDs through a strict dynamic JSON
schema. The service then resolves the ID back to the original engine object;
model-generated moves, targets, damage, and states are structurally impossible.

Requests use `store: false` and a hashed, privacy-preserving match identifier.
The default is `gpt-5.6-sol` with `high` reasoning. Configure it with
`OPENAI_BATTLE_MODEL`, `OPENAI_REASONING_EFFORT`, and
`OPENAI_TIMEOUT_SECONDS`. Any API, refusal, parsing, candidate, or probability
validation failure preserves the deterministic anchor.
