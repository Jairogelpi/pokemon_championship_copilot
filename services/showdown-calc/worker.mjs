import readline from "node:readline";
import { createRequire } from "node:module";
import {
  calculate,
  Field,
  Generations,
  Move,
  Pokemon,
  toID,
} from "@smogon/calc";

const require = createRequire(import.meta.url);
const { version: packageVersion } = require("@smogon/calc/package.json");
const STATUS = {
  burn: "brn",
  poison: "psn",
  toxic: "tox",
  paralysis: "par",
  sleep: "slp",
  freeze: "frz",
};

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
  const gen = Generations.get(generation);
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
    spreadMove: ["allAdjacent", "allAdjacentFoes"].includes(move.target),
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

function handle(message) {
  if (message.method === "health") {
    return {
      status: "ok",
      engine: "@smogon/calc",
      version: packageVersion,
      protocol: 1,
    };
  }
  if (message.method === "calculate") return calculateOne(message.params || {});
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
  throw new Error(`unsupported method: ${message.method}`);
}

const input = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
input.on("line", (line) => {
  let message = {};
  try {
    message = JSON.parse(line);
    process.stdout.write(`${JSON.stringify({ id: message.id, ok: true, result: handle(message) })}\n`);
  } catch (error) {
    process.stdout.write(`${JSON.stringify({
      id: message.id || null,
      ok: false,
      error: { name: error.name, message: error.message },
    })}\n`);
  }
});
