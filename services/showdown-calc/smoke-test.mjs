import { spawn } from "node:child_process";
import assert from "node:assert/strict";
import readline from "node:readline";

const child = spawn(process.execPath, [new URL("./worker.mjs", import.meta.url).pathname], {
  stdio: ["pipe", "pipe", "inherit"],
});
const output = readline.createInterface({ input: child.stdout, crlfDelay: Infinity });
const response = new Promise((resolve) => output.once("line", (line) => resolve(JSON.parse(line))));
child.stdin.write(`${JSON.stringify({
  id: "smoke",
  method: "calculate",
  params: {
    generation: 9,
    attacker: { name: "Garchomp", level: 50, nature: "Jolly", evs: { atk: 252 } },
    defender: { name: "Kingambit", level: 50, nature: "Adamant", evs: { hp: 252 } },
    move: { name: "Earthquake" },
    field: { gameType: "Doubles" },
  },
})}\n`);
const message = await response;
assert.equal(message.ok, true);
assert.equal(message.result.source, "@smogon/calc");
assert.equal(message.result.move, "Earthquake");
assert.ok(message.result.maximumDamage >= message.result.minimumDamage);
assert.ok(message.result.koProbabilityOnHit >= 0 && message.result.koProbabilityOnHit <= 1);
assert.equal(message.result.movePriority, 0);
assert.ok(message.result.attackerSpeed > 0);
child.kill();
