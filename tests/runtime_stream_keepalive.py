"""Exercise camera lifecycle against real HA classes, without camera networking.

Run inside the matching Home Assistant image: python -m unittest
tests.runtime_stream_keepalive. Only the RTSP worker is replaced.
"""

import asyncio
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.components.camera.prefs import DynamicStreamSettings
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from custom_components.philips_avent.camera import AventCamera
from custom_components.philips_avent.const import DPS_PRIVACY_MODE


class StreamKeeperTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.hass = HomeAssistant(self.directory.name)
        self.hass.set_state(CoreState.running)
        self.coordinator = SimpleNamespace(
            lan_only=True, lan_connected=True, data={}, camera_name="Synthetic",
            device_info={}, async_add_listener=MagicMock(return_value=MagicMock()),
        )
        self.camera = AventCamera(self.coordinator, "synthetic")
        self.camera.hass = self.hass
        self.camera.entity_id = "camera.synthetic"
        self.camera.async_write_ha_state = MagicMock()
        self.settings = DynamicStreamSettings()
        self.stream = SimpleNamespace(
            dynamic_stream_settings=self.settings,
            add_provider=MagicMock(), start=AsyncMock(), stop=AsyncMock(),
            async_get_image=AsyncMock(return_value=b"synthetic-jpeg"),
        )

        async def create(_camera):
            _camera.stream = self.stream
            return self.stream

        self.create_patch = patch("homeassistant.components.camera.Camera.async_create_stream", create)
        self.create_patch.start()

    async def asyncTearDown(self):
        await self.camera._async_stop_keeper()
        self.create_patch.stop()
        await self.hass.async_stop(force=True)
        self.directory.cleanup()

    async def drain(self):
        for _ in range(8):
            await asyncio.sleep(0)

    async def add(self):
        await self.camera.async_added_to_hass()
        await self.drain()

    async def update(self):
        self.camera._async_coordinator_updated()
        await self.drain()

    async def test_online_starts_without_viewers_and_reuses_stream_for_snapshots(self):
        await self.add()
        self.stream.start.assert_awaited_once()
        self.assertTrue(self.stream.dynamic_stream_settings.preload_stream)
        self.assertFalse(self.settings.preload_stream)
        self.assertEqual(await self.camera.async_camera_image(320, 180), b"synthetic-jpeg")
        self.stream.async_get_image.assert_awaited_once_with(width=320, height=180)
        self.assertIs(await self.camera.async_create_stream(), self.stream)
        self.stream.stop.assert_not_awaited()

    async def test_offline_boot_waits_then_reconnect_starts_and_disconnect_stops(self):
        self.coordinator.lan_connected = False
        await self.add()
        self.stream.start.assert_not_awaited()
        self.assertIsNone(await self.camera.async_create_stream())
        self.assertIsNone(await self.camera.async_camera_image())
        self.coordinator.lan_connected = True
        await self.update()
        self.stream.start.assert_awaited_once()
        self.coordinator.lan_connected = False
        await self.update()
        self.assertFalse(self.stream.dynamic_stream_settings.preload_stream)
        self.stream.stop.assert_awaited_once()
        self.coordinator.lan_connected = True
        await self.update()
        self.assertEqual(self.stream.start.await_count, 2)

    async def test_privacy_stops_without_changing_camera_setting_and_resumes(self):
        await self.add()
        self.coordinator.data[DPS_PRIVACY_MODE] = "1"
        await self.update()
        self.stream.stop.assert_awaited_once()
        self.assertIsNone(await self.camera.async_camera_image())
        self.assertEqual(self.coordinator.data[DPS_PRIVACY_MODE], "1")
        self.coordinator.data[DPS_PRIVACY_MODE] = "0"
        await self.update()
        self.assertEqual(self.stream.start.await_count, 2)

    async def test_removal_stops_and_late_updates_cannot_restart(self):
        await self.add()
        await self.camera.async_will_remove_from_hass()
        self.stream.stop.assert_awaited_once()
        self.assertIs(self.stream.dynamic_stream_settings, self.settings)
        await self.update()
        self.camera._async_start_keeper(self.hass)
        await self.drain()
        self.assertEqual(self.stream.start.await_count, 1)
        self.assertTrue(self.camera._stream_task.done())

    async def test_setup_failure_retries_on_next_connection_update(self):
        self.stream.start.side_effect = [HomeAssistantError("bridge starting"), None]
        await self.add()
        await self.update()
        self.assertEqual(self.stream.start.await_count, 2)
        self.assertFalse(self.camera._stream_task.done())

    async def test_reconnect_during_stop_is_serialized(self):
        await self.add()
        stopping, release = asyncio.Event(), asyncio.Event()

        async def stop():
            stopping.set()
            await release.wait()

        self.stream.stop.side_effect = stop
        self.coordinator.lan_connected = False
        self.camera._async_coordinator_updated()
        await stopping.wait()
        self.coordinator.lan_connected = True
        self.camera._async_coordinator_updated()
        release.set()
        await self.drain()
        self.assertEqual(self.stream.start.await_count, 2)
        self.assertTrue(self.stream.dynamic_stream_settings.preload_stream)

    async def test_opt_out_and_cloud_mode_remain_on_demand(self):
        self.camera._keep_stream_open = False
        await self.add()
        self.assertIsNone(self.camera._stream_task)
        self.stream.start.assert_not_awaited()
        self.coordinator.lan_only = False
        cloud = AventCamera(self.coordinator, "cloud")
        self.assertFalse(cloud._keep_stream_open)

    async def test_cold_ha_start_defers_worker(self):
        self.hass.set_state(CoreState.starting)
        await self.add()
        self.stream.start.assert_not_awaited()
        self.hass.set_state(CoreState.running)
        self.hass.bus.async_fire("homeassistant_started")
        await self.drain()
        self.stream.start.assert_awaited_once()

    async def test_snapshot_does_not_create_stream_after_unload(self):
        await self.add()
        await self.camera.async_will_remove_from_hass()
        self.assertIsNone(await self.camera.async_create_stream())
        with patch("custom_components.philips_avent.camera.ffmpeg_get_image", side_effect=AssertionError("FFmpeg started")):
            self.assertIsNone(await self.camera.async_camera_image())


if __name__ == "__main__":
    unittest.main(verbosity=2)
