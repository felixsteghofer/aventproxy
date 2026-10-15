# Philips Avent LAN Bridge

Experimental Home Assistant App for direct LAN video/audio from compatible Philips
Avent cameras. It complements the integration's **LAN only (no cloud access)**
option. The original cloud-assisted WebRTC App remains available separately.

- Authenticated Tuya LAN 3.3 signaling and direct local ICE/KCP/AES media.
- Starts a camera session only when a snapshot or viewer requests it.
- Shares one producer between viewers and stops it after the last consumer leaves.
- Copies H.264 video and encodes mono PCM audio once as AAC for HA HLS playback.
- RTSP `127.0.0.1:38554`; management API `127.0.0.1:19845`.
- Read-only HA configuration mount; no Supervisor, HA API or Docker API access.
- Runs go2rtc, the camera client and FFmpeg as UID/GID 1000.

See [DOCS.md](DOCS.md) for installation, provisioning and limitations.
