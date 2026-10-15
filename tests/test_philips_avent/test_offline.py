"""Durable provisioning and the integration-to-LAN-App contract."""

import json
import stat
import unittest
from unittest.mock import AsyncMock

from const import sanitize_rtsp_path
from offline import (
    build_lan_bridge_config,
    lan_bridge_filename,
    provision_media,
    validate_lan_devices,
    write_lan_bridge_config,
)
from redact import redact_secrets

CAMERA = {
    "id": "test-camera",
    "name": "Test camera",
    "product_id": "test-product",
    "local_key": "test-local-key16",
    "ip": "192.0.2.10",
    "version": 3.3,
}
# Exactly 16 bytes, entirely fabricated.
CAMERA["local_key"] = "test-local-key16"


class ProvisionTests(unittest.IsolatedAsyncioTestCase):
    async def test_cloud_used_once_then_cached_media_works_without_api(self):
        api = AsyncMock()
        api.get_rtc_config.return_value = {
            "id": CAMERA["id"],
            "p2pType": 4,
            "password": "test-media-password",
            "p2pConfig": {"session": json.dumps({"uid": "test-sender"})},
        }
        cameras = await provision_media(api, [CAMERA], "")
        api.get_rtc_config.assert_awaited_once_with(CAMERA["id"])
        self.assertEqual(await provision_media(None, cameras, ""), cameras)
        payload = build_lan_bridge_config(cameras, sanitize_rtsp_path)
        self.assertEqual(payload["cameras"][0]["stream_name"], "Test_camera")
        config = payload["cameras"][0]["lan_config"]
        self.assertEqual(config["media_auth_password"], "test-media-password")
        self.assertEqual(config["sender_id"], "test-sender")
        self.assertNotIn("sid", json.dumps(payload))
        self.assertNotIn("test-media-password", json.dumps(redact_secrets(cameras)))

    async def test_no_cloud_fallback_for_missing_media(self):
        with self.assertRaises(ValueError):
            await provision_media(None, [CAMERA], "")

    async def test_wrong_camera_is_rejected(self):
        api = AsyncMock()
        api.get_rtc_config.return_value = {"id": "other-test-camera", "p2pType": 4}
        with self.assertRaises(ValueError):
            await provision_media(api, [CAMERA], "test-sender")


def test_offline_camera_does_not_block_metadata_or_other_streams():
    camera = {**CAMERA, "ip": None}
    assert validate_lan_devices([camera])[0]["ip"] is None
    assert build_lan_bridge_config([camera], sanitize_rtsp_path)["cameras"] == []


def test_bridge_export_is_private_atomic_and_does_not_rewrite_unchanged_data(tmp_path):
    path = tmp_path / lan_bridge_filename("test-entry")
    config = {"version": 1, "cameras": []}
    write_lan_bridge_config(path, config)
    before = path.stat()
    write_lan_bridge_config(path, config)
    assert path.stat().st_mtime_ns == before.st_mtime_ns
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    write_lan_bridge_config(path, {**config, "test": "changed"})
    assert json.loads(path.read_text())["test"] == "changed"
    assert not list(tmp_path.glob(".avent-lan-*"))
