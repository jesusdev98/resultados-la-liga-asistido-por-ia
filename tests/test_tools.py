"""Structured tool contracts; deterministic payloads, no network/model calls."""
import copy
import unittest
from unittest.mock import patch

import tools


def data():
    return {
        "season_year": 2026, "updated_at": "2026-10-28T00:00Z",
        "standings": [{"team": name, "rank": i + 1, "points": 20 - i}
                      for i, name in enumerate(["Real Madrid", "Barcelona", "Atlético Madrid", "Valencia"])],
        "fixtures": [{"home": "Real Madrid", "away": name, "date": f"2026-11-{day:02d}T17:00Z", "time_valid": True}
                     for day, name in zip([1, 8, 15], ["Barcelona", "Atlético Madrid", "Valencia"])],
        "results": [{"home": "Barcelona", "away": "Real Madrid", "date": "2026-10-25T20:00Z",
                     "home_score": 1, "away_score": 2}],
    }


class ToolContractTests(unittest.TestCase):
    def assertRejected(self, name, args, payload=None):
        result = tools.run_tool(name, args, payload if payload is not None else data())
        self.assertFalse(result["ok"], result)
        self.assertTrue(result["error"])
        return result

    def test_malformed_names_and_argument_shapes(self):
        for name in [None, 4, "", "  ", "not-a-tool"]:
            self.assertRejected(name, {})
        for args in [None, [], 8, "bad"]:
            self.assertTrue(tools.run_tool(" tabla ", args, data())["ok"])

    def test_ambiguous_invalid_and_conflicting_teams(self):
        cases = [
            {"equipo": "madrid barca atleti"}, {"rival": "madrid barca"},
            {"equipo": "madrid", "rival": "unknown"},
            {"equipo": "madrid barca", "rival": "atleti"},
            {"equipo": "madrid", "rival": "real madrid"},
        ]
        for args in cases:
            with self.subTest(args=args):
                self.assertRejected("proximo_partido", args)
        extended = data()
        extended["standings"] += [{"team": f"Club {i}", "rank": i + 8} for i in range(15)]
        self.assertIn("…", self.assertRejected("proximo_partido", {"equipo": "unknown"}, extended)["error"])

    def test_temporal_argument_rejections(self):
        for args in [
            {"despues_de": "2026-11-01T17:00Z"}, {"equipo": "madrid", "despues_de": ""},
            {"equipo": "madrid", "despues_de": "invalid"}, {"despues_del_dia": "2026-11-01"},
            {"equipo": "madrid", "despues_del_dia": "bad"},
            {"equipo": "madrid", "despues_de": "2026-11-01T17:00Z", "despues_del_dia": "2026-11-01"},
            {"equipo": "madrid", "fecha_exacta": "bad"},
            {"equipo": "madrid", "fecha_exacta": "2026-11-01T17:00Z", "despues_de": "2026-11-01T17:00Z"},
            {"equipo": "madrid", "fecha_exacta": "2026-11-01T17:00Z", "despues_del_dia": "2026-11-01"},
        ]:
            with self.subTest(args=args):
                self.assertRejected("proximo_partido", args)

    def test_source_filters_exact_calendar_and_empty_schedules(self):
        payload = data()
        exact = tools.run_tool("proximo_partido", {"equipo": "madrid", "fecha_exacta": "2026-11-08T17:00Z"}, payload)
        self.assertEqual(exact["datos"]["partidos"][0]["visitante"], "Atlético Madrid")
        after = tools.run_tool("proximo_partido", {"equipo": "madrid", "despues_del_dia": "2026-11-01"}, payload)
        self.assertEqual(len(after["datos"]["partidos"]), 2)
        self.assertEqual(len(tools.run_tool("proximo_partido", {}, payload)["datos"]["partidos"]), 3)
        for args in [{}, {"equipo": "madrid"}, {"equipo": "barca", "rival": "atleti"}]:
            self.assertRejected("proximo_partido", args, {**payload, "fixtures": []})
        missing_home = copy.deepcopy(payload)
        missing_home["fixtures"] = [{"away": "Real Madrid", "date": "2026-11-01T17:00Z"}]
        row = tools.run_tool("proximo_partido", {"equipo": "madrid"}, missing_home)["datos"]["partidos"][0]
        self.assertEqual(row["local"], "?")
        self.assertEqual(row["estadio"], "")

    def test_result_required_teams_and_source_scores(self):
        for args in [{}, {"equipo": "unknown"}, {"equipo": "madrid barca"},
                     {"equipo": "madrid", "rival": "unknown"}, {"equipo": "madrid", "rival": "madrid"},
                     {"equipo": "atleti"}, {"equipo": "madrid", "rival": "valencia"}]:
            with self.subTest(args=args):
                self.assertRejected("ultimo_resultado", args)
        result = tools.run_tool("ultimo_resultado", {"equipo": "madrid"}, data())["datos"]
        self.assertEqual((result["goles_local"], result["goles_visitante"]), (1, 2))
        malformed = data()
        malformed["results"][0]["away_score"] = None
        self.assertRejected("ultimo_resultado", {"equipo": "madrid"}, malformed)

    def test_standings_position_and_relegation_contracts(self):
        payload = data()
        self.assertEqual(tools.run_tool("posicion", {"equipo": "madrid"}, payload)["datos"]["pos"], 1)
        for args in [{}, {"equipo": "unknown"}, {"equipo": "madrid barca"}]:
            self.assertRejected("posicion", args)
        self.assertRejected("tabla", {}, {"standings": []})
        self.assertRejected("descenso", {}, {"standings": []})
        fallback = tools.run_tool("descenso", {}, payload)["datos"]["filas"]
        self.assertEqual([r["equipo"] for r in fallback], ["Barcelona", "Atlético Madrid", "Valencia"])
        for row in payload["standings"][1:]:
            row["zone"] = "relegation"
        self.assertEqual([r["equipo"] for r in tools.run_tool("descenso", {}, payload)["datos"]["filas"]],
                         ["Barcelona", "Atlético Madrid", "Valencia"])
        # Defensive seam: an upstream resolver return without the promised table row.
        with patch("tools._resolve_equipo", return_value=(["Missing canonical row"], None)):
            self.assertRejected("posicion", {"equipo": "madrid"}, payload)

    def test_unfiltered_panel_and_unknown_payload_names(self):
        result = tools.run_tool("laliga_panel", {}, data())
        self.assertTrue(result["ok"])
        self.assertTrue(result["content"])
        self.assertRejected("laliga_panel", {"equipo": "unknown"})


if __name__ == "__main__":
    unittest.main()
