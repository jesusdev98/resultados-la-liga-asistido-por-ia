"""Offline checks for the same MCP-UI resource over stdio and HTTP."""

import json
import base64
import hashlib
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from urllib.request import Request, urlopen

import panel
import resolve
import server
import tools


def payload():
    return {
        "season": "La Liga 2026-27", "season_year": 2026,
        "updated_at": "2026-10-25T12:00Z", "today_label": "domingo 25 de octubre de 2026",
        "standings": [
            {"team": name, "rank": rank, "points": 20, "played": 9,
             "wins": 6, "draws": 2, "losses": 1, "goal_diff": 10,
             "logo": f"https://a.espncdn.com/i/teamlogos/soccer/500/{rank}.png"}
            for rank, name in enumerate(["Real Madrid", "Atlético Madrid"], 1)
        ],
        "fixtures": [{"date": "2027-04-04T18:00Z", "home": "Real Madrid",
                      "away": "Atlético Madrid", "time_valid": False}],
        "results": [],
    }


class PanelTests(unittest.TestCase):
    def test_panel_without_season_year_and_malformed_logo_ports(self):
        data = payload()
        data.pop("season_year", None)
        html = panel.panel_result(data)["content"][0]["resource"]["text"]
        self.assertIn("La Liga", html)
        self.assertEqual(panel.safe_logo("https://a.espncdn.com:bad/logo.png"), "")
        self.assertEqual(panel.safe_logo("https://[invalid/logo.png"), "")

    def test_implicit_upcoming_after_verified_team_result_keeps_the_subject(self):
        data = payload()
        data["standings"].append({"team": "Barcelona", "rank": 3, "points": 18})
        data["results"] = [{"date": "2026-10-25T20:00Z", "home": "Barcelona", "away": "Real Madrid",
                            "home_score": 1, "away_score": 2}]
        answer = resolve.answer("vale y el proximo partido cuando es?", data, "como quedo el madrid")
        self.assertEqual(answer["intent"], "next_match_team")
        self.assertEqual(answer["teams"], ["Real Madrid"])
        # An unresolved or ambiguous prior query is not permission to guess a club.
        for previous in ["balon de oro", "como quedo madrid barsa"]:
            unresolved = resolve.answer("vale y el proximo partido cuando es?", data, previous)
            self.assertNotEqual(unresolved["intent"], "next_match_team")

    def test_atletico_de_madrid_is_one_team_not_an_accidental_derby(self):
        data = payload()
        question = "si los 3 proximos partidos que jugara el atletico de madrid"
        self.assertEqual(resolve.find_teams(question, data), ["Atlético Madrid"])
        answer = resolve.answer(question, data)
        self.assertEqual(answer["teams"], ["Atlético Madrid"])
        self.assertEqual(answer["tool_args"]["cantidad"], 3)
        self.assertNotIn("rival", answer["tool_args"])
        self.assertEqual(resolve.find_teams("atletico de madrid contra real madrid", data),
                         ["Atlético Madrid", "Real Madrid"])

    def test_reported_schedule_queries_preserve_subject_boundary_and_count(self):
        data = payload()
        opponents = ["Racing Santander", "Valencia", "Sevilla", "Barcelona"]
        for name in opponents:
            data["standings"].append({"team": name, "rank": 8, "points": 10})
        data["fixtures"] = [
            {"date": f"2026-11-{day:02d}T16:15Z", "home": "Real Madrid", "away": opponent, "time_valid": True}
            for day, opponent in zip([1, 8, 15, 22], opponents)
        ]
        context = {"equipo": "Real Madrid", "despues_de": "2026-11-01T16:15Z"}
        phrases = [
            "y el siguiente partido al madrid racing cual es que juegue el real madrid?",
            "despues de ese evento del dia 1 de noviembre cual es el proximo partido posterior a ese?",
            "cual es el partido que tiene que jugar el real madrid despues del proximo 1 de noviembre de 2026 y que fecha posterior a esa seria?",
            "vale tengo claro el partido del dia 1 de noviembre del madrid pero el partido que viene despues que ese del madrid cual es?",
        ]
        for phrase in phrases:
            with self.subTest(phrase=phrase):
                resolved = resolve.answer(phrase, data, "cuando juega el madrid", context)
                self.assertEqual(resolved["intent"], "next_match_team")
                args = resolved["tool_args"]
                self.assertEqual(args["equipo"], "Real Madrid")
                self.assertNotIn("rival", args)
                result = tools.run_tool("proximo_partido", args, data)
                self.assertTrue(result["ok"], result)
                self.assertEqual(result["datos"]["partidos"][0]["visitante"], "Valencia")
        resolved = resolve.answer("vale dime los tres proximos partidos del madrid", data, fixture_context=context)
        result = tools.run_tool("proximo_partido", resolved["tool_args"], data)
        self.assertEqual([m["visitante"] for m in result["datos"]["partidos"]], opponents[:3])
        explicit = resolve.answer(phrases[2], data)
        self.assertEqual(tools.run_tool("proximo_partido", explicit["tool_args"], data)["datos"]["partidos"][0]["visitante"], "Valencia")
        two = tools.run_tool("proximo_partido", {"equipo": "Real Madrid", "cantidad": 2}, data)
        self.assertEqual(len(two["datos"]["partidos"]), 2)
        for invalid in [0, 5, True, "3"]:
            self.assertFalse(tools.run_tool("proximo_partido", {"equipo": "Real Madrid", "cantidad": invalid}, data)["ok"])
        self.assertFalse(tools.run_tool("proximo_partido", {"equipo": "Real Madrid", "despues_del_dia": "no-date"}, data)["ok"])
        for phrase in ["vale y los 3 siguientes?", "y los tres siguientes partidos?", "los 3 proximos partidos despues de ese"]:
            with self.subTest(phrase=phrase):
                resolved = resolve.answer(phrase, data, fixture_context=context)
                self.assertEqual(resolved["tool_args"]["cantidad"], 3)
                result = tools.run_tool("proximo_partido", resolved["tool_args"], data)
                self.assertEqual([m["visitante"] for m in result["datos"]["partidos"]], opponents[1:])
        data["fixtures"].append({"date": "2026-11-29T16:15Z", "home": "Real Madrid", "away": "Getafe", "time_valid": True})
        compound = resolve.answer("vale y el siguiente a ese? y despues de eso los 3 siguientes?", data, fixture_context=context)
        self.assertEqual(compound["tool_args"]["cantidad"], 4)
        result = tools.run_tool("proximo_partido", compound["tool_args"], data)
        self.assertEqual([m["visitante"] for m in result["datos"]["partidos"]], opponents[1:] + ["Getafe"])

    def test_next_match_phrasing_and_next_team_after_named_fixture(self):
        data = payload()
        data["standings"].extend([
            {"team": "Barcelona", "rank": 3, "points": 18},
            {"team": "Sevilla", "rank": 4, "points": 16},
        ])
        data["fixtures"] = [
            {"date": "2026-10-25T20:00Z", "home": "Real Madrid", "away": "Barcelona", "time_valid": True},
            {"date": "2026-11-02T18:00Z", "home": "Sevilla", "away": "Real Madrid", "time_valid": False},
            {"date": "2026-11-09T18:00Z", "home": "Barcelona", "away": "Sevilla", "time_valid": False},
        ]

        upcoming = resolve.answer("¿Cuál es el siguiente partido del Madrid?", data)
        self.assertEqual(upcoming["intent"], "next_match_team")
        self.assertIn("25 de octubre de 2026", upcoming["answer"])

        after_clasico = resolve.answer(
            "Y después del Madrid-Barça, ¿cuál es el siguiente partido del Madrid?", data,
        )
        self.assertEqual(after_clasico["intent"], "next_match_team")
        self.assertEqual(after_clasico["teams"], ["Real Madrid"])
        self.assertIn("2 de noviembre de 2026", after_clasico["answer"])
        self.assertEqual(after_clasico["tool_args"]["despues_de"], "2026-10-25T20:00Z")

    def test_yesterday_score_question_resolves_head_to_head_result(self):
        data = payload()
        data["standings"].append({"team": "Barcelona", "rank": 3, "points": 18})
        data["results"] = [{
            "date": "2026-10-25T20:00Z", "home": "Barcelona", "away": "Real Madrid",
            "home_score": 1, "away_score": 2,
        }]
        answer = resolve.answer("como quedo el madrid barsa de ayer", data)
        self.assertEqual(answer["intent"], "last_match_h2h")
        self.assertIn("Barcelona 1-2 Real Madrid", answer["answer"])
        answer = resolve.answer("y cuanto ha quedado el madrid barsa?", data)
        self.assertEqual(answer["intent"], "last_match_h2h")

    def test_awards_are_outside_verified_league_data(self):
        answer = resolve.answer("dime como quedo el balon de oro", payload())
        self.assertEqual(answer["intent"], "unknown_football")
        self.assertFalse(answer["answer"])

    def test_chained_next_fixture_queries_never_enter_free_chat(self):
        data = payload()
        data["fixtures"].insert(0, {"date": "2026-10-25T20:00Z", "home": "Real Madrid", "away": "Atlético Madrid", "time_valid": True})
        http = ThreadingHTTPServer(("127.0.0.1", 0), server.PruebaHandler)
        thread = threading.Thread(target=http.serve_forever, daemon=True)
        thread.start()
        def ask(question, context=None, previous=None):
            request = Request(f"http://127.0.0.1:{http.server_port}/api/ask", data=json.dumps({
                "question": question, "fixture_context": context, "previous_question": previous,
            }).encode(), headers={"Content-Type": "application/json"})
            with urlopen(request, timeout=5) as response:
                return json.load(response)
        try:
            with patch("laliga.get_payload", return_value=data):
                first = ask("cuando es el proximo madrid atleti")
                self.assertIn("25 de octubre", first["answer"])
                second = ask("vale y el siguiente a ese?", first["fixture_context"], "cuando es el proximo madrid atleti")
                self.assertEqual(second["intent"], "next_match_h2h")
                self.assertIn("4 de abril de 2027", second["answer"])
                self.assertIn("Por definir", second["answer"])
                self.assertNotIn("21:00", second["answer"])
                self.assertEqual(second["tool_args"]["despues_de"], "2026-10-25T20:00Z")
                verified = ask("seguro?", second["fixture_context"], "vale y el siguiente a ese?")
                self.assertEqual(verified["answer"], second["answer"])
                executed = tools.run_tool("proximo_partido", verified["tool_args"], data)
                self.assertTrue(executed["ok"], executed)
                self.assertEqual(executed["datos"]["partidos"][0]["fecha_iso"], second["fixture_context"]["despues_de"])
                self.assertEqual(ask("y a que hora?", second["fixture_context"], "vale y el siguiente a ese?")["answer"], second["answer"])
                self.assertEqual(ask("y ese estadio?", second["fixture_context"])["intent"], "unknown_football")
                self.assertEqual(ask("hola", second["fixture_context"])["intent"], "none")
                exhausted = ask("y el siguiente?", second["fixture_context"], "vale y el siguiente a ese?")
                self.assertEqual(exhausted["intent"], "unknown_football")
                self.assertEqual(exhausted["content"], [])
                self.assertEqual(ask("y el siguiente?")["intent"], "unknown_football")
                unchanged = ask("cuando es el proximo madrid atleti", second["fixture_context"])
                self.assertEqual(unchanged["answer"], first["answer"])

                data["fixtures"] = [
                    {"date": "2026-11-01T16:15Z", "home": "Racing Santander", "away": "Real Madrid", "time_valid": True},
                    {"date": "2026-11-08T16:15Z", "home": "Real Madrid", "away": "Valencia", "time_valid": True},
                ]
                after_racing = ask(
                    "vale y despues del del 1 de noviembre cual es el proximo partido del madrid?",
                    {"equipo": "Real Madrid", "rival": "Racing Santander", "despues_de": "2026-11-01T16:15Z"},
                    "cuando vuelve a jugar el madrid",
                )
                self.assertEqual(after_racing["intent"], "next_match_team")
                self.assertEqual(after_racing["tool_args"]["equipo"], "Real Madrid")
                self.assertEqual(after_racing["tool_args"]["despues_de"], "2026-11-01T16:15Z")
                self.assertIn("8 de noviembre", after_racing["answer"])
                return_to_play = ask(
                    "y después de ese partido cuándo vuelve a jugar el madrid?",
                    {"equipo": "Real Madrid", "rival": "Racing Santander", "despues_de": "2026-11-01T16:15Z"},
                    "no el siguiente partido del madrid cuando es?",
                )
                self.assertEqual(return_to_play["intent"], "next_match_team")
                self.assertEqual(return_to_play["tool_args"]["equipo"], "Real Madrid")
                self.assertEqual(return_to_play["tool_args"]["despues_de"], "2026-11-01T16:15Z")
                clarify_target = ask(
                    "vale y después del día 1 de noviembre lo dicho cuál es el próximo partido del madrid y contra quién juega? no el del racing",
                    {"equipo": "Real Madrid", "rival": "Racing Santander", "despues_de": "2026-11-01T16:15Z"},
                    "y después de ese partido cuándo vuelve a jugar el madrid?",
                )
                self.assertEqual(clarify_target["intent"], "next_match_team")
                self.assertEqual(clarify_target["tool_args"]["equipo"], "Real Madrid")
                self.assertEqual(clarify_target["tool_args"]["despues_de"], "2026-11-01T16:15Z")
        finally:
            http.shutdown()
            http.server_close()
            thread.join(timeout=5)

    def test_next_tool_respects_verified_anchor_and_invalid_anchor_fails(self):
        data = payload()
        args = {"equipo": "madrid", "rival": "atleti", "despues_de": "2027-04-04T18:00Z"}
        self.assertFalse(tools.run_tool("proximo_partido", args, data)["ok"])
        args["despues_de"] = "no-es-fecha"
        self.assertFalse(tools.run_tool("proximo_partido", args, data)["ok"])

    def test_last_result_tool_can_query_exact_head_to_head(self):
        data = payload()
        data["standings"].append({"team": "Barcelona", "rank": 3, "points": 18})
        data["results"] = [
            {"date": "2026-10-26T18:00Z", "home": "Real Madrid", "away": "Sevilla", "home_score": 3, "away_score": 0},
            {"date": "2026-10-25T20:00Z", "home": "Barcelona", "away": "Real Madrid", "home_score": 1, "away_score": 2},
        ]
        result = tools.run_tool("ultimo_resultado", {"equipo": "Real Madrid", "rival": "Barcelona"}, data)
        self.assertTrue(result["ok"])
        self.assertEqual((result["datos"]["local"], result["datos"]["visitante"]), ("Barcelona", "Real Madrid"))
        self.assertEqual((result["datos"]["goles_local"], result["datos"]["goles_visitante"]), (1, 2))
    def test_tool_keeps_provisional_date_and_never_publishes_placeholder_hour(self):
        result = tools.run_tool("proximo_partido", {"equipo": "madrid", "rival": "atleti"}, payload())
        self.assertTrue(result["ok"])
        match = result["datos"]["partidos"][0]
        self.assertEqual(match["dia"], "domingo 4 de abril de 2027")
        self.assertEqual(match["hora"], "Por definir")
        self.assertFalse(match["horario_confirmado"])
        resource, fallback = result["content"]
        self.assertEqual(resource["resource"]["mimeType"], "text/html")
        self.assertEqual(resource["resource"]["uri"], panel.PANEL_URI)
        for text in [resource["resource"]["text"], fallback["text"]]:
            self.assertIn("Fecha provisional", text)
            self.assertIn("4 de abril de 2027", text)
            self.assertNotIn("20:00", text)
            self.assertNotIn("18:00", text)

    def test_safe_html_and_logo_policy(self):
        data = payload()
        data["standings"][0]["team"] = '<script>alert("x")</script>'
        data["standings"][0]["logo"] = "https://evil.example/track.png"
        result = panel.panel_result(data)
        html = result["content"][0]["resource"]["text"]
        self.assertEqual(html.count("<script>"), 1)
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn("evil.example", html)
        self.assertIn("Content-Security-Policy", html)
        self.assertIn("default-src 'none'", html)
        self.assertIn("https://a.espncdn.com", html)
        digest = base64.b64encode(hashlib.sha256(panel.RESIZE_SCRIPT.encode()).digest()).decode()
        self.assertIn(f"'sha256-{digest}'", html)
        self.assertNotIn("allow-same-origin", html)
        self.assertLess(len(html.encode()), 15 * 1024)
        self.assertEqual(panel.safe_logo("javascript:alert(1)"), "")
        self.assertEqual(panel.safe_logo("https://a.espncdn.com.evil.example/x"), "")
        self.assertEqual(panel.safe_logo("https://user@a.espncdn.com/x"), "")

    def test_chat_resource_displays_all_tool_selected_matches(self):
        result = tools.run_tool("proximo_partido", {"equipo": "madrid", "rival": "atleti"}, payload())
        datos = result["datos"]
        datos["partidos"].append({**datos["partidos"][0], "dia": "domingo 9 de mayo de 2027"})
        html = panel.panel_result(payload(), datos)["content"][0]["resource"]["text"]
        self.assertIn('class="compact"', html)
        self.assertIn("4 de abril de 2027", html)
        self.assertIn("9 de mayo de 2027", html)

    def test_actual_http_and_mcp_wrappers_share_resource(self):
        import mcp_server
        data = payload()
        data["standings"].append({"team": "Barcelona", "rank": 3, "points": 18, "logo": "https://a.espncdn.com/i/teamlogos/soccer/500/83.png"})
        data["results"] = [{"date": "2026-10-25T18:00Z", "home": "Barcelona", "away": "Real Madrid", "home_score": 1, "away_score": 2}]
        http = ThreadingHTTPServer(("127.0.0.1", 0), server.PruebaHandler)
        thread = threading.Thread(target=http.serve_forever, daemon=True)
        thread.start()
        try:
            with patch("laliga.get_payload", return_value=data):
                request = Request(
                    f"http://127.0.0.1:{http.server_port}/api/tool",
                    data=json.dumps({"name": "laliga_panel", "args": {"equipo": "madrid", "rival": "atleti"}}).encode(),
                    headers={"Content-Type": "application/json"},
                )
                with urlopen(request, timeout=5) as response:
                    actual = json.load(response)
                with urlopen(f"http://127.0.0.1:{http.server_port}/mcp-ui.js", timeout=5) as response:
                    self.assertEqual(response.headers.get("Cache-Control"), "no-store")
                mcp = mcp_server.laliga_panel(equipo="madrid", rival="atleti").model_dump(by_alias=True, mode="json", exclude_none=True)
                self.assertTrue(actual["ok"])
                self.assertEqual(actual["content"], mcp["content"])
                with urlopen(f"http://127.0.0.1:{http.server_port}/api/ask?q=cuando%20juega%20madrid%20atleti", timeout=5) as response:
                    answer = json.load(response)
                self.assertEqual(answer["intent"], "next_match_h2h")
                self.assertEqual(answer["content"], actual["content"])
                self.assertEqual(answer["team_logos"]["Real Madrid"], data["standings"][0]["logo"])
                self.assertEqual(answer["team_logos"]["Atlético Madrid"], data["standings"][1]["logo"])
                with urlopen(f"http://127.0.0.1:{http.server_port}/api/ask?q=cuando%20juega%20madrid", timeout=5) as response:
                    team_only = json.load(response)
                self.assertIn("Real Madrid", team_only["team_logos"])
                self.assertIn("Atlético Madrid", team_only["team_logos"])
                with urlopen(f"http://127.0.0.1:{http.server_port}/api/ask?q=como%20quedo%20el%20madrid%20barsa%20de%20ayer", timeout=5) as response:
                    score = json.load(response)
                self.assertIn("Barcelona", score["team_logos"], repr(score))
                self.assertIn("Real Madrid", score["team_logos"])
                bad = tools.run_tool("laliga_panel", {"equipo": "inventado"}, data)
                self.assertFalse(bad["ok"])
                self.assertNotIn("content", bad)
        finally:
            http.shutdown()
            http.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
