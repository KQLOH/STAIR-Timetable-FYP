"""Offline endpoint function tests: synthetic HTML and mocked clients/filesystem.

No HTTP server, authenticated login, network request or private response is used.
"""

from contextlib import redirect_stdout
from io import StringIO
import unittest
from unittest.mock import MagicMock, patch

from fastapi import HTTPException
from backend import main
from backend.models.event import CanonicalTimetable
from tests.fixtures import timetable_html


class ApiContractTests(unittest.TestCase):
    def setUp(self):
        self.clients = patch.dict(main.CONNECTED_CLIENTS, {}, clear=True)
        self.clients.start()
        self.addCleanup(self.clients.stop)
        self.client = MagicMock()
        self.client.fetch_latest_timetable_html.return_value = timetable_html()
        self.client.student_name = "SYNTHETIC TEST NAME"
        self.client.last_timetable_url = "https://synthetic.invalid/timetable"

    def assert_packet(self, result):
        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["events"][0]["subject_code"], "TEST1001")
        CanonicalTimetable.model_validate(result["data"]["canonical"])

    def test_existing_routes_preserved(self):
        routes = {(method, route.path) for route in main.app.routes for method in getattr(route, "methods", ())}
        expected = {("GET", "/"), ("GET", "/api/health"), ("POST", "/api/intranet/connect"),
                    ("POST", "/api/intranet/timetable"), ("POST", "/api/intranet/disconnect"),
                    ("GET", "/api/intranet/timetable/sample")}
        self.assertTrue(expected.issubset(routes))

    def test_connect_adds_contract_and_keeps_local_session_architecture(self):
        with patch.object(main, "TARUMTClient", return_value=self.client), patch.object(main, "PROJECT_ROOT", MagicMock()), redirect_stdout(StringIO()):
            result = main.connect_tarumt(main.TARUMTLoginRequest(student_id="SYNTHETIC_TEST_ID", password="SYNTHETIC_TEST_PASSWORD"))
        self.assert_packet(result)
        self.client.login.assert_called_once_with("SYNTHETIC_TEST_ID", "SYNTHETIC_TEST_PASSWORD")
        self.assertIs(main.CONNECTED_CLIENTS[result["connection_token"]], self.client)
        self.assertNotIn("SYNTHETIC_TEST_PASSWORD", str(result))

    def test_refresh_adds_contract_without_new_login(self):
        main.CONNECTED_CLIENTS["synthetic-token"] = self.client
        result = main.refresh_tarumt_timetable(main.TARUMTSessionRequest(token="synthetic-token"))
        self.assert_packet(result)
        self.client.login.assert_not_called()

    def test_sample_endpoint_adds_contract_with_mocked_file(self):
        fake_file = MagicMock()
        fake_file.exists.return_value = True
        fake_file.read_text.return_value = timetable_html()
        with patch.object(main, "SAMPLE_RESPONSE_FILE", fake_file):
            self.assert_packet(main.sample_timetable())

    def test_expired_session_still_raises_401(self):
        with self.assertRaises(HTTPException) as raised:
            main.refresh_tarumt_timetable(main.TARUMTSessionRequest(token="synthetic-missing"))
        self.assertEqual(raised.exception.status_code, 401)

    def test_disconnect_still_closes_existing_client(self):
        main.CONNECTED_CLIENTS["synthetic-token"] = self.client
        self.assertTrue(main.disconnect_tarumt(main.TARUMTSessionRequest(token="synthetic-token"))["success"])
        self.client.close.assert_called_once()
        self.assertNotIn("synthetic-token", main.CONNECTED_CLIENTS)


if __name__ == "__main__":
    unittest.main()
