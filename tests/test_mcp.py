"""Offline MCP tool contracts, distinct from browser/model verification."""
import unittest
import builtins
import contextlib
import io
import runpy
from unittest.mock import patch

import laliga
import mcp_server


class McpContractTests(unittest.TestCase):
    def setUp(self):
        self.data = {
            "season": "2026-27", "season_year": 2026, "updated_at": "2026-10-27T00:00Z",
            "today_label": "martes 27 de octubre de 2026",
            "standings": [{"team": name, "rank": i + 1, "points": 20 - i, "played": 10,
                           "goal_diff": 3 - 3 * i, "zone": "Champions League" if i == 0 else ""}
                          for i, name in enumerate(["Real Madrid", "Barcelona", "Valencia"])],
            "results": [{"date": "2026-10-25T20:00Z", "home": "Barcelona", "away": "Real Madrid",
                         "home_score": 1, "away_score": 2}],
            "fixtures": [{"date": "2026-11-01T16:00Z", "home": "Real Madrid", "away": "Valencia", "time_valid": False}],
        }

    def test_verified_queries_and_empty_or_unknown_requests(self):
        with patch("laliga.get_payload", return_value=self.data):
            self.assertIn("Hechos verificados", mcp_server.laliga_preguntar("cuando juega el madrid"))
            self.assertIn("vacía", mcp_server.laliga_preguntar(" "))
            self.assertIn("No tengo ese dato verificado", mcp_server.laliga_preguntar("balon de oro"))

    def test_table_contains_rank_points_difference_and_zone(self):
        with patch("laliga.get_payload", return_value=self.data):
            text = mcp_server.laliga_tabla()
            self.assertIn("Real Madrid", text)
            self.assertIn("20 pts", text)
            self.assertIn("+3 DG", text)
            self.assertIn("0 DG", text)
            self.assertIn("-3 DG", text)
            self.assertIn("Champions League", text)

    def test_lists_keep_actual_result_and_provisional_metadata(self):
        with patch("laliga.get_payload", return_value=self.data):
            for limit in [-1, 1, 1000]:
                self.assertIn("Barcelona 1-2 Real Madrid", mcp_server.laliga_resultados(limit))
                fixture = mcp_server.laliga_proximos(limit)
                self.assertIn("Real Madrid vs Valencia", fixture)
                self.assertIn("Fecha provisional", fixture)
                self.assertIn("Por definir", fixture)
                self.assertNotIn("16:00", fixture)
        with patch("laliga.get_payload", return_value={**self.data, "results": [], "fixtures": []}):
            self.assertIn("no hay resultados", mcp_server.laliga_resultados())
            self.assertIn("No hay partidos", mcp_server.laliga_proximos())

    def test_panel_shares_resource_and_rejects_unknown_clubs(self):
        with patch("laliga.get_payload", return_value=self.data):
            self.assertEqual(mcp_server._panel_title(self.data), "La Liga 2026-27")
            self.assertIn("<!DOCTYPE html>", mcp_server._panel_html(self.data))
            result = mcp_server.laliga_panel(equipo="madrid")
            self.assertFalse(result.is_error)
            self.assertTrue(result.content)
            invalid = mcp_server.laliga_panel(equipo="unknown-club")
            self.assertTrue(invalid.is_error)

    def test_upstream_failure_is_explicit_in_every_tool(self):
        with patch("laliga.get_payload", side_effect=laliga.DataError("test upstream unavailable")):
            calls = [lambda: mcp_server.laliga_preguntar("tabla"), mcp_server.laliga_tabla,
                     mcp_server.laliga_resultados, mcp_server.laliga_proximos]
            for invoke in calls:
                self.assertIn("No pude leer los datos", invoke())
            self.assertTrue(mcp_server.laliga_panel().is_error)

    def test_verified_answer_without_facts_has_no_empty_fact_section(self):
        text = mcp_server._verified_text({"intent": "table", "answer": "Source answer", "facts": []}, "today")
        self.assertIn("Source answer", text)
        self.assertNotIn("Hechos verificados", text)

    def test_optional_sdk_absence_exits_with_install_instructions(self):
        original = builtins.__import__
        def missing(name, *args, **kwargs):
            if name == "mcp":
                raise ImportError("test missing optional dependency")
            return original(name, *args, **kwargs)
        output = io.StringIO()
        with patch("builtins.__import__", side_effect=missing), contextlib.redirect_stdout(output):
            with self.assertRaises(SystemExit) as exited:
                runpy.run_module("mcp_server", run_name="__main__")
        self.assertEqual(exited.exception.code, 0)
        self.assertIn(".venv/bin/python mcp_server.py", output.getvalue())
        self.assertIn("python3 server.py", output.getvalue())

    def test_stdio_entry_and_keyboard_interrupt_exit_without_network(self):
        from mcp.server.mcpserver import MCPServer
        for interrupt in [False, True]:
            with patch.object(MCPServer, "run", side_effect=KeyboardInterrupt if interrupt else None) as run:
                if interrupt:
                    with self.assertRaises(SystemExit) as exited:
                        runpy.run_module("mcp_server", run_name="__main__")
                    self.assertEqual(exited.exception.code, 0)
                else:
                    runpy.run_module("mcp_server", run_name="__main__")
                run.assert_called_once_with(transport="stdio")


if __name__ == "__main__":
    unittest.main()
