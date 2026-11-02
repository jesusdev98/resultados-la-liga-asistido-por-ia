"""Deterministic Spanish question resolver for the La Liga payload.

The model never computes dates, never looks anything up and never resolves team
names: this module maps a question to concrete rows of the payload and formats
the result in Spain local time. Standard library only, no I/O.

Public helpers used by ``check.py``: ``normalize``, ``day_label``,
``datetime_label``, ``local_datetime``, ``today_label`` and ``answer``.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from laliga import fixture_schedule

MADRID = ZoneInfo("Europe/Madrid")

WEEKDAYS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
MONTHS = (
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
)

# Curated alias table. Keys are canonical team names as ESPN spells them; an
# alias is only usable when its canonical team is present in the payload.
TEAM_ALIASES: dict[str, tuple[str, ...]] = {
    "Barcelona": ("barcelona", "barca", "barsa", "barza", "blaugrana", "blaugranas", "cules"),
    "Real Madrid": ("real madrid", "madrid", "merengues", "merengue"),
    "Atlético Madrid": ("atletico de madrid", "atletico madrid", "atletico", "atleti", "colchoneros", "colchonero"),
    "Real Betis": ("real betis", "betis", "verdiblancos", "verdiblanco"),
    "Real Sociedad": ("real sociedad", "sociedad", "la real", "txuri urdin"),
    "Athletic Club": ("athletic club", "athletic", "athletic bilbao", "leones"),
    "Celta Vigo": ("celta vigo", "celta", "celestes"),
    "Rayo Vallecano": ("rayo vallecano", "rayo"),
    "Racing Santander": ("racing santander", "racing"),
    "Deportivo": ("deportivo", "depor", "deportivo la coruna"),
    "Valencia": ("valencia",),
    "Villarreal": ("villarreal", "submarino amarillo"),
    "Sevilla": ("sevilla", "nervionenses"),
    "Osasuna": ("osasuna",),
    "Getafe": ("getafe", "azulones"),
    "Elche": ("elche", "franjiverdes"),
    "Levante": ("levante", "granotes"),
    "Espanyol": ("espanyol", "espanyol barcelona", "periquitos"),
    "Alavés": ("alaves", "alaveses"),
    "Málaga": ("malaga", "boquerones"),
}

# A single alias that means "both teams of the clásico".
CLASICO_ALIASES = ("clasico", "clasicos")

# Trigger phrases, already normalized (lowercase, no accents).
FUTURE_TRIGGERS = (
    "cuando",
    "proximo",
    "proxima",
    "proximos",
    "proximo partido",
    "siguiente",
    "siguiente partido",
    "juega",
    "juega el",
    "juegan",
    "se enfrenta",
    "se enfrentan",
    "enfrenta",
    "contra",
    "vs",
    "clasico",
)
PAST_TRIGGERS = (
    "como salio",
    "salio",
    "quedo",
    "ha quedado",
    "han quedado",
    "ayer",
    "ultimo",
    "ultima",
    "ultimos",
    "resultado",
    "resultados",
    "que hizo",
    "ganaron",
    "gano",
    "perdieron",
    "perdio",
    "empate",
)
STANDING_TRIGGERS = (
    "puntos",
    "puesto",
    "posicion",
    "como va",
    "tabla",
    "clasificacion",
)
TABLE_TRIGGERS = ("tabla", "clasificacion", "posiciones", "como va")
BOTTOM_TRIGGERS = ("descenso", "desciende", "descienden", "ultimos", "colistas", "colero")
RESULTS_TRIGGERS = ("resultados", "jornada")

# Follow-up phrases. They are about the PREVIOUS answer, never about new data, so
# they only match when no team was found and the question is short (see
# ``_meta_intent``). Already normalized.
META_CONFIRM = (
    "seguro",
    "estas seguro",
    "estas segura",
    "de verdad",
    "en serio",
    "es cierto",
    "confirmas",
    "fijo",
)
META_CHALLENGE = (
    "creo q no",
    "creo que no",
    "no es cierto",
    "no creo",
    "estas inventando",
    "te lo estas inventando",
    "lo estas inventando",
    "no me lo creo",
    "mentira",
    "eso es falso",
)

# Football vocabulary: tells "no tengo ese dato" (unknown_football) apart from a
# genuinely unrelated question (none). A detected team counts as football too.
FOOTBALL_TERMS = (
    "balon de oro",
    "partido",
    "partidos",
    "juega",
    "jornada",
    "gol",
    "goles",
    "liga",
    "laliga",
    "clasico",
    "tabla",
    "puntos",
    "fichaje",
    "fichajes",
    "entrenador",
    "arbitro",
    "var",
    "descenso",
    "temporada",
    "goleador",
    "goleadores",
    "alineacion",
    "estadio",
    "lesion",
    "lesiones",
    "tarjeta",
    "penalti",
)

# Words that add no topic of their own. They may sit around a team name
# ("y el atleti") or around a meta phrase ("¿y seguro?") without changing it.
_FILLER = frozenset(
    {
        "y",
        "e",
        "o",
        "u",
        "el",
        "la",
        "lo",
        "los",
        "las",
        "un",
        "una",
        "de",
        "del",
        "al",
        "a",
        "en",
        "con",
        "por",
        "para",
        "que",
        "q",
        "se",
        "me",
        "te",
        "le",
        "les",
        "nos",
        "mi",
        "tu",
        "su",
        "sus",
        "es",
        "son",
        "esta",
        "estas",
        "estan",
        "eres",
        "soy",
        "eso",
        "esto",
        "sera",
        "pues",
        "entonces",
        "che",
        "dale",
        "oye",
        "vale",
        "ok",
        "okey",
        "bueno",
        "ah",
        "eh",
        "si",
        "tambien",
        "ahora",
        "hoy",
        "muy",
        "mas",
    }
)

# A meta phrase is at most a few words: anything longer is a real question that
# happens to contain "de verdad", not a follow-up.
MAX_META_TOKENS = 6


# ------------------------------------------------------------------ text utils
def normalize(text: Any) -> str:
    """Lowercase, strip accents/punctuation and collapse whitespace."""
    raw = unicodedata.normalize("NFKD", str(text))
    without_accents = "".join(char for char in raw if not unicodedata.combining(char))
    cleaned = re.sub(r"[^a-z0-9]+", " ", without_accents.lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def _word_pattern(term: str) -> re.Pattern[str]:
    return re.compile(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])")


def _has_any(text: str, phrases: tuple[str, ...]) -> bool:
    return any(_word_pattern(normalize(phrase)).search(text) for phrase in phrases)


def _blank(text: str, start: int, end: int) -> str:
    """Replace a matched span with spaces so shorter aliases cannot re-match it."""
    return text[:start] + (" " * (end - start)) + text[end:]


# --------------------------------------------------------------------- dates
def _parse(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def local_datetime(value: Any) -> datetime | None:
    """Parse an ISO timestamp and convert it to Europe/Madrid."""
    parsed = _parse(value)
    return None if parsed is None else parsed.astimezone(MADRID)


def day_label(value: Any) -> str:
    """`domingo 25 de octubre de 2026` in Spain local time."""
    local = local_datetime(value)
    if local is None:
        return str(value or "")
    return f"{WEEKDAYS[local.weekday()]} {local.day} de {MONTHS[local.month - 1]} de {local.year}"


def time_label(value: Any) -> str:
    """`21:00` in Spain local time."""
    local = local_datetime(value)
    if local is None:
        return ""
    return local.strftime("%H:%M")


def datetime_label(value: Any) -> str:
    """`domingo 25 de octubre de 2026, 21:00` in Spain local time."""
    local = local_datetime(value)
    if local is None:
        return str(value or "")
    return f"{day_label(local)}, {local.strftime('%H:%M')}"


def fixture_labels(match: dict) -> dict:
    """Expose the shared date/confirmation contract without inventing a kickoff."""
    stamp, confirmed = fixture_schedule(match)
    return {
        "dia": day_label(stamp),
        "hora": time_label(stamp) if confirmed else "Por definir",
        "estado_fecha": "Fecha confirmada" if confirmed else "Fecha provisional",
        "horario_confirmado": confirmed,
    }


def today_label(now: datetime | None = None) -> str:
    stamp = now or datetime.now(timezone.utc)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    local = stamp.astimezone(MADRID)
    return f"{WEEKDAYS[local.weekday()]} {local.day} de {MONTHS[local.month - 1]} de {local.year}"


# --------------------------------------------------------------------- teams
def _standings_index(payload: dict) -> dict[str, dict]:
    index: dict[str, dict] = {}
    for row in payload.get("standings") or []:
        name = row.get("team")
        if name:
            index[normalize(name)] = row
    return index


def _alias_pairs(index: dict[str, dict]) -> list[tuple[str, str]]:
    """Alias -> canonical name, only for teams present in the payload."""
    pairs: list[tuple[str, str]] = []
    for canonical, aliases in TEAM_ALIASES.items():
        row = index.get(normalize(canonical))
        if row is None:
            continue  # team is not in this league/season: drop all its aliases
        for alias in aliases:
            pairs.append((normalize(alias), row["team"]))
    pairs.sort(key=lambda item: (-len(item[0]), item[0]))
    return pairs


def find_teams(question: str, payload: dict) -> list[str]:
    """Longest-first alias matching; every matched span is blanked afterwards."""
    text = normalize(question)
    index = _standings_index(payload)
    found: list[tuple[int, str]] = []
    seen: set[str] = set()

    for alias, canonical in _alias_pairs(index):
        match = _word_pattern(alias).search(text)
        if match is None:
            continue
        start = match.start()
        text = _blank(text, match.start(), match.end())
        if canonical not in seen:
            seen.add(canonical)
            found.append((start, canonical))

    for alias in CLASICO_ALIASES:
        match = _word_pattern(alias).search(text)
        if match is None:
            continue
        start = match.start()
        text = _blank(text, match.start(), match.end())
        for key in ("barcelona", "real madrid"):
            row = index.get(key)
            if row is None or row["team"] in seen:
                continue
            seen.add(row["team"])
            found.append((start, row["team"]))

    found.sort(key=lambda item: item[0])
    return [team for _, team in found]


def _core_phrase(text: str) -> str:
    """Drop filler words from both ends so `¿y seguro?` and `seguro` compare equal."""
    tokens = normalize(text).split()
    start, end = 0, len(tokens)
    while start < end and tokens[start] in _FILLER:
        start += 1
    while end > start and tokens[end - 1] in _FILLER:
        end -= 1
    return " ".join(tokens[start:end])


def _meta_intent(text: str, teams: list[str]) -> str | None:
    """`confirm` / `challenge` for a short follow-up with no team in it, else None.

    Deliberately conservative: a long question that merely contains "de verdad"
    or "es cierto" keeps its own resolution instead of becoming a follow-up.
    """
    if teams:
        return None
    tokens = text.split()
    if not tokens or len(tokens) > MAX_META_TOKENS:
        return None
    core = _core_phrase(text)
    if not core:
        return None
    for kind, phrases in (("confirm", META_CONFIRM), ("challenge", META_CHALLENGE)):
        if any(core == _core_phrase(phrase) for phrase in phrases):
            return kind
    return None


def _bare_entity_question(question: str, payload: dict, teams: list[str]) -> bool:
    """True when the question is only team name(s) plus filler ("y el atleti").

    That is what a user types right after a verified card, so it means "and that
    team?". Any other content word ("como esta el vestuario del barca") is a real
    question we cannot answer, and must NOT silently become a fixture.
    """
    text = normalize(question)
    index = _standings_index(payload)
    for alias, canonical in _alias_pairs(index):
        if canonical not in teams:
            continue
        while (match := _word_pattern(alias).search(text)) is not None:
            text = _blank(text, match.start(), match.end())
    if {"Barcelona", "Real Madrid"}.issubset(set(teams)):
        for alias in CLASICO_ALIASES:
            while (match := _word_pattern(alias).search(text)) is not None:
                text = _blank(text, match.start(), match.end())
    return all(word in _FILLER for word in text.split())


def _is_football(text: str, teams: list[str]) -> bool:
    """Any team mention, or football vocabulary, means the question is about football."""
    return bool(teams) or _has_any(text, FOOTBALL_TERMS)


# ------------------------------------------------------------ payload lookups
def _sorted_fixtures(payload: dict) -> list[dict]:
    return sorted(
        payload.get("fixtures") or [],
        key=lambda match: str(match.get("date") or ""),
    )


def _sorted_results(payload: dict) -> list[dict]:
    return sorted(
        payload.get("results") or [],
        key=lambda match: str(match.get("date") or ""),
        reverse=True,
    )


def _involves(match: dict, team: str) -> bool:
    return team in (match.get("home"), match.get("away"))


def _diff_text(value: Any) -> str:
    diff = value if isinstance(value, int) else 0
    return f"+{diff}" if diff > 0 else str(diff)


# ------------------------------------------------------------------- results
def _result(intent: str, confidence: str, answer: str, facts: list[str], teams: list[str]) -> dict:
    return {
        "intent": intent,
        "confidence": confidence,
        "answer": answer,
        "facts": facts,
        "teams": teams,
    }


def next_fixtures(payload: dict, teams: list[str], after: str | None = None,
                  after_day: str | None = None) -> list[dict]:
    """Select the same team/pair after a source-backed event, never a guessed date."""
    fixtures = [match for match in _sorted_fixtures(payload)
                if (set(teams) == {match.get("home"), match.get("away")} if len(teams) == 2
                    else bool(teams) and _involves(match, teams[0]))]
    if after:
        boundary = _parse(after)
        anchors = fixtures + [match for match in payload.get("results", [])
                              if (set(teams) == {match.get("home"), match.get("away")} if len(teams) == 2
                                  else bool(teams) and _involves(match, teams[0]))]
        if boundary is None or not any(_parse(match.get("date")) == boundary for match in anchors):
            return []
        fixtures = [match for match in fixtures if _parse(match.get("date")) and _parse(match["date"]) > boundary]
    if after_day is not None:
        try:
            boundary_day = datetime.strptime(after_day, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return []
        fixtures = [match for match in fixtures if local_datetime(match.get("date"))
                    and local_datetime(match["date"]).date() > boundary_day]
    return fixtures


def _next_match(payload: dict, teams: list[str], after: str | None = None) -> dict | None:
    fixtures = next_fixtures(payload, teams, after)
    if len(teams) == 2:
        wanted = set(teams)
        match = next(
            (item for item in fixtures if {item.get("home"), item.get("away")} == wanted),
            None,
        )
        intent = "next_match_h2h"
    else:
        match = next((item for item in fixtures if _involves(item, teams[0])), None)
        intent = "next_match_team"

    if match is None:
        return None

    home = match.get("home") or "?"
    away = match.get("away") or "?"
    labels = fixture_labels(match)
    day = labels["dia"] or "Fecha por definir"
    timing = (f"{labels['hora']} (hora de España)" if labels["horario_confirmado"]
              else f"{labels['hora']} · {labels['estado_fecha']}")
    return _result(
        intent,
        "high",
        f"{home} vs {away} · {day}, {timing}",
        [f"{home} vs {away}", day, timing],
        teams,
    )


def _last_match(payload: dict, teams: list[str]) -> dict | None:
    results = _sorted_results(payload)
    if len(teams) == 2:
        wanted = set(teams)
        match = next(
            (item for item in results if {item.get("home"), item.get("away")} == wanted),
            None,
        )
        intent = "last_match_h2h"
    else:
        match = next((item for item in results if _involves(item, teams[0])), None)
        intent = "last_match_team"

    if match is None:
        return None

    home = match.get("home") or "?"
    away = match.get("away") or "?"
    home_score = match.get("home_score")
    away_score = match.get("away_score")
    if not isinstance(home_score, int) or not isinstance(away_score, int):
        return None

    score = f"{home} {home_score}-{away_score} {away}"
    return _result(
        intent,
        "high",
        f"{score} · {day_label(match.get('date'))}",
        [score, day_label(match.get("date"))],
        teams,
    )


def _standing(row: dict, teams: list[str]) -> dict:
    rank = row.get("rank") or 0
    points = row.get("points") or 0
    played = row.get("played") or 0
    wins = row.get("wins") or 0
    draws = row.get("draws") or 0
    losses = row.get("losses") or 0
    diff = _diff_text(row.get("goal_diff"))

    answer = (
        f"{row.get('team')} está {rank}.º con {points} puntos en {played} partidos "
        f"({wins} victorias, {draws} empates, {losses} derrotas, {diff})."
    )
    facts = [
        f"Puesto {rank}.º",
        f"{points} puntos en {played} partidos",
        f"{wins} victorias, {draws} empates, {losses} derrotas",
        f"Diferencia de goles: {diff}",
    ]
    return _result("team_standing", "high", answer, facts, teams)


def _table(payload: dict) -> dict:
    rows = sorted(payload.get("standings") or [], key=lambda row: row.get("rank") or 0)
    lines = [f"Tabla de La Liga ({len(rows)} equipos):"]
    for row in rows:
        lines.append(
            f"{row.get('rank')}. {row.get('team')} · {row.get('points')} pts · "
            f"{row.get('played')} PJ · DG {_diff_text(row.get('goal_diff'))}"
        )
    facts = [
        f"{row.get('rank')}. {row.get('team')} · {row.get('points')} pts"
        for row in (rows[:3] + rows[-1:])
    ]
    return _result("table", "high", "\n".join(lines), facts, [])


def _bottom(payload: dict) -> dict:
    rows = sorted(payload.get("standings") or [], key=lambda row: row.get("rank") or 0)
    relegation = [
        row for row in rows if re.search(r"relegation|descenso", str(row.get("zone") or ""), re.IGNORECASE)
    ]
    if len(relegation) != 3:
        relegation = rows[-3:]

    lines = [f"Descenso ({len(relegation)} equipos):"]
    facts: list[str] = []
    for row in relegation:
        line = f"{row.get('rank')}. {row.get('team')} · {row.get('points')} pts"
        lines.append(line)
        facts.append(line)
    return _result("bottom", "high", "\n".join(lines), facts, [])


def _last_results(payload: dict) -> dict:
    results = _sorted_results(payload)[:6]
    lines = ["Últimos resultados:"]
    for match in results:
        lines.append(
            f"{day_label(match.get('date'))} · {match.get('home')} "
            f"{match.get('home_score')}-{match.get('away_score')} {match.get('away')}"
        )
    facts = [
        f"{match.get('home')} {match.get('home_score')}-{match.get('away_score')} "
        f"{match.get('away')} · {day_label(match.get('date'))}"
        for match in results[:5]
    ]
    return _result("last_results", "high", "\n".join(lines), facts, [])


# -------------------------------------------------------------------- public
def _resolve_question(question: str, payload: dict, text: str, teams: list[str]) -> dict:
    """Resolve one question against the payload; never follows context."""
    index = _standings_index(payload)

    def nothing() -> dict:
        return {"intent": "none", "confidence": "low", "answer": "", "facts": [], "teams": teams}

    def unknown() -> dict:
        # Football-shaped but not resolvable with this payload: saying so is what
        # keeps the client from handing the question to the model.
        if _is_football(text, teams):
            return {
                "intent": "unknown_football",
                "confidence": "low",
                "answer": "",
                "facts": [],
                "teams": teams,
            }
        return nothing()

    if not text or len(teams) > 2:
        return nothing()

    if len(teams) == 1 and _has_any(text, STANDING_TRIGGERS):
        row = index.get(normalize(teams[0]))
        return _standing(row, teams) if row else unknown()

    if teams:
        if _has_any(text, PAST_TRIGGERS):
            return _last_match(payload, teams) or unknown()
        if _has_any(text, FUTURE_TRIGGERS):
            return _next_match(payload, teams) or unknown()
        # Bare entity ("y el atleti", "y madrid atleti"): the user is pointing at
        # teams, so answer with their next fixture. Only when no trigger matched
        # and nothing else is being asked.
        if _bare_entity_question(question, payload, teams):
            return _next_match(payload, teams) or unknown()
        return unknown()

    if _has_any(text, TABLE_TRIGGERS):
        return _table(payload) if payload.get("standings") else unknown()
    if _has_any(text, RESULTS_TRIGGERS):
        return _last_results(payload) if payload.get("results") else unknown()
    if _has_any(text, BOTTOM_TRIGGERS):
        return _bottom(payload) if payload.get("standings") else unknown()
    return unknown()


def _follow_up(kind: str, previous_question: str | None, payload: dict) -> dict:
    """Re-resolve the previous question and report it as a confirmation/challenge.

    Exactly one level: ``_resolve_question`` never follows context, and a
    previous_question that is itself a meta phrase is not usable.
    """
    intent = "confirm_last" if kind == "confirm" else "challenge_last"
    previous = previous_question.strip() if isinstance(previous_question, str) else ""
    if previous:
        previous_text = normalize(previous)
        previous_teams = find_teams(previous, payload)
        if _meta_intent(previous_text, previous_teams) is None:
            resolved = _resolve_question(previous, payload, previous_text, previous_teams)
            if resolved["answer"]:
                return {**resolved, "intent": intent, "follow_up_of": previous}
    return {
        "intent": intent,
        "confidence": "high",
        "answer": "",
        "facts": [],
        "teams": [],
        "follow_up_of": None,
    }


def _schedule_query(text: str, teams: list[str], payload: dict, context: dict) -> dict | None:
    """Normalize subject, chronology and count independently for fixture requests."""
    count_match = re.search(r"\b(un|uno|dos|tres|[1-3])\s+(?:proximos?|siguientes?)(?:\s+partidos?)?\b", text)
    count = {"un": 1, "uno": 1, "dos": 2, "tres": 3}.get(count_match[1], 0) if count_match else 1
    if count_match and not count:
        count = int(count_match[1])
    advance = bool(re.search(r"\b(?:despues|posterior|tras)\b|\bsiguiente\s+partido\s+al\b", text)
                   or count_match and "siguiente" in count_match[0])
    # "The next one, then the following three" requests four in total.
    if count_match and re.search(r"\bsiguiente\s+a\s+ese\b.*\bdespues\b", text):
        count += 1
    if not count_match and not advance:
        return None
    # Temporal words alone do not turn questions about results/stadiums into fixtures.
    if not re.search(r"\b(?:partidos?|juega|jugar|juegue|siguientes?|proximos?|posterior)\b", text):
        return None
    focal = context.get("equipo")
    target = re.search(r"(?:partido\s+(?:del|de)|juegue|jugar)\s+(?:el\s+)?(.+?)(?:\?|$)", text)
    explicit = find_teams(target[1], payload) if target else []
    if len(teams) == 1:
        selected = teams
    elif explicit:
        selected = explicit[:1]
    elif focal and advance:
        selected = [focal]
        if not teams and context.get("rival"):
            selected.append(context["rival"])
    else:
        selected = teams or ([focal] if focal else [])
    if not selected:
        return _result("unknown_football", "low", "", [], [])
    args = {**dict(zip(("equipo", "rival"), selected)), "cantidad": count}
    date_match = re.search(r"\b(\d{1,2})\s+de\s+(" + "|".join(MONTHS) + r")(?:\s+de\s+(\d{4}))?\b", text)
    if advance and date_match:
        reference = _parse(context.get("despues_de")) or _parse(payload.get("today"))
        year = int(date_match[3]) if date_match[3] else (reference.year if reference else int(payload.get("season_year") or datetime.now(MADRID).year))
        try:
            args["despues_del_dia"] = datetime(year, MONTHS.index(date_match[2]) + 1, int(date_match[1])).date().isoformat()
        except ValueError:
            return _result("unknown_football", "low", "", [], selected)
        anchor_time = local_datetime(context.get("despues_de"))
        if anchor_time and anchor_time.date().isoformat() == args["despues_del_dia"]:
            del args["despues_del_dia"]
            args["despues_de"] = context["despues_de"]
    elif advance:
        anchor = context.get("despues_de")
        if not anchor and len(teams) == 2:
            events = next_fixtures(payload, teams)
            anchor = events[0].get("date") if events else None
        if not anchor:
            return _result("unknown_football", "low", "", [], selected)
        args["despues_de"] = anchor
    fixtures = next_fixtures(payload, selected, args.get("despues_de"), args.get("despues_del_dia"))
    resolved = _next_match({**payload, "fixtures": fixtures}, selected)
    if not resolved:
        resolved = _result("unknown_football", "low", "", [], selected)
    return {**resolved, "tool_args": args}


def answer(question: str, payload: dict, previous_question: str | None = None,
           fixture_context: dict | None = None) -> dict:
    """Resolve a Spanish question against the shaped payload (never invents data).

    ``previous_question`` is the question that produced the answer on screen. It
    is only used for follow-ups ("seguro?", "creo q no"): the resolver stays
    stateless, the caller passes the context in.
    """
    text = normalize(question)
    teams = find_teams(question, payload)

    meta = _meta_intent(text, teams)
    following = not teams and bool(re.search(r"\b(?:el siguiente|despues de (?:ese|este)|y despues|el posterior)\b", text))
    context = fixture_context if isinstance(fixture_context, dict) else {}
    if (not teams and not following and previous_question
            and re.search(r"\b(?:proximos?|siguientes?|vuelve a jugar)\b", text)
            and not re.search(r"\b(?:despues|posterior|tras)\b", text)):
        prior_teams = find_teams(previous_question, payload)
        if len(prior_teams) == 1:
            prior = _resolve_question(previous_question, payload, normalize(previous_question), prior_teams)
            if prior.get("answer"):
                teams = prior_teams
    schedule = _schedule_query(text, teams, payload, context)
    if schedule is not None and meta is None:
        return schedule
    if context and not teams and re.search(r"\b(?:a que hora|que dia|que fecha)\b", text):
        meta = "confirm"
    if (following or meta is not None) and context:
        context_teams = find_teams(" ".join(str(context.get(key) or "") for key in ("equipo", "rival")), payload)
        anchor = context.get("despues_de")
        if context_teams and isinstance(anchor, str) and _parse(anchor):
            if following:
                resolved = _next_match(payload, context_teams, anchor)
                if resolved:
                    resolved["tool_args"] = {**dict(zip(("equipo", "rival"), context_teams)), "despues_de": anchor}
                    return resolved
            else:
                matches = [match for match in next_fixtures(payload, context_teams)
                           if _parse(match.get("date")) == _parse(anchor)]
                if matches:
                    resolved = _next_match({**payload, "fixtures": matches}, context_teams)
                    return {**resolved, "intent": "confirm_last" if meta == "confirm" else "challenge_last",
                            "follow_up_of": previous_question}
        if meta is not None:
            return {**_result("confirm_last" if meta == "confirm" else "challenge_last", "low", "", [], []),
                    "follow_up_of": previous_question}
    if following:
        return _result("unknown_football", "low", "", [], [])
    if meta is not None:
        return _follow_up(meta, previous_question, payload)

    resolved = _resolve_question(question, payload, text, teams)
    if context and resolved["intent"] == "none" and re.search(r"\b(?:ese|esa|este|ahi|estadio|horario)\b", text):
        return _result("unknown_football", "low", "", [], [])
    return resolved
