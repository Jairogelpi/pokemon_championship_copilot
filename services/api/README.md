# Application API

The HTTP layer uses Python's standard library and serves both JSON endpoints and
the static battle console. Canonical battle state lives in the deterministic
engine; the HTTP layer does not maintain a second representation. Exact damage
calls cross a persistent local worker running the pinned official
`@smogon/calc` package.

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
