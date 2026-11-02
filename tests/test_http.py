"""Offline public HTTP contracts; these tests never exercise or simulate Qwen."""
import http.client
import contextlib
import io
import json
import runpy
import sys
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import MagicMock, patch

import laliga
import server


class HttpContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = {
            "season": "2026-27", "season_year": 2026, "updated_at": "2026-10-27T00:00Z",
            "today_label": "martes 27 de octubre de 2026", "context_text": "Verified fixture test data",
            "standings": [{"team": name, "rank": i + 1, "points": 20 - i}
                          for i, name in enumerate(["Real Madrid", "Barcelona", "Valencia", "Sevilla"])],
            "results": [{"home": "Barcelona", "away": "Real Madrid", "date": "2026-10-25T20:00Z",
                         "home_score": 1, "away_score": 2}],
            "fixtures": [{"home": "Real Madrid", "away": name, "date": f"2026-11-{day:02d}T17:00Z", "time_valid": True}
                         for day, name in zip([1, 8, 15], ["Barcelona", "Valencia", "Sevilla"])],
        }
        cls.http = ThreadingHTTPServer(("127.0.0.1", 0), server.PruebaHandler)
        cls.thread = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        cls.thread.join(timeout=5)

    def request(self, method, route, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.http.server_port, timeout=5)
        if isinstance(body, dict):
            body = json.dumps(body).encode()
        try:
            connection.request(method, route, body, headers or {})
            response = connection.getresponse()
            raw = response.read()
            parsed = json.loads(raw) if response.getheader("Content-Type", "").startswith("application/json") else raw
            self.assertEqual(response.getheader("Cache-Control"), "no-store")
            return response.status, parsed
        finally:
            connection.close()

    def test_routes_return_source_data_and_clamp_limits(self):
        with patch("laliga.get_payload", return_value=self.data):
            for route in ["/api/health", "/api/laliga/context", "/api/laliga/standings",
                          "/api/laliga/results", "/api/laliga/fixtures", "/", "/app.js"]:
                with self.subTest(route=route):
                    status, result = self.request("GET", route)
                    self.assertEqual(status, 200)
                    if route == "/api/health":
                        self.assertTrue(result["ok"])
            for raw, expected in [("1", 1), ("0", 1), ("-5", 1), ("garbage", 3), ("1000", 3)]:
                with self.subTest(limit=raw):
                    status, result = self.request("GET", f"/api/laliga/fixtures?limit={raw}")
                    self.assertEqual(status, 200)
                    self.assertEqual(len(result["fixtures"]), expected)

    def test_invalid_question_requests_fail_before_upstream(self):
        with patch("laliga.get_payload") as upstream:
            for body in [{}, {"question": 4}, {"question": " "}, {"question": "x" * 501},
                         {"question": "cuando juega madrid", "previous_question": "x" * 501}]:
                with self.subTest(body=body):
                    self.assertEqual(self.request("POST", "/api/ask", body)[0], 400)
            upstream.assert_not_called()

    def test_malformed_bodies_are_bad_requests(self):
        for body, headers in [(b"{", {}), (b"[]", {}), (b"\xff", {}), (b"", {}),
                              (b"{}", {"Content-Length": "invalid"}),
                              (b"x" * (server.MAX_BODY_BYTES + 1), {})]:
            with self.subTest(size=len(body), headers=headers):
                status, result = self.request("POST", "/api/ask", body, headers)
                self.assertEqual(status, 400)
                self.assertEqual(result["error"], server.BAD_JSON)

    def test_tools_preserve_transport_and_failure_contract(self):
        with patch("laliga.get_payload", return_value=self.data):
            for body in [{}, {"name": 1}, {"name": "tabla", "args": []}]:
                self.assertEqual(self.request("POST", "/api/tool", body)[0], 400)
            for body, ok in [({"name": "tabla"}, True), ({"name": "unknown"}, False),
                             ({"name": "proximo_partido", "args": {"equipo": "Madrid", "cantidad": 3}}, True),
                             ({"name": "ultimo_resultado", "args": {"equipo": "inventado"}}, False)]:
                with self.subTest(body=body):
                    status, result = self.request("POST", "/api/tool", body)
                    self.assertEqual(status, 200)
                    self.assertEqual(result["ok"], ok)
            status, result = self.request("GET", "/api/tool?name=proximo_partido&equipo=madrid")
            self.assertEqual(status, 200)
            self.assertTrue(result["ok"])

    def test_unknown_routes(self):
        for method, route in [("GET", "/api/not-real"), ("POST", "/api/not-real")]:
            self.assertEqual(self.request(method, route, {})[0], 404)

    def test_unresolved_confirmation_does_not_project_a_fixture(self):
        unresolved = {"intent": "confirm_last", "answer": "", "teams": [], "facts": [], "confidence": "low"}
        prior = {**unresolved, "intent": "none"}
        with patch("laliga.get_payload", return_value=self.data), patch("resolve.answer", side_effect=[unresolved, prior]):
            status, result = self.request("POST", "/api/ask", {"question": "seguro?", "previous_question": "consulta",
                                          "fixture_context": {"equipo": "Real Madrid", "despues_de": "invalid"}})
        self.assertEqual(status, 200)
        self.assertEqual(result["answer"], "")
        self.assertEqual(result["content"], [])
        self.assertIsNone(result["fixture_context"])

    def test_disconnected_clients_are_not_retried(self):
        # Socket failure injection keeps this deterministic without racing a real close.
        for failure in [BrokenPipeError, ConnectionResetError]:
            handler = MagicMock(spec=server.PruebaHandler)
            handler.wfile = MagicMock()
            handler.wfile.write.side_effect = failure
            server.PruebaHandler.send_json(handler, {"ok": True})
            handler.wfile.write.assert_called_once()
            handler.path = "/api/laliga/context"
            with patch("laliga.get_payload", side_effect=failure):
                server.PruebaHandler.do_GET(handler)
            handler.send_api_error.assert_not_called()
            handler.path = "/api/ask"
            handler.read_json_body.return_value = {"question": "tabla"}
            handler.send_ask.side_effect = failure
            server.PruebaHandler.do_POST(handler)
            handler.send_ask.assert_called_once()
            handler.send_api_error.assert_not_called()

    def test_server_logging_keeps_response_bodies_out(self):
        handler = MagicMock(spec=server.PruebaHandler)
        handler.address_string.return_value = "127.0.0.1"
        output = io.StringIO()
        with contextlib.redirect_stderr(output):
            server.PruebaHandler.log_message(handler, "event %s", "closed")
        self.assertEqual(output.getvalue(), "127.0.0.1 - event closed\n")

    def test_upstream_and_internal_errors_do_not_leak_details(self):
        for failure, expected in [(laliga.DataError("private upstream detail"), 502),
                                  (RuntimeError("private internal detail"), 500)]:
            with patch("laliga.get_payload", side_effect=failure):
                for method, route, body in [("GET", "/api/laliga/context", None),
                                            ("POST", "/api/ask", {"question": "cuando juega madrid"})]:
                    with self.subTest(method=method, expected=expected):
                        status, result = self.request(method, route, body)
                        self.assertEqual(status, expected)
                        self.assertNotIn("private", result["error"])


class ServerStartupTests(unittest.TestCase):
    def test_arguments_and_missing_static_entry(self):
        args = server.parse_args([])
        self.assertEqual((args.host, args.port), ("0.0.0.0", 8000))
        args = server.parse_args(["--host", "127.0.0.1", "--port", "9011"])
        self.assertEqual((args.host, args.port), ("127.0.0.1", 9011))
        with patch("pathlib.Path.is_file", return_value=False), patch("server.ThreadingHTTPServer") as listener:
            error = io.StringIO()
            with contextlib.redirect_stderr(error):
                self.assertEqual(server.main([]), 1)
            listener.assert_not_called()
            self.assertIn("missing", error.getvalue())

    def test_startup_and_shutdown_close_the_listener_once(self):
        for interrupt in [False, True]:
            listener = MagicMock()
            if interrupt:
                listener.serve_forever.side_effect = KeyboardInterrupt
            output = io.StringIO()
            with patch("server.ThreadingHTTPServer", return_value=listener) as factory, contextlib.redirect_stdout(output):
                self.assertEqual(server.main(["--host", "127.0.0.1", "--port", "9011"]), 0)
            factory.assert_called_once_with(("127.0.0.1", 9011), server.PruebaHandler)
            self.assertTrue(listener.daemon_threads)
            listener.serve_forever.assert_called_once()
            listener.server_close.assert_called_once()
            self.assertIn("http://localhost:9011", output.getvalue())
            self.assertEqual("Cerrando" in output.getvalue(), interrupt)

    def test_module_entry_exits_successfully_without_binding_a_socket(self):
        listener = MagicMock()
        with patch("http.server.ThreadingHTTPServer", return_value=listener), patch.object(sys, "argv", ["server.py"]), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as exited:
                runpy.run_module("server", run_name="__main__")
        self.assertEqual(exited.exception.code, 0)
        listener.server_close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
