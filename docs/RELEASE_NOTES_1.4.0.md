# GOLEM LINK 1.4.0 — Remote operations, link health, heavy results

1.4.0 turns the fleet from "I can push updates" into "I can see and recover
every paired node", and fixes two transport limits that could silently break a
link.

## Remote operations

Each paired device now exposes a read-only Remote panel on the Devices page,
fed by `GET /peer/ops/diagnostics` and `GET /peer/ops/logs?lines=`. It shows
version, platform, hostname, frozen/source mode, uptime, CPU/RAM/disk, task
counts, update state (with `rollback_supported`) and the last 300 log lines.
Everything goes over the same pinned-TLS peer channel and requires the same
`allow_control` grant as updates. There is no shell and no arbitrary command.

Two actions are now real remote actions instead of printed instructions:

- **restart** — the node exits cleanly after a short delay; systemd
  (`Restart=always`, `RestartSec=5`) or the Windows service manager brings it
  back. The caller gets `scheduled: true` and a mode, not a dead connection.
- **rollback** — restores `<install>.previous` over the current install.
  Windows runs a detached SYSTEM task that moves the folders, starts the
  service, and falls back to the newer build if the restore does not start.
  Linux goes through the 1.3.1 root helper with a new `rollback` action, and
  is refused when the helper predates that action or the backup is missing.

Restoring disables `update.enabled` / `auto_stage` / `auto_install` on the
restored node (root-owned JSON edit on Linux, BOM-free PowerShell edit on
Windows), so autopilot cannot immediately re-upgrade the build you reverted.

Both actions require `confirm: true` and are audited as `peer:<name>`.

## Link health

- `PeerRuntime` now tracks `last_error`, `reconnects` and `online_since`.
- A 10-second application ping keeps latency current instead of waiting for the
  idle read timeout; the ping is cancelled with the socket.
- Devices and Network show the last connection error when a peer is offline and
  a reconnect count when it came back, instead of a bare "offline".
- Inbound WebSocket capability pushes now update **all four** grants
  (tasks, storage, update-serving, control). Before, the inbound path dropped
  updates and control, so a permission change on one node could stay invisible
  on the other until a refresh.

## Heavy results

- Task updates larger than 512 KB skip the WebSocket (client frame limit was
  2 MB) and go over HTTP `POST /peer/tasks/update` with a 60-second timeout.
- Results over 4 MB are stored as an explicit
  `{"truncated": true, "original_bytes": n, "note": ...}` marker instead of
  failing the task or closing a socket mid-session.

## Upgrade

Windows upgrades itself from the Updates page or the manifest; Linux 1.3.1
nodes with the root helper upgrade unattended. Nodes without the helper keep
the single `sudo` step. Rollback for Linux requires the helper that ships with
1.4.0, so a rollback of a 1.4.0 install is only offered after the helper is
updated by the same release.

Revoke remote maintenance at any time with:

```bash
sudo systemctl disable --now golem-link-update.path
sudo rm /etc/golem-link/remote-updates.enabled
```
