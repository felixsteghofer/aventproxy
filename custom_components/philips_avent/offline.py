"""Validate durable LAN provisioning without persisting cloud device payloads."""

from __future__ import annotations

import json
import os
import tempfile
from ipaddress import ip_address

LAN_BRIDGE_PREFIX = "philips_avent_lan_"


def lan_bridge_filename(entry_id: str) -> str:
    """Use a separate namespace so the cloud bridge cannot consume LAN keys."""
    if not entry_id or not entry_id.replace("-", "").isalnum():
        raise ValueError("Invalid entry identity")
    return f"{LAN_BRIDGE_PREFIX}{entry_id}.json"


def validate_lan_devices(devices: list[dict]) -> list[dict]:
    """Reject incomplete provisioning; never include credential values in errors."""
    if not devices:
        raise ValueError("No provisioned cameras")
    seen = set()
    result = []
    for camera in devices:
        if not isinstance(camera, dict):
            raise ValueError("Invalid camera metadata")
        cam_id = camera.get("id")
        key = camera.get("local_key")
        host = camera.get("ip")
        if not isinstance(cam_id, str) or not cam_id or cam_id in seen:
            raise ValueError("Invalid camera identity")
        if not isinstance(key, str) or len(key.encode()) != 16:
            raise ValueError("Missing camera local key")
        address = None
        if host is not None:
            try:
                address = ip_address(host)
            except (ValueError, TypeError) as err:
                raise ValueError("Invalid camera LAN address") from err
            if address.version != 4 or address.is_unspecified or address.is_multicast or address.is_loopback:
                raise ValueError("Invalid camera LAN address")
        # A powered-off camera still has a durable identity/key. Without a
        # cached IP, the existing LAN discovery loop finds it when it returns.
        version = camera.get("version", 3.3)
        if version not in (3.1, 3.2, 3.3, 3.4, 3.5):
            raise ValueError("Unsupported LAN protocol")
        seen.add(cam_id)
        validated = {
            "id": cam_id,
            "name": camera.get("name") or cam_id,
            "product_id": camera.get("product_id", ""),
            "local_key": key,
            "ip": str(address) if address is not None else None,
            "version": version,
        }
        for field in ("sender_id", "media_auth_password"):
            value = camera.get(field)
            if value is not None:
                if not isinstance(value, str) or not value:
                    raise ValueError("Invalid LAN media provisioning")
                validated[field] = value
        result.append(validated)
    return result


def provision_from_coordinators(stored: list[dict], coordinators: dict, previous: list[dict]) -> list[dict]:
    """Snapshot only keys/metadata, never cloud DPS or uploaded-media URLs."""
    cached = {cam["id"]: cam for cam in previous}
    cameras = []
    for camera in stored:
        cam_id = camera["id"]
        coordinator = coordinators.get(cam_id)
        known = dict(cached.get(cam_id, {}))
        if coordinator is not None:
            known.update(coordinator.lan_provisioning)
        known.update({"id": cam_id, "name": camera["name"], "product_id": camera.get("product_id", "")})
        cameras.append(known)
    return validate_lan_devices(cameras)


async def provision_media(api, cameras: list[dict], fallback_uid: str) -> list[dict]:
    """One-time authenticated provisioning in the options flow, never at LAN startup."""
    result = []
    for original in cameras:
        camera = dict(original)
        if not camera.get("sender_id") or not camera.get("media_auth_password"):
            if api is None:
                raise ValueError("Enable cloud mode to provision LAN media first")
            rtc = await api.get_rtc_config(camera["id"])
            if not isinstance(rtc, dict) or rtc.get("id") != camera["id"] or rtc.get("p2pType") != 4:
                raise ValueError("Unsupported LAN media device")
            p2p = rtc.get("p2pConfig", {})
            if isinstance(p2p, str):
                p2p = json.loads(p2p)
            if not isinstance(p2p, dict):
                raise ValueError("Invalid RTC configuration")
            session = p2p.get("session", {})
            if isinstance(session, str):
                session = json.loads(session)
            if not isinstance(session, dict):
                raise ValueError("Invalid RTC session")
            camera["sender_id"] = session.get("uid") or fallback_uid
            camera["media_auth_password"] = rtc.get("password")
            if not camera["sender_id"] or not camera["media_auth_password"]:
                raise ValueError("Incomplete LAN media provisioning")
        result.append(camera)
    return validate_lan_devices(result)


def build_lan_bridge_config(cameras: list[dict], path_builder) -> dict:
    """Export only local media credentials; keep the existing RTSP entity paths."""
    streams = []
    for camera in validate_lan_devices(cameras):
        if not camera["ip"] or not camera.get("sender_id") or not camera.get("media_auth_password"):
            continue  # Offline cameras become streamable when LAN discovery finds them.
        if camera["version"] != 3.3:
            continue  # The experimentally verified media transport is Tuya LAN 3.3.
        streams.append(
            {
                "stream_name": path_builder(camera["name"], camera["id"]),
                "lan_config": {
                    "camera_ip": camera["ip"],
                    "port": 6668,
                    "device_id": camera["id"],
                    "sender_id": camera["sender_id"],
                    "local_key": camera["local_key"],
                    "hgw_version": str(camera["version"]),
                    "media_auth_password": camera["media_auth_password"],
                },
            }
        )
    return {"version": 1, "cameras": streams}


def write_lan_bridge_config(path, config: dict) -> None:
    """Atomic, owner-only, and unchanged files are not rewritten on every LAN push."""
    payload = json.dumps(config, sort_keys=True)
    if path.exists() and path.read_text() == payload:
        os.chmod(path, 0o600)
        return
    fd, name = tempfile.mkstemp(prefix=".avent-lan-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(payload)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)
