"""Servidor MCP (stdio) de La Liga 2026-27: tools de texto y panel MCP-UI.

Los datos y las respuestas salen de ``laliga.py`` y del resolvedor determinístico
``resolve.py``: el servidor nunca inventa fechas, resultados ni posiciones.

Necesita el SDK oficial ``mcp`` 2.x dentro del venv del proyecto::

    .venv/bin/python mcp_server.py

Sin el paquete instalado el módulo avisa en español y sale con código 0.
"""

from __future__ import annotations

import sys

try:
    from mcp import types
    from mcp.server.mcpserver import MCPServer
except ImportError:  # dependencia opcional
    print(
        "El servidor MCP necesita el paquete oficial «mcp» 2.x.\n"
        "Corrélo con el entorno virtual del proyecto:\n"
        "  cd '/home/jesus/prueba deepseek' && .venv/bin/python mcp_server.py\n"
        "Si el venv no existe: python3 -m venv .venv && .venv/bin/pip install mcp\n"
        "La app web funciona igual sin esto: python3 server.py"
    )
    raise SystemExit(0)

import laliga
import resolve
import panel
import tools

PANEL_URI = panel.PANEL_URI
PANEL_MIME = panel.PANEL_MIME

INSTRUCTIONS = (
    "Servidor local de La Liga 2026-27. Expone datos verificados de ESPN a través de "
    "laliga.py y del resolvedor determinístico de resolve.py: próximo partido de un equipo, "
    "últimos resultados, tabla de posiciones completa y zona de descenso, con las fechas en "
    "hora de España (Europe/Madrid). Para preguntas en español usá laliga_preguntar; para una "
    "vista visual usá laliga_panel, que devuelve un recurso MCP-UI con la tabla y los próximos "
    "partidos (y un texto equivalente para clientes sin MCP-UI). Los datos se cachean 5 minutos."
)

server = MCPServer(name="prueba-deepseek", instructions=INSTRUCTIONS)

# ------------------------------------------------------------------ helpers
def _payload_or_error() -> tuple[dict | None, str]:
    try:
        return laliga.get_payload(), ""
    except laliga.DataError as exc:
        return None, f"No pude leer los datos de La Liga: {exc}"


def _diff_text(value: int) -> str:
    return f"+{value}" if value > 0 else str(value)


def _format_table(payload: dict) -> str:
    lines = [f"Tabla {payload['season']} (actualizado {payload['updated_at']}, hora UTC)", ""]
    for row in payload["standings"]:
        zone = f" [{row['zone']}]" if row.get("zone") else ""
        lines.append(
            f"{row['rank']:>2}. {row['team']} — {row['points']} pts "
            f"({row['played']} PJ, {_diff_text(row['goal_diff'])} DG){zone}"
        )
    return "\n".join(lines)


def _format_result(match: dict) -> str:
    return (
        f"{resolve.day_label(match['date'])} · {match['home']} "
        f"{match['home_score']}-{match['away_score']} {match['away']}"
    )


def _format_fixture(match: dict) -> str:
    labels = resolve.fixture_labels(match)
    return f"{labels['dia']}, {labels['hora']} · {labels['estado_fecha']} · {match['home']} vs {match['away']}"


def _verified_text(answer: dict, today: str) -> str:
    """Respuesta verificada + hechos, tal como la devuelve el resolvedor."""
    lines = [f"Referencia: {today} (hora de España)."]
    if answer["intent"] == "none" or not answer["answer"].strip():
        lines.append("No tengo ese dato verificado para esa consulta.")
        lines.append(
            "Probá con un equipo de La Liga 2026-27, por ejemplo: "
            "«cuando juega el barca», «como salio el madrid», «tabla» o «quien desciende»."
        )
        return "\n".join(lines)

    lines.append(answer["answer"])
    if answer["facts"]:
        lines.append("")
        lines.append("Hechos verificados:")
        lines.extend(f"- {fact}" for fact in answer["facts"])
    return "\n".join(lines)


def _panel_title(payload: dict) -> str:
    year = int(payload["season_year"])
    return f"La Liga {year}-{(year + 1) % 100:02d}"


def _panel_html(payload: dict) -> str:
    """Compatibility helper for the shared resource renderer."""
    return panel.panel_result(payload)["content"][0]["resource"]["text"]


# -------------------------------------------------------------------- tools
@server.tool(
    description=(
        "Responde en español una pregunta sobre La Liga 2026-27 (próximo partido, último "
        "resultado, tabla o descenso) usando el resolvedor determinístico sobre los datos en vivo "
        "de ESPN. Devuelve la respuesta verificada con sus hechos y la fecha de referencia; si no "
        "reconoce el dato dice «No tengo ese dato verificado» en vez de inventarlo."
    )
)
def laliga_preguntar(question: str) -> str:
    payload, error = _payload_or_error()
    if payload is None:
        return error
    text = (question or "").strip()
    if not text:
        return "La pregunta está vacía. Probá con «cuando juega el barca» o «tabla»."
    resolved = resolve.answer(text, payload)
    return _verified_text(resolved, payload.get("today_label") or resolve.today_label())


@server.tool(
    description=(
        "Devuelve la tabla de posiciones completa de La Liga 2026-27: los 20 equipos con puntos, "
        "partidos jugados, diferencia de goles y zona (Champions, Europa, Conference o descenso)."
    )
)
def laliga_tabla() -> str:
    payload, error = _payload_or_error()
    if payload is None:
        return error
    return _format_table(payload)


@server.tool(
    description=(
        "Devuelve los últimos resultados de La Liga 2026-27, del más reciente al más antiguo, con "
        "marcador final y fecha en hora de España. `limit` va de 1 a 100 (por defecto 10)."
    )
)
def laliga_resultados(limit: int = 10) -> str:
    payload, error = _payload_or_error()
    if payload is None:
        return error
    limit = max(1, min(int(limit), 100))
    matches = payload["results"][:limit]
    if not matches:
        return "Todavía no hay resultados en esta temporada."
    return "\n".join(_format_result(match) for match in matches)


@server.tool(
    description=(
        "Devuelve los próximos partidos de La Liga 2026-27, del más cercano al más lejano, con día, "
        "fecha y hora en hora de España. `limit` va de 1 a 100 (por defecto 6)."
    )
)
def laliga_proximos(limit: int = 6) -> str:
    payload, error = _payload_or_error()
    if payload is None:
        return error
    limit = max(1, min(int(limit), 100))
    matches = payload["fixtures"][:limit]
    if not matches:
        return "No hay partidos programados."
    return "\n".join(_format_fixture(match) for match in matches)


@server.tool(
    description=(
        "Devuelve un panel MCP-UI con escudos y altura adaptativa, con la clasificación "
        "completa de La Liga y los próximos partidos, con escudos de ESPN. "
        "Con equipo y rival muestra ese cruce, incluida la fecha provisional y hora por definir. "
        "Incluye además un texto con la tabla para clientes que no renderizan MCP-UI."
    ),
    structured_output=False,
)
def laliga_panel(equipo: str = "", rival: str = "") -> types.CallToolResult:
    payload, error = _payload_or_error()
    if payload is None:
        return types.CallToolResult(content=[types.TextContent(type="text", text=error)], is_error=True)

    result = tools.run_tool("laliga_panel", {"equipo": equipo, "rival": rival}, payload)
    if not result["ok"]:
        return types.CallToolResult(content=[types.TextContent(type="text", text=result["error"])], is_error=True)
    return types.CallToolResult.model_validate({"content": result["content"], "isError": False})


if __name__ == "__main__":
    try:
        server.run(transport="stdio")
    except KeyboardInterrupt:
        sys.exit(0)
