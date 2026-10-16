"""Exercise bridge health failures without contacting cameras."""

import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

spec = importlib.util.spec_from_file_location("avent_healthcheck", Path(__file__).with_name("healthcheck.py"))
healthcheck = importlib.util.module_from_spec(spec)
spec.loader.exec_module(healthcheck)


class HealthcheckTests(unittest.TestCase):
    def setUp(self):
        self.opener_patch = patch.object(healthcheck, "build_opener")
        self.opener = self.opener_patch.start().return_value
        self.addCleanup(self.opener_patch.stop)
        self.opener.open.return_value.__enter__.return_value.status = 200
        self.socket_patch = patch.object(healthcheck.socket, "create_connection")
        self.connect = self.socket_patch.start()
        self.addCleanup(self.socket_patch.stop)
        self.connection = self.connect.return_value.__enter__.return_value
        self.response = self.connection.makefile.return_value.__enter__.return_value
        self.response.readline.return_value = b"RTSP/1.0 200 OK\r\n"

    def test_idle_bridge_is_healthy_without_reading_camera_data(self):
        self.assertTrue(healthcheck.healthy())
        self.opener.open.assert_called_once_with("http://127.0.0.1:19845/api/streams", timeout=3)
        self.connect.assert_called_once_with(("127.0.0.1", 38554), timeout=3)
        self.connection.sendall.assert_called_once_with(b"OPTIONS * RTSP/1.0\r\nCSeq: 1\r\n\r\n")

    def test_api_error_is_unhealthy(self):
        self.opener.open.side_effect = URLError("unreachable")
        self.assertFalse(healthcheck.healthy())
        self.connect.assert_not_called()

    def test_api_non_success_is_unhealthy(self):
        self.opener.open.return_value.__enter__.return_value.status = 503
        self.assertFalse(healthcheck.healthy())

    def test_rtsp_refused_or_timed_out_is_unhealthy(self):
        for error in (ConnectionRefusedError(), TimeoutError()):
            with self.subTest(error=error):
                self.connect.side_effect = error
                self.assertFalse(healthcheck.healthy())

    def test_rtsp_rejects_request_or_closes_is_unhealthy(self):
        for status in (b"RTSP/1.0 500 Error\r\n", b""):
            with self.subTest(status=status):
                self.response.readline.return_value = status
                self.assertFalse(healthcheck.healthy())


if __name__ == "__main__":
    unittest.main()
