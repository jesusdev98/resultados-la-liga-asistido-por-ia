"""Static + JSON server for Prueba DeepSeek (standard library only)."""

from __future__ import annotations

import argparse
import json
import sys
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import laliga
import panel
import resolve
import tools

BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "web"

UPSTREAM_ERROR = "No pude leer los datos de La Liga. Reintentá en un toque."
EMPTY_QUESTION = "La pregunta está vacía."
LONG_QUESTION = "La pregunta es demasiado larga (máximo 500 caracteres)."
LONG_PREVIOUS_QUESTION = "La pregunta anterior es demasiado larga (máximo 500 caracteres)."
BAD_JSON = "No pude leer el JSON de la pregunta."
MAX_QUESTION_CHARS = 500
MAX_BODY_BYTES = 64 * 1024

MISSING_TOOL_NAME = "Falta el nombre de la herramienta."
BAD_TOOL_ARGS = "Los argumentos de la herramienta tienen que ser un objeto JSON."


def _clamp_limit(raw: str | None, default: int, maximum: int = 100) -> int:
    try:
        value = int(raw) if raw is not None else default
    except (TypeError, ValueError):
        return default
    return max(1, min(value, maximum))


class PruebaHandler(SimpleHTTPRequestHandler):
    server_version = "PruebaDeepSeek/1.0"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_DIR), **kwargs)

    def end_headers(self):
        # Small local app: never reuse stale UI modules after a code update.
        if not self.path.startswith("/api/"):
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    # --- responses -----------------------------------------------------
    def send_json(self, payload: dict, status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def send_api_error(self, status: int, message: str) -> None:
        self.send_json({"error": message}, status=status)

    # --- /api/ask -------------------------------------------------------
    def read_json_body(self) -> dict | None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            return None
        if length <= 0 or length > MAX_BODY_BYTES:
            return None
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None

    def send_ask(self, raw_question: object, raw_previous: object = None, raw_fixture_context: object = None) -> None:
        """Resolve a question deterministically; may raise like the other routes."""
        question = raw_question.strip() if isinstance(raw_question, str) else ""
        previous = raw_previous.strip() if isinstance(raw_previous, str) else ""
        if not question:
            self.send_api_error(HTTPStatus.BAD_REQUEST, EMPTY_QUESTION)
            return
        if len(question) > MAX_QUESTION_CHARS:
            self.send_api_error(HTTPStatus.BAD_REQUEST, LONG_QUESTION)
            return
        if len(previous) > MAX_QUESTION_CHARS:
            self.send_api_error(HTTPStatus.BAD_REQUEST, LONG_PREVIOUS_QUESTION)
            return

        payload = laliga.get_payload()
        fixture_context = raw_fixture_context if isinstance(raw_fixture_context, dict) else None
        resolved = resolve.answer(question, payload, previous or None, fixture_context)
        # The no-model path consumes the same MCP-UI resource as tool execution.
        subject = resolved
        if resolved["intent"] in ("confirm_last", "challenge_last"):
            subject = resolve.answer(previous, payload) if previous else resolved
            if fixture_context and resolved["answer"]:
                subject = {**resolved, "intent": "next_match_h2h" if len(resolved["teams"]) == 2 else "next_match_team"}
        names = subject.get("teams") or []
        intent = subject["intent"]
        tool_name = {"table": "tabla", "bottom": "descenso", "team_standing": "posicion",
                     "last_match_team": "ultimo_resultado",
                     "last_match_h2h": "ultimo_resultado"}.get(intent)
        if intent.startswith("next_match"):
            tool_name = "proximo_partido"
        args = resolved.get("tool_args") or dict(zip(("equipo", "rival"), names))
        if tool_name == "proximo_partido":
            args = {"cantidad": 1, **args}
        # A confirmation must render the exact fixture on screen, not its predecessor.
        visual_payload = payload
        if resolved["intent"] in ("confirm_last", "challenge_last") and fixture_context and resolved["answer"]:
            args = {key: value for key, value in args.items() if key not in ("despues_de", "despues_del_dia")}
            args["fecha_exacta"] = fixture_context.get("despues_de")
            visual_payload = {**payload, "fixtures": [match for match in payload.get("fixtures", [])
                                                   if match.get("date") == fixture_context.get("despues_de")]}
        visual = tools.run_tool(tool_name, args, visual_payload) if tool_name else {}
        displayed = visual.get("datos", {}).get("partidos") or []
        shown = displayed[-1] if displayed else None
        next_context = {**{key: value for key, value in args.items() if key in ("equipo", "rival")},
                        "despues_de": shown["fecha_iso"]} if shown else None
        # A team-only query still displays a fixture with two participants.
        # Include both source-backed names so neither club crest is omitted.
        wanted_teams = set(resolved.get("teams") or [])
        for match in displayed:
            wanted_teams.update(name for name in (match.get("local"), match.get("visitante")) if name)
        score_data = visual.get("datos", {})
        if score_data.get("goles_local") is not None:
            wanted_teams.update(name for name in (score_data.get("local"), score_data.get("visitante")) if name)
        team_logos = {
            row["team"]: safe_logo
            for row in payload.get("standings", [])
            if row.get("team") in wanted_teams
            and (safe_logo := panel.safe_logo(row.get("logo")))
        }
        self.send_json(
            {
                "intent": resolved["intent"],
                "confidence": resolved["confidence"],
                "answer": resolved["answer"],
                "facts": resolved["facts"],
                "teams": resolved["teams"],
                "team_logos": team_logos,
                "today_label": payload.get("today_label") or resolve.today_label(),
                "context_text": payload.get("context_text") or "",
                "content": visual.get("content", []),
                "fixture_context": next_context,
                "tool_args": args if tool_name == "proximo_partido" else resolved.get("tool_args"),
            }
        )

    # --- /api/tool ------------------------------------------------------
    def send_tool(self, raw_name: object, raw_args: object = None) -> None:
        """Run one tool chosen by the model; `ok: false` is a 200, not a 4xx.

        The error text is the model's correction signal, so it travels in the body
        exactly like a successful result. Only a malformed request is a 400.
        """
        name = raw_name.strip() if isinstance(raw_name, str) else ""
        if not name:
            self.send_api_error(HTTPStatus.BAD_REQUEST, MISSING_TOOL_NAME)
            return

        if raw_args is None:
            args: dict = {}
        elif isinstance(raw_args, dict):
            args = raw_args
        else:
            self.send_api_error(HTTPStatus.BAD_REQUEST, BAD_TOOL_ARGS)
            return

        payload = laliga.get_payload()
        self.send_json(tools.run_tool(name, args, payload))

    # --- routing -------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if not path.startswith("/api/"):
            if path == "/":
                self.path = "/index.html"
            super().do_GET()
            return

        try:
            if path == "/api/health":
                payload = laliga.get_payload()
                self.send_json(
                    {
                        "ok": True,
                        "season": payload["season"],
                        "updated_at": payload["updated_at"],
                    }
                )
                return

            if path == "/api/ask":
                self.send_ask(
                    (query.get("q") or [""])[0],
                    (query.get("previous_question") or [""])[0],
                )
                return

            if path == "/api/tool":
                # Same contract as the POST, with the args flattened into the query
                # string so the tools are usable from curl.
                args = {
                    key: values[0]
                    for key, values in query.items()
                    if key != "name" and values
                }
                self.send_tool((query.get("name") or [""])[0], args)
                return

            if path == "/api/laliga/context":
                self.send_json(laliga.get_payload())
                return

            if path == "/api/laliga/standings":
                payload = laliga.get_payload()
                self.send_json(
                    {
                        "season": payload["season"],
                        "season_year": payload["season_year"],
                        "updated_at": payload["updated_at"],
                        "standings": payload["standings"],
                    }
                )
                return

            if path == "/api/laliga/results":
                limit = _clamp_limit((query.get("limit") or [None])[0], default=12)
                payload = laliga.get_payload()
                self.send_json(
                    {
                        "season": payload["season"],
                        "updated_at": payload["updated_at"],
                        "results": payload["results"][:limit],
                    }
                )
                return

            if path == "/api/laliga/fixtures":
                limit = _clamp_limit((query.get("limit") or [None])[0], default=6)
                payload = laliga.get_payload()
                self.send_json(
                    {
                        "season": payload["season"],
                        "updated_at": payload["updated_at"],
                        "fixtures": payload["fixtures"][:limit],
                    }
                )
                return

            self.send_api_error(HTTPStatus.NOT_FOUND, "Ruta de API desconocida.")
        except laliga.DataError as exc:
            print(f"upstream error: {exc}", file=sys.stderr)
            self.send_api_error(HTTPStatus.BAD_GATEWAY, UPSTREAM_ERROR)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:  # keep tracebacks out of client responses
            print(f"unexpected server error: {exc!r}", file=sys.stderr)
            self.send_api_error(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "Algo falló en el servidor. Mirá la consola.",
            )

    def do_POST(self) -> None:  # noqa: N802 (http.server API)
        parsed = urlparse(self.path)

        if parsed.path not in ("/api/ask", "/api/tool"):
            self.send_api_error(HTTPStatus.NOT_FOUND, "Ruta de API desconocida.")
            return

        try:
            body = self.read_json_body()
            if body is None:
                self.send_api_error(HTTPStatus.BAD_REQUEST, BAD_JSON)
                return
            if parsed.path == "/api/tool":
                self.send_tool(body.get("name"), body.get("args"))
            else:
                self.send_ask(body.get("question"), body.get("previous_question"), body.get("fixture_context"))
        except laliga.DataError as exc:
            print(f"upstream error: {exc}", file=sys.stderr)
            self.send_api_error(HTTPStatus.BAD_GATEWAY, UPSTREAM_ERROR)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:  # keep tracebacks out of client responses
            print(f"unexpected server error: {exc!r}", file=sys.stderr)
            self.send_api_error(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "Algo falló en el servidor. Mirá la consola.",
            )

    # --- logging -------------------------------------------------------
    def log_message(self, format: str, *args) -> None:  # noqa: A002
        sys.stderr.write(f"{self.address_string()} - {format % args}\n")

    def log_request(self, code="-", size="-") -> None:
        # One short line per request; never log response bodies.
        sys.stderr.write(f'{self.address_string()} "{self.requestline}" {code}\n')


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prueba DeepSeek local server")
    parser.add_argument("--host", default="0.0.0.0", help="bind address (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8000, help="port (default: 8000)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not (WEB_DIR / "index.html").is_file():
        print(f"error: missing {WEB_DIR / 'index.html'}", file=sys.stderr)
        return 1

    httpd = ThreadingHTTPServer((args.host, args.port), PruebaHandler)
    httpd.daemon_threads = True

    print(f"Prueba DeepSeek  ·  http://localhost:{args.port}")
    print("La Liga 2026-27  ·  datos en vivo de ESPN")
    print("Abrí esa URL en Chrome o Edge (necesita WebGPU).")
    sys.stdout.flush()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nCerrando…")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
