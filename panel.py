"""Shared legacy MCP-UI resources for stdio and the web adapter."""

import base64
import hashlib
from html import escape
from urllib.parse import urlsplit

import resolve

PANEL_URI = "ui://prueba-deepseek/panel"
PANEL_MIME = "text/html"

# The only permitted script reports layout height. It cannot access the parent,
# fetch data, navigate, or handle commands. CSP admits these exact bytes only.
RESIZE_SCRIPT = """(() => {
  const wrap = document.querySelector('.wrap');
  const report = () => parent.postMessage({
    type: 'laliga-panel:resize', height: Math.ceil(wrap.getBoundingClientRect().height)
  }, '*');
  new ResizeObserver(report).observe(wrap);
  addEventListener('load', report);
  report();
})();"""


def safe_logo(value):
    """Only source-owned HTTPS images, with no userinfo or nonstandard port."""
    if not isinstance(value, str):
        return ""
    try:
        url = urlsplit(value)
        if (url.scheme == "https" and url.hostname == "a.espncdn.com"
                and not url.username and not url.password and url.port in (None, 443)):
            return value
    except ValueError:
        pass
    return ""


def _team(name, logos):
    image = safe_logo(logos.get(name))
    crest = f'<img src="{escape(image, quote=True)}" alt="" width="36" height="36" referrerpolicy="no-referrer">' if image else ""
    return f'<span class="team">{crest}<span>{escape(str(name or "?"))}</span></span>'


CSS = """
:root{color-scheme:dark}*{box-sizing:border-box}body{margin:0;padding:18px;background:#101827;color:#edf3fc;font:14px/1.5 system-ui,sans-serif}
.wrap{max-width:760px;margin:auto}h1{font-size:18px;margin:0 0 4px}h2{font-size:13px;color:#9aaccc}p{margin:6px 0}.meta,footer{font-size:12px;color:#9aaccc}
ul{list-style:none;padding:0;margin:12px 0}li{background:#162237;border:1px solid #2b3d58;border-radius:14px;padding:16px;margin:10px 0}
.pair{display:flex;align-items:center;justify-content:space-between;gap:12px}.team{display:inline-flex;align-items:center;gap:9px}.team img{object-fit:contain;flex:none}.versus{color:#8ea4c7;font-size:12px}
.date{font-size:16px;font-weight:650;margin-top:14px}.badge{display:inline-block;background:#493b1d;color:#ffe1a3;border-radius:20px;padding:3px 10px;font-size:12px}.confirmed{background:#123e34;color:#a3f4d9}
table{width:100%;border-collapse:collapse;font-size:13px}td,th{padding:7px 3px;border-bottom:1px solid #26364c;text-align:right}td.team,th:nth-child(2){text-align:left}td.pos{color:#9aaccc;text-align:left}td.team{display:table-cell}td .team img{width:24px;height:24px}footer{margin-top:14px} @media(max-width:420px){body{padding:10px}.pair{gap:7px}.team{gap:5px;font-size:12px}.team img{width:28px;height:28px}}
html{overflow:hidden}body.compact{padding:0;background:transparent}.compact .wrap{max-width:none}.compact header,.compact footer,.compact h2{display:none}.compact ul{margin:0}.compact li{margin:0;padding:18px;background:#1a2233;border:1px solid #253046;border-radius:14px}.compact .pair>.team{flex:1;min-width:0}.compact .pair>.team:last-child{justify-content:flex-end}.compact table{background:#1a2233;border-radius:14px}.compact .badge{margin-top:4px}
"""


def panel_result(payload, datos=None):
    """Return the canonical MCP CallToolResult wire shape, not model-generated HTML.

    HTTP transports this same content array; MCP validates it with its SDK. The
    browser uses one CSP-pinned layout reporter, without origin privileges.
    """
    compact = datos is not None
    logos = {row.get("team"): row.get("logo") for row in payload.get("standings", [])}
    if datos is None:
        datos = {
            "partidos": [dict(local=m.get("home"), visitante=m.get("away"), **resolve.fixture_labels(m))
                         for m in payload.get("fixtures", [])[:6]],
            "filas": [{"pos": row.get("rank"), "equipo": row.get("team"), "puntos": row.get("points"),
                       "jugados": row.get("played"), "ganados": row.get("wins"), "empatados": row.get("draws"),
                       "perdidos": row.get("losses"), "dg": row.get("goal_diff")}
                      for row in payload.get("standings", [])],
        }
    matches = datos.get("partidos", [])
    # Tool output is already bounded to the requested one, two or three fixtures.
    rows = datos.get("filas", [])
    if "pos" in datos:
        rows = [datos]
    title = f"La Liga {payload.get('season_year', '')}"
    if payload.get("season_year"):
        title += f"-{(int(payload['season_year']) + 1) % 100:02d}"
    fragments, fallback = [], [title]
    if matches:
        cards = []
        for match in matches:
            confirmed = match.get("horario_confirmado") is True
            state = "Fecha confirmada" if confirmed else "Fecha provisional · horario por definir"
            hour = str(match.get("hora") or "Por definir") if confirmed else "Por definir"
            day = str(match.get("dia") or "Fecha por definir")
            home, away = match.get("local"), match.get("visitante")
            cards.append(
                f'<li><div class="pair">{_team(home, logos)}<span class="versus">VS</span>{_team(away, logos)}</div>'
                f'<p class="date">{escape(day)}</p><p>{escape(hour)}'
                f'{" · hora de España" if confirmed else ""}</p>'
                f'<span class="badge{" confirmed" if confirmed else ""}">{state}</span></li>'
            )
            fallback.append(f"{home} vs {away} · {day}, {hour} · {state}")
        fragments.append(f'<h2>Próximos partidos</h2><ul>{"".join(cards)}</ul>')
    if rows:
        body = []
        detailed = "jugados" in rows[0]
        for row in rows:
            name, pos, points = row.get("equipo"), row.get("pos"), row.get("puntos")
            stats = "".join(f'<td>{escape(str(row.get(key, "")))}</td>' for key in ("jugados", "ganados", "empatados", "perdidos", "dg")) if detailed else ""
            body.append(f'<tr><td class="pos">{escape(str(pos))}</td><td class="team">{_team(name, logos)}</td>{stats}<td>{escape(str(points))}</td></tr>')
            fallback.append(f"{pos}. {name} · {points} pts")
        headings = "<th>PJ</th><th>G</th><th>E</th><th>P</th><th>DG</th>" if detailed else ""
        fragments.append('<h2>Clasificación</h2><table><thead><tr><th>#</th><th>Equipo</th>' + headings + '<th>PTS</th></tr></thead><tbody>' + "".join(body) + '</tbody></table>')
    if "goles_local" in datos:
        home, away = datos.get("local"), datos.get("visitante")
        score = f"{datos.get('goles_local')}-{datos.get('goles_visitante')}"
        day = str(datos.get("dia") or "")
        fragments.append(f'<ul><li><div class="pair">{_team(home, logos)}<strong>{escape(score)}</strong>{_team(away, logos)}</div><p>{escape(day)}</p><span class="badge confirmed">Finalizado</span></li></ul>')
        fallback.append(f"{home} {score} {away} · {day}")
    updated = resolve.datetime_label(payload.get("updated_at"))
    digest = base64.b64encode(hashlib.sha256(RESIZE_SCRIPT.encode()).digest()).decode()
    csp = f"default-src 'none'; script-src 'sha256-{digest}'; style-src 'unsafe-inline'; img-src https://a.espncdn.com; base-uri 'none'; form-action 'none'"
    document = (
        '<!DOCTYPE html><html lang="es"><head><meta charset="utf-8">'
        f'<meta http-equiv="Content-Security-Policy" content="{escape(csp, quote=False)}">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{escape(title)}</title><style>{CSS}</style></head><body class="{"compact" if compact else "panel"}"><div class="wrap">'
        f'<header><h1>{escape(title)} · datos de ESPN</h1><p class="meta">Actualizado {escape(updated)} · hora de España</p></header>'
        + "".join(fragments)
        + '<footer>MCP-UI · servidor local. Escudos de ESPN; nombres disponibles aunque no carguen las imágenes.</footer></div>'
        + f'<script>{RESIZE_SCRIPT}</script></body></html>'
    )
    return {"content": [
        {"type": "resource", "resource": {"uri": PANEL_URI, "mimeType": PANEL_MIME, "text": document}},
        {"type": "text", "text": "\n".join(fallback)},
    ], "isError": False}
