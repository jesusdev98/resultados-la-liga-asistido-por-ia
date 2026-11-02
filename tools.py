"""Tool layer for the in-browser model: structured La Liga data, never prose.

The browser model picks ONE tool name plus arguments; ``server.py`` validates that
choice here against the live payload and answers with JSON. The model then writes
its sentence from that JSON, so every number comes from the API.

Standard library only. Every value is derived from the payload: when a datum is
missing the tool fails with a Spanish reason instead of filling the gap. The
``ok: False`` result is not a transport error — it is the feedback the model uses
to correct itself on the single retry the client allows.
"""

from __future__ import annotations

import re
from typing import Any, Callable

import resolve
import panel

TOOL_NAMES = ("proximo_partido", "ultimo_resultado", "tabla", "posicion", "descenso", "laliga_panel")

# One answer never needs the whole calendar: the next few matches are enough and
# keep the JSON small for a 1.5B model.
MAX_PARTIDOS = 3
MAX_EQUIPOS_SUGERIDOS = 10


# --------------------------------------------------------------------- results
def _ok(**datos: Any) -> dict:
    return {"ok": True, "datos": datos}


def _fail(message: str) -> dict:
    return {"ok": False, "error": message}


# ----------------------------------------------------------------------- args
def _text(value: Any) -> str:
    """Argument as trimmed text. Anything that is not a string counts as absent."""
    return value.strip() if isinstance(value, str) else ""


def _team_rows(payload: dict) -> list[dict]:
    return sorted(payload.get("standings") or [], key=lambda row: row.get("rank") or 0)


def _team_names(payload: dict) -> list[str]:
    return [row["team"] for row in _team_rows(payload) if row.get("team")]


def _suggest_names(payload: dict) -> str:
    """`Barcelona, Atlético Madrid, …` — closed with a period, ready to paste."""
    names = _team_names(payload)
    shown = names[:MAX_EQUIPOS_SUGERIDOS]
    return ", ".join(shown) + ("…" if len(names) > len(shown) else ".")


def _resolve_equipo(raw: Any, payload: dict, what: str = "equipo") -> tuple[list[str], str | None]:
    """Resolve one argument to canonical team names.

    Returns ``([], None)`` when the argument is absent, and ``([], message)`` when
    it is present but does not name a La Liga team — that message is what the model
    reads to fix itself.
    """
    text = _text(raw)
    if not text:
        return [], None

    found = resolve.find_teams(text, payload)
    if not found:
        return [], (
            f"«{text}» no es un equipo de La Liga. "
            f"Equipos disponibles: {_suggest_names(payload)}"
        )
    if len(found) > 2:
        return [], (
            f"«{text}» menciona más de dos equipos. Indicá uno solo, o un equipo y su rival."
        )
    if what == "rival" and len(found) > 1:
        return [], f"«{text}» menciona más de un equipo. El rival tiene que ser un solo equipo."
    return found, None


def _fixture_row(match: dict) -> dict:
    stamp = match.get("date")
    return {
        "local": match.get("home") or "?",
        "visitante": match.get("away") or "?",
        "fecha_iso": stamp,
        **resolve.fixture_labels(match),
        # Empty string when ESPN did not publish a venue: the tool never guesses one.
        "estadio": match.get("venue") or "",
    }


def _standing_row(row: dict) -> dict:
    return {
        "pos": row.get("rank"),
        "equipo": row.get("team"),
        "puntos": row.get("points"),
        "jugados": row.get("played"),
        "ganados": row.get("wins"),
        "empatados": row.get("draws"),
        "perdidos": row.get("losses"),
        "dg": row.get("goal_diff"),
    }


def _sorted_fixtures(payload: dict) -> list[dict]:
    return sorted(payload.get("fixtures") or [], key=lambda match: str(match.get("date") or ""))


def _sorted_results(payload: dict) -> list[dict]:
    return sorted(
        payload.get("results") or [],
        key=lambda match: str(match.get("date") or ""),
        reverse=True,
    )


def _involves(match: dict, team: str) -> bool:
    return team in (match.get("home"), match.get("away"))


# ---------------------------------------------------------------------- tools
def _proximo_partido(args: dict, payload: dict) -> dict:
    """Next 1..4 fixtures (legacy default three), optionally head-to-head."""
    equipo, error = _resolve_equipo(args.get("equipo"), payload, "equipo")
    if error:
        return _fail(error)
    rival, error = _resolve_equipo(args.get("rival"), payload, "rival")
    if error:
        return _fail(error)

    if len(equipo) == 2 and rival:
        return _fail(
            "Ya indicaste dos equipos en «equipo». Dejá «rival» vacío o pasá un solo equipo."
        )

    repeated = [team for team in equipo if team in rival]
    if repeated:
        return _fail(
            f"«{repeated[0]}» no puede ser rival de sí mismo. Indicá dos equipos distintos."
        )

    wanted = list(dict.fromkeys(equipo + rival))
    count = args.get("cantidad", MAX_PARTIDOS)
    if type(count) is not int or not 1 <= count <= 4:
        return _fail("La cantidad debe ser un entero entre 1 y 4.")
    fixtures = _sorted_fixtures(payload)
    after = _text(args.get("despues_de"))
    if "despues_de" in args:
        if not wanted or not after:
            return _fail("Necesito el partido anterior para buscar el siguiente.")
        fixtures = resolve.next_fixtures(payload, wanted, after)
    if "despues_del_dia" in args:
        if not wanted or "despues_de" in args:
            return _fail("Indicá un equipo y una sola referencia temporal.")
        fixtures = resolve.next_fixtures(payload, wanted, after_day=args["despues_del_dia"])
    if "fecha_exacta" in args:
        if "despues_de" in args or "despues_del_dia" in args:
            return _fail("La confirmación requiere una sola fecha exacta.")
        exact = resolve._parse(args["fecha_exacta"])
        if exact is None:
            return _fail("La fecha del partido no es válida.")
        fixtures = [match for match in fixtures if resolve._parse(match.get("date")) == exact]

    if len(wanted) == 2:
        pair = set(wanted)
        picked = [match for match in fixtures if {match.get("home"), match.get("away")} == pair][
            :count
        ]
        if not picked:
            return _fail(
                f"No hay próximos partidos entre {' y '.join(wanted)} en los datos actuales."
            )
    elif len(wanted) == 1:
        team = wanted[0]
        picked = [match for match in fixtures if _involves(match, team)][:count]
        if not picked:
            return _fail(f"No hay próximos partidos de {team} en los datos actuales.")
    else:
        # No team at all ("¿cuándo es el próximo partido?"): the league's next ones.
        picked = fixtures[:count]
        if not picked:
            return _fail("No hay próximos partidos en los datos actuales.")

    return _ok(partidos=[_fixture_row(match) for match in picked])


def _ultimo_resultado(args: dict, payload: dict) -> dict:
    equipo, error = _resolve_equipo(args.get("equipo"), payload, "equipo")
    if error:
        return _fail(error)
    rival, error = _resolve_equipo(args.get("rival"), payload, "rival")
    if error:
        return _fail(error)
    if not equipo:
        return _fail("Necesito el nombre de un equipo para buscar su último resultado.")
    if len(equipo) > 1 or len(rival) > 1:
        return _fail(
            "Indicá un equipo y, como máximo, un rival para buscar el último resultado."
        )

    team = equipo[0]
    if rival and rival[0] == team:
        return _fail("Un equipo no puede ser rival de sí mismo.")
    if rival:
        wanted = {team, rival[0]}
        match = next(
            (item for item in _sorted_results(payload) if {item.get("home"), item.get("away")} == wanted),
            None,
        )
    else:
        match = next((item for item in _sorted_results(payload) if _involves(item, team)), None)
    if match is None:
        return _fail(f"No hay resultados de {team} en los datos actuales.")

    home_score = match.get("home_score")
    away_score = match.get("away_score")
    if not isinstance(home_score, int) or not isinstance(away_score, int):
        return _fail(f"El último resultado de {team} no trae marcador en los datos actuales.")

    stamp = match.get("date")
    return _ok(
        local=match.get("home") or "?",
        visitante=match.get("away") or "?",
        goles_local=home_score,
        goles_visitante=away_score,
        fecha_iso=stamp,
        dia=resolve.day_label(stamp),
    )


def _tabla(args: dict, payload: dict) -> dict:
    rows = _team_rows(payload)
    if not rows:
        return _fail("No hay tabla de posiciones en los datos actuales.")
    return _ok(filas=[_standing_row(row) for row in rows])


def _posicion(args: dict, payload: dict) -> dict:
    equipo, error = _resolve_equipo(args.get("equipo"), payload, "equipo")
    if error:
        return _fail(error)
    if not equipo:
        return _fail("Necesito el nombre de un equipo para buscar su posición.")
    if len(equipo) > 1:
        return _fail(
            f"«{_text(args.get('equipo'))}» menciona más de un equipo. "
            "Indicá un solo equipo para buscar su posición."
        )

    row = next((item for item in _team_rows(payload) if item.get("team") == equipo[0]), None)
    if row is None:
        return _fail(f"No hay datos de {equipo[0]} en la tabla actual.")
    return _ok(**_standing_row(row))


def _descenso(args: dict, payload: dict) -> dict:
    rows = _team_rows(payload)
    if len(rows) < 3:
        return _fail("La tabla actual no tiene equipos suficientes para calcular el descenso.")

    relegation = [
        row
        for row in rows
        if re.search(r"relegation|descenso", str(row.get("zone") or ""), re.IGNORECASE)
    ]
    if len(relegation) != 3:
        relegation = rows[-3:]  # no zone metadata: the last three by rank

    return _ok(
        filas=[
            {"pos": row.get("rank"), "equipo": row.get("team"), "puntos": row.get("points")}
            for row in sorted(relegation, key=lambda item: item.get("rank") or 0)
        ]
    )


_HANDLERS: dict[str, Callable[[dict, dict], dict]] = {
    "proximo_partido": _proximo_partido,
    "ultimo_resultado": _ultimo_resultado,
    "tabla": _tabla,
    "posicion": _posicion,
    "descenso": _descenso,
}


def run_tool(name: str, args: dict | None, payload: dict) -> dict:
    """Run ONE tool against the payload.

    Returns ``{"ok": True, "datos": {...}}`` or ``{"ok": False, "error": "..."}``.
    A malformed or wrong tool name is a model-correctable error, not an exception.
    """
    if not isinstance(name, str) or not name.strip():
        return _fail("Falta el nombre de la herramienta.")

    safe_args = args if isinstance(args, dict) else {}
    if name.strip() == "laliga_panel":
        if safe_args.get("equipo") or safe_args.get("rival"):
            return run_tool("proximo_partido", safe_args, payload)
        return {"ok": True, "datos": {}, **panel.panel_result(payload)}
    handler = _HANDLERS.get(name.strip())
    if handler is None:
        return _fail(
            f"Herramienta desconocida: «{name.strip()}». "
            f"Herramientas disponibles: {', '.join(TOOL_NAMES)}."
        )

    result = handler(safe_args, payload)
    if result.get("ok"):
        result.update(panel.panel_result(payload, result["datos"]))
    return result
