// WebMCP: expone la API determinística de La Liga como herramientas para agentes.
//
// Chrome todavía no trae `document.modelContext` nativo: lo instala el polyfill
// @mcp-b/global que index.html carga antes de este módulo. Si el CDN falla o el
// navegador no lo soporta, este módulo no hace nada y la app sigue igual.

const API_URL = "/api/ask";
const NO_DATA = "No tengo ese dato verificado";
const POLYFILL_WARNING =
  "[WebMCP] document.modelContext no está disponible (¿CDN bloqueada o navegador sin polyfill?). " +
  "Las herramientas para agentes quedan desactivadas; la app funciona igual.";

const state = {
  attempted: false,
  available: false,
  tools: [],
  todayLabel: "",
};

function textResult(text) {
  return { content: [{ type: "text", text: String(text) }] };
}

function clean(value) {
  return typeof value === "string" ? value.trim() : "";
}

// --------------------------------------------------------------- API access

async function askApi(question) {
  const response = await fetch(API_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
  });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}

function referenceLine(todayLabel) {
  const label = todayLabel || state.todayLabel;
  return label ? `Referencia: ${label} (hora de España).` : "Referencia: fecha no disponible.";
}

// Every tool answers from the deterministic resolver, never from the model.
function renderAnswer(result) {
  const answer = clean(result?.answer);
  const lines = [referenceLine(result?.today_label)];

  if (!answer || result?.intent === "none") {
    lines.push(`${NO_DATA} para esa consulta.`);
  } else {
    lines.push(answer);
    if (Array.isArray(result?.facts) && result.facts.length) {
      lines.push("", "Hechos verificados:");
      for (const fact of result.facts) lines.push(`- ${fact}`);
    }
  }

  lines.push("", "Fuente: API determinística de la app (datos de ESPN, sin modelo de lenguaje).");
  return textResult(lines.join("\n"));
}

async function answerQuestion(question) {
  try {
    const result = await askApi(question);
    if (result?.today_label) state.todayLabel = result.today_label;
    return renderAnswer(result);
  } catch (error) {
    console.warn("[WebMCP] no pude consultar /api/ask", error);
    return textResult(
      "No pude consultar la API local de La Liga. Revisá que el servidor siga corriendo en " +
        `${window.location.origin}.`,
    );
  }
}

// ---------------------------------------------------------------- tool table

const TOOLS = [
  {
    name: "laliga_proximo_partido",
    description:
      "Datos verificados de La Liga 2026-27. Devuelve el próximo partido de un equipo (o de un " +
      "cruce entre dos equipos) con el día, la fecha y la hora en hora de España. Acepta nombres " +
      "en español o alias habituales (barca, madrid, atleti, clásico). La respuesta sale de la API " +
      "en vivo de la app, nunca de un modelo de lenguaje.",
    inputSchema: {
      type: "object",
      properties: {
        team: { type: "string", description: "Equipo de La Liga, por ejemplo «Barcelona» o «Betis»." },
        opponent: {
          type: "string",
          description: "Rival opcional para buscar un cruce concreto, por ejemplo «Real Madrid».",
        },
      },
    },
    buildQuestion({ team, opponent }) {
      const local = clean(team);
      const rival = clean(opponent);
      if (!local) return null;
      return rival
        ? `cuando juega el proximo partido entre ${local} y ${rival}`
        : `cuando juega el proximo partido del ${local}`;
    },
    missing:
      "Necesito el nombre de un equipo de La Liga para buscar su próximo partido " +
      "(por ejemplo: team: \"Barcelona\").",
  },
  {
    name: "laliga_ultimo_resultado",
    description:
      "Datos verificados de La Liga 2026-27. Devuelve el último partido ya jugado de un equipo con " +
      "el marcador final y la fecha en hora de España. La respuesta sale de la API en vivo de la " +
      "app, nunca de un modelo de lenguaje.",
    inputSchema: {
      type: "object",
      properties: {
        team: { type: "string", description: "Equipo de La Liga, por ejemplo «Real Madrid»." },
      },
      required: ["team"],
    },
    buildQuestion({ team }) {
      const local = clean(team);
      return local ? `como salio el ultimo partido del ${local}` : null;
    },
    missing: "Necesito el nombre del equipo (por ejemplo: team: \"Real Madrid\").",
  },
  {
    name: "laliga_tabla",
    description:
      "Datos verificados de La Liga 2026-27. Devuelve la tabla de posiciones completa con los 20 " +
      "equipos, puntos, partidos jugados y diferencia de goles, más los tres primeros y el último " +
      "como hechos destacados. La respuesta sale de la API en vivo de la app.",
    inputSchema: { type: "object", properties: {} },
    buildQuestion() {
      return "tabla de posiciones";
    },
  },
  {
    name: "laliga_descenso",
    description:
      "Datos verificados de La Liga 2026-27. Devuelve los equipos que hoy ocupan la zona de descenso " +
      "según la tabla real, con su puesto y sus puntos. La respuesta sale de la API en vivo de la app.",
    inputSchema: { type: "object", properties: {} },
    buildQuestion() {
      return "quienes estan en descenso";
    },
  },
  {
    name: "laliga_preguntar",
    description:
      "Pregunta libre en español sobre La Liga 2026-27: fechas de partidos, resultados, tabla o " +
      "descenso. La app resuelve la consulta de forma determinística contra la API en vivo y " +
      "devuelve la respuesta verificada con sus hechos; si no reconoce el dato responde " +
      "«No tengo ese dato verificado» en vez de inventarlo. No sirve para temas fuera de La Liga.",
    inputSchema: {
      type: "object",
      properties: {
        question: {
          type: "string",
          description: "Pregunta en español, por ejemplo «cuando juega el barca».",
        },
      },
      required: ["question"],
    },
    buildQuestion({ question }) {
      const local = clean(question);
      return local || null;
    },
    missing: "Necesito una pregunta en español (por ejemplo: question: \"cuando juega el barca\").",
  },
];

async function runTool(tool, args) {
  const input = args && typeof args === "object" ? args : {};
  let question = null;
  try {
    question = tool.buildQuestion(input);
  } catch (error) {
    console.warn(`[WebMCP] argumentos inválidos en ${tool.name}`, error);
  }
  // Every nullable question builder in this closed catalog declares its message.
  if (!question) return textResult(tool.missing);
  return answerQuestion(question);
}

// ------------------------------------------------------------- registration

async function waitForModelContext(timeoutMs = 2500) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (document.modelContext) return document.modelContext;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  return document.modelContext || null;
}

async function registerTools() {
  const modelContext = await waitForModelContext();
  if (!modelContext || typeof modelContext.registerTool !== "function") {
    console.warn(POLYFILL_WARNING);
    return;
  }

  const registered = [];
  for (const tool of TOOLS) {
    try {
      await modelContext.registerTool({
        name: tool.name,
        description: tool.description,
        inputSchema: tool.inputSchema,
        execute: (args) => runTool(tool, args),
      });
      registered.push(tool.name);
    } catch (error) {
      console.warn(`[WebMCP] no pude registrar ${tool.name}`, error);
    }
  }

  state.tools = registered;
  state.available = registered.length === TOOLS.length;
  if (state.available) {
    console.info(`[WebMCP] ${registered.length} herramientas registradas: ${registered.join(", ")}`);
  } else if (registered.length) {
    console.warn(`[WebMCP] registro parcial: ${registered.join(", ")}`);
  } else {
    console.warn("[WebMCP] el navegador aceptó el polyfill pero no registró ninguna herramienta.");
  }
}

/**
 * Registra las herramientas una sola vez. Es idempotente: llamarla de nuevo
 * devuelve el estado actual sin volver a registrar.
 */
export async function setupWebMcp({ today_label: todayLabel = "" } = {}) {
  if (state.attempted) return webmcpStatus();
  state.attempted = true;
  if (todayLabel) state.todayLabel = todayLabel;

  try {
    await registerTools();
  } catch (error) {
    console.warn("[WebMCP] falló el registro de herramientas", error);
    state.available = false;
    state.tools = [];
  }
  return webmcpStatus();
}

/** Resumen de solo lectura para la UI. */
export function webmcpStatus() {
  return { available: state.available, tools: [...state.tools] };
}
