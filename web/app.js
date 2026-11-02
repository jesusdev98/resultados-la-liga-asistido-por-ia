// Prueba DeepSeek — local LLM (WebLLM/WebGPU) + La Liga data from the Python API.

import { setupWebMcp } from "./webmcp.js";
import { runAgent, runScopeReply } from "./agent.js";
import { appendMcpResources } from "./mcp-ui.js";
import { safeTeamLogo, appendFixtureCard, appendResultCard } from "./match-cards.js";

const WEBLLM_URL = "https://cdn.jsdelivr.net/npm/@mlc-ai/web-llm@0.2.79/+esm";
const MODEL_KEY = "prueba-deepseek:model";
const DEFAULT_MODEL = "Llama-3.2-3B-Instruct-q4f32_1-MLC";
const MAX_HISTORY = 6; // last 3 turns, system prompt excluded

const state = {
  engine: null,
  webllm: null,
  loading: false,
  generating: false,
  ready: false,
  modelId: DEFAULT_MODEL,
  history: [],
  data: null,
  // Last question that produced a verified answer: the next turn sends it as
  // previous_question so follow-ups ("seguro?", "creo q no") keep their context.
  lastQuestion: null,
  lastVerified: null,
  lastFixture: null,
  // Last turn the MODEL answered: { question, calls, answer, datos }. A follow-up
  // re-verifies this claim, not what the deterministic resolver would answer now.
  lastAgent: null,
};

const el = {
  modelSelect: document.getElementById("model-select"),
  loadButton: document.getElementById("load-model"),
  status: document.getElementById("engine-status"),
  progressTrack: document.getElementById("progress-track"),
  progressBar: document.getElementById("progress-bar"),
  notice: document.getElementById("notice"),
  chatLog: document.getElementById("chat-log"),
  chatForm: document.getElementById("chat-form"),
  chatInput: document.getElementById("chat-input"),
  chatHint: document.getElementById("chat-hint"),
  sendButton: document.getElementById("send-button"),
  clearButton: document.getElementById("clear-chat"),
  dataUpdated: document.getElementById("data-updated"),
  todayLabel: document.getElementById("today-label"),
  tabs: Array.from(document.querySelectorAll(".tab")),
  panes: {
    standings: document.getElementById("tab-standings"),
    results: document.getElementById("tab-results"),
    fixtures: document.getElementById("tab-fixtures"),
  },
};

// ---------------------------------------------------------------- helpers

function setStatus(kind, text) {
  el.status.className = `chip chip-${kind}`;
  el.status.textContent = text;
}

function showNotice(message, isError = false) {
  el.notice.hidden = false;
  el.notice.classList.toggle("error", isError);
  el.notice.textContent = message;
}

function hideNotice() {
  el.notice.hidden = true;
  el.notice.textContent = "";
}

function setProgress(ratio) {
  const pct = Math.max(0, Math.min(100, Math.round(ratio * 100)));
  el.progressTrack.hidden = false;
  el.progressBar.style.width = `${pct}%`;
}

function scrollChat() {
  el.chatLog.scrollTop = el.chatLog.scrollHeight;
}

function appendUserBubble(text) {
  const article = document.createElement("article");
  article.className = "bubble bubble-user";
  const body = document.createElement("div");
  body.className = "bubble-text";
  body.textContent = text;
  article.appendChild(body);
  el.chatLog.appendChild(article);
  scrollChat();
  return body;
}

function appendAssistantBubble() {
  const article = document.createElement("article");
  article.className = "bubble bubble-assistant";
  const body = document.createElement("div");
  body.className = "bubble-text thinking";
  body.textContent = "Pensando…";
  article.appendChild(body);
  el.chatLog.appendChild(article);
  scrollChat();
  return body;
}

function appendNoticeBubble(text) {
  const article = document.createElement("article");
  article.className = "bubble bubble-system";
  const body = document.createElement("div");
  body.className = "bubble-text";
  body.textContent = text;
  article.appendChild(body);
  el.chatLog.appendChild(article);
  scrollChat();
}

function appendTeamCrests(container, teamLogos) {
  const entries = Object.entries(teamLogos || {}).filter(([, value]) => safeTeamLogo(value));
  if (!entries.length) return;
  const row = document.createElement("div");
  row.className = "verified-crests";
  for (const [team, value] of entries) {
    const item = document.createElement("span");
    item.className = "verified-crest";
    const image = document.createElement("img");
    image.src = safeTeamLogo(value);
    image.alt = "";
    image.title = team;
    image.referrerPolicy = "no-referrer";
    image.loading = "eager";
    image.decoding = "async";
    image.width = 36;
    image.height = 36;
    image.addEventListener("error", () => image.remove());
    const name = document.createElement("span");
    name.textContent = team;
    item.append(image, name);
    row.appendChild(item);
  }
  container.appendChild(row);
}

function renderToolResults(calls, teamLogos = {}) {
  console.debug("[LaLiga] tools", calls.map(({ name, args, ok, ms }) => ({ name, args, ok, ms })));
  for (const call of calls) {
    if (!call.ok) continue;
    if (call.name === "proximo_partido" && call.datos?.partidos?.[0]) {
      // Render the chat card in the parent document. The compact MCP iframe can
      // deliver an early, short height and clip crests or metadata in some browsers.
      for (const match of call.datos.partidos) appendFixtureCard(el.chatLog, match, teamLogos);
    } else if (call.name === "ultimo_resultado" && call.datos?.goles_local !== undefined) {
      // Avoid the legacy iframe's blank strip for compact score cards as well.
      appendResultCard(el.chatLog, call.datos, teamLogos);
    } else {
      appendResourceBubble(call.content);
    }
  }
}

function appendResourceBubble(content) {
  const article = document.createElement("article");
  article.className = "bubble bubble-mcp-ui";
  if (!appendMcpResources(article, content)) return;
  el.chatLog.appendChild(article);
  scrollChat();
}

// The model's own sentence, written from the tool JSON. Text only.
function appendModelBubble(text, teamLogos = {}) {
  const article = document.createElement("article");
  article.className = "bubble bubble-assistant bubble-model";
  const body = document.createElement("div");
  body.className = "bubble-text";
  body.textContent = text;
  appendTeamCrests(article, teamLogos);
  article.appendChild(body);
  el.chatLog.appendChild(article);
  scrollChat();
  return article;
}

function setChatAvailability() {
  const on = !state.generating;
  el.chatInput.disabled = !on;
  el.sendButton.disabled = !on;
  el.chatInput.placeholder = "Escribí tu pregunta…";
  el.chatHint.textContent = state.ready && state.engine
    ? "Enter para enviar · La inferencia no sale de tu equipo."
    : "Primero debés cargar el modelo para que pueda interactuar contigo.";
}

function trimHistory() {
  if (state.history.length > MAX_HISTORY) {
    state.history = state.history.slice(-MAX_HISTORY);
  }
}

// ------------------------------------------------------------- data panel

const ZONES = [
  { match: /champions/i, cls: "zone-cl", label: "Champions League" },
  { match: /europa/i, cls: "zone-el", label: "Europa League" },
  { match: /conference/i, cls: "zone-ecl", label: "Conference League" },
  { match: /relegation|descenso/i, cls: "zone-rel", label: "Descenso" },
];

function zoneClass(zone) {
  const found = ZONES.find((item) => item.match.test(zone || ""));
  return found ? found.cls : "";
}

function formatDate(iso) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return new Intl.DateTimeFormat("es-ES", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function renderStandings(rows) {
  if (!rows?.length) {
    el.panes.standings.innerHTML = '<p class="data-empty">Sin datos de tabla.</p>';
    return;
  }

  const table = document.createElement("table");
  table.className = "standings";

  const thead = document.createElement("thead");
  thead.innerHTML =
    "<tr><th>#</th><th>Equipo</th><th>PJ</th><th>DG</th><th>PTS</th></tr>";
  table.appendChild(thead);

  const tbody = document.createElement("tbody");
  for (const row of rows) {
    const tr = document.createElement("tr");
    const cls = zoneClass(row.zone);
    if (cls) tr.className = cls;
    if (row.zone) tr.title = row.zone;

    const pos = document.createElement("td");
    pos.className = "pos";
    pos.textContent = row.rank;

    const team = document.createElement("td");
    team.className = "team-cell";
    const inner = document.createElement("div");
    inner.className = "team-inner";
    if (row.logo) {
      const img = document.createElement("img");
      img.src = row.logo;
      img.alt = "";
      img.loading = "lazy";
      img.referrerPolicy = "no-referrer";
      inner.appendChild(img);
    }
    const name = document.createElement("span");
    name.textContent = row.team;
    inner.appendChild(name);
    team.appendChild(inner);

    const played = document.createElement("td");
    played.textContent = row.played;

    const diff = document.createElement("td");
    diff.className = `dg ${row.goal_diff > 0 ? "pos" : row.goal_diff < 0 ? "neg" : ""}`;
    diff.textContent = row.goal_diff > 0 ? `+${row.goal_diff}` : String(row.goal_diff);

    const points = document.createElement("td");
    points.className = "pts";
    points.textContent = row.points;

    tr.append(pos, team, played, diff, points);
    tbody.appendChild(tr);
  }
  table.appendChild(tbody);
  el.panes.standings.replaceChildren(table);

  const legend = document.createElement("div");
  legend.className = "legend";
  legend.innerHTML = [
    ["#3b82f6", "Champions"],
    ["#f59e0b", "Europa"],
    ["#14b8a6", "Conference"],
    ["#ef4444", "Descenso"],
  ]
    .map(([color, label]) => `<span><i style="background:${color}"></i>${label}</span>`)
    .join("");
  el.panes.standings.appendChild(legend);
}

function renderResults(rows) {
  if (!rows?.length) {
    el.panes.results.innerHTML = '<p class="data-empty">Todavía no hay resultados.</p>';
    return;
  }

  const list = document.createElement("ul");
  list.className = "match-list";
  for (const match of rows) {
    const li = document.createElement("li");
    li.className = "match";

    const teams = document.createElement("div");
    teams.className = "match-teams";
    for (const name of [match.home, match.away]) {
      const span = document.createElement("span");
      span.className = "match-team";
      span.textContent = name;
      teams.appendChild(span);
    }

    const score = document.createElement("div");
    score.className = "match-score";
    const home = document.createElement("div");
    home.textContent = match.home_score;
    const away = document.createElement("div");
    away.textContent = match.away_score;
    score.append(home, away);

    const date = document.createElement("div");
    date.className = "match-date";
    date.textContent = `${formatDate(match.date)} · ${match.label}`;

    li.append(teams, score, date);
    list.appendChild(li);
  }
  el.panes.results.replaceChildren(list);
}

function renderFixtures(rows) {
  if (!rows?.length) {
    el.panes.fixtures.innerHTML = '<p class="data-empty">No hay partidos programados.</p>';
    return;
  }

  const list = document.createElement("ul");
  list.className = "match-list";
  for (const match of rows) {
    const li = document.createElement("li");
    li.className = "match";

    const teams = document.createElement("div");
    teams.className = "match-teams";
    for (const name of [match.home, match.away]) {
      const span = document.createElement("span");
      span.className = "match-team";
      span.textContent = name;
      teams.appendChild(span);
    }

    const vs = document.createElement("div");
    vs.className = "match-score";
    vs.innerHTML = '<span class="vs">vs</span>';

    const date = document.createElement("div");
    date.className = "match-date";
    const provisionalDay = new Date(`${String(match.date).slice(0, 10)}T12:00:00Z`)
      .toLocaleDateString("es-ES", { dateStyle: "long", timeZone: "UTC" });
    date.textContent = match.time_valid === true
      ? `${formatDate(match.date)} · ${match.label}`
      : `${provisionalDay} · Fecha provisional · horario por definir`;

    li.append(teams, vs, date);
    list.appendChild(li);
  }
  el.panes.fixtures.replaceChildren(list);
}

function renderData(data) {
  state.data = data;
  renderStandings(data.standings);
  renderResults(data.results);
  renderFixtures(data.fixtures);
  if (data.today_label) {
    el.todayLabel.textContent = `Hoy: ${data.today_label}`;
  }
  if (data.updated_at) {
    el.dataUpdated.textContent = `Actualizado ${formatDate(data.updated_at)}`;
  }
}

async function loadData() {
  try {
    const response = await fetch("/api/laliga/context", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    renderData(data);
    return data;
  } catch (error) {
    const message = "No pude cargar los datos de La Liga. Revisá que el servidor siga corriendo.";
    for (const key of Object.keys(el.panes)) {
      el.panes[key].innerHTML = `<p class="data-error">${message}</p>`;
    }
    showNotice(message, true);
    console.error("data load failed", error);
    return null;
  }
}

// ------------------------------------------------------------------- tabs

function selectTab(name) {
  for (const tab of el.tabs) {
    const active = tab.dataset.tab === name;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", String(active));
    el.panes[tab.dataset.tab]?.classList.toggle("active", active);
    if (el.panes[tab.dataset.tab]) {
      el.panes[tab.dataset.tab].hidden = !active;
    }
  }
}

for (const tab of el.tabs) {
  tab.addEventListener("click", () => selectTab(tab.dataset.tab));
}

// ---------------------------------------------------------------- engine

function readSavedModel() {
  try {
    return localStorage.getItem(MODEL_KEY) || DEFAULT_MODEL;
  } catch {
    return DEFAULT_MODEL;
  }
}

function saveModel(modelId) {
  try {
    localStorage.setItem(MODEL_KEY, modelId);
  } catch {
    /* private mode — ignore */
  }
}

function describeLoadError(error) {
  const text = String(error?.message || error || "");
  if (/out of memory|OOM|memory/i.test(text)) {
    return "El navegador se quedó sin memoria. Probá con Llama 3.2 1B o cerrá otras pestañas.";
  }
  if (/webgpu|adapter|device/i.test(text)) {
    return "WebGPU no está disponible. Usá Chrome o Edge 113+ y revisá chrome://gpu.";
  }
  if (/fetch|network|Failed to load|404/i.test(text)) {
    return "No pude descargar el modelo. Revisá tu conexión y volvé a intentar.";
  }
  return `No se pudo cargar el modelo: ${text.slice(0, 240)}`;
}

async function loadEngine() {
  if (state.loading || state.ready) return;
  if (!navigator.gpu) return;

  const modelId = el.modelSelect.value || DEFAULT_MODEL;
  state.modelId = modelId;
  saveModel(modelId);

  state.loading = true;
  hideNotice();
  el.modelSelect.disabled = true;
  el.loadButton.disabled = true;
  el.loadButton.textContent = "Cargando…";
  el.progressTrack.hidden = false;
  setProgress(0);
  setStatus("loading", "Descargando 0%");

  try {
    if (!state.webllm) {
      state.webllm = await import(WEBLLM_URL);
    }
    const engine = await state.webllm.CreateMLCEngine(modelId, {
      initProgressCallback: (progress) => {
        const ratio = typeof progress.progress === "number" ? progress.progress : 0;
        setProgress(ratio);
        const pct = Math.round(ratio * 100);
        const text = progress.text ? String(progress.text) : "Descargando modelo";
        setStatus("loading", `${text.slice(0, 48)} · ${pct}%`);
      },
    });

    state.engine = engine;
    state.ready = true;
    setStatus("ready", "Listo");
    setProgress(1);
    el.progressTrack.hidden = true;
    if (state.data) await initWebMcp(state.data);
    el.loadButton.textContent = "Modelo cargado";
    el.loadButton.disabled = true;
    el.modelSelect.disabled = false;
    setChatAvailability();
    el.chatInput.focus();
  } catch (error) {
    console.error("engine load failed", error);
    state.engine = null;
    state.ready = false;
    setStatus("error", "Error");
    el.progressTrack.hidden = true;
    el.loadButton.disabled = false;
    el.loadButton.textContent = "Reintentar";
    el.modelSelect.disabled = false;
    setChatAvailability();
    showNotice(describeLoadError(error), true);
  } finally {
    state.loading = false;
  }
}

el.loadButton.addEventListener("click", loadEngine);

el.modelSelect.addEventListener("change", async () => {
  saveModel(el.modelSelect.value);
  if (state.ready) {
    // A different model needs a fresh engine; offer the button again.
    const previous = state.engine;
    state.ready = false;
    state.engine = null;
    setChatAvailability();
    setStatus("idle", "Sin cargar");
    el.loadButton.disabled = false;
    el.loadButton.textContent = "Cargar modelo";
    showNotice("Cambiaste de modelo: tocá «Cargar modelo» para inicializarlo.");
    try {
      await previous?.unload?.();
    } catch {
      /* the old engine is being discarded anyway */
    }
  }
});

// ------------------------------------------------------------------- chat

function finishTurn() {
  state.generating = false;
  el.modelSelect.disabled = !navigator.gpu;
  setChatAvailability();
  el.chatInput.focus();
  scrollChat();
}

async function askResolver(question) {
  const response = await fetch("/api/ask", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, previous_question: state.lastQuestion, fixture_context: state.lastFixture }),
  });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}

// Only a real answer from the resolver becomes context for the next turn.
function rememberVerified(question, result, calls) {
  if (!result.answer && !calls?.some(call => call.ok)) return;
  state.lastQuestion = question;
  state.lastVerified = {
    question,
    intent: result.intent,
    answer: result.answer,
    facts: result.facts || [],
  };
  const actual = calls?.find((call) => call.ok);
  const match = actual?.name === "proximo_partido" ? actual.datos?.partidos?.at(-1) : null;
  state.lastFixture = actual ? (match ? {
    equipo: actual.args.equipo, rival: actual.args.rival, despues_de: match.fecha_iso,
  } : null) : (result.fixture_context || null);
}

// The model chooses a tool, the Python API runs it, the model writes the answer.
// If any step fails closed, show a status message rather than a data-only answer.
async function runAgentTurn(question, result) {
  const thinking = appendAssistantBubble();
  let outcome = { answer: "", calls: [] };

  // The selector is blind without the previous turn: "y el atleti" has no verb.
  const previousOk = state.lastAgent?.calls?.filter((call) => call.ok).at(-1) || null;
  const context = {
    previousQuestion: state.lastAgent?.question || null,
    previousTool: previousOk?.name || null,
    previousArgs: previousOk?.args || null,
  };

  try {
    outcome = await runAgent({
      engine: state.engine,
      question,
      todayLabel: result.today_label,
      context,
      teams: result.teams,
      continuation: result.tool_args,
      onStatus: () => {
        thinking.textContent = "Preparando respuesta…";
        scrollChat();
      },
    });
  } catch (error) {
    console.error("agent failed", error);
  } finally {
    thinking.parentElement?.remove();
  }

  const calls = Array.isArray(outcome.calls) ? outcome.calls : [];
  const answer = String(outcome.answer || "").trim();

  if (!answer) {
    const successful = calls.filter((call) => call.ok);
    appendNoticeBubble(successful.length
      ? "La IA consultó los datos, pero no pudo redactar una respuesta fiable. Probá reformular la pregunta."
      : "La IA no pudo consultar los datos. Probá reformular la pregunta o volver a cargar el modelo.");
    // Failed narration publishes no new claim. Keep the last displayed verified
    // fixture so a clarification still refers to what the user actually saw.
    return;
  }

  if (calls.length) renderToolResults(calls, result.team_logos);

  // The agent validates its prose before returning it.
  const datos = calls.filter((call) => call.ok).map((call) => call.datos);

  appendModelBubble(answer, result.team_logos);
  state.lastAgent = { question, calls, answer, datos, teamLogos: result.team_logos };
  rememberVerified(question, result, calls);
  state.history.push({ role: "user", content: question });
  state.history.push({ role: "assistant", content: answer });
  trimHistory();
}

// Unsupported questions receive only a model-selected, validated scope reply.
async function freeChatTurn(question, result) {
  if (!state.ready || !state.engine) {
    appendNoticeBubble(
      "No pude resolver esa pregunta con los datos y no hay modelo cargado. " +
        "Cargá el modelo para preguntas abiertas.",
    );
    return;
  }

  // No standings, history or unchecked streamed text for unsupported questions.
  const answer = await runScopeReply({ engine: state.engine, question });
  if (!answer) {
    appendNoticeBubble("No pude generar una respuesta. Probá de nuevo.");
    return;
  }
  appendModelBubble(answer);
  state.history.push({ role: "user", content: question });
  state.history.push({ role: "assistant", content: answer });
  trimHistory();
}

async function sendQuestion(question) {
  if (state.generating) return;
  if (!state.ready || !state.engine) {
    appendNoticeBubble("Primero cargá el modelo de IA para poder hacer consultas.");
    return;
  }

  state.generating = true;
  setChatAvailability();
  el.modelSelect.disabled = true;
  appendUserBubble(question);
  el.chatInput.value = "";

  let result;
  try {
    result = await askResolver(question);
  } catch (error) {
    console.error("ask failed", error);
    appendNoticeBubble("No pude consultar los datos. Revisá que el servidor siga corriendo.");
    showNotice("No pude consultar los datos. Revisá que el servidor siga corriendo.", true);
    finishTurn();
    return;
  }

  if (result.today_label) {
    el.todayLabel.textContent = `Hoy: ${result.today_label}`;
  }

  const intent = result.intent || "none";
  if (intent === "unknown_football" && !result.tool_args) {
    await freeChatTurn(question, result);
    finishTurn();
    return;
  }

  // Resolver output is routing context only; all visible answer text comes from AI.
  if (intent === "none") {
    await freeChatTurn(question, result);
    // Whatever the model said now is not a tool claim: do not re-verify the old one.
    state.lastAgent = null;
    state.lastFixture = null;
    state.lastQuestion = null;
    state.lastVerified = null;
    finishTurn();
    return;
  }

  // The loaded model selects the API tool and composes the user-facing answer.
  if (state.ready && state.engine) {
    await runAgentTurn(question, result);
    finishTurn();
    return;
  }

  // Defensive fallback: no query or resolver answer may escape without AI.
  appendNoticeBubble("Primero cargá el modelo de IA para poder hacer consultas.");
  finishTurn();
}

el.chatForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const question = el.chatInput.value.trim();
  if (!question) return;
  sendQuestion(question);
});

el.clearButton.addEventListener("click", () => {
  if (state.generating) return;
  state.history = [];
  state.lastQuestion = null;
  state.lastVerified = null;
  state.lastFixture = null;
  state.lastAgent = null;
  el.chatLog.replaceChildren();
  appendNoticeBubble("Chat limpio. Preguntame lo que quieras de La Liga.");
  el.chatInput.focus();
});

// ------------------------------------------------------------------- boot

async function initWebMcp(data) {
  try {
    await setupWebMcp({ today_label: data?.today_label || "" });
  } catch (error) {
    console.warn("[WebMCP] no pude inicializar las herramientas", error);
  }
}

function showWebGpuError() {
  setStatus("error", "Error");
  el.loadButton.disabled = true;
  el.loadButton.textContent = "No disponible";
  el.modelSelect.disabled = true;
  setChatAvailability();
  showNotice(
    "Para consultar La Liga primero necesitás cargar el modelo local. " +
      "Este navegador no ofrece WebGPU; usá Chrome o Edge con aceleración por hardware.",
    true,
  );
}

async function boot() {
  state.modelId = readSavedModel();
  const known = Array.from(el.modelSelect.options).some(
    (option) => option.value === state.modelId,
  );
  el.modelSelect.value = known ? state.modelId : DEFAULT_MODEL;
  state.modelId = el.modelSelect.value;

  // The standings panel can load independently; chat stays locked until AI is ready.
  await loadData();

  if (!navigator.gpu) {
    showWebGpuError();
    return;
  }

  setStatus("idle", "Sin cargar");
}

boot();
