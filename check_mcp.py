"""Prueba end-to-end del servidor MCP por stdio, con el cliente oficial del SDK.

Arranca ``mcp_server.py`` como subproceso, lista las tools y las ejecuta de verdad.
Las fechas esperadas se derivan del payload en vivo, nunca están hardcodeadas.

Run: cd '/home/jesus/prueba deepseek' && .venv/bin/python check_mcp.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from mcp import Client, StdioServerParameters

import laliga
import resolve

BASE_DIR = Path(__file__).resolve().parent
EXPECTED_TOOLS = (
    "laliga_preguntar",
    "laliga_tabla",
    "laliga_resultados",
    "laliga_proximos",
    "laliga_panel",
)


class Checks:
    def __init__(self) -> None:
        self.failures: list[str] = []
        self.passed = 0

    def check(self, condition: bool, label: str, detail: str = "") -> None:
        if condition:
            self.passed += 1
            print(f"  ok   {label}")
        else:
            self.failures.append(label)
            print(f"  FAIL {label}{f' — {detail}' if detail else ''}")


def text_of(result) -> str:
    """Concatena los bloques de texto de un CallToolResult."""
    parts = [block.text for block in result.content if getattr(block, "type", "") == "text"]
    return "\n".join(parts)


def next_clasico_label(payload: dict) -> tuple[str, str] | None:
    """Día y hora del próximo Barça-Madrid según el payload (jamás hardcodeado)."""
    fixtures = sorted(
        (
            match
            for match in payload.get("fixtures") or []
            if {match.get("home"), match.get("away")} == {"Barcelona", "Real Madrid"}
        ),
        key=lambda match: str(match.get("date") or ""),
    )
    if not fixtures:
        return None
    return resolve.datetime_label(fixtures[0]["date"]), fixtures[0]["date"]


async def exercise(checks: Checks, payload: dict) -> None:
    params = StdioServerParameters(
        command=sys.executable,
        args=["mcp_server.py"],
        cwd=str(BASE_DIR),
    )

    async with Client(params) as client:
        print("\n[1] list_tools()")
        listing = await client.list_tools()
        names = [tool.name for tool in listing.tools]
        print(f"  tools: {', '.join(names) or '(ninguna)'}")
        for expected in EXPECTED_TOOLS:
            checks.check(expected in names, f"la tool {expected} está publicada")
        checks.check(
            len(names) == len(EXPECTED_TOOLS),
            f"hay exactamente {len(EXPECTED_TOOLS)} tools",
            f"got {len(names)}: {names}",
        )

        print("\n[2] laliga_preguntar · clásico")
        clasico = next_clasico_label(payload)
        result = await client.call_tool(
            "laliga_preguntar", {"question": "cuando juega el barca contra el madrid"}
        )
        answer_text = text_of(result)
        print(f"  esperado (payload): {clasico[0] if clasico else '(sin clásico programado)'}")
        print("  respuesta:")
        for line in answer_text.splitlines():
            print(f"    {line}")
        checks.check(not result.is_error, "laliga_preguntar no devuelve error", str(result.is_error))
        checks.check(bool(answer_text.strip()), "laliga_preguntar devuelve texto")
        if clasico is not None:
            checks.check(
                clasico[0] in answer_text,
                f"la respuesta contiene la fecha real del clásico ({clasico[0]})",
                answer_text[:200],
            )
            # La fecha completa (día + hora) también tiene que estar, no solo el día.
            checks.check(
                resolve.time_label(clasico[1]) in answer_text,
                f"la respuesta contiene la hora real ({resolve.time_label(clasico[1])})",
                answer_text[:200],
            )
            checks.check(
                "Barcelona vs Real Madrid" in answer_text,
                "la respuesta nombra el cruce Barcelona vs Real Madrid",
                answer_text[:200],
            )

        print("\n[2b] laliga_preguntar · pregunta fuera de tema")
        off_topic = await client.call_tool(
            "laliga_preguntar", {"question": "cual es la capital de francia"}
        )
        off_text = text_of(off_topic)
        print(f"  respuesta: {off_text.splitlines()[1] if len(off_text.splitlines()) > 1 else off_text}")
        checks.check(
            "No tengo ese dato verificado" in off_text,
            "una pregunta sin dato devuelve el aviso explícito, no una invención",
            off_text[:200],
        )

        print("\n[3] laliga_panel · recurso MCP-UI")
        panel = await client.call_tool("laliga_panel", {})
        checks.check(not panel.is_error, "laliga_panel no devuelve error", str(panel.is_error))
        print(f"  bloques: {[getattr(block, 'type', '?') for block in panel.content]}")

        ui_blocks = [
            block
            for block in panel.content
            if getattr(block, "type", "") == "resource"
            and getattr(getattr(block, "resource", None), "mime_type", None) == "text/html"
            and str(getattr(getattr(block, "resource", None), "uri", "")).startswith("ui://")
        ]
        checks.check(bool(ui_blocks), "hay un EmbeddedResource text/html con uri ui://")
        checks.check(
            any(getattr(block, "type", "") == "text" for block in panel.content),
            "hay un bloque de texto de respaldo para clientes sin MCP-UI",
        )

        html_text = ui_blocks[0].resource.text if ui_blocks else ""
        if ui_blocks:
            uri = str(ui_blocks[0].resource.uri)
            size = len(html_text.encode("utf-8"))
            print(f"  uri: {uri}")
            print(f"  html: {size} bytes ({len(html_text)} caracteres)")
            checks.check(uri == "ui://prueba-deepseek/panel", "la uri del panel es la esperada", uri)
            checks.check(size < 15 * 1024, "el HTML del panel pesa menos de 15 KB", f"{size} bytes")
            checks.check("La Liga 2026-27" in html_text, "el panel muestra «La Liga 2026-27»")

            missing = [
                row["team"]
                for row in payload.get("standings") or []
                if str(row.get("team")) not in html_text
            ]
            checks.check(
                not missing,
                "el HTML contiene los 20 equipos del payload",
                f"faltan {missing[:5]}",
            )
            rows_in_html = html_text.count('<td class="pos">')
            checks.check(rows_in_html == 20, "el HTML tiene 20 filas de tabla", f"got {rows_in_html}")
            checks.check(
                html_text.count("<script>") == 1 and "script-src 'sha256-" in html_text,
                "el panel sólo permite el script de ajuste de altura con hash CSP",
            )
            updated_label = resolve.datetime_label(payload.get("updated_at"))
            checks.check(
                updated_label in html_text,
                f"el panel muestra la hora de actualización ({updated_label})",
                html_text[:200],
            )
            next_six = [
                resolve.fixture_labels(match)["dia"]
                for match in (payload.get("fixtures") or [])[:6]
            ]
            missing_labels = [label for label in next_six if label not in html_text]
            checks.check(
                not missing_labels,
                f"el panel muestra los {len(next_six)} próximos partidos con día y hora reales",
                str(missing_labels[:2]),
            )
            for tag in ("<style", "<table", "<ul", "<li"):
                checks.check(tag in html_text.lower(), f"el panel incluye {tag}>")

        print("\n[4] laliga_tabla")
        table = await client.call_tool("laliga_tabla", {})
        table_text = text_of(table)
        table_rows = [
            line for line in table_text.splitlines() if line.strip()[:1].isdigit() and " pts " in line
        ]
        print(f"  primera línea: {table_text.splitlines()[0] if table_text else '(vacío)'}")
        print(f"  filas: {len(table_rows)}")
        checks.check(not table.is_error, "laliga_tabla no devuelve error")
        checks.check(bool(table_text.strip()), "laliga_tabla devuelve texto")
        checks.check(len(table_rows) == 20, "laliga_tabla devuelve 20 filas", f"got {len(table_rows)}")
        table_missing = [
            row["team"] for row in payload.get("standings") or [] if str(row.get("team")) not in table_text
        ]
        checks.check(not table_missing, "la tabla incluye los 20 equipos", f"faltan {table_missing[:5]}")

        print("\n[5] laliga_resultados / laliga_proximos")
        wanted_results = min(3, len(payload.get("results") or []))
        results = await client.call_tool("laliga_resultados", {"limit": 3})
        results_text = text_of(results)
        print("  resultados:")
        for line in results_text.splitlines():
            print(f"    {line}")
        checks.check(not results.is_error, "laliga_resultados no devuelve error")
        checks.check(
            len(results_text.splitlines()) == wanted_results,
            f"laliga_resultados respeta limit=3 ({wanted_results} filas)",
            str(len(results_text.splitlines())),
        )
        if payload.get("results"):
            first = payload["results"][0]
            expected_day = resolve.day_label(first["date"])
            expected_score = f"{first['home']} {first['home_score']}-{first['away_score']} {first['away']}"
            checks.check(expected_day in results_text, f"el último resultado trae su fecha real ({expected_day})")
            checks.check(expected_score in results_text, "el último resultado trae el marcador real",
                         expected_score)

        wanted_fixtures = min(2, len(payload.get("fixtures") or []))
        fixtures = await client.call_tool("laliga_proximos", {"limit": 2})
        fixtures_text = text_of(fixtures)
        print("  próximos:")
        for line in fixtures_text.splitlines():
            print(f"    {line}")
        checks.check(not fixtures.is_error, "laliga_proximos no devuelve error")
        checks.check(
            len(fixtures_text.splitlines()) == wanted_fixtures,
            f"laliga_proximos respeta limit=2 ({wanted_fixtures} filas)",
            str(len(fixtures_text.splitlines())),
        )
        if payload.get("fixtures"):
            next_match = payload["fixtures"][0]
            labels = resolve.fixture_labels(next_match)
            expected_stamp = f"{labels['dia']}, {labels['hora']}"
            checks.check(
                expected_stamp in fixtures_text,
                f"el próximo partido trae día y hora reales ({expected_stamp})",
            )


async def main() -> int:
    print("Prueba DeepSeek · self-check del servidor MCP (stdio)")
    try:
        payload = laliga.get_payload(force=True)
    except Exception as exc:
        print(f"  FAIL no pude obtener el payload: {exc}")
        return 1

    print("\n[Datos]")
    print(f"  season      : {payload.get('season')}")
    print(f"  today_label : {payload.get('today_label')}")
    print(f"  standings   : {len(payload.get('standings') or [])}")
    print(f"  fixtures    : {len(payload.get('fixtures') or [])}")
    print(f"  server      : {sys.executable} mcp_server.py (cwd={BASE_DIR})")

    checks = Checks()
    try:
        await exercise(checks, payload)
    except Exception as exc:
        print(f"\n  FAIL el cliente MCP falló: {exc!r}")
        return 1

    print("\n[Resumen]")
    if checks.failures:
        print(f"  {len(checks.failures)} fallo(s), {checks.passed} ok")
        for failure in checks.failures:
            print(f"  - {failure}")
        return 1
    print(f"  Todo bien: {checks.passed} checks pasaron.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
