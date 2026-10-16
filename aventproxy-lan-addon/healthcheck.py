"""Probe the bridge itself without starting a camera producer."""

import socket
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener


def healthy():
    """Require a responsive management API and RTSP server on host loopback."""
    try:
        # Ignore proxy environment variables: these services are strictly local.
        with build_opener(ProxyHandler({})).open("http://127.0.0.1:19845/api/streams", timeout=3) as response:
            if response.status != 200:
                return False
        with socket.create_connection(("127.0.0.1", 38554), timeout=3) as connection:
            connection.sendall(b"OPTIONS * RTSP/1.0\r\nCSeq: 1\r\n\r\n")
            with connection.makefile("rb") as response:
                return response.readline(256).startswith(b"RTSP/1.0 200 ")
    except (OSError, URLError):
        return False


if __name__ == "__main__":
    raise SystemExit(0 if healthy() else 1)
