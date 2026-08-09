# Pokémon Champions Battle Copilot

An unofficial, stateful battle assistant for Pokémon Champions doubles.

The project is built around one core rule: language models may interpret and
explain a battle, but they are not the source of truth for battle state,
mechanics, legality, damage, or speed.

## North-star goal

Build a decision system whose play quality can be demonstrated at the level of
a 2,000-point Master player for its supported team and regulation.

This is not a promise to predict every move. Pokémon contains hidden
information, simultaneous choices, novel sets, and randomness. The system must
instead maintain calibrated beliefs about the opponent, search multiple turns,
and select actions that remain strong across plausible responses—including
when its most likely prediction is wrong.

The copilot must:

- recommend the four Pokémon and lead from team preview;
- preserve the complete state of a match without conversational memory drift;
- calculate damage, speed, priority, and field interactions deterministically;
- track revealed opponent moves, items, abilities, and possible Mega Evolution;
- infer opponent sets, leads, targets, switches, Protect usage, and Mega choice
  as probability distributions rather than unsupported facts;
- generate legal joint actions and opponent response sets;
- search tactical lines and rank actions by expected value, worst-case safety,
  information value, and preservation of the match win condition;
- adapt its priors from versioned meta snapshots and the user's match history;
- explain recommendations in concise competitive language;
- replay a battle from its event log for review and debugging.

The first supported player team is the washy Ranked Season M-4 replica team
`GMKXPHAS7D`. Froslass uses Blizzard / Shadow Ball / Aurora Veil / Protect.

## Repository shape

```text
apps/web/                 Dependency-free battle console
services/api/             Standard-library HTTP API and optional AI orchestration
packages/battle-engine/   Deterministic state, belief, mechanics, and decision engine
data/                     Versioned Champions-specific datasets
docs/                     Product scope and architecture decisions
tests/                    Cross-layer scenarios and battle fixtures
```

## System boundaries

The deterministic engine owns:

- active Pokémon, bench, fainted Pokémon, HP, status, boosts, and volatile state;
- weather, terrain, Trick Room, Tailwind, hazards, and turn counters;
- legal actions, targeting, priority, speed order, and damage ranges;
- the append-only battle event log and state reconstruction.

The AI layer is limited to:

- interpreting text, voice, or screenshots as proposed structured events;
- modelling likely opponent intent and incomplete information;
- explaining ranked recommendations.

Every AI-proposed event must be validated before it can mutate battle state.

The Master Decision Engine is divided into five independently testable layers:

1. Exact mechanics and legal action generation.
2. Belief state over hidden opponent information.
3. Tactical search over simultaneous actions and uncertain responses.
4. Strategic evaluation of board control, resources, and win conditions.
5. Explanation and learning from completed matches.

## Initial milestone

The first milestone is a complete manually operated match:

1. Load the fixed player team.
2. Enter the opponent's six Pokémon.
3. Receive a recommended four and lead.
4. Record every turn through structured controls or text.
5. Receive a primary action, alternatives, risk, and explanation.
6. Export and replay the battle log without state divergence.

## Run it

The current implementation has no required third-party dependencies. Python
3.11 or newer is sufficient.

```bash
git clone https://github.com/Jairogelpi/pokemon_championship_copilot.git
cd pokemon_championship_copilot
make check
make run
```

Open <http://127.0.0.1:8765>.

Docker is also supported:

```bash
docker build -t champions-copilot .
docker run --rm -p 8765:8765 champions-copilot
```

### Optional OpenAI interpretation

The application works without an API key using its conservative local text
parser. To enable structured event proposals through the Responses API:

```bash
export OPENAI_API_KEY="..."
export OPENAI_MODEL="gpt-5.6"
make run
```

OpenAI is an interpretation boundary only. A proposal always requires explicit
confirmation in the UI before the deterministic engine applies it.

## Implemented in baseline 0.1

- Complete in-memory match lifecycle and six-versus-six team preview.
- Bring-four and lead baseline for `GMKXPHAS7D`.
- Immutable event history with append-only corrections and deterministic replay.
- HP, status, boosts, switches, fainting, moves, field conditions, Mega, and
  confirmed-fact events.
- Opponent belief distributions that retain an `other` bucket.
- Legal paired-action generation, switches, targets, and duplicate-slot checks.
- Risk-aware baseline ranking with expected value, lower-tail value, strategic
  value, information value, and catastrophic-loss probability.
- Damage-range and effective-speed primitives.
- Battle console with preview, live state, recommendations, alternatives,
  belief state, fast input, interpretation proposals, log, undo, and export.
- Real HTTP integration tests and GitHub Actions CI.

This is a functional baseline, not a validated Master 2000 policy. See
[Implementation status](docs/implementation-status.md) for the exact boundary.

See:

- [V1 scope](docs/v1-scope.md)
- [Master Decision Engine specification](docs/master-decision-engine.md)
- [Evaluation protocol](docs/evaluation-protocol.md)
- [Roadmap and quality gates](docs/roadmap.md)
- [Implementation status](docs/implementation-status.md)
- [ADR-0001: deterministic core](docs/adr/0001-deterministic-core.md)
- [ADR-0002: belief-state planning](docs/adr/0002-belief-state-planning.md)

## Status

Baseline 0.1 is runnable and tested. `STATE_ENGINE_VERIFIED` and every
competitive quality gate remain provisional until the larger frozen corpus and
independent validation described in the evaluation protocol exist.

## Disclaimer

This is an unofficial fan project and is not affiliated with or endorsed by
Nintendo, The Pokémon Company, or Game Freak. Users are responsible for
complying with the rules of any ladder, event, or tournament in which they
participate.
