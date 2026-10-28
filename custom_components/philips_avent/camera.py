"""Camera entity for Philips Avent Baby Monitor."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import replace

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.components.ffmpeg import async_get_image as ffmpeg_get_image
from homeassistant.components.stream import Stream
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.start import async_at_started

from .const import (
    CONF_BRIDGE_HOST,
    CONF_BRIDGE_PORT,
    CONF_KEEP_STREAM_OPEN,
    DEFAULT_BRIDGE_HOST,
    DEFAULT_BRIDGE_PORT,
    DOMAIN,
    DPS_PRIVACY_MODE,
    build_rtsp_url,
)
from .coordinator import PhilipsAventCoordinator
from .entity import build_device_info

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    bridge_port = entry.options.get(CONF_BRIDGE_PORT, DEFAULT_BRIDGE_PORT)
    bridge_host = entry.options.get(CONF_BRIDGE_HOST, DEFAULT_BRIDGE_HOST)
    entities = []
    for cam_id, coordinator in data["coordinators"].items():
        entities.append(AventCamera(
            coordinator, cam_id, bridge_port, bridge_host,
            keep_stream_open=entry.options.get(CONF_KEEP_STREAM_OPEN, True),
        ))
    async_add_entities(entities)


class AventCamera(Camera):
    """Camera entity pointing to the WebRTC bridge RTSP stream."""

    _attr_has_entity_name = True
    _attr_name = "Camera"
    _attr_supported_features = CameraEntityFeature.STREAM

    def __init__(
        self,
        coordinator: PhilipsAventCoordinator,
        cam_id: str,
        bridge_port: int = DEFAULT_BRIDGE_PORT,
        bridge_host: str = DEFAULT_BRIDGE_HOST,
        *,
        keep_stream_open: bool = True,
    ):
        super().__init__()
        self.coordinator = coordinator
        self._cam_id = cam_id
        self._attr_unique_id = f"{cam_id}_camera"
        self._stream_url = build_rtsp_url(
            bridge_host, bridge_port, coordinator.camera_name, cam_id
        )
        self._attr_device_info = build_device_info(coordinator, cam_id)
        self._keep_stream_open = coordinator.lan_only and keep_stream_open
        self._stream_event = asyncio.Event()
        self._stream_task: asyncio.Task[None] | None = None
        self._stream_closing = False
        self._original_stream_settings = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self.coordinator.lan_only:
            self.async_on_remove(self.coordinator.async_add_listener(self._async_coordinator_updated))
        if self._keep_stream_open:
            self.async_on_remove(async_at_started(self.hass, self._async_start_keeper))
            self.async_on_remove(self.hass.bus.async_listen_once(
                EVENT_HOMEASSISTANT_STOP, self._async_stop_keeper
            ))

    @callback
    def _async_coordinator_updated(self) -> None:
        self.async_write_ha_state()
        self._stream_event.set()

    @callback
    def _async_start_keeper(self, _hass: HomeAssistant) -> None:
        if not self._stream_closing and self._stream_task is None:
            self._stream_event.set()
            self._stream_task = self.hass.async_create_background_task(
                self._async_keep_stream_open(), f"Avent stream keeper {self.entity_id}"
            )

    @property
    def _can_stream(self) -> bool:
        return self.available and str((self.coordinator.data or {}).get(DPS_PRIVACY_MODE, "0")) not in ("1", "True")

    async def async_create_stream(self) -> Stream | None:
        if self._keep_stream_open and (self._stream_closing or not self._can_stream):
            return None
        stream = await super().async_create_stream()
        if stream and self._keep_stream_open and self._original_stream_settings is None:
            # Keep our online/offline policy separate from persisted camera prefs.
            self._original_stream_settings = stream.dynamic_stream_settings
            stream.dynamic_stream_settings = replace(stream.dynamic_stream_settings, preload_stream=False)
        return stream

    async def _async_keep_stream_open(self) -> None:
        """Serialize lifecycle changes; HA's worker handles media retry/backoff."""
        try:
            while not self._stream_closing:
                await self._stream_event.wait()
                self._stream_event.clear()
                if self._stream_closing:
                    break
                try:
                    if self._can_stream:
                        if stream := await self.async_create_stream():
                            stream.dynamic_stream_settings.orientation = self._original_stream_settings.orientation
                            stream.dynamic_stream_settings.preload_stream = True
                            stream.add_provider("hls")
                            await stream.start()
                    elif self.stream:
                        self.stream.dynamic_stream_settings.preload_stream = False
                        await self.stream.stop()
                except HomeAssistantError as err:
                    # A later coordinator update retries setup (every 15s in LAN mode).
                    _LOGGER.warning("Could not prepare stream for %s: %s", self.entity_id, err)
        finally:
            if self.stream:
                self.stream.dynamic_stream_settings.preload_stream = False
                await self.stream.stop()
                if self._original_stream_settings is not None:
                    self.stream.dynamic_stream_settings = self._original_stream_settings

    async def _async_stop_keeper(self, _event=None) -> None:
        self._stream_closing = True
        self._stream_event.set()
        if self._stream_task:
            await self._stream_task

    async def async_will_remove_from_hass(self) -> None:
        await self._async_stop_keeper()
        await super().async_will_remove_from_hass()

    @property
    def available(self) -> bool:
        if self.coordinator.lan_only:
            return self.coordinator.lan_connected
        return super().available

    async def stream_source(self) -> str:
        return self._stream_url

    async def async_camera_image(
        self,
        width: int | None = None,
        height: int | None = None,
    ) -> bytes | None:
        """Pull a single JPEG frame from the live RTSP stream.

        Managed LAN streams reuse HA's frame cache. On-demand/cloud cameras
        use the FFmpeg helper, sharing the bridge's existing camera producer.
        """
        if self.coordinator.lan_only and not self._can_stream:
            return None
        try:
            if self._keep_stream_open:
                stream = await self.async_create_stream()
                return await stream.async_get_image(width=width, height=height) if stream else None
            return await ffmpeg_get_image(
                self.hass, self._stream_url, width=width, height=height
            )
        except Exception:
            _LOGGER.exception("ffmpeg snapshot failed for %s", self._stream_url)
            return None
