"""Validate integration-owned camera config without contacting a camera."""

import hashlib
import importlib.util
import json
import stat
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("avent_app", Path(__file__).with_name("run.py"))
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


def camera():
    return {
        "stream_name": "Test_camera",
        "lan_config": {
            "camera_ip": "192.0.2.1",
            "port": 6668,
            "device_id": "test-device",
            "sender_id": "test-sender",
            "local_key": "test-local-key16",
            "hgw_version": "3.3",
            "media_auth_password": "test-media-password",
        },
    }


class RuntimeTests(unittest.TestCase):
    def test_private_files_and_loopback_route(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "runtime"
            config_path = app.prepare({"cameras": [camera()]}, root)
            self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o700)
            for path in root.iterdir():
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            token = hashlib.sha256(b"test-device").hexdigest()
            self.assertTrue((root / f"camera-{token}.json").exists())
            config = json.loads(config_path.read_text())
            self.assertEqual(config["rtsp"]["listen"], "127.0.0.1:38554")
            self.assertEqual(config["api"]["listen"], "127.0.0.1:19845")
            self.assertNotIn("test-media-password", config_path.read_text())
            self.assertIn("Test_camera", config["streams"])

    def test_stale_camera_removed_and_empty_config_keeps_server_available(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app.prepare({"cameras": [camera()]}, root)
            path = app.prepare({"cameras": []}, root)
            self.assertFalse(list(root.glob("camera-*.json")))
            self.assertEqual(json.loads(path.read_text())["streams"], {})

    def test_account_files_merged_without_cloud_config(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "philips_avent_lan_test.json").write_text(json.dumps({"version": 1, "cameras": [camera()]}))
            (root / "philips_avent_bridge_test.json").write_text("not LAN provisioning")
            self.assertEqual(app.load_configuration(root), {"cameras": [camera()]})
            self.assertEqual(len(app.validate({"cameras": [camera(), camera()]})), 1)

    def test_bad_config_rejected_before_writing(self):
        bad = camera()
        bad["lan_config"]["local_key"] = "bad"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "runtime"
            with self.assertRaises(ValueError):
                app.prepare({"cameras": [bad]}, root)
            self.assertFalse(root.exists())


if __name__ == "__main__":
    unittest.main()
