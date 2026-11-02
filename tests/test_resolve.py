"""Resolver behavior and defensive contracts, independent of model inference."""
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import resolve
from tests.test_tools import data


class ResolverContractTests(unittest.TestCase):
    def test_date_labels_and_naive_utc_boundary(self):
        naive = datetime(2026, 10, 25, 20, 0)
        self.assertEqual(resolve._parse(naive).tzinfo, timezone.utc)
        self.assertEqual(resolve.time_label(naive), "21:00")
        self.assertEqual(resolve.time_label("invalid"), "")
        self.assertEqual(resolve.datetime_label("invalid"), "invalid")
        self.assertIn("21:00", resolve.datetime_label(naive))
        self.assertIn("25 de octubre", resolve.today_label(naive))
        with patch("resolve.datetime", wraps=datetime) as clock:
            clock.now.return_value = naive.replace(tzinfo=timezone.utc)
            self.assertIn("25 de octubre", resolve.today_label())

    def test_clasico_aliases_and_meta_fillers(self):
        payload = data()
        payload["standings"].append({})
        self.assertEqual(set(resolve.find_teams("el clasico", payload)), {"Real Madrid", "Barcelona"})
        self.assertEqual(len(resolve.find_teams("madrid y el clasico", payload)), 2)
        self.assertEqual(resolve.find_teams("clasico", {"standings": []}), [])
        self.assertTrue(resolve._bare_entity_question("el clasico", payload, ["Real Madrid", "Barcelona"]))
        self.assertEqual(resolve._core_phrase("y seguro y"), "seguro")
        self.assertIsNone(resolve._meta_intent("y el", []))
        self.assertIsNone(resolve._meta_intent("palabra " * 30, []))

    def test_result_and_standing_routing_uses_source_rows(self):
        payload = data()
        payload["standings"][0]["goal_diff"] = 3
        for phrase, intent in [("como quedo madrid", "last_match_team"), ("como va madrid", "team_standing"),
                               ("tabla", "table"), ("resultados", "last_results"), ("descenso", "bottom")]:
            with self.subTest(phrase=phrase):
                answer = resolve.answer(phrase, payload)
                self.assertEqual(answer["intent"], intent)
                self.assertTrue(answer["answer"])
                self.assertTrue(answer["facts"])
        self.assertIn("+3", resolve.answer("como va madrid", payload)["answer"])
        payload["standings"][0]["goal_diff"] = "invalid"
        self.assertIn("0", resolve.answer("como va madrid", payload)["answer"])
        for row in payload["standings"][1:]:
            row["zone"] = "relegation"
        self.assertEqual(len(resolve.answer("descenso", payload)["facts"]), 3)
        empty = {"standings": [], "fixtures": [], "results": []}
        for phrase in ["tabla", "resultados", "descenso"]:
            self.assertFalse(resolve.answer(phrase, empty)["answer"])

    def test_unavailable_or_malformed_results_never_invent_scores(self):
        payload = data()
        self.assertEqual(resolve.answer("como quedo atleti", payload)["intent"], "unknown_football")
        payload["results"][0]["home_score"] = None
        self.assertEqual(resolve.answer("como quedo madrid", payload)["intent"], "unknown_football")

    def test_unknown_team_questions_and_meta_history(self):
        payload = data()
        for phrase in ["", "madrid barca atleti", "que opina madrid", "hola"]:
            self.assertFalse(resolve.answer(phrase, payload)["answer"])
        verified = resolve.answer("seguro?", payload, "como quedo madrid")
        self.assertEqual(verified["intent"], "confirm_last")
        self.assertIn("1-2", verified["answer"])
        self.assertEqual(resolve.answer("creo que no", payload, "como quedo madrid")["intent"], "challenge_last")
        for previous in [None, "hola", "seguro?"]:
            answer = resolve.answer("seguro?", payload, previous)
            self.assertFalse(answer["answer"])
            self.assertIsNone(answer["follow_up_of"])
        # Defensive helper seam: public team extraction normally ensures this row exists.
        missing = resolve._resolve_question("como va missing", payload, "como va missing", ["Missing"])
        self.assertEqual(missing["intent"], "unknown_football")

    def test_temporal_queries_reject_missing_subject_invalid_dates_and_unmatched_anchors(self):
        payload = data()
        context = {"equipo": "Real Madrid", "despues_de": "2026-11-01T17:00Z"}
        for phrase, ctx in [
            ("los tres proximos partidos", {}),
            ("el proximo partido del madrid despues del 31 de febrero de 2026", context),
            ("el proximo partido del madrid despues de ese", {}),
            ("despues de ese el siguiente partido del madrid", {**context, "despues_de": "invalid"}),
        ]:
            with self.subTest(phrase=phrase):
                self.assertFalse(resolve.answer(phrase, payload, fixture_context=ctx)["answer"])
        for stamp in ["invalid", "2027-12-01T17:00Z"]:
            answer = resolve.answer("seguro?", payload, "cuando juega madrid", {**context, "despues_de": stamp})
            self.assertFalse(answer["answer"])
        pair = resolve._schedule_query("despues de ese partido", [], payload,
                                       {**context, "rival": "Barcelona"})
        self.assertEqual(pair["tool_args"]["rival"], "Barcelona")
        no_result = {**payload, "results": []}
        unresolved = resolve.answer("y el proximo partido cuando es?", no_result, "como quedo madrid")
        self.assertFalse(unresolved["answer"])
        # This helper must not classify temporal words alone as a fixture query.
        self.assertIsNone(resolve._schedule_query("despues de ese estadio", [], payload, context))
        self.assertEqual(resolve.answer("y ahi?", payload, fixture_context=context)["intent"], "unknown_football")


if __name__ == "__main__":
    unittest.main()
