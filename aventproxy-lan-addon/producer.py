#!/usr/bin/env python3
"""One on-demand LAN session; go2rtc owns viewer sharing and this process's lifetime."""

import contextlib
import ipaddress
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


def main():
    if len(sys.argv) != 3 or not re.fullmatch(r"[a-f0-9]{64}", sys.argv[1]):
        raise SystemExit("Expected camera slot and local go2rtc publish URL")
    slot = sys.argv[1]
    output = urlsplit(sys.argv[2])
    if (
        output.scheme != "rtsp"
        or output.hostname != "127.0.0.1"
        or output.port != 38554
        or output.username
        or output.query
        or output.fragment
    ):
        raise SystemExit("Expected go2rtc local RTSP output URL")
    # go2rtc and the publisher share host loopback; no LAN RTSP listener is needed.
    publish_host = os.environ.get("AVENT_PUBLISH_HOST", "127.0.0.1")
    # Configuration, never a value supplied by an RTSP viewer.
    ipaddress.IPv4Address(publish_host)
    target = urlunsplit(("rtsp", f"{publish_host}:38554", output.path, "", ""))
    stopping = False

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    # No persistent media, logs, or trace files. Each session owns its FIFO directory.
    with tempfile.TemporaryDirectory(prefix=f"avent-{slot}-") as directory:
        env = os.environ.copy()
        env["TMPDIR"] = directory
        env["BABYMONITOR_STREAM_TRACE"] = "/dev/null"
        child = subprocess.Popen(
            [
                "/usr/local/bin/babymonitor-cli",
                "stream",
                "--signaling",
                "lan",
                "--lan-config",
                str(Path(os.environ.get("AVENT_SECRETS_DIR", "/secrets")) / f"camera-{slot}.json"),
                "--rtsp-out",
                target,
            ],
            env=env,
            start_new_session=True,
            stdout=sys.stdout.buffer,
            stderr=subprocess.DEVNULL,
        )
        try:
            while not stopping and child.poll() is None:
                time.sleep(0.1)
        finally:
            # The private group includes the Rust client, FFmpeg and FIFO helper.
            with contextlib.suppress(ProcessLookupError):
                os.killpg(child.pid, signal.SIGTERM)
            try:
                child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(child.pid, signal.SIGKILL)
                child.wait()
        return 0 if stopping else child.returncode


if __name__ == "__main__":
    raise SystemExit(main())
