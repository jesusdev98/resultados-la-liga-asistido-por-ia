"""La Liga data layer: fetch, cache and shape ESPN's public endpoints.

Only the Python standard library is used. The shaped payload is cached for a
few minutes and shared by every HTTP endpoint so concurrent requests never
stampede the upstream API.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Iterable
from zoneinfo import ZoneInfo

MADRID = ZoneInfo("Europe/Madrid")
SPANISH_WEEKDAYS = ("lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo")
SPANISH_MONTHS = (
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

STANDINGS_URL = "https://site.web.api.espn.com/apis/v2/sports/soccer/esp.1/standings"
SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/esp.1/scoreboard"
SEASON_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/esp.1/scoreboard?dates={year}&limit=1000"

USER_AGENT = "Prueba-DeepSeek/1.0 (+local demo; python-urllib)"
REQUEST_TIMEOUT = 25
CACHE_TTL = 300  # seconds

# Recent results / upcoming fixtures included in the LLM context block.
CONTEXT_RESULTS = 12
CONTEXT_FIXTURES = 6

_cache: dict[str, Any] = {"payload": None, "built_at": 0.0}
_cache_lock = threading.Lock()


class DataError(Exception):
    """Raised when ESPN data cannot be fetched or parsed."""


def fetch_json(url: str) -> Any:
    """GET a JSON document with a polite User-Agent and a hard timeout."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            raw = response.read()
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
        raise DataError(f"request failed for {url}: {exc}") from exc

    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DataError(f"invalid JSON from {url}: {exc}") from exc


def _to_int(value: Any) -> int | None:
    """Parse a numeric value defensively; ESPN sometimes sends strings or null."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return int(text)
        except ValueError:
            try:
                return int(float(text))
            except ValueError:
                return None
    return None


def _stat_map(entry: dict[str, Any]) -> dict[str, Any]:
    stats = entry.get("stats") or []
    return {
        stat["name"]: stat.get("displayValue")
        for stat in stats
        if isinstance(stat, dict) and "name" in stat
    }


def _short_iso(value: str) -> str:
    return value.replace("+00:00", "Z")


def _parse_date(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _build_standings(raw: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        entries: Iterable[dict[str, Any]] = raw["children"][0]["standings"]["entries"]
    except (KeyError, IndexError, TypeError) as exc:
        raise DataError(f"unexpected standings shape: {exc}") from exc

    rows: list[dict[str, Any]] = []
    for entry in entries:
        team = entry.get("team") or {}
        stats = _stat_map(entry)
        logos = team.get("logos") or []
        logo = ""
        if logos and isinstance(logos[0], dict):
            logo = logos[0].get("href") or ""
        rank = _to_int(stats.get("rank"))
        rows.append(
            {
                "rank": rank if rank is not None else len(rows) + 1,
                "team": team.get("displayName") or "?",
                "short": team.get("abbreviation") or "",
                "logo": logo,
                "played": _to_int(stats.get("gamesPlayed")) or 0,
                "wins": _to_int(stats.get("wins")) or 0,
                "draws": _to_int(stats.get("ties")) or 0,
                "losses": _to_int(stats.get("losses")) or 0,
                "goals_for": _to_int(stats.get("pointsFor")) or 0,
                "goals_against": _to_int(stats.get("pointsAgainst")) or 0,
                "goal_diff": _to_int(stats.get("pointDifferential")) or 0,
                "points": _to_int(stats.get("points")) or 0,
                "zone": ((entry.get("note") or {}).get("description") or ""),
            }
        )

    rows.sort(key=lambda row: row["rank"])
    return rows


def _competitors(event: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        return event["competitions"][0]["competitors"]
    except (KeyError, IndexError, TypeError) as exc:
        raise DataError(f"unexpected event shape: {exc}") from exc


def _side(competitors: list[dict[str, Any]], home_away: str) -> dict[str, Any] | None:
    for competitor in competitors:
        if competitor.get("homeAway") == home_away:
            return competitor
    return None


def _venue_name(event: dict[str, Any]) -> str:
    """`Estadio de Vallecas` when ESPN ships the venue; empty string otherwise.

    The stadium is optional in the ESPN payload: callers must treat "" as "the
    source did not say", never as a value to fill in.
    """
    try:
        venue = event["competitions"][0].get("venue") or event.get("venue") or {}
    except (KeyError, IndexError, TypeError, AttributeError):
        return ""
    if not isinstance(venue, dict):
        return ""
    name = venue.get("fullName")
    return name if isinstance(name, str) else ""


def _team_logo(competitor: dict[str, Any]) -> str:
    team = competitor.get("team") or {}
    logos = team.get("logos") or []
    return team.get("logo") or (logos[0].get("href", "") if logos and isinstance(logos[0], dict) else "")


def _build_matches(raw: dict[str, Any], start_date: str, end_date: str | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    events = raw.get("events")
    if not isinstance(events, list):
        raise DataError("scoreboard payload has no events list")

    now = datetime.now(timezone.utc)
    start = _parse_date(start_date)
    end = _parse_date(end_date) if end_date else None
    results: list[dict[str, Any]] = []
    fixtures: list[dict[str, Any]] = []

    for event in events:
        date = event.get("date") or ""
        played_at = _parse_date(date)
        if played_at is None or (start is not None and played_at < start):
            continue  # exclude the previous season's tail
        if end is not None and (played_at.date() > end.date() if len(end_date) == 10 else played_at > end):
            continue

        status_type = (event.get("status") or {}).get("type") or {}
        completed = bool(status_type.get("completed"))
        label = status_type.get("description") or ("Finalizado" if completed else "Programado")
        competitors = _competitors(event)
        home = _side(competitors, "home") or {}
        away = _side(competitors, "away") or {}
        home_name = (home.get("team") or {}).get("displayName") or "?"
        away_name = (away.get("team") or {}).get("displayName") or "?"

        if completed:
            home_score = _to_int(home.get("score"))
            away_score = _to_int(away.get("score"))
            if home_score is None or away_score is None:
                continue  # never surface a finished match with missing scores
            results.append(
                {
                    "date": date,
                    "home": home_name,
                    "away": away_name,
                    "home_score": home_score,
                    "away_score": away_score,
                    "label": label,
                    "venue": _venue_name(event),
                    "home_logo": _team_logo(home),
                    "away_logo": _team_logo(away),
                }
            )
        else:
            if played_at < now:
                continue  # abandoned or stale events are not upcoming fixtures
            fixtures.append(
                {
                    "date": date,
                    "home": home_name,
                    "away": away_name,
                    "label": label,
                    "venue": _venue_name(event),
                    "time_valid": event["competitions"][0].get("timeValid") is True,
                    "home_logo": _team_logo(home),
                    "away_logo": _team_logo(away),
                }
            )

    results.sort(key=lambda item: item["date"], reverse=True)
    fixtures.sort(key=lambda item: item["date"])
    return results, fixtures


def _to_madrid(value: str) -> datetime | None:
    """Parse an ESPN ISO timestamp and convert it to Spain local time."""
    parsed = _parse_date(value)
    if parsed is None:
        return None
    return parsed.astimezone(MADRID)


def fixture_schedule(match: dict[str, Any]) -> tuple[str, bool]:
    """Return a safe date stamp and strict kickoff confirmation for all callers.

    An unconfirmed timestamp supplies only its source calendar day. Noon keeps
    existing Madrid date formatters from moving that day across a timezone edge.
    """
    value = match.get("date") or ""
    if not isinstance(value, str) or _parse_date(value) is None:
        return "", False
    confirmed = match.get("time_valid") is True
    return (value if confirmed else f"{value[:10]}T12:00:00Z"), confirmed


def _format_match_date(date: str) -> str:
    """`2026-10-25 domingo 21:00` in Spain local time."""
    local = _to_madrid(date)
    if local is None:
        return date
    return (
        f"{local.strftime('%Y-%m-%d')} {SPANISH_WEEKDAYS[local.weekday()]} "
        f"{local.strftime('%H:%M')}"
    )


def _format_day(date: str) -> str:
    """`2026-10-18 domingo` in Spain local time."""
    local = _to_madrid(date)
    if local is None:
        return date
    return f"{local.strftime('%Y-%m-%d')} {SPANISH_WEEKDAYS[local.weekday()]}"


def today_label(now: datetime | None = None) -> str:
    """`domingo 25 de octubre de 2026` for the current Spain local date."""
    stamp = now or datetime.now(timezone.utc)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    local = stamp.astimezone(MADRID)
    return (
        f"{SPANISH_WEEKDAYS[local.weekday()]} {local.day} de "
        f"{SPANISH_MONTHS[local.month - 1]} de {local.year}"
    )


def build_context_text(
    season: str,
    updated_at: datetime,
    standings: list[dict[str, Any]],
    results: list[dict[str, Any]],
    fixtures: list[dict[str, Any]],
    today: str,
) -> str:
    """Compact Spanish block injected into the model prompt (kept small)."""
    lines: list[str] = [
        f"HOY: {today} (hora de España)",
        f"TEMPORADA: {season} (actualizado {updated_at.strftime('%Y-%m-%d %H:%M UTC')})",
        "",
        "TABLA (pos, equipo, PJ, G, E, P, GF, GC, DG, PTS):",
    ]

    for row in standings:
        diff = row["goal_diff"]
        diff_text = f"+{diff}" if diff > 0 else str(diff)
        lines.append(
            f"{row['rank']}. {row['team']} | {row['played']} | {row['wins']} | "
            f"{row['draws']} | {row['losses']} | {row['goals_for']} | "
            f"{row['goals_against']} | {diff_text} | {row['points']}"
        )

    lines.append("")
    lines.append(f"ÚLTIMOS {min(len(results), CONTEXT_RESULTS)} RESULTADOS:")
    if results:
        for match in results[:CONTEXT_RESULTS]:
            lines.append(
                f"{_format_day(match['date'])} {match['home']} "
                f"{match['home_score']}-{match['away_score']} {match['away']}"
            )
    else:
        lines.append("Sin resultados todavía.")

    lines.append("")
    lines.append(f"PRÓXIMOS {min(len(fixtures), CONTEXT_FIXTURES)} PARTIDOS:")
    if fixtures:
        for match in fixtures[:CONTEXT_FIXTURES]:
            stamp, confirmed = fixture_schedule(match)
            schedule = (_format_match_date(stamp) if confirmed else
                        f"{_format_day(stamp)} · Por definir · Fecha provisional")
            lines.append(f"{schedule} {match['home']} vs {match['away']}")
    else:
        lines.append("Sin partidos programados.")

    return "\n".join(lines)


def _fetch_payload() -> dict[str, Any]:
    meta = fetch_json(SCOREBOARD_URL)
    try:
        league = meta["leagues"][0]
        season = league["season"]
        start_date = season["startDate"]
        end_date = season["endDate"]
        season_name = season.get("displayName") or "La Liga"
        season_year = _to_int(season.get("year"))
    except (KeyError, IndexError, TypeError) as exc:
        raise DataError(f"unexpected season metadata: {exc}") from exc

    if season_year is None:
        raise DataError("season metadata has no year")

    start = _parse_date(start_date)
    end = _parse_date(end_date)
    if start is None or end is None or end.date() < start.date() or end.year - start.year > 1:
        raise DataError("season metadata has invalid date bounds")

    standings_raw = fetch_json(STANDINGS_URL)
    events: list[dict[str, Any]] = []
    seen: set[str] = set()
    for year in range(start.year, end.year + 1):
        scoreboard_raw = fetch_json(SEASON_URL.format(year=year))
        year_events = scoreboard_raw.get("events")
        if not isinstance(year_events, list):
            raise DataError(f"scoreboard payload for {year} has no events list")
        for event in year_events:
            identifier = event.get("id")
            if identifier and identifier in seen:
                continue
            if identifier:
                seen.add(identifier)
            events.append(event)

    standings = _build_standings(standings_raw)
    results, fixtures = _build_matches({"events": events}, start_date, end_date)

    updated_at = datetime.now(timezone.utc).replace(microsecond=0)
    today = today_label(updated_at)
    context_text = build_context_text(season_name, updated_at, standings, results, fixtures, today)

    if not standings:
        raise DataError("empty standings")
    if not results:
        raise DataError("empty results")

    return {
        "season": season_name,
        "season_year": season_year,
        "updated_at": _short_iso(updated_at.isoformat()),
        "today_iso": _short_iso(updated_at.isoformat()),
        "today_label": today,
        "standings": standings,
        "results": results,
        "fixtures": fixtures,
        "context_text": context_text,
    }


def get_payload(force: bool = False) -> dict[str, Any]:
    """Return the shaped payload, refetching when the TTL has expired.

    The lock is held during the fetch so a burst of concurrent requests performs
    a single upstream round trip (no stampede), then everyone reads the cache.
    """
    global _cache
    with _cache_lock:
        payload = _cache["payload"]
        age = datetime.now(timezone.utc).timestamp() - _cache["built_at"]
        if not force and payload is not None and age < CACHE_TTL:
            return payload

        fresh = _fetch_payload()
        _cache = {"payload": fresh, "built_at": datetime.now(timezone.utc).timestamp()}
        return fresh


if __name__ == "__main__":
    data = get_payload()
    print(json.dumps({key: data[key] for key in ("season", "season_year", "updated_at")}, ensure_ascii=False))
    print(f"standings={len(data['standings'])} results={len(data['results'])} fixtures={len(data['fixtures'])}")
