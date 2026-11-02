"""Offline check for the tool layer (`tools.py`) against the live payload.

Run: python3 check_tools.py (no server needed).

Every expected value is derived from `laliga.get_payload()`: no date, score or
points total is hardcoded, so the check stays valid as the season moves.
"""

from __future__ import annotations

import json
import sys
from typing import Any

import laliga
import resolve
import tools

ALIASES = (
    ("barca", "Barcelona"),
    ("barsa", "Barcelona"),
    ("madrid", "Real Madrid"),
    ("atleti", "Atlético Madrid"),
    ("betis", "Real Betis"),
    ("la real", "Real Sociedad"),
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


def sorted_standings(payload: dict) -> list[dict]:
    return sorted(payload.get("standings") or [], key=lambda row: row.get("rank") or 0)


def sorted_fixtures(payload: dict) -> list[dict]:
    return sorted(payload.get("fixtures") or [], key=lambda match: str(match.get("date") or ""))


def sorted_results(payload: dict) -> list[dict]:
    return sorted(
        payload.get("results") or [],
        key=lambda match: str(match.get("date") or ""),
        reverse=True,
    )


def involves(match: dict, team: str) -> bool:
    return team in (match.get("home"), match.get("away"))


def run(checks: Checks, payload: dict) -> None:
    standings = sorted_standings(payload)
    fixtures = sorted_fixtures(payload)
    results = sorted_results(payload)
    fixture_dates = {match.get("date") for match in fixtures}
    team_names = [row.get("team") for row in standings]

    # ---------------------------------------------------------------- tabla
    print("\n[tabla]")
    result = tools.run_tool("tabla", {}, payload)
    checks.check(result["ok"] is True, "tabla devuelve ok: true", str(result.get("error")))
    filas = result.get("datos", {}).get("filas") or []
    print(f"  filas: {len(filas)} · líder: {filas[0].get('equipo') if filas else '-'} "
          f"{filas[0].get('puntos') if filas else '-'} pts")
    checks.check(len(filas) == 20, "tabla tiene 20 filas", f"got {len(filas)}")
    checks.check(
        [fila.get("pos") for fila in filas] == list(range(1, 21)),
        "tabla ordenada 1..20",
        repr([fila.get("pos") for fila in filas]),
    )
    checks.check(
        bool(standings) and filas and filas[0].get("puntos") == standings[0].get("points"),
        "fila 1 tiene los puntos del líder del payload",
        f"{filas[0].get('puntos') if filas else None} != {standings[0].get('points') if standings else None}",
    )
    checks.check(
        all(
            fila.get("pos") == row.get("rank")
            and fila.get("equipo") == row.get("team")
            and fila.get("puntos") == row.get("points")
            and fila.get("jugados") == row.get("played")
            and fila.get("ganados") == row.get("wins")
            and fila.get("empatados") == row.get("draws")
            and fila.get("perdidos") == row.get("losses")
            and fila.get("dg") == row.get("goal_diff")
            for fila, row in zip(filas, standings)
        ),
        "cada fila de tabla copia el payload sin recalcular",
    )

    # ------------------------------------------------------------- descenso
    print("\n[descenso]")
    result = tools.run_tool("descenso", {}, payload)
    checks.check(result["ok"] is True, "descenso devuelve ok: true", str(result.get("error")))
    filas = result.get("datos", {}).get("filas") or []
    for fila in filas:
        print(f"  {fila.get('pos')}. {fila.get('equipo')} · {fila.get('puntos')} pts")
    checks.check(len(filas) == 3, "descenso devuelve exactamente 3 filas", f"got {len(filas)}")
    last_three = standings[-3:]
    checks.check(
        [(f.get("pos"), f.get("equipo"), f.get("puntos")) for f in filas]
        == [(r.get("rank"), r.get("team"), r.get("points")) for r in last_three],
        "descenso coincide con los últimos 3 de la tabla",
        f"{filas} != {last_three}",
    )

    # ------------------------------------------------------------- posicion
    print(f"\n[posicion · {len(standings)} equipos]")
    errors: list[str] = []
    for row in standings:
        one = tools.run_tool("posicion", {"equipo": row["team"]}, payload)
        datos = one.get("datos") or {}
        if not one.get("ok") or datos.get("pos") != row.get("rank") or datos.get("equipo") != row.get("team"):
            errors.append(f"{row.get('team')}: {one.get('error') or datos}")
    checks.check(not errors, "posicion de cada equipo = su puesto real", "; ".join(errors[:3]))
    sample = next((row for row in standings if row.get("team") == "Barcelona"), standings[0])
    one = tools.run_tool("posicion", {"equipo": sample["team"]}, payload).get("datos", {})
    print(f"  {sample['team']}: {one.get('pos')}º · {one.get('puntos')} pts · "
          f"{one.get('jugados')} PJ · DG {one.get('dg')}")
    checks.check(
        one.get("puntos") == sample.get("points") and one.get("dg") == sample.get("goal_diff"),
        "posicion trae puntos y diferencia de goles del payload",
    )

    # ------------------------------------------------------ proximo_partido
    print("\n[proximo_partido]")
    result = tools.run_tool("proximo_partido", {"equipo": "barca"}, payload)
    checks.check(result["ok"] is True, "proximo_partido(barca) devuelve ok: true", str(result.get("error")))
    partidos = result.get("datos", {}).get("partidos") or []
    checks.check(bool(partidos), "proximo_partido(barca) devuelve al menos un partido")
    checks.check(
        all(partido.get("fecha_iso") in fixture_dates for partido in partidos),
        "cada fecha_iso existe en los fixtures del payload",
        str([p.get("fecha_iso") for p in partidos]),
    )
    checks.check(
        all("Barcelona" in (partido.get("local"), partido.get("visitante")) for partido in partidos),
        "proximo_partido(barca) resuelve el alias a Barcelona",
        str([(p.get("local"), p.get("visitante")) for p in partidos]),
    )
    first = partidos[0] if partidos else {}
    print(f"  barca -> {first.get('local')} vs {first.get('visitante')} · "
          f"{first.get('dia')} {first.get('hora')} · {first.get('estadio') or '(sin estadio)'}")
    source_fixture = next((m for m in fixtures if m["date"] == first.get("fecha_iso") and m["home"] == first.get("local")), None)
    checks.check(
        source_fixture is not None and all(first.get(key) == value for key, value in resolve.fixture_labels(source_fixture).items()),
        "día y horario respetan la confirmación de la fuente",
    )

    clasicos = [
        match
        for match in fixtures
        if {match.get("home"), match.get("away")} == {"Barcelona", "Real Madrid"}
    ]
    if clasicos:
        result = tools.run_tool("proximo_partido", {"equipo": "barca", "rival": "madrid"}, payload)
        partidos = result.get("datos", {}).get("partidos") or []
        print(f"  barca vs madrid -> {partidos[0].get('fecha_iso') if partidos else '-'} "
              f"(payload: {clasicos[0].get('date')})")
        checks.check(
            bool(partidos) and partidos[0].get("fecha_iso") == clasicos[0].get("date"),
            "proximo_partido(barca, rival=madrid) devuelve el clásico del payload",
            str(partidos),
        )
        checks.check(
            all({p.get("local"), p.get("visitante")} == {"Barcelona", "Real Madrid"} for p in partidos),
            "el cruce devuelve solo partidos entre los dos equipos",
            str([(p.get("local"), p.get("visitante")) for p in partidos]),
        )
    else:
        print("  skip clásico: el payload no programa Barcelona-Real Madrid esta temporada")

    # ----------------------------------------------------- ultimo_resultado
    print("\n[ultimo_resultado]")
    result = tools.run_tool("ultimo_resultado", {"equipo": "madrid"}, payload)
    checks.check(result["ok"] is True, "ultimo_resultado(madrid) devuelve ok: true", str(result.get("error")))
    datos = result.get("datos") or {}
    expected = next((match for match in results if involves(match, "Real Madrid")), None)
    checks.check(expected is not None, "el payload trae algún resultado del Madrid")
    if expected is not None:
        print(f"  madrid -> {datos.get('local')} {datos.get('goles_local')}-"
              f"{datos.get('goles_visitante')} {datos.get('visitante')} · {datos.get('dia')}")
        checks.check(
            datos.get("fecha_iso") == expected.get("date")
            and datos.get("goles_local") == expected.get("home_score")
            and datos.get("goles_visitante") == expected.get("away_score")
            and datos.get("local") == expected.get("home")
            and datos.get("visitante") == expected.get("away"),
            "marcador y fecha = resultado real más reciente del payload",
            f"{datos} != {expected}",
        )
        checks.check(
            datos.get("dia") == resolve.day_label(expected.get("date")),
            "el día del resultado sale de resolve.day_label",
        )

    # ------------------------------------------------------------- aliases
    print("\n[alias]")
    for alias, canonical in ALIASES:
        one = tools.run_tool("posicion", {"equipo": alias}, payload)
        datos = one.get("datos") or {}
        checks.check(
            one.get("ok") is True and datos.get("equipo") == canonical,
            f"alias «{alias}» -> {canonical}",
            str(one.get("error") or datos.get("equipo")),
        )

    # -------------------------------------------------------------- errores
    print("\n[errores corregibles]")
    cases: list[tuple[str, str, dict]] = [
        ("posicion", "posicion equipo=liga", {"equipo": "liga"}),
        ("proximo_partido", "proximo_partido rival=equipo", {"equipo": "barcelona", "rival": "barcelona"}),
        ("posicion", "posicion equipo inventado", {"equipo": "equipo inventado"}),
        ("ultimo_resultado", "ultimo_resultado sin equipo", {}),
        ("ultimo_resultado", "ultimo_resultado equipo vacío", {"equipo": "  "}),
        ("posicion", "posicion sin equipo", {}),
    ]
    messages: dict[str, str] = {}
    for name, label, args in cases:
        one = tools.run_tool(name, args, payload)
        message = str(one.get("error") or "")
        messages[label] = message
        print(f"  {label}: {message[:110]}")
        checks.check(one.get("ok") is False, f"{label} -> ok: false", str(one))
        checks.check(bool(message.strip()), f"{label} trae un mensaje en español")

    liga_message = messages["posicion equipo=liga"]
    checks.check("«liga»" in liga_message, "el error nombra el equipo inválido")
    checks.check("Barcelona" in liga_message, "el error sugiere equipos válidos")
    checks.check(
        "inventado" in messages["posicion equipo inventado"],
        "el error repite el texto ofensivo",
    )

    one = tools.run_tool("herramienta_inventada", {}, payload)
    print(f"  herramienta_inventada: {one.get('error', '')[:110]}")
    checks.check(one.get("ok") is False, "herramienta desconocida -> ok: false")
    checks.check("proximo_partido" in str(one.get("error")), "el error lista las herramientas válidas")

    # Tabla sin argumentos también acepta args ausentes (el server manda {}).
    checks.check(tools.run_tool("tabla", None, payload).get("ok") is True, "run_tool tolera args=None")

    # El contrato del endpoint es JSON plano.
    try:
        json.dumps(tools.run_tool("tabla", {}, payload), ensure_ascii=False)
        serializable = True
    except (TypeError, ValueError) as exc:
        serializable = False
        print(f"  json error: {exc}")
    checks.check(serializable, "los datos de tabla son JSON serializable")

    # La lista de equipos válidos arranca por el líder: sirve para corregirse.
    checks.check(
        bool(team_names) and team_names[0] in liga_message,
        "la lista de equipos válidos incluye al líder de la tabla",
        liga_message,
    )


def main() -> int:
    print("Prueba DeepSeek · check de herramientas")
    try:
        payload = laliga.get_payload(force=True)
    except Exception as exc:  # network or parsing failure
        print(f"  FAIL no pude obtener el payload: {exc}")
        return 1

    checks = Checks()
    print("\n[Datos]")
    print(f"  season      : {payload.get('season')}")
    print(f"  today_label : {payload.get('today_label')}")
    print(f"  standings   : {len(payload.get('standings') or [])}")
    print(f"  results     : {len(payload.get('results') or [])}")
    print(f"  fixtures    : {len(payload.get('fixtures') or [])}")
    print(f"  tools       : {', '.join(tools.TOOL_NAMES)}")

    run(checks, payload)

    print("\n[Resumen]")
    if checks.failures:
        print(f"  {len(checks.failures)} fallo(s), {checks.passed} ok")
        for failure in checks.failures:
            print(f"  - {failure}")
        return 1
    print(f"  Todo bien: {checks.passed} checks pasaron.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
