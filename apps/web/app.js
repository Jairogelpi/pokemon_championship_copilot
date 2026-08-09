const state = {
  health: null,
  team: null,
  match: null,
  proposedEvent: null,
};

const $ = (selector) => document.querySelector(selector);

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.message || payload.error || "Request failed");
  return payload;
}

function toast(message, error = false) {
  const node = $("#toast");
  node.textContent = message;
  node.classList.toggle("error", error);
  node.classList.remove("hidden");
  window.clearTimeout(toast.timer);
  toast.timer = window.setTimeout(() => node.classList.add("hidden"), 3400);
}

function buildPreviewInputs() {
  const examples = ["Charizard", "Garchomp", "Kingambit", "Aerodactyl", "Sylveon", "Farigiraf"];
  $("#opponent-inputs").innerHTML = examples
    .map(
      (name, index) => `
        <label class="opponent-field">
          <input aria-label="Opponent Pokémon ${index + 1}" value="${name}" required maxlength="32" />
        </label>`,
    )
    .join("");
}

function renderHealth() {
  const healthy = state.health?.showdown?.available === true;
  const catalog = state.health?.showdown?.knowledge?.catalog;
  $("#health-dot").classList.toggle("ok", healthy);
  $("#health-label").textContent = healthy
    ? `Showdown ${state.health.showdown.version} · ${catalog?.species || "?"} Pokémon · ${catalog?.moves || "?"} moves`
    : "Showdown calculator unavailable";
  if (state.health?.openai_configured) {
    $("#interpreter-source")?.replaceChildren(document.createTextNode("OPENAI + FALLBACK"));
  }
}

function renderTeam() {
  if (!state.team) return;
  $("#team-name").textContent = `${state.team.name} · ${state.team.id}`;
  const hasUnverified = state.team.members.some((member) => !member.set_verified);
  if (hasUnverified) {
    $("#team-warning").textContent = state.team.warning;
    $("#team-warning").classList.remove("hidden");
  }
}

function pokemonCard(member) {
  const boosts = Object.entries(member.boosts || {})
    .filter(([, value]) => value !== 0)
    .map(([stat, value]) => `<span class="state-tag">${stat} ${value > 0 ? "+" : ""}${value}</span>`)
    .join("");
  const tags = [
    member.status ? `<span class="state-tag">${escapeHtml(member.status)}</span>` : "",
    member.protected ? '<span class="state-tag accent">Protect</span>' : "",
    member.mega_evolved ? '<span class="state-tag accent">Mega</span>' : "",
    boosts,
  ].join("");
  return `
    <article class="pokemon-card ${member.fainted ? "fainted" : ""}">
      <div class="pokemon-top">
        <div>
          <div class="pokemon-name">${escapeHtml(member.name)}</div>
          <div class="pokemon-meta">${escapeHtml(member.role)}${member.item ? ` · ${escapeHtml(member.item)}` : ""}</div>
        </div>
        <div class="hp-value">${member.hp}%</div>
      </div>
      <div class="hp-bar"><span class="${member.hp <= 30 ? "low" : ""}" style="width:${member.hp}%"></span></div>
      <div class="state-tags">${tags}</div>
    </article>`;
}

function benchCard(member) {
  return `<div class="bench-card"><strong>${escapeHtml(member.name)}</strong><span>${member.hp}% · ${escapeHtml(member.status || "ready")}</span></div>`;
}

function aliveCount(side) {
  return Object.values(side.roster).filter((member) => !member.fainted).length;
}

function renderField() {
  const battle = state.match.state;
  $("#turn-number").textContent = battle.turn;
  $("#state-revision").textContent = battle.revision;
  $("#field-weather").textContent = battle.field.weather || "—";
  $("#field-terrain").textContent = battle.field.terrain || "—";
  $("#field-trick-room").textContent = battle.field.trick_room_turns;
  $("#field-tailwind").textContent = battle.player.side_conditions.tailwind || 0;
  $("#player-alive").textContent = `${aliveCount(battle.player)} alive`;
  $("#opponent-alive").textContent = `${aliveCount(battle.opponent)} possible`;
  $("#player-active").innerHTML = battle.player.active
    .map((id) => pokemonCard(battle.player.roster[id]))
    .join("");
  $("#player-bench").innerHTML = battle.player.bench
    .map((id) => benchCard(battle.player.roster[id]))
    .join("");
  $("#opponent-active").innerHTML = battle.opponent.active
    .map((id) => pokemonCard(battle.opponent.roster[id]))
    .join("");
}

function renderRecommendation() {
  const recommendation = state.match.recommendation;
  if (!recommendation) {
    $("#primary-recommendation").innerHTML = "<p>No legal paired action is currently available.</p>";
    $("#alternatives").innerHTML = "";
    return;
  }
  const primary = recommendation.primary;
  const battle = state.match.state;
  const damageRows = primary.damage
    .map((estimate) => {
      const actor = battle.player.roster[estimate.actor]?.name || estimate.actor;
      const target = battle.opponent.roster[estimate.target]?.name || estimate.target;
      const scenarios = estimate.scenarios
        .map(
          (scenario) => `
            <div>
              <span>${escapeHtml(scenario.name.replaceAll("_", " "))} · ${(scenario.weight * 100).toFixed(0)}%</span>
              <b>${scenario.minimum_percent.toFixed(1)}–${scenario.maximum_percent.toFixed(1)}%</b>
              <em>${(scenario.ko_probability * 100).toFixed(0)}% KO</em>
            </div>`,
        )
        .join("");
      return `
        <div class="damage-line">
          <div><strong>${escapeHtml(actor)} · ${escapeHtml(estimate.move)} → ${escapeHtml(target)}</strong><span>${estimate.scenario_count} explicit bulk scenarios</span></div>
          <b>${estimate.minimum_percent.toFixed(1)}–${estimate.maximum_percent.toFixed(1)}%</b>
          <em>${(estimate.knockout_probability_weighted * 100).toFixed(0)}% OHKO</em>
          <details class="scenario-details"><summary>Inspect exact scenarios</summary>${scenarios}</details>
        </div>`;
    })
    .join("");
  const threatRows = primary.threats
    .map((estimate) => {
      const actor = battle.opponent.roster[estimate.actor]?.name || estimate.actor;
      const target = battle.player.roster[estimate.target]?.name || estimate.target;
      return `<div class="threat-line"><span>${escapeHtml(actor)} · ${escapeHtml(estimate.move)} → ${escapeHtml(target)} · priority ${estimate.move_priority} · Spe ${estimate.attacker_speed}</span><b>${estimate.minimum_percent.toFixed(1)}–${estimate.maximum_percent.toFixed(1)}%</b><em>${(estimate.knockout_probability_weighted * 100).toFixed(0)}% KO</em></div>`;
    })
    .join("");
  const counterRows = (primary.principal_lines || [])
    .map(
      (line) => `
        <div class="counter-line">
          <div><strong>${escapeHtml(line.response)}</strong><span>${(line.probability * 100).toFixed(1)}% model prior · utility ${line.utility.toFixed(1)}</span></div>
          <b>in ${line.incoming_damage_percent.toFixed(1)}%</b>
          <em>${(line.incoming_knockout_probability * 100).toFixed(0)}% incoming KO</em>
        </div>`,
    )
    .join("");
  $("#primary-recommendation").innerHTML = `
    <h2>${escapeHtml(primary.label)}</h2>
    <p>${escapeHtml(recommendation.rationale)}</p>
    <div class="score-row">
      <div><span>Expected damage</span><strong>${primary.score.expected_damage_percent.toFixed(1)}%</strong></div>
      <div><span>Combined KO</span><strong>${(primary.score.knockout_probability * 100).toFixed(0)}%</strong></div>
      <div><span>Known reply KO</span><strong>${(primary.score.incoming_knockout_probability * 100).toFixed(0)}%</strong></div>
      <div><span>Risk</span><strong>${escapeHtml(recommendation.risk).toUpperCase()}</strong></div>
    </div>
    <div class="damage-grid">${damageRows || '<div class="calc-warning">This line has no direct-damage calculation.</div>'}</div>
    ${threatRows ? `<div class="threat-grid"><span>Worst damage replies from revealed + meta candidates</span>${threatRows}</div>` : ""}
    ${counterRows ? `<details class="counter-grid" open><summary>Worst concrete counter-lines</summary>${counterRows}</details>` : ""}
    <p class="calc-note">${escapeHtml(recommendation.calculator.compatibility || recommendation.calculator.message || "@smogon/calc")} · ${recommendation.response_model.scenarios_evaluated} simultaneous rival responses · ${(recommendation.response_model.coverage_mass * 100).toFixed(1)}% of the bounded model enumerated · worst 20% tail evaluated.</p>`;
  $("#alternatives").innerHTML = recommendation.alternatives
    .map(
      (alternative, index) => `
        <div class="alternative">
          <span class="alternative-index">0${index + 2}</span>
          <strong>${escapeHtml(alternative.label)}</strong>
          <span class="alternative-score">KO ${(alternative.score.knockout_probability * 100).toFixed(0)}% · ${alternative.score.final_score.toFixed(1)}</span>
        </div>`,
    )
    .join("");
}

function topCategories(distribution) {
  return Object.entries(distribution)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 3);
}

function renderBeliefs() {
  const battle = state.match.state;
  const beliefs = state.match.beliefs.opponent;
  const candidateActions = state.match.recommendation?.response_model?.candidate_actions || {};
  $("#beliefs").innerHTML = battle.opponent.active
    .map((id) => {
      const belief = beliefs[id];
      const member = battle.opponent.roster[id];
      const moveMass = (candidateActions[id] || [])
        .filter((action) => action.kind === "move")
        .reduce((totals, action) => {
          totals[action.move] = (totals[action.move] || 0) + action.probability;
          return totals;
        }, {});
      const concrete = Object.entries(moveMass)
        .sort((a, b) => b[1] - a[1])
        .slice(0, 3);
      return `
        <div class="belief-item">
          <div class="belief-name"><strong>${escapeHtml(member.name)}</strong><span>Mega ${(belief.mega_probability * 100).toFixed(0)}%</span></div>
          <div class="belief-bars">
            ${topCategories(belief.action_categories)
              .map(([name, probability]) => `<div>${escapeHtml(name)} ${(probability * 100).toFixed(0)}%</div>`)
              .join("")}
          </div>
          <div class="move-priors">
            ${concrete
              .map(([move, probability]) => `<span>${escapeHtml(move)} ${(probability * 100).toFixed(0)}%</span>`)
              .join("") || "<span>No ranked move prior</span>"}
          </div>
        </div>`;
    })
    .join("");
}

function formatEvent(event) {
  const payload = event.payload;
  const battle = state.match.state;
  const side = payload.side ? battle[payload.side] : null;
  const member = side?.roster[payload.pokemon];
  if (event.type === "turn_started") return `Turn ${payload.turn} started`;
  if (event.type === "hp_changed") return `${member?.name || "Pokémon"} HP → ${payload.hp ?? payload.delta}`;
  if (event.type === "move_used") return `${member?.name || "Pokémon"} used ${payload.move}`;
  if (event.type === "status_set") return `${member?.name || "Pokémon"} status → ${payload.status}`;
  if (event.type === "faint") return `${member?.name || "Pokémon"} fainted`;
  if (event.type === "mega_evolved") return `${member?.name || "Pokémon"} Mega Evolved`;
  return event.type.replaceAll("_", " ");
}

function renderLog() {
  const events = state.match.events;
  $("#event-count").textContent = `${events.length} event${events.length === 1 ? "" : "s"}`;
  $("#battle-log").innerHTML = events.length
    ? [...events]
        .reverse()
        .map((event, index) => `<li><span>${String(events.length - index).padStart(2, "0")}</span>${escapeHtml(formatEvent(event))}</li>`)
        .join("")
    : '<li class="empty-log">No events recorded yet.</li>';
  const correctedIds = new Set(
    events.filter((event) => event.type === "correction").map((event) => event.payload.target_event_id),
  );
  const lastEffective = [...events]
    .reverse()
    .find((event) => event.type !== "correction" && !correctedIds.has(event.id));
  $("#undo-event-button").disabled = !lastEffective;
  $("#undo-event-button").dataset.eventId = lastEffective?.id || "";
}

function populateActorSelect() {
  const battle = state.match.state;
  $("#event-actor").innerHTML = ["player", "opponent"]
    .flatMap((sideName) =>
      Object.values(battle[sideName].roster).map(
        (member) => `<option value="${sideName}:${member.id}">${sideName === "player" ? "YOU" : "OPP"} · ${escapeHtml(member.name)}</option>`,
      ),
    )
    .join("");
}

function renderMatch() {
  $("#setup-view").classList.add("hidden");
  $("#battle-view").classList.remove("hidden");
  renderField();
  renderRecommendation();
  renderBeliefs();
  renderLog();
  populateActorSelect();
  updateEventForm();
}

function updateEventForm() {
  const type = $("#event-type").value;
  const label = $("#event-value-label");
  const input = $("#event-value");
  const help = $("#event-help");
  const noValue = ["faint", "mega_evolved"].includes(type);
  label.classList.toggle("hidden", noValue);
  input.required = !noValue;
  const config = {
    hp_changed: ["Remaining HP %", "62", "Enter the remaining HP percentage."],
    move_used: ["Move", "Icy Wind", "Record only the move that was actually revealed."],
    status_set: ["Status", "burn", "Use burn, poison, toxic, paralysis, sleep, freeze, or blank to clear."],
    boost_changed: ["Stat and delta", "spe -1", "Example: spe -1 or atk +2."],
    switch: ["Incoming Pokémon", "pokemon id", "Use the internal ID shown in the actor selector."],
    fact_revealed: ["Fact and value", "item Leftovers", "Use item, ability, nature, level, tera_type, or EVs such as evs hp=252,def=252."],
  };
  if (config[type]) {
    label.childNodes[0].textContent = `${config[type][0]} `;
    input.placeholder = config[type][1];
    help.textContent = config[type][2];
  } else {
    help.textContent = noValue ? "This event does not require a value." : "";
  }
}

function buildEvent() {
  const [side, pokemon] = $("#event-actor").value.split(":");
  const type = $("#event-type").value;
  const value = $("#event-value").value.trim();
  if (type === "hp_changed") return { type, payload: { side, pokemon, hp: Number(value) } };
  if (type === "move_used") return { type, payload: { side, pokemon, move: value } };
  if (type === "status_set") return { type, payload: { side, pokemon, status: value || null } };
  if (type === "faint" || type === "mega_evolved") return { type, payload: { side, pokemon } };
  if (type === "boost_changed") {
    const [stat, delta] = value.split(/\s+/);
    return { type, payload: { side, pokemon, stat, delta: Number(delta) } };
  }
  if (type === "switch") return { type, payload: { side, out: pokemon, in: value } };
  if (type === "fact_revealed") {
    const [key, ...parts] = value.split(/\s+/);
    let factValue = parts.join(" ");
    if (key === "level") factValue = Number(factValue);
    if (["evs", "ivs"].includes(key)) {
      factValue = Object.fromEntries(
        factValue.split(",").map((entry) => {
          const [stat, amount] = entry.split("=");
          return [stat.trim(), Number(amount)];
        }),
      );
    }
    return { type, payload: { side, pokemon, key, value: factValue } };
  }
  throw new Error("Unsupported event type");
}

async function applyEvent(event) {
  state.match = await api(`/api/matches/${state.match.state.match_id}/events`, {
    method: "POST",
    body: JSON.stringify(event),
  });
  state.proposedEvent = null;
  $("#proposal").classList.add("hidden");
  renderMatch();
}

function renderProposal(proposal) {
  state.proposedEvent = proposal.event;
  $("#interpreter-source").textContent = proposal.source === "openai" ? "OPENAI" : "LOCAL PARSER";
  $("#proposal").innerHTML = `
    <div class="proposal-head"><strong>${escapeHtml(proposal.event.type)}</strong><span>${(proposal.confidence * 100).toFixed(0)}% confidence</span></div>
    <code>${escapeHtml(JSON.stringify(proposal.event.payload, null, 2))}</code>
    <p class="form-help">${escapeHtml(proposal.explanation)}</p>
    <div class="proposal-actions">
      <button id="confirm-proposal" class="button button-primary button-small" type="button">Confirm event</button>
      <button id="discard-proposal" class="button button-ghost button-small" type="button">Discard</button>
    </div>`;
  $("#proposal").classList.remove("hidden");
  $("#confirm-proposal").addEventListener("click", () => applyEvent(state.proposedEvent));
  $("#discard-proposal").addEventListener("click", () => $("#proposal").classList.add("hidden"));
}

async function startMatch(event) {
  event.preventDefault();
  const opponentTeam = [...document.querySelectorAll("#opponent-inputs input")].map((input) => input.value.trim());
  try {
    state.match = await api("/api/matches", {
      method: "POST",
      body: JSON.stringify({ opponent_team: opponentTeam }),
    });
    renderMatch();
    toast("Battle state created. Recommendation ready.");
  } catch (error) {
    toast(error.message, true);
  }
}

async function submitEvent(event) {
  event.preventDefault();
  try {
    await applyEvent(buildEvent());
    $("#event-value").value = "";
    toast("Event recorded and state replay remains valid.");
  } catch (error) {
    toast(error.message, true);
  }
}

async function interpret(event) {
  event.preventDefault();
  try {
    const result = await api(`/api/matches/${state.match.state.match_id}/interpret`, {
      method: "POST",
      body: JSON.stringify({ text: $("#interpret-text").value }),
    });
    renderProposal(result);
  } catch (error) {
    toast(error.message, true);
  }
}

async function nextTurn() {
  try {
    await applyEvent({ type: "turn_started", payload: { turn: state.match.state.turn + 1 } });
    toast(`Turn ${state.match.state.turn} started.`);
  } catch (error) {
    toast(error.message, true);
  }
}

async function undoLastEvent() {
  const targetEventId = $("#undo-event-button").dataset.eventId;
  if (!targetEventId) return;
  try {
    state.match = await api(`/api/matches/${state.match.state.match_id}/corrections`, {
      method: "POST",
      body: JSON.stringify({ target_event_id: targetEventId, replacement: null }),
    });
    renderMatch();
    toast("Correction appended. Canonical state rebuilt from history.");
  } catch (error) {
    toast(error.message, true);
  }
}

async function exportMatch() {
  try {
    const payload = await api(`/api/matches/${state.match.state.match_id}/export`);
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `champions-match-${state.match.state.match_id}.json`;
    anchor.click();
    URL.revokeObjectURL(url);
  } catch (error) {
    toast(error.message, true);
  }
}

function newMatch() {
  state.match = null;
  state.proposedEvent = null;
  $("#battle-view").classList.add("hidden");
  $("#setup-view").classList.remove("hidden");
  window.scrollTo({ top: 0, behavior: "smooth" });
}

async function init() {
  buildPreviewInputs();
  $("#preview-form").addEventListener("submit", startMatch);
  $("#event-form").addEventListener("submit", submitEvent);
  $("#interpret-form").addEventListener("submit", interpret);
  $("#event-type").addEventListener("change", updateEventForm);
  $("#next-turn-button").addEventListener("click", nextTurn);
  $("#undo-event-button").addEventListener("click", undoLastEvent);
  $("#export-button").addEventListener("click", exportMatch);
  $("#new-match-button").addEventListener("click", newMatch);
  try {
    [state.health, state.team] = await Promise.all([api("/api/health"), api("/api/team")]);
    renderHealth();
    renderTeam();
  } catch (error) {
    renderHealth();
    toast(error.message, true);
  }
}

document.addEventListener("DOMContentLoaded", init);
