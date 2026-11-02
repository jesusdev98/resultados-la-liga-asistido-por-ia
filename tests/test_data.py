"""Data acquisition, shaping and cache tests. No upstream requests leave the process."""
import contextlib
import io
import json
import runpy
import unittest
import urllib.error
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import laliga
from tests.test_schedule import Clock, event


def standings_document():
    return {"children": [{"standings": {"entries": [
        {"team": {"displayName": "Real Madrid", "abbreviation": "RMA", "logos": [{"href": "https://a.espncdn.com/logo.png"}]},
         "stats": [{"name": "rank", "displayValue": "1"}, {"name": "pointDifferential", "displayValue": "3"}],
         "note": {"description": "Champions League"}},
        {"team": {"displayName": "Barcelona", "logos": [None]}, "stats": [None, {}, {"name": "pointDifferential", "displayValue": "-2"}]},
        {},
    ]}}]}


def documents():
    return {
        laliga.SCOREBOARD_URL: {"leagues": [{"season": {"year": 2026,
            "startDate": "2026-08-01", "endDate": "2027-06-30"}}]},
        laliga.STANDINGS_URL: standings_document(),
        laliga.SEASON_URL.format(year=2026): {"events": [event("result", "2026-10-25T20:00Z", True)]},
        laliga.SEASON_URL.format(year=2027): {"events": [event(None, "2027-04-04T20:00Z")]},
    }


class DataContractTests(unittest.TestCase):
    def test_transport_headers_timeout_and_decoding(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"value":3}'
        with patch("urllib.request.urlopen", return_value=response) as opened:
            self.assertEqual(laliga.fetch_json("https://example.invalid/data"), {"value": 3})
            request = opened.call_args.args[0]
            self.assertEqual(request.get_header("User-agent"), laliga.USER_AGENT)
            self.assertEqual(opened.call_args.kwargs["timeout"], laliga.REQUEST_TIMEOUT)
        for error in [urllib.error.URLError("offline"), TimeoutError("timeout"), OSError("unavailable"),
                      urllib.error.HTTPError("https://example.invalid", 503, "unavailable", {}, None)]:
            with self.subTest(error=type(error).__name__), patch("urllib.request.urlopen", side_effect=error):
                with self.assertRaises(laliga.DataError):
                    laliga.fetch_json("https://example.invalid")
        for body in [b"\xff", b"not JSON"]:
            response.__enter__.return_value.read.return_value = body
            with patch("urllib.request.urlopen", return_value=response), self.assertRaises(laliga.DataError):
                laliga.fetch_json("https://example.invalid")

    def test_numeric_and_standings_shaping(self):
        for raw, expected in [(None, None), (True, None), (4, 4), (4.8, 4), ("", None),
                              (" ", None), ("4.8", 4), ("bad", None), ([], None)]:
            self.assertEqual(laliga._to_int(raw), expected)
        rows = laliga._build_standings(standings_document())
        self.assertEqual([row["rank"] for row in rows], [1, 2, 3])
        self.assertEqual(rows[0]["logo"], "https://a.espncdn.com/logo.png")
        self.assertEqual(rows[1]["logo"], "")
        self.assertEqual(rows[2]["team"], "?")
        for raw in [{}, {"children": []}, {"children": None}]:
            with self.assertRaises(laliga.DataError):
                laliga._build_standings(raw)

    def test_malformed_events_venue_and_optional_sides(self):
        for raw in [{}, {"competitions": []}, {"competitions": None}]:
            with self.assertRaises(laliga.DataError):
                laliga._competitors(raw)
            self.assertEqual(laliga._venue_name(raw), "")
        self.assertEqual(laliga._venue_name({"competitions": [{}], "venue": "invalid"}), "")
        self.assertEqual(laliga._venue_name({"competitions": [{"venue": {"fullName": "Source stadium"}}]}), "Source stadium")
        self.assertEqual(laliga._venue_name({"competitions": [{"venue": {"fullName": 5}}]}), "")
        with self.assertRaises(laliga.DataError):
            laliga._build_matches({}, "2026-08-01")
        stale = event("stale", "2026-12-31T18:00Z")
        missing_sides = event("future", "2027-04-04T18:00Z")
        missing_sides["competitions"][0]["competitors"] = []
        with patch("laliga.datetime", Clock):
            results, fixtures = laliga._build_matches({"events": [stale, missing_sides]}, "2026-08-01")
        self.assertEqual(results, [])
        self.assertEqual(len(fixtures), 1)
        self.assertEqual((fixtures[0]["home"], fixtures[0]["away"]), ("?", "?"))

    def test_date_context_and_no_fixture_output(self):
        self.assertIsNone(laliga._to_madrid("bad"))
        self.assertEqual(laliga._format_match_date("bad"), "bad")
        self.assertEqual(laliga._format_day("bad"), "bad")
        self.assertIn("21:00", laliga._format_match_date("2026-10-25T20:00Z"))
        self.assertIn("25 de octubre", laliga.today_label(datetime(2026, 10, 25, 20)))
        rows = laliga._build_standings(standings_document())
        result = {"date": "2026-10-25T20:00Z", "home": "Barcelona", "away": "Real Madrid", "home_score": 1, "away_score": 2}
        text = laliga.build_context_text("2026-27", Clock.now(), rows, [result], [], "hoy")
        self.assertIn("Barcelona 1-2 Real Madrid", text)
        self.assertIn("+3", text)
        self.assertIn("Sin partidos programados", text)

    def test_fetch_validates_metadata_bounds_and_required_content(self):
        for meta in [{}, {"leagues": []}, {"leagues": [{"season": {"startDate": "2026-08-01", "endDate": "2027-06-30"}}]}]:
            with patch("laliga.fetch_json", return_value=meta), self.assertRaises(laliga.DataError):
                laliga._fetch_payload()
        for start, end in [("bad", "2027-06-30"), ("2026-08-01", "bad"),
                           ("2027-08-01", "2026-06-30"), ("2026-08-01", "2029-06-30")]:
            doc = documents()
            doc[laliga.SCOREBOARD_URL]["leagues"][0]["season"].update(startDate=start, endDate=end)
            with patch("laliga.fetch_json", side_effect=doc.__getitem__), self.assertRaises(laliga.DataError):
                laliga._fetch_payload()
        for failure in ["year_events", "empty_standings", "empty_results"]:
            doc = documents()
            if failure == "year_events":
                doc[laliga.SEASON_URL.format(year=2026)] = {}
            elif failure == "empty_standings":
                doc[laliga.STANDINGS_URL]["children"][0]["standings"]["entries"] = []
            else:
                doc[laliga.SEASON_URL.format(year=2026)] = {"events": []}
            with patch("laliga.fetch_json", side_effect=doc.__getitem__), patch("laliga.datetime", Clock), self.assertRaises(laliga.DataError):
                laliga._fetch_payload()
        doc = documents()
        with patch("laliga.fetch_json", side_effect=doc.__getitem__), patch("laliga.datetime", Clock):
            result = laliga._fetch_payload()
        self.assertEqual(result["season"], "La Liga")
        self.assertEqual(len(result["fixtures"]), 1)

    def test_cache_hit_expiry_force_and_failed_refresh(self):
        stamp = datetime.now(timezone.utc).timestamp()
        old, fresh = {"old": True}, {"fresh": True}
        with patch("laliga._cache", {"payload": old, "built_at": stamp}), patch("laliga._fetch_payload", return_value=fresh) as fetch:
            self.assertIs(laliga.get_payload(), old)
            fetch.assert_not_called()
            self.assertIs(laliga.get_payload(force=True), fresh)
            fetch.assert_called_once()
        for cached in [None, old]:
            with patch("laliga._cache", {"payload": cached, "built_at": 0}), patch("laliga._fetch_payload", return_value=fresh):
                self.assertIs(laliga.get_payload(), fresh)
        with patch("laliga._cache", {"payload": old, "built_at": 0}), patch("laliga._fetch_payload", side_effect=laliga.DataError("offline")):
            with self.assertRaises(laliga.DataError):
                laliga.get_payload()
            self.assertIs(laliga._cache["payload"], old)

    def test_module_command_prints_a_real_shaped_payload_with_mocked_http(self):
        doc = documents()
        def opened(request, **kwargs):
            response = MagicMock()
            response.__enter__.return_value.read.return_value = json.dumps(doc[request.full_url]).encode()
            return response
        out = io.StringIO()
        with patch("urllib.request.urlopen", side_effect=opened), contextlib.redirect_stdout(out):
            runpy.run_module("laliga", run_name="__main__")
        self.assertIn('"season_year": 2026', out.getvalue())
        self.assertIn("standings=3 results=1 fixtures=1", out.getvalue())


if __name__ == "__main__":
    unittest.main()
