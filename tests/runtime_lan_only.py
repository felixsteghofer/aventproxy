"""Exercise the real integration using HA 2026.10.1, with networking disabled."""

import importlib
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

sys.path[:0] = ["/src", "/src/deps"]
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import UpdateFailed

integration = importlib.import_module("custom_components.philips_avent")
from custom_components.philips_avent.binary_sensor import AventMotionDetected, AventSoundDetected
from custom_components.philips_avent.camera import AventCamera
from custom_components.philips_avent.config_flow import PhilipsAventOptionsFlowHandler
from custom_components.philips_avent.const import CONF_LAN_DEVICES, CONF_LAN_ONLY, DOMAIN
from custom_components.philips_avent.coordinator import PhilipsAventCoordinator
from custom_components.philips_avent.diagnostics import async_get_config_entry_diagnostics
from custom_components.philips_avent.lan import TuyaLANClient
from custom_components.philips_avent.offline import validate_lan_devices

CAMERA = {
    "id": "synthetic-camera",
    "name": "Test camera",
    "product_id": "test-product",
    "local_key": "0123456789abcdef",
    "ip": "192.0.2.10",
    "version": 3.3,
    "sender_id": "synthetic-sender",
    "media_auth_password": "synthetic-media-password",
}


class LocalClient:
    def __init__(self, hass, dev_id, key, on_dps_update, *, ip=None, version=None):
        self.connected = False
        self._ip = ip
        self._version = version
        self.callback = on_dps_update
        self.set_dps = AsyncMock(return_value={"success": True})

    async def start(self):
        self.connected = True

    async def stop(self):
        self.connected = False


class OfflineRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.hass = HomeAssistant(self.directory.name)
        self.entry = MagicMock()
        self.cleanup_callbacks = []
        self.entry.async_on_unload.side_effect = self.cleanup_callbacks.append
        self.entry.entry_id = "synthetic-entry"
        self.entry.data = {CONF_LAN_DEVICES: [dict(CAMERA)], "cameras": [{"id": CAMERA["id"], "name": CAMERA["name"]}]}
        self.entry.options = {CONF_LAN_ONLY: True, "bridge_host": "192.0.2.20", "bridge_port": 38554, "talkback": False}
        self.hass.config_entries = SimpleNamespace(
            async_forward_entry_setups=AsyncMock(),
            async_unload_platforms=AsyncMock(return_value=True),
            async_update_entry=MagicMock(),
        )
        # HA normally installs the frame-reporting helper during full startup;
        # this harness does not start servers or the rest of the instance.
        self.frame_patch = patch("homeassistant.helpers.frame.report_usage")
        self.frame_patch.start()
        self.local_patch = patch("custom_components.philips_avent.coordinator.TuyaLANClient", LocalClient)
        self.local_patch.start()
        self.cloud_patch = patch.object(
            integration, "PhilipsAventAPI", side_effect=AssertionError("Cloud client constructed")
        )
        self.cloud_patch.start()
        self.session_patch = patch.object(
            integration.aiohttp, "ClientSession", side_effect=AssertionError("HTTP session constructed")
        )
        self.session_patch.start()

    async def asyncTearDown(self):
        runtime = self.hass.data.get(DOMAIN, {}).get(self.entry.entry_id)
        if runtime:
            for c in runtime["coordinators"].values():
                await c.stop_lan()
                await c.async_shutdown()
        for cleanup in self.cleanup_callbacks:
            cleanup()
        await self.hass.async_stop(force=True)
        self.local_patch.stop()
        self.cloud_patch.stop()
        self.session_patch.stop()
        self.frame_patch.stop()
        self.directory.cleanup()

    async def boot(self):
        self.assertTrue(await integration.async_setup_entry(self.hass, self.entry))
        return self.hass.data[DOMAIN][self.entry.entry_id]["coordinators"][CAMERA["id"]]

    async def test_cold_start_and_refresh_without_cloud_or_cached_states(self):
        c = await self.boot()
        self.assertIsNone(c.api)
        self.assertEqual(c.data, {})
        self.assertTrue(c.lan_connected)
        self.assertEqual(await c._async_update_data(), {})
        self.hass.config_entries.async_forward_entry_setups.assert_awaited_once()

    async def test_options_provisions_media_once_before_atomic_mode_switch(self):
        await self.boot()
        entry = self.entry
        for key in ("sender_id", "media_auth_password"):
            entry.data[CONF_LAN_DEVICES][0].pop(key)
        api = AsyncMock()
        api.get_rtc_config.return_value = {
            "id": CAMERA["id"], "p2pType": 4, "password": "test-provisioned-password",
            "p2pConfig": {"session": {"uid": "test-provisioned-sender"}},
        }
        self.hass.data[DOMAIN][entry.entry_id]["api"] = api

        class Flow(PhilipsAventOptionsFlowHandler):
            @property
            def config_entry(self):
                return entry

        flow = Flow()
        flow.hass = self.hass
        result = await flow.async_step_init({CONF_LAN_ONLY: True})
        self.assertEqual(result["type"], "create_entry")
        api.get_rtc_config.assert_awaited_once_with(CAMERA["id"])
        update = self.hass.config_entries.async_update_entry.call_args.kwargs
        self.assertTrue(update["options"][CONF_LAN_ONLY])
        self.assertEqual(update["data"][CONF_LAN_DEVICES][0]["media_auth_password"], "test-provisioned-password")

    async def test_export_uses_discovered_lan_address_and_keeps_media_credentials(self):
        import json
        from pathlib import Path
        from custom_components.philips_avent.offline import lan_bridge_filename

        coordinator = await self.boot()
        coordinator._lan_client._ip = "192.0.2.99"
        await integration._async_write_lan_bridge(self.hass, self.entry, {CAMERA["id"]: coordinator})
        path = Path(self.directory.name) / lan_bridge_filename(self.entry.entry_id)
        exported = json.loads(path.read_text())["cameras"][0]
        self.assertEqual(exported["stream_name"], "Test_camera")
        self.assertEqual(exported["lan_config"]["camera_ip"], "192.0.2.99")
        self.assertEqual(exported["lan_config"]["media_auth_password"], CAMERA["media_auth_password"])

    async def test_write_waits_for_actual_camera_state(self):
        c = await self.boot()
        await c.set_dps({"139": True})
        self.assertNotIn("139", c.data)
        c._on_lan_dps_update({"139": True})
        self.assertTrue(c.data["139"])
        c._lan_client.connected = False
        with self.assertRaises(HomeAssistantError):
            await c.set_dps({"139": False})
        with self.assertRaises(UpdateFailed):
            await c._async_update_data()
        c._lan_client.connected = True
        self.assertTrue((await c._async_update_data())["139"])

    async def test_native_sound_and_motion_events_do_not_replay_on_poll(self):
        c = await self.boot()
        sound = AventSoundDetected(c, CAMERA["id"])
        motion = AventMotionDetected(c, CAMERA["id"])
        c._on_lan_dps_update({"139": True, "134": True, "141": "decibel_upload"})
        self.assertTrue(sound._sound_reported())
        self.assertFalse(sound._sound_reported())
        c._on_lan_dps_update({"207": 2200})
        self.assertFalse(sound._sound_reported())
        c._on_lan_dps_update({"250": "motion_detection"})
        self.assertTrue(motion._motion_reported())
        self.assertFalse(motion._motion_reported())
        c._on_lan_dps_update({"141": "decibel_upload"})
        self.assertTrue(sound._sound_reported())

    async def test_diagnostics_does_not_expose_cached_keys_in_lists(self):
        await self.boot()
        diagnostic = await async_get_config_entry_diagnostics(self.hass, self.entry)
        import json

        self.assertNotIn(CAMERA["local_key"], json.dumps(diagnostic))
        self.assertEqual(diagnostic["config_entry"][CONF_LAN_DEVICES][0]["local_key"], "**REDACTED**")

    async def test_offline_unload_never_closes_a_cloud_session(self):
        await self.boot()
        self.assertTrue(await integration.async_unload_entry(self.hass, self.entry))

    async def test_options_cache_all_cameras_and_preserve_bridge_options(self):
        await self.boot()
        entry = self.entry

        class Flow(PhilipsAventOptionsFlowHandler):
            @property
            def config_entry(self):
                return entry

        flow = Flow()
        flow.hass = self.hass
        result = await flow.async_step_init({CONF_LAN_ONLY: True})
        self.assertEqual(result["data"]["bridge_host"], "192.0.2.20")
        self.hass.config_entries.async_update_entry.assert_called_once()
        data = self.hass.config_entries.async_update_entry.call_args.kwargs["data"]
        self.assertEqual(validate_lan_devices(data[CONF_LAN_DEVICES])[0]["local_key"], CAMERA["local_key"])

    async def test_disabling_offline_mode_restores_original_entry_objects(self):
        await self.boot()
        entry = self.entry
        original_data = {k: v for k, v in entry.data.items() if k != CONF_LAN_DEVICES}
        original_options = {k: v for k, v in entry.options.items() if k != CONF_LAN_ONLY}

        class Flow(PhilipsAventOptionsFlowHandler):
            @property
            def config_entry(self):
                return entry

        flow = Flow()
        flow.hass = self.hass
        result = await flow.async_step_init({CONF_LAN_ONLY: False})
        self.assertEqual(result["data"], original_options)
        self.assertEqual(self.hass.config_entries.async_update_entry.call_args.kwargs["data"], original_data)

    async def test_one_offline_camera_does_not_block_the_other_camera(self):
        class PartlyOffline(LocalClient):
            def __init__(self, hass, dev_id, *args, **kwargs):
                super().__init__(hass, dev_id, *args, **kwargs)
                self.offline = dev_id == "offline-camera"

            async def start(self):
                self.connected = not self.offline

        self.entry.data[CONF_LAN_DEVICES].append({**CAMERA, "id": "offline-camera", "ip": "192.0.2.11"})
        with patch("custom_components.philips_avent.coordinator.TuyaLANClient", PartlyOffline):
            await self.boot()
        coordinators = self.hass.data[DOMAIN][self.entry.entry_id]["coordinators"]
        self.assertTrue(coordinators[CAMERA["id"]].last_update_success)
        self.assertFalse(coordinators["offline-camera"].last_update_success)
        coordinators["offline-camera"]._lan_client.connected = True
        await coordinators["offline-camera"].async_refresh()
        self.assertTrue(coordinators["offline-camera"].last_update_success)

    async def test_offline_camera_without_cached_ip_can_be_provisioned(self):
        camera = {**CAMERA, "ip": None}
        self.assertIsNone(validate_lan_devices([camera])[0]["ip"])
        with self.assertRaises(ValueError):
            validate_lan_devices([{**camera, "local_key": ""}])
        with self.assertRaises(ValueError):
            validate_lan_devices([{**camera, "ip": "not an address"}])

    async def test_camera_availability_updates_after_local_reconnection(self):
        c = await self.boot()
        camera = AventCamera(c, CAMERA["id"], 38554, "192.0.2.20")
        camera.hass = self.hass
        camera.async_on_remove = MagicMock()
        camera.async_write_ha_state = MagicMock()
        with patch("homeassistant.components.camera.Camera.async_added_to_hass", new=AsyncMock()):
            await camera.async_added_to_hass()
        c._lan_client.connected = False
        await c.async_refresh()
        self.assertFalse(camera.available)
        camera.async_write_ha_state.assert_called()
        camera.async_write_ha_state.reset_mock()
        c._lan_client.connected = True
        await c.async_refresh()
        self.assertTrue(camera.available)
        camera.async_write_ha_state.assert_called()
        camera.async_on_remove.call_args.args[0]()

    async def test_missing_provisioning_never_starts_cloud_fallback(self):
        self.entry.data = {CONF_LAN_DEVICES: []}
        self.assertFalse(await integration.async_setup_entry(self.hass, self.entry))


class LANCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_camera_readback_updates_state_after_a_write(self):
        async def executor(fn, *args):
            return fn(*args)

        hass = SimpleNamespace(async_add_executor_job=executor)
        callback = MagicMock()
        client = TuyaLANClient(hass, "synthetic", CAMERA["local_key"], callback, ip=CAMERA["ip"])
        device = MagicMock()
        device.set_value.return_value = {"dps": {"139": True}}
        with patch("custom_components.philips_avent.lan.tinytuya.Device", return_value=device):
            self.assertTrue((await client.set_dps({"139": True}))["success"])
        callback.assert_called_once_with({"139": True})

    async def test_rejected_write_is_not_reported_as_success(self):
        async def executor(fn, *args):
            return fn(*args)

        hass = SimpleNamespace(async_add_executor_job=executor)
        client = TuyaLANClient(hass, "synthetic", CAMERA["local_key"], lambda d: None, ip=CAMERA["ip"])
        device = MagicMock()
        device.set_value.return_value = {"Err": "901", "Error": "synthetic failure"}
        with patch("custom_components.philips_avent.lan.tinytuya.Device", return_value=device):
            self.assertIsNone(await client.set_dps({"139": True}))
        device.close.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
