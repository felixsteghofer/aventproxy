# Setup

1. Add `https://github.com/felixsteghofer/aventproxy` to **Settings → Apps → App
   Store → Repositories** and install **Philips Avent LAN Bridge**. The first
   installation builds the Rust camera client from pinned source; this can take
   several minutes on a Raspberry Pi.
2. Install the integration from this fork and complete its normal account setup.
   This one-time provisioning still needs the Philips/Tuya cloud.
3. Open the integration's **Configure** dialog. Set bridge host to `127.0.0.1`,
   port to `38554`, disable talkback, and enable **LAN only (no cloud access)**.
   The flow automatically saves local control/media credentials. No camera IDs,
   keys, YAML or App options need to be copied by hand.
4. Stop the original WebRTC Bridge App before starting this App: both use port
   `38554`. Start the LAN App, verify a snapshot/live feed, then enable automatic
   startup and watchdog if desired.

Camera entity IDs, existing RTSP paths and dashboards stay the same. The App reads
integration-generated, mode-0600 `philips_avent_lan_<entry-id>.json` files through
its read-only HA config mount. Local discovery updates camera addresses; the App
reloads changed provisioning without restarting HA. Cameras without a known LAN
address are omitted until local discovery finds them. Multiple integration entries
are merged; conflicting stream names are rejected.

The watchdog uses Docker's in-container health check to probe the loopback API and
RTSP server. It does not request media or contact cameras: monitors can be powered
off whenever they are not needed. Supervisor can restart an unhealthy bridge with
the Watchdog toggle enabled. Version 0.1.1 replaces the old host-address TCP probe,
which could not reach the private listener; re-enable Watchdog after updating if
you previously disabled it to stop repeated restarts.

# What still needs the cloud?

The initial account login and one-time media credential provisioning require cloud
access. In LAN-only mode, the integration does not construct a cloud client, poll
cloud state, sync commands to the cloud or refresh sessions. The App reads cached
LAN credentials and has no cloud fallback. Wi-Fi RSSI is unavailable in this mode.
Settings begin unknown after startup until the camera reports them locally.

Camera reset/re-pairing can require fresh provisioning: disable LAN-only mode,
complete the normal online setup again, then re-enable it. Credentials remain in
HA's private integration configuration and its backups. Treat backups as private.
The App keeps derived configuration in temporary, owner-only files and does not
record camera video or audio.

# Supported/tested scope

The media transport is experimental and currently exports only Tuya LAN 3.3
cameras. SCD923/26 was tested with 1080p H.264 and 16-kHz mono AAC. H.264 is copied,
not re-encoded. Talkback is not implemented in this App; leave that integration
option off. The App uses fixed loopback ports `38554` and `19845`.

The original local prototype was verified on a Raspberry Pi 4 with HA 2026.10.1:
strict video/audio decode, snapshot retrieval, advancing native HLS playback, and
on-demand producer cleanup. Bridge usage was about 16% of one Pi CPU core and
47 MiB during playback, and 26 MiB / near-zero CPU when idle. This fork additionally
automates provisioning/export instead of relying on prototype App options.

Real motion/sound events and recovery after camera reboot **with the camera's WAN
blocked** have not been verified. The code supports native local event messages;
it does not analyze a continuous video/audio feed to synthesize alarms. The local
client's pinned source does not establish indefinite media-key validity across
reset/re-pairing.

# Rollback

Stop the LAN App, disable LAN-only mode in the integration's Configure dialog and
start the original WebRTC Bridge App. Preserve your existing bridge host/port if
the original bridge runs elsewhere. Disabling LAN-only removes its cached local
provisioning and deletes its App export; it does not change entity IDs.

Installing an upstream HACS integration update can replace this fork's LAN-only
implementation. Review updates before relying on offline operation.

# Development

The Dockerfile downloads `babymonitor-client` commit
`61439ccdb7328ddd7d8183ed981d073d777988e8`, verifies its archive SHA-256, applies
`live-probe.patch` and builds with Rust 1.90.0. go2rtc v1.9.14 downloads are also
SHA-256 checked for amd64/aarch64. No prebuilt local artifacts or secrets are
required to build a fresh checkout.

`live-probe.patch` adds direct RTSP/AAC publishing, receive-time timestamps and
bounded live probing, while retaining explicit timestamps for finite replay. It
is derived from the owner-tested bridge implementation. Upstream source and
licenses: [babymonitor-client](https://github.com/eisbaw/babymonitor-client) (MIT),
[go2rtc](https://github.com/AlexxIT/go2rtc) (MIT). Original notices are included in
the runtime image. FFmpeg is supplied by Debian bookworm.

Run offline App tests with:

```sh
python3 -m unittest discover -s aventproxy-lan-addon -p 'test_*.py' -v
```

The repository CI also runs integration unit tests, real-HA LAN-only runtime tests
with networking disabled, and a native amd64 Docker build/startup smoke test.
