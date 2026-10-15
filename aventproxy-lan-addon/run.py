"""Watch integration-owned provisioning and supervise the unprivileged LAN bridge."""

import hashlib
import ipaddress
import json
import os
import signal
import subprocess
import tempfile
import time
from pathlib import Path

FIELDS = {"camera_ip", "port", "device_id", "sender_id", "local_key", "hgw_version", "media_auth_password"}


def load_configuration(directory):
    cameras = []
    for path in sorted(directory.glob("philips_avent_lan_*.json")):
        if path.stat().st_size > 1024 * 1024:
            raise ValueError("Oversized provisioning file")
        config = json.loads(path.read_text())
        if not isinstance(config, dict) or config.get("version") != 1 or not isinstance(config.get("cameras"), list):
            raise ValueError("Invalid provisioning format")
        cameras.extend(config["cameras"])
    return {"cameras": cameras}


def validate(options):
    cameras = options.get("cameras")
    if not isinstance(cameras, list):
        raise ValueError("Invalid camera list")
    result = {}
    for camera in cameras:
        if not isinstance(camera, dict) or set(camera) != {"stream_name", "lan_config"}:
            raise ValueError("Invalid camera configuration")
        name = camera["stream_name"]
        if not isinstance(name, str) or not name or any(ord(c) < 32 or c in "/\\?#" for c in name):
            raise ValueError("Invalid stream path")
        config = camera["lan_config"]
        if not isinstance(config, dict) or set(config) != FIELDS:
            raise ValueError("Invalid camera fields")
        address = ipaddress.IPv4Address(config["camera_ip"])
        if address.is_loopback or address.is_multicast or address.is_unspecified:
            raise ValueError("Invalid camera address")
        if type(config["port"]) is not int or not 1 <= config["port"] <= 65535:
            raise ValueError("Invalid camera port")
        if any(not isinstance(config[key], str) or not config[key] for key in FIELDS - {"port"}):
            raise ValueError("Invalid camera text fields")
        if len(config["local_key"].encode()) != 16 or config["hgw_version"] != "3.3":
            raise ValueError("Invalid or unsupported LAN key/protocol")
        token = hashlib.sha256(config["device_id"].encode()).hexdigest()
        if name in result:
            if result[name] != {**config, "token": token}:
                raise ValueError("Conflicting camera stream paths across accounts")
            continue
        result[name] = dict(config)
        # Token is derived locally; owner IDs are never interpolated into exec commands.
        result[name]["token"] = token
    return result


def prepare(options, root):
    cameras = validate(options)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    for old in root.glob("camera-*.json"):
        old.unlink()
    streams = {}
    for name, original in cameras.items():
        config = dict(original)
        token = config.pop("token")
        path = root / f"camera-{token}.json"
        with open(path, "w", opener=lambda p, f: os.open(p, f, 0o600)) as handle:
            json.dump(config, handle)
        streams[name] = f"exec:/usr/bin/python3 /opt/avent/producer.py {token} {{output}}#killsignal=15#killtimeout=5"
    path = root / "go2rtc.json"
    with open(path, "w", opener=lambda p, f: os.open(p, f, 0o600)) as handle:
        json.dump(
            {
                "api": {"listen": "127.0.0.1:19845"},
                "rtsp": {"listen": "127.0.0.1:38554"},
                "webrtc": {"listen": ""},
                "exec": {"allow_paths": ["/usr/bin/python3"]},
                "streams": streams,
                "log": {"level": "warn"},
            },
            handle,
        )
    return path


def stop_child(child):
    if child is not None and child.poll() is None:
        child.terminate()
        try:
            child.wait(timeout=6)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()


def main():
    os.umask(0o077)
    stopping = False

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    child = None
    previous = None
    # Credentials never enter /data, logs, argv or the image. Supervisor backs
    # up the HA integration's durable provisioning along with its config entry.
    with tempfile.TemporaryDirectory(prefix="avent-lan-") as directory:
        root = Path(directory)
        try:
            while not stopping:
                try:
                    options = load_configuration(Path("/homeassistant"))
                    validate(options)
                    current = hashlib.sha256(json.dumps(options, sort_keys=True).encode()).digest()
                except (ValueError, OSError, TypeError):
                    stop_child(child)
                    child = None
                    previous = None
                    print("LAN provisioning unavailable; waiting for the integration.", flush=True)
                else:
                    if child is None or child.poll() is not None or current != previous:
                        stop_child(child)
                        path = prepare(options, root)
                        for item in root.iterdir():
                            os.chown(item, 1000, 1000)
                        os.chown(root, 1000, 1000)
                        env = {
                            key: value
                            for key, value in os.environ.items()
                            if key not in ("SUPERVISOR_TOKEN", "HASSIO_TOKEN")
                        }
                        env.update(AVENT_PUBLISH_HOST="127.0.0.1", AVENT_SECRETS_DIR=str(root))
                        child = subprocess.Popen(
                            ["/usr/local/bin/go2rtc", "-config", str(path)],
                            env=env,
                            user=1000,
                            group=1000,
                            extra_groups=[],
                        )
                        previous = current
                        print("Avent LAN Bridge ready; media starts only when requested.", flush=True)
                for _ in range(50):
                    if stopping:
                        break
                    time.sleep(0.1)
        finally:
            stop_child(child)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
