import readline from "node:readline";
import { createRequire } from "node:module";
import { Generations as DataGenerations } from "@pkmn/data";
import { Dex } from "@pkmn/dex";
import {
  calculate,
  Field,
  Generations as CalcGenerations,
  Move,
  Pokemon,
  toID,
} from "@smogon/calc";

const require = createRequire(import.meta.url);
const { version: packageVersion } = require("@smogon/calc/package.json");
const { version: dataVersion } = require("@pkmn/data/package.json");
const { version: dexVersion } = require("@pkmn/dex/package.json");
const { getFinalSpeed } = require("@smogon/calc/dist/mechanics/util");
const dataGenerations = new DataGenerations(Dex);
const STATUS = {
  burn: "brn",
  poison: "psn",
  toxic: "tox",
  paralysis: "par",
  sleep: "slp",
  freeze: "frz",
};

function latestDataSpecies(name, requestedGeneration) {
  for (let generation = requestedGeneration; generation >= 1; generation -= 1) {
    const gen = dataGenerations.get(generation);
    const species = gen.species.get(name);
    if (species) return { gen, species, generation };
  }
  return null;
}

function latestCalcSpecies(name, requestedGeneration) {
  for (let generation = requestedGeneration; generation >= 1; generation -= 1) {
    const gen = CalcGenerations.get(generation);
    const species = gen.species.get(toID(name));
    if (species) return { species, generation };
  }
  return null;
}

function clamp(value, minimum, maximum) {
  return Math.max(minimum, Math.min(maximum, value));
}

function pokemonOptions(spec = {}) {
  const options = { ...spec };
  delete options.name;
  delete options.hpPercent;
  delete options.scenario;
  if (options.status) options.status = STATUS[options.status] || options.status;
  return options;
}

function buildPokemon(gen, spec = {}) {
  if (!spec.name) throw new Error("pokemon.name is required");
  const options = pokemonOptions(spec);
  if (!gen.species.get(toID(spec.name))) {
    const legacy = latestCalcSpecies(spec.name, gen.num);
    if (!legacy) throw new Error("pokemon species was not found in any supported generation");
    options.overrides = { ...legacy.species, ...(options.overrides || {}) };
  }
  const atFullHealth = new Pokemon(gen, spec.name, options);
  if (spec.hpPercent === undefined || spec.hpPercent === null) return atFullHealth;
  const current = Math.max(1, Math.round(atFullHealth.maxHP() * clamp(Number(spec.hpPercent), 0, 100) / 100));
  return new Pokemon(gen, spec.name, { ...options, curHP: current });
}

function convolve(left, right) {
  const result = new Map();
  for (const [leftDamage, leftCount] of left.entries()) {
    for (const [rightDamage, rightCount] of right.entries()) {
      const total = leftDamage + rightDamage;
      result.set(total, (result.get(total) || 0) + leftCount * rightCount);
    }
  }
  return result;
}

function damageDistribution(damage) {
  if (typeof damage === "number") return new Map([[damage, 1]]);
  if (!Array.isArray(damage) || damage.length === 0) return new Map([[0, 1]]);
  if (Array.isArray(damage[0])) {
    return damage.reduce(
      (distribution, hit) => convolve(distribution, damageDistribution(hit)),
      new Map([[0, 1]]),
    );
  }
  if (damage.length < 16) {
    return new Map([[damage.reduce((total, roll) => total + Number(roll), 0), 1]]);
  }
  const distribution = new Map();
  for (const roll of damage) distribution.set(Number(roll), (distribution.get(Number(roll)) || 0) + 1);
  return distribution;
}

function probabilityAtLeast(distribution, threshold) {
  let successes = 0;
  let total = 0;
  for (const [damage, count] of distribution.entries()) {
    total += count;
    if (damage >= threshold) successes += count;
  }
  return total ? successes / total : 0;
}

function expectedDamage(distribution) {
  let weighted = 0;
  let total = 0;
  for (const [damage, count] of distribution.entries()) {
    weighted += damage * count;
    total += count;
  }
  return total ? weighted / total : 0;
}

function normalizedField(spec = {}) {
  return new Field({ gameType: "Doubles", ...spec });
}

function calculateOne(request) {
  const generation = Number(request.generation || 9);
  const gen = CalcGenerations.get(generation);
  const attacker = buildPokemon(gen, request.attacker);
  const defender = buildPokemon(gen, request.defender);
  const move = new Move(gen, request.move?.name, {
    ...request.move,
    name: undefined,
    ability: request.attacker?.ability,
    item: request.attacker?.item,
  });
  const field = normalizedField(request.field);
  const result = calculate(gen, attacker, defender, move, field);
  const distribution = damageDistribution(result.damage);
  const [minimum, maximum] = result.range();
  const defenderHP = defender.maxHP();
  const currentHP = defender.curHP();
  const mean = expectedDamage(distribution);
  const moveData = gen.moves.get(toID(request.move.name));
  const baseAccuracy = moveData?.accuracy === true ? 1 : clamp(Number(moveData?.accuracy || 100) / 100, 0, 1);
  const koOnHit = probabilityAtLeast(distribution, currentHP);
  const rolls = [...distribution.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([damage, weight]) => ({ damage, weight }));

  return {
    source: "@smogon/calc",
    sourceVersion: packageVersion,
    generation,
    gameType: field.gameType,
    scenario: request.scenario || null,
    attacker: attacker.name,
    defender: defender.name,
    move: move.name,
    moveCategory: move.category,
    moveType: move.type,
    movePriority: Number(move.priority || 0),
    moveTarget: move.target,
    spreadMove: ["allAdjacent", "allAdjacentFoes"].includes(move.target),
    attackerSpeed: attacker.stats.spe,
    defenderSpeed: defender.stats.spe,
    defenderMaxHP: defenderHP,
    defenderCurrentHP: currentHP,
    minimumDamage: minimum,
    maximumDamage: maximum,
    expectedDamage: Number(mean.toFixed(4)),
    minimumPercent: Number((minimum * 100 / defenderHP).toFixed(3)),
    maximumPercent: Number((maximum * 100 / defenderHP).toFixed(3)),
    expectedPercent: Number((mean * 100 / defenderHP).toFixed(3)),
    baseAccuracyProbability: Number(baseAccuracy.toFixed(4)),
    koProbabilityOnHit: Number(koOnHit.toFixed(6)),
    koProbabilityWithBaseAccuracy: Number((koOnHit * baseAccuracy).toFixed(6)),
    rolls,
    koChanceText: result.kochance(false),
    description: result.fullDesc("%", false),
  };
}

function speedOne(request) {
  const generation = Number(request.generation || 9);
  const gen = CalcGenerations.get(generation);
  const pokemon = buildPokemon(gen, request.pokemon);
  const field = normalizedField(request.field);
  const side = field.attackerSide;
  return {
    source: "@smogon/calc",
    sourceVersion: packageVersion,
    generation,
    pokemon: pokemon.name,
    rawSpeed: pokemon.rawStats.spe,
    boostedSpeed: pokemon.stats.spe,
    finalSpeed: getFinalSpeed(gen, pokemon, field, side),
    maxHP: pokemon.maxHP(),
    trickRoom: Boolean(request.trickRoom),
    tailwind: side.isTailwind,
  };
}

function compactEffect(effect) {
  if (!effect) throw new Error("entry was not found in the selected generation");
  return {
    id: effect.id,
    name: effect.name,
    kind: effect.kind || effect.effectType,
    generationIntroduced: effect.gen,
    exists: Boolean(effect.exists),
    isNonstandard: effect.isNonstandard || null,
    description: effect.desc || effect.shortDesc || "",
    shortDescription: effect.shortDesc || "",
  };
}

function compactMove(move) {
  return {
    ...compactEffect(move),
    type: move.type,
    category: move.category,
    basePower: move.basePower,
    accuracy: move.accuracy,
    pp: move.pp,
    priority: move.priority,
    target: move.target,
    flags: Object.keys(move.flags || {}).sort(),
    multihit: move.multihit || null,
    multiaccuracy: Boolean(move.multiaccuracy),
    critRatio: move.critRatio || 0,
    willCrit: Boolean(move.willCrit),
    recoil: move.recoil || null,
    drain: move.drain || null,
    heal: move.heal || null,
    status: move.status || null,
    boosts: move.boosts || null,
    volatileStatus: move.volatileStatus || null,
    weather: move.weather || null,
    terrain: move.terrain || null,
    sideCondition: move.sideCondition || null,
    pseudoWeather: move.pseudoWeather || null,
    slotCondition: move.slotCondition || null,
    forceSwitch: Boolean(move.forceSwitch),
    secondary: move.secondary || null,
    secondaries: move.secondaries || null,
    selfEffect: move.self || null,
  };
}

function compactSpecies(species) {
  return {
    ...compactEffect(species),
    baseSpecies: species.baseSpecies,
    forme: species.forme || null,
    types: species.types,
    baseStats: species.baseStats,
    baseStatTotal: species.bst,
    abilities: species.abilities,
    weightKg: species.weightkg,
    preEvolution: species.prevo || null,
    evolutions: species.evos || [],
    otherFormes: species.otherFormes || [],
    tier: species.tier || null,
    doublesTier: species.doublesTier || null,
  };
}

function lookupOne(request) {
  const generation = Number(request.generation || 9);
  const gen = dataGenerations.get(generation);
  const kind = String(request.kind || "").toLowerCase();
  const name = request.name;
  if (!name) throw new Error("name is required");
  let entry;
  if (kind === "species" || kind === "pokemon") {
    const resolved = latestDataSpecies(name, generation);
    if (!resolved) throw new Error("species was not found in any supported generation");
    entry = { ...compactSpecies(resolved.species), dataGeneration: resolved.generation };
  } else if (kind === "move") {
    entry = compactMove(gen.moves.get(name));
  } else if (kind === "item") {
    const item = gen.items.get(name);
    entry = { ...compactEffect(item), flingPower: item.fling?.basePower || null };
  } else if (kind === "ability") {
    entry = compactEffect(gen.abilities.get(name));
  } else if (kind === "nature") {
    const nature = gen.natures.get(name);
    entry = { ...compactEffect(nature), plus: nature.plus || null, minus: nature.minus || null };
  } else if (kind === "type") {
    const type = gen.types.get(name);
    entry = {
      ...compactEffect(type),
      category: type.category || null,
      effectiveness: type.effectiveness,
    };
  } else {
    throw new Error("kind must be species, move, item, ability, nature, or type");
  }
  return {
    source: "@pkmn/dex",
    sourceVersion: dexVersion,
    generation,
    entry,
  };
}

async function learnset(request) {
  const generation = Number(request.generation || 9);
  const resolved = latestDataSpecies(request.species, generation);
  if (!resolved) throw new Error("species was not found in any supported generation");
  const { gen, species } = resolved;
  const restriction = request.restriction || undefined;
  const learnable = await gen.learnsets.learnable(species.name, restriction);
  const moves = Object.entries(learnable || {})
    .map(([id, sources]) => {
      const move = gen.moves.get(id);
      return move ? { ...compactMove(move), sources } : null;
    })
    .filter(Boolean)
    .sort((left, right) => left.name.localeCompare(right.name));
  return {
    source: "@pkmn/data + @pkmn/dex",
    dataVersion,
    dexVersion,
    generation,
    dataGeneration: resolved.generation,
    restriction: restriction || "generation-compatible transfers",
    species: compactSpecies(species),
    moveCount: moves.length,
    moves,
  };
}

function typeMatchup(request) {
  const generation = Number(request.generation || 9);
  const gen = dataGenerations.get(generation);
  const attackType = gen.types.get(request.attackType);
  const resolved = latestDataSpecies(request.defender, generation);
  const defender = resolved?.species;
  if (!attackType) throw new Error("attackType was not found");
  if (!defender) throw new Error("defender species was not found");
  return {
    source: "@pkmn/data + @pkmn/dex",
    generation,
    defenderDataGeneration: resolved?.generation || generation,
    attackType: attackType.name,
    defender: defender.name,
    defenderTypes: defender.types,
    multiplier: attackType.totalEffectiveness(defender),
  };
}

function catalog() {
  const gen = dataGenerations.get(9);
  return {
    source: "@pkmn/data + @pkmn/dex",
    generation: 9,
    species: [...gen.species].length,
    moves: [...gen.moves].length,
    items: [...gen.items].length,
    abilities: [...gen.abilities].length,
    natures: [...gen.natures].length,
    types: [...gen.types].length,
  };
}

async function handle(message) {
  if (message.method === "health") {
    return {
      status: "ok",
      engine: "@smogon/calc",
      version: packageVersion,
      knowledge: {
        engine: "@pkmn/data + @pkmn/dex",
        dataVersion,
        dexVersion,
        catalog: catalog(),
      },
      protocol: 2,
    };
  }
  if (message.method === "calculate") return calculateOne(message.params || {});
  if (message.method === "speed") return speedOne(message.params || {});
  if (message.method === "batch") {
    const requests = message.params?.requests;
    if (!Array.isArray(requests)) throw new Error("batch requests must be an array");
    return {
      results: requests.map((request) => {
        try {
          return { ok: true, result: calculateOne(request) };
        } catch (error) {
          return { ok: false, error: { name: error.name, message: error.message } };
        }
      }),
    };
  }
  if (message.method === "lookup") return lookupOne(message.params || {});
  if (message.method === "learnset") return learnset(message.params || {});
  if (message.method === "type_matchup") return typeMatchup(message.params || {});
  if (message.method === "catalog") return catalog();
  throw new Error(`unsupported method: ${message.method}`);
}

const input = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
input.on("line", async (line) => {
  let message = {};
  try {
    message = JSON.parse(line);
    const result = await handle(message);
    process.stdout.write(`${JSON.stringify({ id: message.id, ok: true, result })}\n`);
  } catch (error) {
    process.stdout.write(`${JSON.stringify({
      id: message.id || null,
      ok: false,
      error: { name: error.name, message: error.message },
    })}\n`);
  }
});
