"""Deterministic season coverage and provisional kickoff regressions."""

import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import laliga
import resolve


class Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2027, 1, 2, tzinfo=timezone.utc)


def event(identifier, date, completed=False, time_valid=False):
    return {
        "id": identifier, "date": date,
        "status": {"type": {"completed": completed}},
        "competitions": [{"timeValid": time_valid, "competitors": [
            {"homeAway": "home", "score": "2", "team": {
                "displayName": "Real Madrid", "logo": "home.png"}},
            {"homeAway": "away", "score": "1", "team": {
                "displayName": "Atlético Madrid", "logos": [{"href": "away.png"}]}},
        ]}],
    }


class ScheduleTests(unittest.TestCase):
    def fetch(self, failure=False):
        derby = event("derby", "2027-04-04T18:00Z")
        documents = {
            laliga.SCOREBOARD_URL: {"leagues": [{"season": {
                "year": 2026, "displayName": "2026-27",
                "startDate": "2026-08-01T00:00Z", "endDate": "2027-06-30",
            }}]},
            laliga.STANDINGS_URL: {},
            laliga.SEASON_URL.format(year=2026): {"events": [
                event("old", "2026-07-31T19:00Z", True),
                event("result", "2026-12-30T19:00Z", True), derby,
            ]},
            laliga.SEASON_URL.format(year=2027): {"events": [
                derby, event("last", "2027-06-30T22:00Z"),
                event("outside", "2027-07-01T18:00Z"),
            ]},
        }

        def load(url):
            if failure and url == laliga.SEASON_URL.format(year=2027):
                raise laliga.DataError("year unavailable")
            return documents[url]

        with patch.object(laliga, "fetch_json", side_effect=load) as fetch, \
                patch.object(laliga, "_build_standings", return_value=[{"team": "Real Madrid"}]), \
                patch.object(laliga, "build_context_text", return_value="context"), \
                patch.object(laliga, "datetime", Clock):
            payload = laliga._fetch_payload()
        return payload, [call.args[0] for call in fetch.call_args_list]

    def test_full_season_deduplicates_and_filters_both_boundaries(self):
        payload, urls = self.fetch()
        self.assertEqual(urls, [laliga.SCOREBOARD_URL, laliga.STANDINGS_URL,
                               laliga.SEASON_URL.format(year=2026),
                               laliga.SEASON_URL.format(year=2027)])
        self.assertEqual([m["date"] for m in payload["fixtures"]],
                         ["2027-04-04T18:00Z", "2027-06-30T22:00Z"])
        self.assertEqual(len(payload["results"]), 1)
        self.assertEqual(payload["fixtures"][0]["home_logo"], "home.png")
        self.assertEqual(payload["fixtures"][0]["away_logo"], "away.png")

    def test_year_failure_never_returns_partial_season(self):
        with self.assertRaises(laliga.DataError):
            self.fetch(failure=True)

    def test_timestamp_bounds_are_inclusive_and_compare_actual_instants(self):
        raw = {"events": [
            event("before", "2026-08-01T00:59:59+02:00", True),
            event("start", "2026-08-01T01:00:00+02:00", True),
            event("end", "2027-06-30T22:00:00Z"),
            event("after", "2027-06-30T22:00:01Z"),
        ]}
        with patch.object(laliga, "datetime", Clock):
            results, fixtures = laliga._build_matches(
                raw, "2026-07-31T23:00:00Z", "2027-06-30T22:00:00Z")
        self.assertEqual([m["date"] for m in results], ["2026-08-01T01:00:00+02:00"])
        self.assertEqual([m["date"] for m in fixtures], ["2027-06-30T22:00:00Z"])

    def test_kickoff_flag_is_strict_and_results_still_require_scores(self):
        for flag in (True, False, None, "true", 1):
            with self.subTest(flag=flag), patch.object(laliga, "datetime", Clock):
                item = event("fixture", "2027-04-04T18:00Z", time_valid=flag)
                if flag is None:
                    del item["competitions"][0]["timeValid"]
                _, fixtures = laliga._build_matches({"events": [item]}, "2026-08-01")
                self.assertIs(fixtures[0].get("time_valid"), flag is True)
        broken = event("result", "2026-12-30T19:00Z", True)
        broken["competitions"][0]["competitors"][0]["score"] = None
        with patch.object(laliga, "datetime", Clock):
            self.assertEqual(laliga._build_matches({"events": [broken]}, "2026-08-01"), ([], []))

    def test_provisional_day_is_not_shifted_by_placeholder_timezone(self):
        match = {"date": "2027-04-04T23:00Z"}
        self.assertEqual(resolve.fixture_labels(match), {
            "dia": "domingo 4 de abril de 2027", "hora": "Por definir",
            "estado_fecha": "Fecha provisional", "horario_confirmado": False,
        })
        match["time_valid"] = True
        self.assertEqual(resolve.fixture_labels(match), {
            "dia": "lunes 5 de abril de 2027", "hora": "01:00",
            "estado_fecha": "Fecha confirmada", "horario_confirmado": True,
        })

    def test_missing_date_is_not_invented(self):
        self.assertEqual(resolve.fixture_labels({})["dia"], "")
        self.assertEqual(resolve.fixture_labels({})["hora"], "Por definir")
        self.assertEqual(resolve.fixture_labels({"date": "invalid", "time_valid": True})["dia"], "")
        for flag in (False, None, "true", 1):
            self.assertFalse(resolve.fixture_labels({
                "date": "2027-04-04T18:00Z", "time_valid": flag,
            })["horario_confirmado"])

    def test_parser_answers_and_context_share_safe_schedule(self):
        payload, _ = self.fetch()
        payload["standings"].append({"team": "Atlético Madrid"})
        for question in ("vale y una duda cuando es el proximo madrid atleti?",
                         "si el proximo madrid atleti te dije", "y madrid atleti"):
            result = resolve.answer(question, payload)
            self.assertEqual(result["intent"], "next_match_h2h")
            self.assertIn("domingo 4 de abril de 2027", result["answer"])
            self.assertIn("Por definir", result["answer"])
            self.assertIn("Fecha provisional", result["answer"])
            self.assertNotIn("20:00", str(result))
        context = laliga.build_context_text("2026-27", Clock.now(), [], [], payload["fixtures"], "hoy")
        self.assertIn("Por definir", context)
        self.assertNotIn("20:00", context)
        payload["fixtures"][0]["time_valid"] = True
        confirmed = resolve.answer("cuando juega el madrid contra el atleti", payload)
        self.assertIn("domingo 4 de abril de 2027, 20:00", confirmed["answer"])
        self.assertEqual(confirmed["teams"], ["Real Madrid", "Atlético Madrid"])
        self.assertEqual(resolve.answer("seguro?", payload)["intent"], "confirm_last")


if __name__ == "__main__":
    unittest.main()
