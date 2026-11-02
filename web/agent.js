// Tool-calling loop for the in-browser model.
//
// WebLLM's native `tools` field only supports three Hermes 8B models (~4.9 GB of
// VRAM), which this machine's iGPU cannot load. So the model still CHOOSES the
// tool itself — constrained with response_format grammar decoding on Qwen2.5 — and
// this module executes that choice against the Python API. The model never sees the
// answer until the real data comes back, and the final sentence is written from
// that JSON only.
//
// The engine is injected: this module never imports WebLLM.

const TOOLS = [
  {
    name: "proximo_partido",
    args: ["equipo", "rival", "cantidad", "despues_de", "despues_del_dia", "fecha_exacta"],
    when: "preguntan CUÁNDO se juega, cuál es el PRÓXIMO partido, a qué hora, o usan 'proximo', 'juega', 'contra', 'vs', 'clasico'",
  },
  {
    name: "ultimo_resultado",
    args: ["equipo", "rival"],
    when: "preguntan CÓMO SALIÓ, el ÚLTIMO partido ya jugado, el marcador, goles, 'resultado', 'ganó', 'perdió'",
  },
  {
    name: "tabla",
    args: [],
    when: "preguntan la TABLA completa, clasificación general, posiciones de todos",
  },
  {
    name: "posicion",
    args: ["equipo"],
    when: "preguntan por UN equipo: cuántos puntos tiene, en qué puesto está, cómo va",
  },
  {
    name: "descenso",
    args: [],
    when: "preguntan quién DESCIENDE, zona baja, colistas, últimos",
  },
];

const TOOL_GUIDE = TOOLS.map(
  (tool) => `- ${tool.name}(${tool.args.join(", ")}) -> ${tool.when}`,
).join("\n");

// Built ONCE: the engine reuses its GrammarMatcher only when the schema string is
// byte-identical, and the first constrained call pays the ~2.5 s grammar_init_s.
const TOOL_SCHEMA_JSON = JSON.stringify({
  type: "object",
  properties: {
    herramienta: { type: "string", enum: TOOLS.map((tool) => tool.name) },
    argumentos: {
      type: "object",
      properties: { equipo: { type: "string" }, rival: { type: "string" }, cantidad: { type: "integer", minimum: 1, maximum: 4 } },
      additionalProperties: false,
    },
  },
  required: ["herramienta"],
  additionalProperties: false,
});
const TOOL_ONLY_SCHEMA_JSON = JSON.stringify({
  type: "object",
  properties: { herramienta: { type: "string", enum: TOOLS.map(tool => tool.name) } },
  required: ["herramienta"], additionalProperties: false,
});

const SELECTOR_PROMPT = [
  "Sos el selector de datos de una app de La Liga 2026-27.",
  "Elegís UNA herramienta para responder la pregunta. Respondés SOLO el JSON del esquema.",
  "Reglas:",
  TOOL_GUIDE,
  "Elegí la herramienta que corresponde al verbo de la pregunta. Si dice 'cuando/proximo/juega' es proximo_partido.",
  "Si dice 'como salio/ultimo/resultado/marcador' es ultimo_resultado.",
  "Si nombra UN equipo y pregunta puntos/puesto/como va, es posicion.",
  "El equipo que nombra la pregunta va SIEMPRE en `equipo`: nunca dejes `equipo` vacío si hay un equipo escrito.",
  "Usá `rival` SOLO si la pregunta nombra DOS equipos.",
  "Normalizá el nombre del equipo a su forma oficial (barca->Barcelona, madrid->Real Madrid, atleti->Atletico Madrid).",
].join("\n");

const TOOL_URL = "/api/tool";
const MAX_ATTEMPTS = 2; // one attempt + one correction, never more

// Opt-in test observer only: no production storage, network, or changed output.
function diagnostic(event) {
  if (typeof globalThis.__LALIGA_DIAGNOSTICS__ !== "function") return;
  try { globalThis.__LALIGA_DIAGNOSTICS__(structuredClone(event)); } catch { /* Observer cannot affect inference. */ }
}

// Few-shot: measured, the 1.5B follows the shape of a concrete JSON example far
// better than a rule that forbids something ("no repitas el rival").
const SELECTOR_EXAMPLES = [
  "EJEMPLOS de respuesta (solo el JSON del esquema, sin explicación):",
  '"cuando juega el barca contra el madrid" -> {"herramienta":"proximo_partido","argumentos":{"equipo":"Barcelona","rival":"Real Madrid"}}',
  '"como salio el barca" -> {"herramienta":"ultimo_resultado","argumentos":{"equipo":"Barcelona"}}',
  '"cuanto quedo el madrid barsa" -> {"herramienta":"ultimo_resultado","argumentos":{"equipo":"Real Madrid","rival":"Barcelona"}}',
  '"como va la liga" -> {"herramienta":"tabla","argumentos":{}}',
].join("\n");

/**
 * The previous turn, in prose, inside the system prompt.
 *
 * The selector only sees the current question: without this, `"y el atleti"` has no
 * verb and lands on whatever tool happened to run last. Measured live: after a
 * next-match turn it picked `ultimo_resultado` and answered about a played game.
 */
function contextNote(context) {
  const previousQuestion =
    typeof context?.previousQuestion === "string" ? context.previousQuestion.trim() : "";
  if (!previousQuestion) return "";

  const tool = typeof context?.previousTool === "string" ? context.previousTool : "";
  const args =
    context?.previousArgs && typeof context.previousArgs === "object"
      ? JSON.stringify(context.previousArgs)
      : "{}";

  return [
    `CONTEXTO: la pregunta anterior del usuario fue "${previousQuestion}" y elegiste la ` +
      `herramienta ${tool || "ninguna"} con los argumentos ${args}.`,
    'Si la pregunta actual es un seguimiento sin verbo ("y el atleti", "y el barca"), elegí la ' +
      "MISMA herramienta que en el turno anterior y armá los argumentos SOLO con el equipo que " +
      "nombra la pregunta actual: el `rival` del turno anterior no se repite.",
    "Si la pregunta actual tiene su propio verbo, ignorá el contexto y elegí por ese verbo.",
  ].join("\n");
}

// --------------------------------------------------------------- model calls

// Exported so the selector prompt can be inspected/probed without an engine.
export function buildSelectorPrompt(context, correctionNote) {
  return [
    SELECTOR_PROMPT,
    SELECTOR_EXAMPLES,
    // Last on purpose: the 1.5B weights the tail of the system prompt most, and this
    // is the line that decides a verbless follow-up.
    contextNote(context),
    correctionNote
      ? `Contexto verificado o corrección: ${correctionNote}`
      : "",
  ]
    .filter(Boolean)
    .join("\n\n");
}

async function chooseTool(engine, question, correctionNote, context, grounded) {
  // ONE system message, always first: WebLLM's Qwen template throws
  // "System prompt should always be the first message in `messages`" otherwise.
  // The correction and the conversation context travel in that same system prompt
  // (measured: appending either as a second system message fails, and appending
  // only the error text to the user turn leaves the model repeating the same tool).
  const system = grounded
    ? "Elige una herramienta de La Liga. Responde solo JSON con herramienta. " +
      "proximo_partido: partidos futuros; ultimo_resultado: marcador pasado; tabla: clasificación; " +
      "posicion: puesto de un club; descenso: zona baja. " +
      "La consulta actual tiene equipo, fecha y cantidad verificados para partidos futuros. No generes argumentos."
    : buildSelectorPrompt(context, correctionNote);
  const started = atNow();
  const response = await engine.chat.completions.create({
    messages: [
      { role: "system", content: system },
      { role: "user", content: question },
    ],
    temperature: 0,
    max_tokens: grounded ? 32 : 100,
    // Grammar decoding: the model can only emit JSON that matches the schema.
    response_format: { type: "json_object", schema: grounded ? TOOL_ONLY_SCHEMA_JSON : TOOL_SCHEMA_JSON },
  });
  diagnostic({ stage: "selector", text: response?.choices?.[0]?.message?.content || "",
    finish_reason: response?.choices?.[0]?.finish_reason, elapsed_ms: elapsed(started),
    prompt_tokens: response?.usage?.prompt_tokens, completion_tokens: response?.usage?.completion_tokens });
  return response?.choices?.[0]?.message?.content || "";
}

function parseChoice(content) {
  const text = String(content || "").trim();
  if (!text) return null;

  const candidates = [text];
  const fenced = text.match(/```(?:json)?\s*([\s\S]*?)```/i);
  if (fenced) candidates.push(fenced[1].trim());
  const first = text.indexOf("{");
  const last = text.lastIndexOf("}");
  if (first >= 0 && last > first) candidates.push(text.slice(first, last + 1));

  for (const candidate of candidates) {
    let parsed;
    try {
      parsed = JSON.parse(candidate);
    } catch {
      continue;
    }
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) continue;
    const name = typeof parsed.herramienta === "string" ? parsed.herramienta.trim() : "";
    if (!name) continue;
    const rawArgs = parsed.argumentos;
    const args =
      rawArgs && typeof rawArgs === "object" && !Array.isArray(rawArgs) ? rawArgs : {};
    return { name, args };
  }
  return null;
}

// Keep only the arguments the chosen tool declares. The grammar schema always offers
// `equipo` and `rival`, so `tabla` arrived as `{"equipo":"liga","rival":"liga"}`: the
// API ignored the extras silently, but the request did not match the trace the user
// sees. No-arg tools send `{}`.
function sanitizeArgs(name, args) {
  const declared = TOOLS.find((tool) => tool.name === name)?.args || [];
  const permitted = name === "proximo_partido" ? [...declared, "despues_de"] : declared;
  const clean = {};
  if (!args || typeof args !== "object" || Array.isArray(args)) return clean;
  for (const key of permitted) {
    const value = args[key];
    if (key === "cantidad") {
      if (Number.isInteger(value) && value >= 1 && value <= 4) clean[key] = value;
      continue;
    }
    if (typeof value !== "string") continue;
    const trimmed = value.trim();
    if (trimmed) clean[key] = trimmed;
  }
  return clean;
}

// The tool is the MODEL's choice; the team names are the APP's. `teams` is the
// resolver's own extraction for this question (`result.teams` in app.js), so the 1.5B
// cannot swap the subject: measured, "y el atleti" produced `equipo:"Real Madrid"` /
// `equipo:"Real Betis"` on different runs of the same prompt. A tool that takes
// `equipo` gets the resolved team; `rival` exists only when the question named two.
function groundTeamArgs(name, args, teams) {
  const declared = TOOLS.find((tool) => tool.name === name)?.args || [];
  if (!declared.includes("equipo")) return args;

  const known = Array.isArray(teams)
    ? teams.filter((team) => typeof team === "string" && team.trim())
    : [];
  if (!known.length) return args; // no team in the question: keep what the model sent

  const grounded = { ...args, equipo: known[0] };
  if (declared.includes("rival")) {
    if (known.length >= 2) grounded.rival = known[1];
    else delete grounded.rival;
  }
  return grounded;
}

async function callTool(name, args) {
  const started = atNow();
  try {
    const response = await fetch(TOOL_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, args }),
    });
    if (!response.ok) {
      return {
        ok: false,
        error: `La API de la app respondió con un error (HTTP ${response.status}).`,
        ms: elapsed(started),
      };
    }
    const result = await response.json();
    if (result && result.ok === true) {
      return { ok: true, datos: result.datos, content: result.content, ms: elapsed(started) };
    }
    return {
      ok: false,
      error: String(result?.error || "La herramienta no devolvió datos."),
      ms: elapsed(started),
    };
  } catch (error) {
    return {
      ok: false,
      error: "No pude conectar con la API de la app.",
      ms: elapsed(started),
    };
  }
}

// The table has 20 rows; measured, a 1.5B writer answers a payload that size by
// echoing the JSON instead of writing a sentence. The writer therefore sees a
// projection (every value verbatim, just fewer rows) plus ONE explicit task.
const TABLA_PREVIEW_ROWS = 5;

function writerBrief(datos) {
  const filas = Array.isArray(datos?.filas) ? datos.filas : [];

  if (Array.isArray(datos?.partidos) && datos.partidos.length) {
    return {
      // Proyección: el redactor recibe solo los campos que debe verbalizar. Si ve
      // "estadio" lo nombra (y lo reescribe: "Estadi" -> "Estadio"). El dato
      // completo sigue visible en "ver datos".
      datos: {
        partidos: datos.partidos.map((partido) => ({
          local: partido?.local,
          visitante: partido?.visitante,
          dia: partido?.dia,
          hora: partido?.hora,
          estado_fecha: partido?.estado_fecha,
          horario_confirmado: partido?.horario_confirmado,
        })),
      },
      today: true,
      task:
        `TAREA: escribe ${datos.partidos.length} línea(s), una por CADA partido, en orden. Cada línea debe decir quién juega contra quién, el día y la hora. ` +
        'Copiá "local", "visitante", "dia" y "hora" de cada partido tal cual están ' +
        'escritos. El campo "dia" ya trae el día y la fecha correctos: copialo tal cual. ' +
        "Si horario_confirmado es false, decí «fecha provisional y horario por definir», nunca una hora. " +
        "Si es true, el horario está confirmado: no digas provisional ni por definir. " +
        "No uses la fecha de hoy ni escribas «este domingo». No agregues el estadio.",
    };
  }

  if (datos && datos.goles_local !== undefined && datos.goles_visitante !== undefined) {
    return {
      datos,
      task:
        "TAREA: una sola oración COMPLETA, en pasado y con verbo, que diga quién jugó, el " +
        "marcador con los dos números separados por un guion y el día. Seguí la forma " +
        "«El X ganó 2-1 al Y el día Z» usando los valores de DATOS. Nunca escribas " +
        "«marcó 4 goles».",
    };
  }

  if (filas.length) {
    const isTabla = filas.some((row) => row && row.jugados !== undefined);
    if (isTabla) {
      return {
        datos:
          filas.length > TABLA_PREVIEW_ROWS
            ? { total_equipos: filas.length, filas: filas.slice(0, TABLA_PREVIEW_ROWS) }
            : datos,
        task:
          "TAREA: una sola oración que diga qué equipo lidera la tabla y con cuántos puntos. " +
          '"total_equipos" es la cantidad de equipos de la liga.',
      };
    }
    return {
      datos,
      task:
        `TAREA: una sola oración que nombre los ${filas.length} equipos que están en descenso, ` +
        "cada uno con sus puntos, sin saltear ninguno.",
    };
  }

  if (datos && datos.pos !== undefined) {
    return {
      datos,
      task:
        'TAREA: una sola oración que diga en qué puesto está "equipo" y cuántos puntos tiene.',
    };
  }

  return {
    datos,
    task: "TAREA: una sola oración que responda la pregunta usando solo los valores de DATOS.",
  };
}

function writerPrompt(datos, todayLabel, correction = "") {
  const match = datos?.partidos?.length === 1 ? datos.partidos[0] : null;
  if (match) {
    const evidence = { partidos: [{ local: match.local, visitante: match.visitante, dia: match.dia, hora: match.hora, estado: fixtureStatus(match) }] };
    return [
      "Devuelve JSON: respuesta es una oración en español con ambos equipos y el día literal de DATOS; estado copia el valor de DATOS. No agregues estadio ni otros datos.",
      match.horario_confirmado === true
        ? "Fecha y horario confirmados: no digas provisional ni por definir."
        : "Fecha provisional y horario por definir: incluye esa aclaración y no inventes una hora.",
      correction,
      `DATOS: ${JSON.stringify(evidence)}`,
    ].filter(Boolean).join("\n");
  }
  const brief = writerBrief(datos);
  const lines = [
    "Sos el redactor de una app de La Liga. Escribís en español, de forma breve y respetando la TAREA.",
    "Usá SOLO los valores de DATOS: no agregues horas, fechas, goles, estadios ni equipos que no estén ahí.",
    "No copies el JSON: nada de llaves, comillas ni nombres de campos dentro de tu oración.",
    "No menciones fecha_iso ni valores ISO (por ejemplo 2026-10-25T20:00Z).",
    "No saludes, no preguntes, no expliques de dónde salen los datos.",
    brief.task,
  ];
  // NO se inyecta "Hoy es ...": medido, el modelo tomaba la fecha de hoy y la
  // escribía en lugar del campo "dia" del partido (dijo domingo 25/10 para un
  // fixture del sábado 31/10). El dato ya viaja con su propio día.
  void todayLabel;
  lines.push("", `DATOS: ${JSON.stringify(brief.datos)}`);
  return lines.join("\n");
}

function fixtureStatus(match) {
  return match.horario_confirmado === true
    ? "Fecha y horario confirmados"
    : "Fecha provisional y horario por definir";
}

function fixtureResponseFormat(match) {
  return { type: "json_object", schema: JSON.stringify({
    type: "object",
    properties: {
      respuesta: { type: "string" },
      estado: { type: "string", enum: [fixtureStatus(match)] },
    },
    required: ["respuesta", "estado"],
    additionalProperties: false,
  }) };
}

// A sentence never contains braces or `"campo":`. If the writer echoes the payload,
// the app fails closed and the verified card speaks instead of showing raw JSON.
const JSON_ECHO_RE = /[{}]|"[^"\n]{1,40}"\s*:/;

function looksLikeJsonEcho(text) {
  // The writer boundary has already admitted a nonempty string.
  return JSON_ECHO_RE.test(text);
}

async function writeAnswer(engine, question, datos, todayLabel) {
  const matches = datos?.partidos || [];
  if (matches.length <= 1) return writeSingleAnswer(engine, question, datos, todayLabel);

  // Small local models already handle the one-match task. Do not make them
  // retain several dates/opponents and a list-format contract in one generation.
  // One bounded model run per fixture, sequentially on the same local engine.
  const answers = [];
  let corrected = false;
  for (const [index, match] of matches.entries()) {
    let rejection;
    let text = await writeSingleAnswer(engine,
      "Describe únicamente el partido de DATOS, con los dos equipos, la fecha y el horario o su estado provisional.",
      { partidos: [match] }, todayLabel, 160, { requireTeams: true, onReject: reason => { rejection = reason; } });
    if (!text && rejection === "provisional_status" && !corrected) {
      corrected = true;
      diagnostic({ stage: "writer_correction", index, reason: rejection });
      text = await writeSingleAnswer(engine, "Corrige únicamente el partido de DATOS.",
        { partidos: [match] }, todayLabel, 160, {
          requireTeams: true,
          correction: "CORRECCIÓN: escribe solo la oración final. Incluye local, visitante y dia de DATOS y «fecha provisional y horario por definir». No copies las instrucciones ni expliques la corrección.",
        });
    }
    // Field admission belongs to writeSingleAnswer for single and multiple matches.
    if (!text) {
      diagnostic({ stage: "validation", reason: "empty_fixture_answer", index, match, text });
      console.warn("[agent] fixture narration rejected", { index: index + 1, count: matches.length });
      return "";
    }
    answers.push(text);
  }
  return answers.join("\n");
}

async function writeSingleAnswer(engine, question, datos, todayLabel, maxTokens = 90, options = {}) {
  try {
    const match = datos?.partidos?.length === 1 ? datos.partidos[0] : null;
    const started = atNow();
    const response = await engine.chat.completions.create({
      messages: [
        { role: "system", content: writerPrompt(datos, todayLabel, options.correction) },
        { role: "user", content: question },
      ],
      temperature: 0.1,
      max_tokens: match ? 160 : maxTokens,
      ...(match ? { response_format: fixtureResponseFormat(match) } : {}),
    });
    let text = String(response?.choices?.[0]?.message?.content || "").trim();
    diagnostic({ stage: "writer", text, data: writerBrief(datos).datos,
      format: match ? "fixture-json/v1" : "plain-text/v1",
      finish_reason: response?.choices?.[0]?.finish_reason, max_tokens: match ? 160 : maxTokens, elapsed_ms: elapsed(started),
      prompt_tokens: response?.usage?.prompt_tokens, completion_tokens: response?.usage?.completion_tokens });
    const reject = reason => {
      diagnostic({ stage: "validation", reason, text });
      options.onReject?.(reason);
      return "";
    };
    if (match) {
      let output;
      try { output = JSON.parse(text); } catch { return reject("structured_fixture_response"); }
      if (!output || typeof output !== "object" || Array.isArray(output) ||
          Object.keys(output).length !== 2 || typeof output.respuesta !== "string" ||
          !output.respuesta.trim() || typeof output.estado !== "string") return reject("structured_fixture_response");
      if (output.estado !== fixtureStatus(match)) return reject(match.horario_confirmado === true ? "confirmed_status" : "provisional_status");
      // Both displayed strings must have been generated by the local model. Never
      // fill a missing status or factual sentence from the deterministic tool data.
      text = `${output.respuesta.trim()}\n${output.estado}`;
      // A grounded singleton can say "el próximo partido" without losing its
      // subject. A list needs both clubs in each sentence to disambiguate rows.
      const required = options.requireTeams ? [match.local, match.visitante, match.dia] : [match.dia];
      if (!required.every(value =>
        typeof value === "string" && value && output.respuesta.includes(value))) return reject("missing_fixture_fields");
    }
    if (!text) return reject("missing_fixture_line");
    if (looksLikeJsonEcho(text)) {
      console.warn("[agent] el redactor copió el JSON en vez de escribir una oración");
      return reject("json_echo");
    }
    if (unsupportedClaims(text, writerBrief(datos).datos).length) return reject("unsupported_date_or_time");
    const matches = datos?.partidos || [];
    for (const match of matches) {
      // writeAnswer splits fixture lists before calling this single-fixture writer.
      const line = text;
      const statusText = line.normalize("NFD").replace(/[\u0300-\u036f]/g, "");
      if (match.horario_confirmado === false &&
          (!/provisional/i.test(line) || !/por definir/i.test(line) ||
            /\b(?:fecha|horario)(?:\s+y\s+horario)?\s+(?:(?:esta|estan)\s+)?confirmad[oa]s?\b/i.test(statusText))) return reject("provisional_status");
      if (match.horario_confirmado === true &&
          (/provisional|por definir|por confirmar/i.test(line) || !line.includes(match.hora))) return reject("confirmed_status");
    }
    return text;
  } catch (error) {
    diagnostic({ stage: "writer_error", name: error?.name || "Error" });
    console.warn("[agent] la redacción final falló", error);
    return "";
  }
}

// ------------------------------------------------------------------- helpers

function atNow() {
  return typeof performance !== "undefined" ? performance.now() : Date.now();
}

function elapsed(started) {
  return Math.round(atNow() - started);
}

// ---------------------------------------------------------------- public API

/**
 * Run the whole turn: the model picks a tool, the app runs it, the model writes.
 * Fails closed: an empty answer means "do not show model prose".
 *
 * `context` is the previous turn (`previousQuestion` / `previousTool` /
 * `previousArgs`): the selector needs it to read follow-ups like "y el atleti".
 * `teams` is the resolver's team extraction for THIS question; it grounds the
 * arguments, never the tool choice.
 *
 * @returns {Promise<{answer: string, calls: Array<object>}>}
 */
export async function runAgent({ engine, question, todayLabel, onStatus, context, teams, continuation }) {
  const status = typeof onStatus === "function" ? onStatus : () => {};
  const calls = [];

  let chosen = null;
  let correction = "";

  for (let attempt = 0; attempt < MAX_ATTEMPTS && !chosen; attempt += 1) {
    status(attempt === 0 ? "consultando la API..." : "corrigiendo la consulta...");

    let choice = null;
    try {
      // Selection always belongs to the loaded model. Grounded query dimensions
      // travel as context and are applied only to a compatible model choice.
      const queryContext = continuation ? { ...context, previousQuestion: context?.previousQuestion || question,
        previousTool: "proximo_partido", previousArgs: continuation } : context;
      const note = [correction, continuation ? `La consulta de calendario verificada es ${JSON.stringify(continuation)}. Selecciona la herramienta para próximos partidos.` : ""].filter(Boolean).join(" ");
      choice = parseChoice(await chooseTool(engine, question, note, queryContext, Boolean(continuation)));
    } catch (error) {
      console.warn("[agent] el modelo no pudo elegir la herramienta", error);
      choice = null;
    }

    if (!choice) {
      correction = "la respuesta anterior no era el JSON del esquema.";
      continue;
    }

    // The request carries only what the tool declared, exactly like the trace, with
    // the team names the resolver already found in this question.
    if (continuation && choice.name !== "proximo_partido") {
      correction = "La pregunta pide partidos futuros; selecciona proximo_partido.";
      continue;
    }
    const args = sanitizeArgs(choice.name, continuation || groundTeamArgs(choice.name, choice.args, teams));
    const result = await callTool(choice.name, args);
    const call = { name: choice.name, args, ok: result.ok, ms: result.ms };
    if (result.ok) {
      call.datos = result.datos;
      call.content = result.content;
      calls.push(call);
      chosen = { name: choice.name, datos: result.datos };
    } else {
      call.error = result.error;
      calls.push(call);
      correction = result.error;
      if (continuation) break;
    }
  }

  if (!chosen) return { answer: "", calls };

  status("redactando...");
  const answer = await writeAnswer(engine, question, chosen.datos, todayLabel);
  return { answer, calls };
}

// Out-of-scope turns have no evidence to narrate. The local model may formulate
// only a scoped limitation, never invent facts or reuse league points as awards.
const SCOPE_REPLIES = [
  "No tengo datos verificados sobre esa consulta. Puedo ayudarte con resultados, próximos partidos y clasificación de La Liga.",
  "Puedo ayudarte con resultados, próximos partidos y clasificación de La Liga, pero no tengo información verificada para responder esa pregunta.",
];
const SCOPE_SCHEMA = JSON.stringify({ type: "object", properties: {
  answer: { type: "string", enum: SCOPE_REPLIES },
}, required: ["answer"], additionalProperties: false });

export async function runScopeReply({ engine, question }) {
  try {
    const response = await engine.chat.completions.create({
      messages: [
        { role: "system", content: "La consulta no tiene evidencia en las herramientas disponibles. Elige una respuesta de alcance del esquema. No respondas hechos sobre premios, personas ni otros temas." },
        { role: "user", content: question },
      ],
      temperature: 0, max_tokens: 120,
      response_format: { type: "json_object", schema: SCOPE_SCHEMA },
    });
    const reply = JSON.parse(response?.choices?.[0]?.message?.content || "{}");
    return SCOPE_REPLIES.includes(reply.answer) ? reply.answer : "";
  } catch {
    return "";
  }
}

/**
 * Re-run the successful calls of a past agent turn — the SAME tools with the SAME
 * arguments — and return fresh results. Used by "seguro?"/"creo q no": re-verifying
 * the model's own claim, never a resolver guess about what the question meant.
 */
export async function replayCalls(calls) {
  const source = Array.isArray(calls) ? calls.filter((call) => call?.ok) : [];
  const fresh = [];
  for (const call of source) {
    const args = sanitizeArgs(call.name, call.args);
    const result = await callTool(call.name, args);
    const next = { name: call.name, args, ok: result.ok, ms: result.ms };
    if (result.ok) {
      next.datos = result.datos;
      next.content = result.content;
    }
    else next.error = result.error;
    fresh.push(next);
  }
  return fresh;
}

/** Short Spanish summary of the executed calls, for the trace bubble. */
export function summarizeCalls(calls) {
  if (!Array.isArray(calls) || calls.length === 0) return "sin consultas";
  return calls
    .map((call) => {
      // Only the arguments the tool declares: `tabla` ignores extra keys, so showing
      // them would claim a query the API never ran.
      const declared = TOOLS.find((tool) => tool.name === call?.name)?.args;
      const args =
        call?.args && typeof call.args === "object"
          ? Object.entries(call.args)
              .filter(
                ([key, value]) =>
                  (!declared || declared.includes(key)) &&
                  value !== "" &&
                  value !== null &&
                  value !== undefined,
              )
              .map(([key, value]) => `${key}=${value}`)
              .join(", ")
          : "";
      const failed = call?.ok ? "" : " ✗";
      return `${call?.name || "?"}(${args})${failed}`;
    })
    .join(" · ");
}

const TIME_RE = /\b\d{1,2}:\d{2}\b/g;
const DATE_RE =
  /\b\d{1,2}\s+de\s+(enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|noviembre|diciembre)(?:\s+de\s+\d{4})?/gi;
const ISO_RE = /\b\d{4}-\d{2}-\d{2}\b/g;
// `2026-10-18T19:00Z` is a UTC stamp, not a Spain-local time: comparing a claimed
// "19:00" against it would let a wrong hour pass as grounded.
const ISO_STAMP_RE = /\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})?/g;

/**
 * Times/dates the model mentioned that the API did not return.
 * An empty array means the answer is grounded in the tool data.
 */
export function unsupportedClaims(answer, datos) {
  const serialized = JSON.stringify(datos ?? {});
  const timeHaystack = serialized.replace(ISO_STAMP_RE, " ");
  const found = [];
  const text = String(answer || "");
  for (const match of text.matchAll(TIME_RE)) {
    if (!timeHaystack.includes(match[0])) found.push(match[0]);
  }
  for (const pattern of [DATE_RE, ISO_RE]) {
    for (const match of text.matchAll(pattern)) {
      if (!serialized.includes(match[0])) found.push(match[0]);
    }
  }
  return [...new Set(found)];
}
