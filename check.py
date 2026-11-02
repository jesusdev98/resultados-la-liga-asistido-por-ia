"""Self-check for the La Liga payload and the deterministic resolver.

Run: python3 check.py (no server needed).
"""

from __future__ import annotations

import re
import sys
import unicodedata
from datetime import datetime, timedelta, timezone

import laliga
import resolve

MAX_CONTEXT_CHARS = 6000
FIXTURE_GRACE = timedelta(minutes=5)
ANSWER_STAMP = re.compile(r"(\d{1,2}) de ([a-z]+) de (\d{4}), (\d{2}):(\d{2})")


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


def parse_date(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def stamp_from_answer(text: str) -> tuple[int, str, int, int, int] | None:
    """Parse `domingo 25 de octubre de 2026, 21:00` into comparable parts."""
    plain = "".join(
        char for char in unicodedata.normalize("NFKD", str(text)) if not unicodedata.combining(char)
    ).lower()
    match = ANSWER_STAMP.search(plain)
    if match is None:
        return None
    day, month, year, hour, minute = match.groups()
    return (int(day), month, int(year), int(hour), int(minute))


def stamp_from_fixture(value: str) -> tuple[int, str, int, int, int] | None:
    local = resolve.local_datetime(value)
    if local is None:
        return None
    return (local.day, resolve.MONTHS[local.month - 1], local.year, local.hour, local.minute)


def answer_matches_fixture(text: str, match: dict) -> bool:
    """Check source date and strict kickoff availability, not placeholder hours."""
    labels = resolve.fixture_labels(match)
    if labels["dia"] not in text:
        return False
    if labels["horario_confirmado"]:
        return stamp_from_answer(text) == stamp_from_fixture(match.get("date", ""))
    return ("Por definir" in text and "Fecha provisional" in text
            and stamp_from_answer(text) is None)


def run_resolver(checks: Checks, payload: dict) -> dict[str, dict]:
    """Resolve the reference questions and assert the contract + bug invariant."""
    print("\n[Resolver determinístico]")

    # La referencia sale del payload, nunca hardcodeada: cuando ese partido se
    # juegue o cambien los puntos, el check tiene que seguir siendo válido.
    clasicos = sorted(
        (
            match
            for match in payload.get("fixtures") or []
            if {match.get("home"), match.get("away")} == {"Barcelona", "Real Madrid"}
        ),
        key=lambda match: str(match.get("date") or ""),
    )
    clasico = clasicos[0] if clasicos else None
    clasico_labels = resolve.fixture_labels(clasico) if clasico else {}
    clasico_day = clasico_labels.get("dia")
    clasico_time = clasico_labels.get("hora")

    barca = next(
        (row for row in payload.get("standings") or [] if row.get("team") == "Barcelona"),
        None,
    )
    barca_points = str(barca.get("points")) if barca else None

    h2h_needles = ["Barcelona vs Real Madrid"] + ([clasico_day] if clasico_day else [])

    cases = [
        ("cuando juega el barca contra el madrid", "next_match_h2h", h2h_needles),
        ("cuando es el proximo madrid barsa", "next_match_h2h", h2h_needles),
        ("cuando juega el barca", "next_match_team", ["Barcelona vs Real Madrid"]),
        ("que hora juega el clasico", "next_match_h2h", [clasico_time] if clasico_time else []),
        ("cuantos puntos tiene el barca", "team_standing", [barca_points] if barca_points else []),
        ("como salio el madrid", "last_match_team", []),
        ("tabla", "table", []),
        ("quien desciende", "bottom", []),
        ("cual es la capital de francia", "none", []),
        # Default de entidad: el usuario señala un equipo después de una tarjeta
        # verificada ("y el atleti") y quiere el próximo partido.
        ("y el atleti", "next_match_team", ["Atlético Madrid"]),
        # Seguimiento sin contexto: nunca puede caer en el chat libre del modelo.
        ("seguro?", "confirm_last", []),
        # Pregunta de fútbol que la API no puede verificar: aviso, no invención.
        ("como esta el vestuario del barca", "unknown_football", []),
    ]

    resolved: dict[str, dict] = {}
    for question, expected_intent, needles in cases:
        result = resolve.answer(question, payload)
        resolved[question] = result
        one_line = result["answer"].replace("\n", " | ") or "(vacía)"
        print(f"\n  P: {question}")
        print(f"     intent   : {result['intent']} ({result['confidence']})")
        print(f"     equipos  : {', '.join(result['teams']) or '-'}")
        print(f"     respuesta: {one_line}")
        print(f"     hechos   : {'; '.join(result['facts']) or '-'}")
        checks.check(
            result["intent"] == expected_intent,
            f"resolver «{question}» -> {expected_intent}",
            result["intent"],
        )
        for needle in needles:
            checks.check(
                needle in result["answer"],
                f"resolver «{question}» contiene {needle!r}",
                result["answer"][:150],
            )

    # Escenario del bug original: la fecha tiene que ser exactamente la del
    # fixture real (no el 27, el 28 ni el 30 de octubre que inventó el modelo).
    h2h = resolved["cuando juega el barca contra el madrid"]
    if clasico is not None:
        checks.check(
            answer_matches_fixture(h2h["answer"], clasico),
            f"resolver: fecha del clásico = {clasico_day}, {clasico_time} (la real del payload)",
            h2h["answer"],
        )
    else:
        print("  skip fecha del clásico: no quedan Barça-Madrid programados esta temporada")

    # Último resultado real del Madrid, tomado del payload (nunca hardcodeado).
    madrid_results = sorted(
        (
            match
            for match in payload.get("results") or []
            if "Real Madrid" in (match.get("home"), match.get("away"))
        ),
        key=lambda match: str(match.get("date") or ""),
        reverse=True,
    )
    madrid_answer = resolved["como salio el madrid"]["answer"]
    if madrid_results:
        last = madrid_results[0]
        expected = f"{last['home']} {last['home_score']}-{last['away_score']} {last['away']}"
        checks.check(
            expected in madrid_answer,
            "resolver «como salio el madrid» usa el resultado real del payload",
            f"esperaba {expected!r}, obtuve {madrid_answer!r}",
        )
    else:
        checks.check(False, "resolver «como salio el madrid» tiene un resultado real", "payload sin partidos del Madrid")

    # Tabla completa: los 20 nombres del payload aparecen en la respuesta.
    table_answer = resolved["tabla"]["answer"]
    missing = [row.get("team") for row in payload.get("standings") or [] if row.get("team") not in table_answer]
    checks.check(not missing, "resolver «tabla» incluye los 20 equipos", f"faltan {missing[:3]}")

    bottom_rows = resolved["quien desciende"]["facts"]
    checks.check(
        len(bottom_rows) == 3,
        "resolver «quien desciende» devuelve exactamente 3 filas",
        f"got {len(bottom_rows)}",
    )

    checks.check(
        resolved["cual es la capital de francia"]["answer"] == "",
        "resolver pregunta fuera de tema devuelve answer vacío",
    )

    checks.check(
        resolved["seguro?"]["answer"] == "",
        "resolver «seguro?» sin previous_question no inventa una respuesta",
    )
    checks.check(
        resolved["como esta el vestuario del barca"]["answer"] == "",
        "resolver «como esta el vestuario del barca» devuelve answer vacío",
    )

    # El bug en vivo: el usuario escribió «y madrid atleti» y el modelo inventó un
    # partido y una hora. El resolvedor tiene que devolver el cruce real o decir
    # que no lo tiene; nunca dejar el intent en «none».
    madrid_atleti = sorted(
        (
            match
            for match in payload.get("fixtures") or []
            if {match.get("home"), match.get("away")} == {"Real Madrid", "Atlético Madrid"}
        ),
        key=lambda match: str(match.get("date") or ""),
    )
    madrid_atleti_result = resolve.answer("y madrid atleti", payload)
    resolved["y madrid atleti"] = madrid_atleti_result
    madrid_atleti_line = madrid_atleti_result["answer"].replace("\n", " | ") or "(vacía)"
    print("\n  P: y madrid atleti")
    print(f"     intent   : {madrid_atleti_result['intent']} ({madrid_atleti_result['confidence']})")
    print(f"     equipos  : {', '.join(madrid_atleti_result['teams']) or '-'}")
    print(f"     respuesta: {madrid_atleti_line}")
    checks.check(
        madrid_atleti_result["teams"] == ["Real Madrid", "Atlético Madrid"],
        "resolver «y madrid atleti» detecta los dos equipos",
        str(madrid_atleti_result["teams"]),
    )
    if madrid_atleti:
        # The source calendar day is valid even while kickoff is provisional.
        derby_labels = resolve.fixture_labels(madrid_atleti[0])
        print(
            f"     fixture  : {derby_labels['dia']}, {derby_labels['hora']} "
            "(cruce presente en el payload)"
        )
        checks.check(
            madrid_atleti_result["intent"] == "next_match_h2h",
            "resolver «y madrid atleti» -> next_match_h2h",
            madrid_atleti_result["intent"],
        )
        checks.check(
            "Real Madrid" in madrid_atleti_result["answer"]
            and "Atlético Madrid" in madrid_atleti_result["answer"],
            "resolver «y madrid atleti» nombra los dos equipos",
            madrid_atleti_result["answer"][:150],
        )
        checks.check(
            answer_matches_fixture(madrid_atleti_result["answer"], madrid_atleti[0]),
            "resolver «y madrid atleti» usa la fecha real del payload",
            madrid_atleti_result["answer"],
        )
    else:
        # Esta temporada el payload no programa Real Madrid-Atlético: la única
        # respuesta honesta es unknown_football (nunca una fecha inventada).
        print("     nota     : el payload no trae Real Madrid-Atlético esta temporada")
        checks.check(
            madrid_atleti_result["intent"] == "unknown_football",
            "resolver «y madrid atleti» sin fixture -> unknown_football",
            madrid_atleti_result["intent"],
        )
        checks.check(
            madrid_atleti_result["answer"] == "",
            "resolver «y madrid atleti» sin fixture no inventa fecha ni hora",
            madrid_atleti_result["answer"][:150],
        )

    # «quien gana el clasico» es una pregunta de fútbol con equipos: la fecha que
    # devuelve tiene que ser la del clásico real del payload.
    clasico_question = resolve.answer("quien gana el clasico", payload)
    resolved["quien gana el clasico"] = clasico_question
    print("\n  P: quien gana el clasico")
    print(f"     intent   : {clasico_question['intent']} ({clasico_question['confidence']})")
    print(f"     equipos  : {', '.join(clasico_question['teams']) or '-'}")
    print(f"     respuesta: {clasico_question['answer'].replace(chr(10), ' | ') or '(vacía)'}")
    if clasico is not None:
        checks.check(
            clasico_question["intent"] == "next_match_h2h",
            "resolver «quien gana el clasico» -> next_match_h2h",
            clasico_question["intent"],
        )
        checks.check(
            answer_matches_fixture(clasico_question["answer"], clasico),
            f"resolver «quien gana el clasico» usa la fecha real ({clasico_day}, {clasico_time})",
            clasico_question["answer"],
        )
    else:
        print("  skip «quien gana el clasico»: no quedan Barça-Madrid programados esta temporada")
        checks.check(
            clasico_question["intent"] == "unknown_football",
            "resolver «quien gana el clasico» sin fixture -> unknown_football",
            clasico_question["intent"],
        )

    # Seguimientos: sin contexto responden vacío, con contexto re-resuelven la
    # pregunta anterior. El contexto lo pasa el cliente (resolvedor sin estado).
    print("\n[Seguimientos con previous_question]")
    previous = "cuando juega el barca contra el madrid"
    direct = resolve.answer(previous, payload)
    confirm = resolve.answer("seguro?", payload, previous)
    challenge = resolve.answer("creo q no", payload, previous)
    inventing = resolve.answer("te lo estas inventando", payload, previous)
    nested = resolve.answer("seguro?", payload, "creo q no")

    for label, result in (
        ("seguro? + contexto", confirm),
        ("creo q no + contexto", challenge),
        ("te lo estas inventando + contexto", inventing),
    ):
        print(f"\n  P: {label}")
        print(f"     intent   : {result['intent']} ({result['confidence']})")
        print(f"     equipos  : {', '.join(result['teams']) or '-'}")
        print(f"     respuesta: {result['answer'].replace(chr(10), ' | ') or '(vacía)'}")
        print(f"     de       : {result.get('follow_up_of') or '-'}")

    checks.check(
        confirm["intent"] == "confirm_last" and confirm["answer"] == direct["answer"],
        "resolver «seguro?» con contexto repite la respuesta verificada",
        f"{confirm['answer'][:120]!r} != {direct['answer'][:120]!r}",
    )
    checks.check(
        confirm["follow_up_of"] == previous,
        "resolver «seguro?» registra de qué pregunta viene",
        str(confirm.get("follow_up_of")),
    )
    checks.check(
        challenge["intent"] == "challenge_last" and bool(challenge["answer"]),
        "resolver «creo q no» con contexto devuelve la respuesta verificada",
        challenge["answer"][:120],
    )
    if clasico is not None:
        checks.check(
            answer_matches_fixture(challenge["answer"], clasico),
            "resolver «creo q no» mantiene la fecha real (no inventa otra hora)",
            challenge["answer"],
        )
    checks.check(
        inventing["intent"] == "challenge_last" and bool(inventing["answer"]),
        "resolver «te lo estas inventando» con contexto devuelve answer",
        inventing["answer"][:120],
    )
    checks.check(
        nested["intent"] == "confirm_last"
        and nested["answer"] == ""
        and nested["follow_up_of"] is None,
        "resolver: un previous_question que también es meta no se resuelve (un solo nivel)",
        f"{nested['intent']} / {nested['answer'][:80]!r} / {nested.get('follow_up_of')}",
    )

    # Regresión del bug real: toda fecha de un next_match_* debe ser un fixture.
    fixtures = payload.get("fixtures") or []

    print("\n[Invariante: fechas de próximos partidos]")
    next_match_results = [
        (question, result)
        for question, result in resolved.items()
        if result["intent"].startswith("next_match")
    ]
    for row in payload.get("standings") or []:
        team = row.get("team") or ""
        probe = resolve.answer(f"cuando juega {team}", payload)
        if probe["intent"].startswith("next_match"):
            next_match_results.append((f"cuando juega {team}", probe))

    for question, result in next_match_results:
        checks.check(
            any(answer_matches_fixture(result["answer"], match) for match in fixtures),
            f"fecha real en «{question}»",
            result["answer"],
        )

    checks.check(bool(next_match_results), "el invariante cubrió al menos un próximo partido")

    # Invariante duro: si el intent no es «no tengo el dato», tiene que haber una
    # respuesta verificada. Un intent resuelto con answer vacío es el agujero por
    # el que el modelo terminaba improvisando.
    print("\n[Invariante: ningún intent resuelto queda vacío]")
    no_answer_intents = {"none", "confirm_last", "challenge_last", "unknown_football"}
    empty_answers = [
        f"{question} -> {result['intent']}"
        for question, result in resolved.items()
        if result["intent"] not in no_answer_intents and not result["answer"]
    ]
    checks.check(
        not empty_answers,
        "todo intent resuelto devuelve answer no vacío",
        "; ".join(empty_answers[:3]),
    )
    return resolved


def main() -> int:
    print("Prueba DeepSeek · self-check")
    try:
        payload = laliga.get_payload(force=True)
    except Exception as exc:  # network or parsing failure
        print(f"  FAIL no pude obtener el payload: {exc}")
        return 1

    checks = Checks()

    print("\n[Datos]")
    print(f"  season       : {payload.get('season')}")
    print(f"  season_year  : {payload.get('season_year')}")
    print(f"  updated_at   : {payload.get('updated_at')}")
    print(f"  today_label  : {payload.get('today_label')}")
    print(f"  standings    : {len(payload.get('standings') or [])}")
    print(f"  results      : {len(payload.get('results') or [])}")
    print(f"  fixtures     : {len(payload.get('fixtures') or [])}")
    print(f"  context_text : {len(payload.get('context_text') or '')} chars")

    print("\n[Invariantes]")
    season = payload.get("season") or ""
    checks.check(bool(season.strip()), "season name is not empty", repr(season))
    checks.check(
        isinstance(payload.get("season_year"), int) and not isinstance(payload.get("season_year"), bool),
        "season_year is an int",
        repr(payload.get("season_year")),
    )

    standings = payload.get("standings") or []
    checks.check(len(standings) == 20, "standings has exactly 20 rows", f"got {len(standings)}")
    ranks = [row.get("rank") for row in standings]
    checks.check(ranks == list(range(1, 21)), "ranks are 1..20 in order", repr(ranks))

    for row in standings:
        for field in ("played", "wins", "draws", "losses", "goals_for", "goals_against", "goal_diff", "points"):
            value = row.get(field)
            if type(value) is not int:
                checks.check(False, f"standings[{row.get('rank')}].{field} is int", repr(value))
                break
        else:
            continue
        break
    else:
        checks.check(True, "all standings numeric fields are ints")

    results = payload.get("results") or []
    checks.check(bool(results), "results is not empty", f"got {len(results)}")

    bad_score = [m for m in results if type(m.get("home_score")) is not int or type(m.get("away_score")) is not int]
    checks.check(not bad_score, "every result has int scores", f"{len(bad_score)} bad rows")

    same_team = [m for m in results if m.get("home") == m.get("away")]
    checks.check(not same_team, "every result has home != away", f"{len(same_team)} bad rows")

    result_dates = [parse_date(m.get("date", "")) for m in results]
    checks.check(all(d is not None for d in result_dates), "every result has a parseable date")
    checks.check(
        all(a >= b for a, b in zip(result_dates, result_dates[1:]) if a and b),
        "results are newest first",
    )

    fixtures = payload.get("fixtures") or []
    now = datetime.now(timezone.utc)
    fixture_dates = [parse_date(m.get("date", "")) for m in fixtures]
    checks.check(all(d is not None for d in fixture_dates), "every fixture has a parseable date")
    stale = [
        m.get("date")
        for m, date in zip(fixtures, fixture_dates)
        if date is None or date < now - FIXTURE_GRACE
    ]
    checks.check(not stale, "every fixture date is >= now", f"{len(stale)} stale: {stale[:3]}")
    checks.check(bool(fixtures), "fixtures is not empty", f"got {len(fixtures)}")

    context = payload.get("context_text") or ""
    checks.check(bool(context.strip()), "context_text is not empty")
    checks.check(
        len(context) < MAX_CONTEXT_CHARS,
        f"context_text under {MAX_CONTEXT_CHARS} chars",
        f"got {len(context)}",
    )

    print("\n[Fecha de hoy]")
    today_iso = payload.get("today_iso") or ""
    today_text = payload.get("today_label") or ""
    checks.check(parse_date(today_iso) is not None, "today_iso es ISO UTC", repr(today_iso))
    checks.check(
        re.fullmatch(r"(lunes|martes|miercoles|jueves|viernes|sabado|domingo) .+ de .+ de \d{4}", today_text) is not None,
        "today_label tiene día de la semana y fecha",
        repr(today_text),
    )
    checks.check(context.startswith("HOY: "), "context_text arranca con HOY:", repr(context[:40]))

    run_resolver(checks, payload)

    print("\n[Resumen]")
    if checks.failures:
        print(f"  {len(checks.failures)} fallo(s), {checks.passed} ok")
        return 1
    print(f"  Todo bien: {checks.passed} checks pasaron.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
