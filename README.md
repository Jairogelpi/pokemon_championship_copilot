# Pokémon Champions Battle Copilot

An unofficial, stateful battle assistant for Pokémon Champions doubles. Release
0.6 connects Codex and the risk-aware search kernel to sampled, reachable future
battle states grounded in the pinned upstream
[`@smogon/calc`](https://github.com/smogon/damage-calc) engine used by the
Pokémon Showdown damage calculator.

The project is built around one core rule: language models may interpret and
explain a battle, but they are not the source of truth for battle state,
mechanics, legality, damage, or speed.

## North-star goal

Build a decision system whose play quality can be demonstrated at the level of
a 2,000-point Master player for its supported team and regulation. Release 0.6
combines exhaustive bounded one-turn evaluation, a verified-sampled two-turn
search, and tool-grounded Codex selection; it is not yet evidence that this goal
has been reached.

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

The AI layer may:

- interpret text, voice, or screenshots as proposed structured events;
- model likely opponent intent and incomplete information;
- select among the deterministic engine's verified legal-action candidates;
- explain ranked recommendations.

Every AI-proposed event must be validated before it can mutate battle state.
Every AI-selected action is resolved by candidate ID against an immutable legal
catalog; the model cannot inject a move, target, damage value, or future state.

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

Python 3.11+ and Node.js 20+ are required. The JavaScript dependency is pinned
to `@smogon/calc` 0.11.0 in `package-lock.json`.

```bash
git clone https://github.com/Jairogelpi/pokemon_championship_copilot.git
cd pokemon_championship_copilot
npm ci --ignore-scripts
make check
make run
```

Open <http://127.0.0.1:8765>.

Docker is also supported:

```bash
docker build -t champions-copilot .
docker run --rm -p 8765:8765 champions-copilot
```

### Codex strategic brain

The application works without an API key and falls back to its deterministic
policy. To make Codex the final strategic selector and enable structured event
proposals through the Responses API:

```bash
export OPENAI_API_KEY="..."
export OPENAI_MODEL="gpt-5.6-sol"
export OPENAI_BATTLE_MODEL="gpt-5.6-sol"
export OPENAI_REASONING_EFFORT="high"
make run
```

Codex receives canonical state, beliefs, recent events, twelve verified player
candidates, calculator evidence, response coverage, and principal counter-lines.
It also receives the completed multi-turn principal line, search statistics,
reachable sampled outcomes, and explicit uncertainty boundaries. Multi-turn
search defaults on when Codex is configured and can be tuned through the
`MULTITURN_*` variables in `.env.example`.
Before selecting, the Responses API requires at least one read-only tool call.
Codex can inspect candidates and exact damage matrices, query any Gen 9 species,
move, item, ability, nature or type, load complete learnsets, test type matchups,
read dated meta priors, and calculate a hidden-but-learnable move against the
canonical live position. Tool results retain their source and cannot mutate the
match.
It returns a schema-constrained candidate ID, opponent-plan hypotheses, win
condition, failure mode, risk, and explanation. Invalid output, refusal,
timeout, or API failure falls back to the deterministic anchor. Event proposals
still require explicit confirmation before the engine applies them.

## Implemented in Codex strategist 0.6

- Complete in-memory match lifecycle and six-versus-six team preview.
- Bring-four and lead baseline for `GMKXPHAS7D`.
- Immutable event history with append-only corrections and deterministic replay.
- HP, status, boosts, switches, fainting, moves, field conditions, Mega, and
  confirmed-fact events.
- Opponent belief distributions that retain an `other` bucket.
- Legal paired-action generation, switches, targets, and duplicate-slot checks.
- Persistent Node worker around the Smogon/Pokémon Showdown damage
  library, with request IDs, timeouts, crash detection, health reporting, and
  batch isolation.
- Complete upstream roll distributions for every live player move/target pair,
  including Doubles spread reduction, current HP, status, boosts, items,
  abilities, weather, terrain, screens, Protect, and base move accuracy when
  represented by the compatibility profile.
- Explicit no-bulk, HP-invested, and maximum relevant-bulk opponent scenarios;
  confirming EVs collapses the uncertainty to one scenario.
- Exact combined double-target KO probability by convolving both moves' roll
  distributions and their base accuracy, rather than adding two OHKO numbers.
- Joint response expansion across both opposing slots (36 paired response
  categories with a residual `other` branch) and a probability-weighted worst
  20% tail instead of an unweighted scenario shortcut.
- Reverse Showdown calculations for every revealed opposing attack against all
  available player switch-ins. Ranking penalizes the strongest legal reply per
  opponent, including both targets of revealed spread moves.
- Risk-aware ranking with expected damage, combined KO chance, expected value,
  lower-tail value, strategic value, information value, and catastrophic-loss
  probability.
- Effective-speed primitive for independently testable speed checks.
- Queryable Gen 9 knowledge service backed by pinned `@pkmn/data` and
  `@pkmn/dex`: 876 species/forms, 685 moves, 249 items, 310 abilities, 25
  natures, 19 types, legal generation-compatible learnsets, and type matchups.
- Dated Regulation M-B S3 strategy snapshot covering the ordered moves, items,
  abilities, rank, and win rate for 25 meta Pokémon. Order is converted into a
  labelled heuristic prior and is never presented as observed action frequency.
- Daily GitHub Actions meta refresh at 05:17 UTC. It fetches the current M-B S3
  ranked pages, extracts source-reported move/item/ability usage and win rates,
  rejects partial or malformed results, runs every regression, and commits only
  a material data change. Git history preserves previous snapshots.
- Concrete opponent-action generation for both active slots: candidate moves,
  all live single targets, spread targets, Protect, legal bench switches, and
  a residual hidden-response branch.
- Exhaustive Cartesian enumeration of the bounded response model (192 legal
  joint replies in the default Charizard + Garchomp fixture), with explicit
  coverage mass and no discarded probability.
- One-turn simultaneous adversarial search that evaluates every player paired
  action against every modelled joint reply. Priority, scenario speed, Trick
  Room, faster KO, and Fake Out suppression affect the incoming-risk estimate.
- Inspectable principal counter-lines showing probability, outgoing and
  incoming damage, KO risk, utility, and speed-order evidence.
- Live risk-aware two-turn search over deterministic samples of exact hidden
  bulk scenarios, accuracy outcomes, and weighted Showdown damage rolls. Every
  sampled next state is reachable under its selected scenario; expected damage
  is never written into state as if it were an actual roll, and a sampled hidden
  EV, nature, and legal-ability profile remains locked throughout the future
  trajectory.
- Verified sampled turn transitions for simultaneous switching and retargeting,
  priority and dynamic final speed, Tailwind, Trick Room, Protect and Feint,
  Fake Out and flinching, Sucker Punch, spread targeting and friendly fire,
  Focus Sash and Sturdy, recoil and drain, simple secondary effects, burn and
  poison residual damage, and field-duration decrement.
- Deterministic systematic sampling of the complete opponent-response
  distribution, with node/time budgets, transposition caching, lower-tail risk,
  principal lines, and exact search telemetry.
- Strict promotion gate: an incomplete depth or too many unresolved mechanics
  preserves the exhaustive one-turn recommendation. Forced replacement,
  unsupported effects, complex counters, and unverified residuals become
  penalized uncertainty leaves instead of fabricated futures.
- Battle-console evidence for multi-turn depth, sampled outcomes, resolved
  fraction, promotion decision, and principal line, explicitly labelled
  sampled and non-exhaustive.
- Battle console with preview, live state, recommendations, alternatives,
  belief state, fast input, interpretation proposals, log, undo, and export.
- Codex strategic selection through the Responses API using strict Structured
  Outputs and a dynamic enum of verified candidate IDs.
- Responses function-calling loop with eight strict, read-only battle tools and
  an eight-call budget. The loop preserves reasoning items and returns every
  tool result to the model before final selection.
- On-demand verified damage for any active Pokémon, learnable move, and current
  target. Learnability comes from pinned `@pkmn/data`; rolls come from pinned
  `@smogon/calc`; unsupported moves return an error instead of an estimate.
- Auditable search-space telemetry: legal player actions, expanded opponent
  replies, evaluated action/response pairs, truncation count, horizon, and an
  explicit exhaustive-within-horizon flag.
- Fail-closed Codex boundary: no API key, refusal, timeout, malformed output,
  unknown action ID, or invalid probability mass preserves the deterministic
  recommendation.
- Visible Codex decision evidence: selected candidate, confidence, win
  condition, opponent hypotheses, main failure mode, and deterministic anchor.
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
- [ADR-0003: official Showdown calculator boundary](docs/adr/0003-showdown-calculator-boundary.md)
- [ADR-0004: verified multi-ply transition boundary](docs/adr/0004-multi-ply-transition-boundary.md)
- [ADR-0005: Codex strategic selector boundary](docs/adr/0005-codex-strategic-selector.md)
- [ADR-0006: tool-grounded battle research](docs/adr/0006-tool-grounded-battle-research.md)
- [ADR-0007: live verified-sampled multi-turn search](docs/adr/0007-live-verified-sampled-multiturn.md)

## Status

Codex strategist 0.6 is runnable and tested. When an API key is configured,
Codex owns the final strategic choice inside the verified candidate envelope;
otherwise the same endpoint degrades to the deterministic anchor. Damage values
are exact under each displayed Showdown Gen 9 scenario. Pokémon Champions is not a
one-to-one copy of Gen 9: Champions-only Mega stats, custom items, regulation
legality, and any divergent mechanics still need an authoritative dataset.
Those gaps are surfaced as assumptions and are never silently treated as exact.

This is not yet a validated 2,000-point Master policy. That claim still requires
the frozen benchmark and independent evaluation described in the evaluation
protocol.

## Calculator API

`POST /api/calculate/showdown` accepts the same conceptual inputs as the
upstream package:

```json
{
  "generation": 9,
  "attacker": {"name": "Garchomp", "level": 50, "nature": "Jolly", "evs": {"atk": 252}},
  "defender": {"name": "Kingambit", "level": 50, "nature": "Adamant", "evs": {"hp": 252}},
  "move": {"name": "Earthquake"},
  "field": {"gameType": "Doubles"}
}
```

The response includes all weighted rolls, absolute and percentage ranges,
conditional KO chance, KO chance including base accuracy, and the upstream
Showdown description. `POST /api/calculate/showdown/batch` accepts a `requests`
array. A bad matchup is isolated inside the batch instead of discarding the
valid calculations.

The mechanics knowledge endpoints are:

```text
POST /api/knowledge/lookup        species, move, item, ability, nature, or type
POST /api/knowledge/learnset      generation-compatible legal move pool
POST /api/knowledge/type-matchup  attacking type against a defending species
POST /api/meta/species            dated Regulation M-B S3 strategy entry
```

“100% response coverage” means every joint response generated from the current
revealed moves, six ranked meta candidates per active Pokémon, legal targets,
Protect, switches, and residual-other actions. It does not mean every hidden
four-move set or every future Champions-specific mechanic is known. The response
also reports the exact action/response pair count and whether the configured
one-turn horizon was exhaustively evaluated without truncation.

The scheduled updater can also be run manually from the Actions tab or locally:

```bash
python scripts/update_meta.py
```

The updater is fail-closed: an HTTP error, format change, fewer than 20 complete
entries, missing moves/items/abilities, duplicate ranks, or an invalid win rate
causes the job to fail without replacing the last valid snapshot.

## Disclaimer

This is an unofficial fan project and is not affiliated with or endorsed by
Nintendo, The Pokémon Company, or Game Freak. Users are responsible for
complying with the rules of any ladder, event, or tournament in which they
participate.
