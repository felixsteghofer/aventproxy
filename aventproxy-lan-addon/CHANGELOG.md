# 0.1.1

- Fix repeated Supervisor watchdog restarts by checking bridge health inside the container.
- Check the loopback API and RTSP server without starting media or requiring cameras to be online.
- Keep both listeners private; no additional network ports or configuration are needed.
- Update the App, then enable its Watchdog toggle if it was disabled as a workaround.

# 0.1.0

- Package the tested LAN transport as a Home Assistant App built from pinned source.
- Add on-demand H.264/AAC, shared producers and loopback-only listeners.
- Load camera provisioning automatically from the integration's LAN-only options flow.
- Reload discovered addresses and revoke removed credentials without restarting HA.
