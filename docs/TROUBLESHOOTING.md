# Troubleshooting

Start with `golem-link doctor` (or
`sudo -u golem-link /opt/golem-link/bin/golem-link doctor` on Debian). It
prints the node identity, paths, plugin catalog, input-sharing detection and
configured ports without starting the agent. `golem-link status` adds paired
peers, task counters and open alerts.

Logs:

| Platform | Location |
| --- | --- |
| Agent log | `<data_dir>/logs/golem-link.log` (5 × 5 MiB rotation) |
| Windows service wrapper | `%ProgramData%\GolemLink\logs\golem-link.*.log` |
| Debian | `journalctl -u golem-link` |

## The Linux installer

- **“this build is for x86_64 Debian”** — the file carries an x86_64 CPython.
  Use a matching build (`python packaging/linux/build_run.py` on an x86_64
  machine) for other architectures.
- **No `python3` on the machine is fine** — the installer unpacks its own
  Python to `/opt/golem-link/python` and never calls apt, pip or the network.
  Only `tar`, `sha256sum` and `grep` are required.
- **Where the generated password is** — printed at the end of the install and
  written to `/var/lib/golem-link/initial-password.txt` (0600, root only).
- **Pairing timed out** — the agent keeps running; retry with
  `sudo runuser -u golem-link -- /opt/golem-link/bin/golem-link link auto`,
  or open <http://127.0.0.1:8080> → Devices.
- **Payload/checksum error** — the download was truncated; compare
  `sha256sum GOLEM-LINK-Debian13.run` with the published value and fetch it
  again.

## The dashboard does not load

- **Connection refused** — the agent is not running.
  `systemctl status golem-link` / `Get-Service golem-link`.
- **HTTP 403 “GOLEM LINK is LAN-only”** — the client address is not a
  private/loopback/link-local address. Connect from the LAN interface, or fix
  the firewall/VPN path; this check is intentional and cannot be disabled in
  the UI.
- **Blank page after upgrading** — the web bundle is served from `GOLEM_WEB_DIR`,
  `golem_link/web` or `apps/dashboard/dist`. Rebuild with
  `cd apps/dashboard && npm run build`, or reinstall the package.
- **Login rejected: “too many failed attempts; try again later”** — five failed
  logins from one address within five minutes. Wait for the window to pass.
- **Forgot the admin password** — stop the service, back up `config.json`,
  remove `admin_password_hash`, `admin_password_salt` and set
  `first_run_complete` to `false`, then run
  `golem-link setup --data-dir <data_dir>`. Sessions are signed with
  `secrets/session.key`; deleting that file also invalidates every session.

## Pairing fails

- **The peer is not listed** — check that the other machine is running, both are
  on the same subnet, and Windows treats the network as **Private**. Try the
  manual fallback: enter `<peer-lan-ip>:8443` on the Devices/pairing page.
  mDNS needs avahi + `libnss-mdns` on Debian; the UDP beacon on 45873 is the
  fallback.
- **Code mismatch / pairing aborted** — someone approved a different request, or
  the two machines are not talking to the same peer. Cancel, start a new
  pairing, and compare the six digits before approving.
- **“invalid pairing signature” / certificate errors** — the request was
  tampered with or came from a different node ID. Reject it. If you rebuilt a
  node with a new data directory, it has a new identity and must be paired
  again.
- **“unknown pairing request”** — the request expired (or the agent restarted).
  Start a new pairing. Terminal results are cached for 300 seconds, so normal
  overlapping dashboard polls do not trigger this.
- **Peer shows offline but the machine is up** — check TCP 8443 and UDP 45873
  are open to the LAN, then look for reconnect attempts in the log. The peer
  is marked offline after 20 seconds without heartbeats and reconnect attempts
  continue with backoff.

## Tasks stay queued or fail

- **Queued with a message about permission** — the executing node has not
  granted “accept tasks from this device”. Pairing grants it by default in
  1.0.1; if it was switched off, open Devices on that machine and turn it back
  on. The Devices card shows a red/green line stating exactly which direction
  is blocked.
- **“remote dispatch … task execution is not authorized by the remote node”** —
  permission was revoked after scheduling. The scheduler retries a bounded
  number of times (default 2), then marks the task failed. Re-grant permission
  and hit Retry.
- **Queued forever with no peer online** — distributed tasks need a second node
  with task permission. Local-only work still runs.
- **Plugin unavailable** — `image_resize` needs Pillow inside the agent
  environment; the other plugins have no extra dependencies. Custom Python
  tasks require `scheduling.allow_custom_python=true` on the **executing**
  node.
- **Timeout** — each task has a timeout (`default_timeout_s`, default one hour).
  Timeouts are failures and count toward the retry budget.
- **Overload / thermal** — the node refuses new tasks while CPU/RAM are above
  critical thresholds, or while a critical temperature is sustained. Queued
  work is re-placed, and 1.5.0 can stop and requeue this node's own running task
  (see *Automatic host protection* below). Operating-system processes and other
  applications are never killed or migrated.
- **Work keeps starting on a busy host** — *Keep this machine light* is on by
  default (Settings → Scheduling). It offloads new tasks once CPU reaches 55%
  or RAM 70%. If tasks still run locally, check: the peer is offline or does
  not accept tasks (Devices), the peer is itself above a pressure score of 80,
  or no peer reported a pressure sample yet. The task timeline shows an
  `offloaded` event when the guard acted.
- **Automatic host protection never triggers** — it needs all of: *Protect this
  machine automatically* on, the host above the host-light limits for
  `preempt_after_s` seconds, a running task submitted from this node (not one
  accepted from a peer), no pinned `target_node`, the task below 95% progress,
  and at least one healthy online peer with task permission. The task timeline
  shows a `preempted` event when it acted, and every task moves at most
  `preempt_max_per_task` times.
- **A moved task restarted from zero** — running work is not checkpointed, so a
  preempted task begins again on the peer. Nothing is lost from the queue;
  progress lives only inside the plugin. Keep the host-light limits realistic
  if restarts are expensive.
- **A task keeps coming back to a busy host** — check that a peer is online,
  accepts tasks and reports a pressure score below 80. If no peer can take the
  work, protection deliberately does nothing rather than kill the task with
  nowhere to put it. After `preempt_max_per_task` moves the task stays local
  until it finishes or fails.
- **The host is still heavy but the task list is empty** — GOLEM LINK releases
  its own memory and moves its own tasks, not Chrome, Discord or other OS
  processes. Resources → *Heavy processes* names the top CPU/RAM consumers;
  close or move them yourself.

## Storage and transfers

- **A share is missing** — shares must be added and explicitly approved on the
  machine that owns the files. GOLEM LINK never shares a drive by default.
- **Upload denied** — the share may be read-only, or delete/overwrite may be
  disabled. Check the share flags under Storage.
- **“path escapes the shared root”** — the requested relative path resolves
  outside the share (including through symlinks). Choose a path inside the
  share.
- **“checksum mismatch; partial file discarded”** — the transfer was corrupted
  in transit or the source changed during the transfer. The partial `.glpart`
  file is removed; retry. Resumable transfers continue from the last confirmed
  offset when the server supports it.
- **Download does not appear where expected** — downloads land in
  `<data_dir>/downloads/<peer_node_id>/<share_id>/<relative path>`.
- **Transfer stalls** — check free space on both sides and TCP 8443. Transfers
  are chunked and resumable; cancel and retry to resume from the partial file.

## Input sharing (keyboard and mouse)

GOLEM LINK detects and configures Deskflow/Input Leap; it does not inject input
itself.

- **“not installed”** — install Deskflow (recommended) or Input Leap on both
  machines.
- **Configuration missing** — save a two-screen layout with left/right sides in
  Settings → Input sharing, then generate again. Files are written to
  `<data_dir>/input-sharing/`.
- **Nothing happens on Wayland** — GNOME Wayland restricts remote input. Use an
  X11 session, or KDE with uinput support.
- **Works only after login** — Deskflow/Input Leap must run inside the
  logged-in desktop session, not as the background service. Clipboard sharing
  is provided by that tool.
- **Does not reconnect after sleep or logout** — keep auto-reconnect enabled and
  start the KVM tool through the generated `start-server.*` / `start-client.*`
  script. Those scripts relaunch the tool every three seconds when it exits;
  starting it once by hand has no reconnect behaviour.

## The dashboard returns to the login screen

- **"Session expired — sign in again."** — the session cookie is invalid or
  expired. Since 1.0.2 the WebSocket closes with code `4401` and the dashboard
  returns to the login form instead of retrying in the background. Sign in
  again; if it happens repeatedly, check that the machine clock is correct
  (cookies are validated against their issue time).
- **Dashboard feels slow** — 1.0.2 removed the per-event refresh storm and the
  whole-app re-render on every 2 s metrics sample. If an old build still lags,
  verify the footer version reads `v1.0.2`.

## Alerts and telemetry look wrong

- **Temperatures show a GPU value instead of a CPU value, or `—`** — Windows 10
  does not expose CPU temperature sensors to a service process. Since 1.0.2 the
  card falls back to the GPU temperature reported by `nvidia-smi` (labelled
  `°C GPU`, with a source note on the Resources page) and overload/thermal
  protection scores from it. On machines without a supported GPU there is no
  reading, and the UI shows `—` rather than a fabricated number. Install and
  run LibreHardwareMonitor (admin) if you need CPU sensors; the agent picks up
  its WMI sensors automatically and caches them for 60 s.
- **No temperatures** — the platform reports no usable thermal sensors. GOLEM
  LINK leaves temperature thresholds unset rather than inventing values; set
  `temp_warn_c`/`temp_crit_c` only when real readings exist.
- **Repeated alerts** — alerts use a dedupe key while one is open; acknowledge
  or resolve the condition, then a new occurrence is recorded.
- **Snapshot age warning in the wizard** — samples arrive every two seconds by
  default. Since 1.5.0 adaptive sampling doubles that while CPU stays at the
  warning level (up to `max_sample_interval_s`), so a 4–6 s gap during heavy
  work is expected. A gap far beyond the configured ceiling means the monitor
  task stopped; restart the agent and check the log.
- **Memory relief does nothing visible** — it only reclaims the agent's own
  pages, above `relieve_ram_percent`, once per cooldown. Settings →
  Diagnostics shows the run count and total reclaimed bytes; `0 B` is a valid
  result when the agent had little cached memory. It never touches other
  applications, and it is not a system-wide RAM cleaner.

## Text relay

- **"runs an older GOLEM LINK without text relay"** — the target peer is on an
  older release. Install 1.7.0 on that machine; the sender maps the raw 404 to
  this message on purpose.
- **"Paste from clipboard" reads nothing / "could not read the clipboard"** — on
  plain HTTP the browser refuses clipboard access, so the page asks the agent.
  If the agent cannot reach the desktop (no user logged on, or the Linux
  service cannot reach the X11/Wayland session) the message names that reason;
  paste into the box by hand. Install `xclip` or `wl-clipboard` on Linux.
- **"does not grant control to this node" (or the device is greyed out)** — on
  the target open Devices → permissions and enable *Allow remote maintenance*
  for this peer. Both nodes check the grant.
- **Received but not copied** — the receiving agent could not reach a desktop
  clipboard: no user logged on, or on Linux the service user cannot reach the
  X11/Wayland session. Press **Copy** on the item in Text Relay; that copies in
  the browser's own session. The item keeps the real error (for example
  "no active desktop session was found").
- **Windows copied to the wrong session** — the helper targets the active
  console session. With multiple logged-on users or an RDP session, only the
  console user receives the text automatically; the inbox still has the item.
- **Text is too long** — the default limit is 256 KB
  (`clipboard.max_chars`); send a file through Storage instead.

## Updates and remote maintenance

- **`No build for this platform` (`no_artifact`)** — the manifest is newer but
  lists no artifact for this OS. Rebuild the release with both installers and
  regenerate `manifest.json` with `packaging/make_update_manifest.py`.
- **`Up to date` but a newer release exists** — the manifest is not visible.
  The manifest is read from `update.source_dir` (default `<data>/updates`) and
  from paired peers that set *Allow updates*. Check the source path on the
  Updates page, pair the machine that has the release, and confirm its Devices
  page shows “serves updates”.
- **`has not granted remote control` (403)** — on the target device open
  Devices → permissions and enable *Allow remote maintenance*, or press
  *Let peers maintain this node* on its Updates page to grant every paired
  device at once. It is off by default and is independent from task/file
  permissions.
- **Linux stays at `Verified, ready to install` and nothing happens** — this is
  the designed limit without the opt-in helper: the service runs with
  `NoNewPrivileges` and cannot escalate. Autopilot stages and verifies the
  artifact; run the command shown on the Updates page once
  (`sudo <artifact> --upgrade --no-auto-pair --no-firewall`), or reinstall once
  with `--allow-remote-updates` to let paired devices apply it. On Windows the
  same state is brief because the detached installer starts on its own.
- **An update installed without asking after 1.3.0** — `auto_stage` and
  `auto_install` default to on for existing configs (one-time migration when
  `auto_stage` is absent). Turn them off in the Autopilot panel; the choice is
  persisted and respected on later loads.
- **`this device runs an older agent` (404 on fleet status)** — that machine
  predates 1.2.0. Update it once with the installer; it then appears in the
  fleet table.
- **Download stops at a percentage** — a peer dropped mid-transfer or the file
  did not match the manifest. The partial file is removed and the state returns
  to `available`; retry, or place the artifact beside the manifest and check
  again (local copy is used when present).
- **`failed SHA-256 verification`** — the artifact was modified in transit or
  on disk. The staged file is deleted and the error is shown. Rebuild the
  release, regenerate the manifest and copy both together.
- **Windows: “Installing” never finishes** — the detached task logs to
  `<data>/updates/install.log` and runs as scheduled task `GolemLinkUpdate`.
  Check `schtasks /Query /TN GolemLinkUpdate` and that log. A new agent that
  starts with the expected version flips the state to `Installed`; otherwise
  the state becomes an error naming the unfinished update after the service
  restarts.
- **Windows rollback** — the installer keeps the previous program folder as
  `<install_dir>.previous` and restores it automatically if the new version
  does not answer. To roll back by hand: stop the service, rename the current
  folder aside, move `.previous` back, start the service, or rerun the previous
  installer.
- **Linux: “Verified, ready to install” stays put** — by design unless the node
  was installed with `--allow-remote-updates`. The service runs unprivileged
  with `NoNewPrivileges`, so it never escalates. Run the copyable command from
  the Updates page, or reinstall the `.run` once with `--allow-remote-updates`
  (keep `--no-auto-pair --no-firewall` if you do not want to touch pairing or
  the firewall).
- **Linux: “the root helper did not pick up the update request”** — the path
  unit is not running. `systemctl status golem-link-update.path`,
  `journalctl -u golem-link-update.service`, and confirm
  `/etc/golem-link/remote-updates.enabled` exists and
  `<data>/updates` is writable by `golem-link`. Re-enable with
  `sudo systemctl enable --now golem-link-update.path`.
- **Linux: the helper rejected or failed the request** — read
  `/var/log/golem-link-update.log` and the
  `<data>/updates/remote-update.result` JSON. Rejections are path/hash/size
  mismatches (the staged file changed after verification) or a missing bundled
  Python. Installer failures are restored automatically from
  `/opt/golem-link.previous`; the result file keeps the exit code.
- **Linux rollback** — the upgrade script backs up `/opt/golem-link` to
  `/opt/golem-link.previous` and the unit to
  `/etc/golem-link/golem-link.service.previous`, and restores them if the new
  service fails to start. The rollback command is shown on the Updates page
  whenever the backup exists.
- **Devices → Remote says the peer “still runs an older GOLEM LINK”** — that
  node predates 1.4.0, so it has no `/peer/ops` routes. Update it; the panel
  works as soon as it reconnects with 1.4.x. A `403` instead means *Allow
  remote maintenance* is off on that node.
- **Remote → Roll back is disabled** — rollback needs the program-folder
  backup the installer keeps and, on Linux, a root helper that knows the
  `rollback` action (1.4.0+). Install once with the current `.run`, then the
  button becomes available.
- **A node stopped downloading updates after a rollback** — restoring disables
  `update.enabled`, `auto_stage` and `auto_install` on purpose so autopilot
  cannot reinstall the build you just reverted. Turn them back on in Autopilot
  on that node when it is healthy.
- **Remote → Logs is empty** — the node writes to `<data>/logs/golem-link.log`;
  a source run (not a service) may not have created it yet. The call returns at
  most 1000 lines from the last 256 KB, so very old entries are not shown.
- **Dashboard still shows the old version after an update** — the service was
  restarted; reload the page and sign in again. The Updates page reads
  `current_version` from the new process, not from the browser.
- **Auto-check is on but nothing happens** — checks are jittered and skipped
  while an update is already staged or while updates are disabled. Use
  *Check now*, and remember Linux automatic install needs the opt-in root
  helper (`--allow-remote-updates`).

## Collecting diagnostics for a bug report

```bash
golem-link doctor > doctor.json
golem-link status > status.json
# Windows: %ProgramData%\GolemLink\logs\golem-link.log
# Debian:  journalctl -u golem-link --since "1 hour ago" > service.log
```

Remove peer names, LAN addresses and share paths before posting logs publicly.
Never post `secrets/` (identity key, peer keys, TLS key, session key).
