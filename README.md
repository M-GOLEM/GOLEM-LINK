# GOLEM LINK 1.7.0

GOLEM LINK turns two computers on the same LAN — a Debian 13 laptop and a
Windows 10 desktop — into one workstation. Each machine runs an independent
agent; there is no master node, no cloud account and no telemetry. One dark
dashboard shows both machines side by side, and either machine can run work on
the other **with explicit per-peer permission**.

| Area | What it does |
| --- | --- |
| Dashboard | React/TypeScript UI on TCP 8080: Overview, Devices, Resources, Tasks, Storage, Network, Text Relay, Alerts, Updates, Settings, plus a first-run wizard |
| Discovery | mDNS/DNS-SD (`_golem-link._tcp`) and a UDP beacon on 45873, with manual `IP:port` fallback |
| Pairing | X25519 ECDH + a six-digit code compared by a human on both screens; Ed25519 node identity persisted on disk |
| Transport | Pinned self-signed TLS on TCP 8443, persistent WebSocket, heartbeats, latency and offline detection, automatic reconnect |
| Monitoring | Real CPU/RAM/disk/network samples every two seconds; temperatures only when the platform reports them |
| Scheduling | Balanced / local-first / remote-first policies, a host-light guard that offloads new work to the other machine when this one is busy, priorities, queues, timeouts, bounded retries, health checks, reservations, result sync, history and recovery |
| Storage | Opt-in shares (never a whole drive by default), read-only and delete controls, chunked resumable transfers, SHA-256 verification, progress and cancel |
| Input sharing | Generates configuration for Deskflow or Input Leap (left/right screens, clipboard) and detects whether the tool is installed/running |
| Text relay | Send a copied text to another paired computer and have it placed on that machine's clipboard (automatic when it can, one-click Copy otherwise); "Paste from clipboard" reads this machine's clipboard through the agent when the browser refuses; only text, never files |
| Updates | Autopilot: checks on a schedule, downloads and SHA-256-verifies without a click, installs unattended on Windows, stages on Linux for one sudo command; fleet rollout with a rollback copy at every step |
| Security | scrypt passwords, HMAC session cookies + CSRF token, admin/viewer roles, rate-limited login, audit log, LAN-only request guard, validation on every request |
| Operations | systemd unit on Debian, WinSW-managed Windows service, Private-profile firewall rules, clean uninstall paths |

GOLEM LINK does **not** merge RAM or CPU into a fake supercomputer, does not
migrate running OS processes between machines, and does not kill processes when
a machine is overloaded. Aggregates in the UI are labelled as aggregates.

## Install

Two single-file installers do everything on the target machine: create the
service account, install the agent and dashboard, generate and show the
first-run admin password, open the LAN-scoped firewall, start the service,
find the other machine on the LAN and finish pairing. Nothing has to be typed:
the only manual step is clicking Approve after the six-digit code matches on
both screens. Build output:

| File | Platform | Size |
| --- | --- | --- |
| `dist\GOLEM-LINK-Setup-Windows.exe` | Windows 10 x64 | ~37 MB |
| `dist/GOLEM-LINK-Debian13.run` | Debian 13 x64 (Linux) | ~41 MB (carries its own CPython) |

### Windows 10 desktop

1. Build on a Windows machine with Python 3.11+ and Node.js 18+ (Inno Setup is
   not needed):
   `python packaging\windows\build_setup_exe.py`
   (`packaging\windows\build-installer.ps1` does the same and also refreshes
   the portable bundle).
2. Double-click `dist\GOLEM-LINK-Setup-Windows.exe` and accept the elevation
   prompt. It extracts the agent, registers `golem-link` with WinSW, adds four
   `profile=private remoteip=localsubnet` firewall rules, opens
   <http://localhost:8080>, generates the admin password (shown and saved) and
   pairs with the other machine it finds on the LAN.
3. Optional overrides: `/Password=<pw>`, `/Name=<name>`, `/Pair=<ip>` (skip
   discovery), `/Approve=<code>`, `/NoPair` (install only), `/Update` (upgrade
   in place, keep data), `/Uninstall`, `/Purge`. No flags are needed for the
   normal install.

### Debian 13 laptop

1. Build on any machine with Python 3.11+: `python packaging/linux/build_run.py`.
   It downloads the portable CPython 3.13 runtime once (checksum-pinned) and
   collects the matching manylinux wheels; the installer then installs the
   agent completely offline — no system Python, pip, apt or network on the
   target.
2. Copy `dist/GOLEM-LINK-Debian13.run` to the laptop, mark it executable once
   (`chmod +x`), then run it as root: `sudo ./GOLEM-LINK-Debian13.run`.
   It unpacks its bundled Python to `/opt/golem-link/python`, creates the
   `golem-link` system user, installs the agent into `/opt/golem-link/site`,
   generates and prints the admin password, opens the ports in ufw when it is
   present, enables `golem-link.service`, waits for the dashboard, finds the
   other machine on the LAN and finishes pairing (printing the code that must
   match on both screens).
3. Optional overrides: `--peer 192.168.1.20` (skip discovery),
   `--approve 123456`, `--password PW`, `--no-auto-pair` (install only),
   `--upgrade` (replace the program, keep data and roll back on failure).
   Inspect the file first with `./GOLEM-LINK-Debian13.run --check`.

### Pairing two machines

The six-digit code must be compared by a human on both screens before a peer
is pinned. The installers automate the discovery and the request; approval is
one click in the dashboard. The same steps manually:

```bash
# find and finish pairing automatically (Linux, as the service user)
sudo runuser -u golem-link -- /opt/golem-link/bin/golem-link link auto

# or name the peer / approve a waiting request explicitly
sudo /opt/golem-link/bin/golem-link link init --peer 192.168.1.20
sudo /opt/golem-link/bin/golem-link link approve --code 123456

# see unpaired machines the agent discovered (JSON)
sudo runuser -u golem-link -- /opt/golem-link/bin/golem-link link discover
```

On Windows the same commands are
`"%ProgramFiles%\GOLEM LINK\golem-link.exe" link auto|init|approve|discover`.
They wait for the encrypted link to come online and report the peer name and
node ID.

### Package / portable alternatives

- **Debian .deb**: `bash packaging/linux/build-deb.sh` then
  `sudo apt install ./build/GOLEM-LINK-Debian13.deb` (needs dpkg on the target).
- **Portable Windows bundle**: `dist\GOLEM-LINK\` and
  `GOLEM-LINK-Portable-Windows.zip`; unzip and run
  `GOLEM-LINK\golem-link.exe run --data-dir <path>`. Unsigned, no service.

### Portable / development run

```bash
python3 -m venv .venv
. .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cd apps/dashboard && npm ci && npm run build && cd ../..
golem-link run --data-dir ./dev-data
```

The dashboard is served from `apps/dashboard/dist` in a source checkout, from
`golem_link/web` in a frozen bundle, or from `GOLEM_WEB_DIR` when set.

## First run

Open the dashboard, create the admin password (there is **no default
password**), then follow the eight-step wizard: OS/network detection, discovery,
pairing, screen layout, shares, scheduling policy, autostart, and a real
connection test. Pairing requires the same six-digit code to be shown on both
machines; approve it on the machine that received the request.

## Updates and remote maintenance

The **Updates** page is a first-class part of the dashboard, not an external
updater. Each node can serve its own release to paired devices, pull a newer
release from them, verify it and install it with a rollback path. Nothing is
installed unless its SHA-256 and size match the manifest.

**Autopilot (1.3.0 defaults)**

Updates no longer need a click. `auto_check`, `auto_stage` and `auto_install`
are on by default:

- every `check_interval_s` (6 h by default) the agent asks its sources for a
  newer manifest;
- a newer artifact is downloaded and verified immediately, so *ready* is the
  normal resting state after a release;
- on Windows the verified installer is then run detached (`/Update /Silent`)
  and the service restarts itself;
- on Linux the service cannot escalate (`NoNewPrivileges`). If the node was
  installed with `--allow-remote-updates`, a root-owned one-shot unit applies
  the verified artifact and the service restarts itself; otherwise autopilot
  stops at *ready* and the page shows the one command to run:
  `sudo <artifact> --upgrade --no-auto-pair --no-firewall`.

**Linux remote apply (opt-in, 1.3.1)**

Install once with `sudo ./GOLEM-LINK-Debian13.run --allow-remote-updates` to
add the privileged half:

- `/etc/golem-link/remote-updates.enabled` — root-owned marker.
- `golem-link-update.path` — watches `<data_dir>/updates/remote-update.request`.
- `golem-link-update.service` — oneshot, runs as root.
- `/opt/golem-link/bin/golem-link-apply-update` — fixed helper, **no
  arguments**. It parses the request, rejects anything outside
  `<data_dir>/updates/staged/<version>/`, re-checks SHA-256 and size, copies
  the artifact into `/opt/golem-link-update` (root-only), verifies the copy
  again and runs it as `--upgrade --no-auto-pair --no-firewall`. It cannot run
  arbitrary commands.

The agent still has no privilege: it can only write a request file. The marker
makes existing and future peers default to `allow_control` on that node; the
operator's install command is the standing grant. Revoke at any time:

```bash
sudo systemctl disable --now golem-link-update.path
sudo rm /etc/golem-link/remote-updates.enabled
```

Residual risk: while enabled, any paired device granted remote maintenance can
cause a root install of *any artifact the agent has accepted*. Requests are
LAN-only, pinned-TLS, HMAC-authenticated and audited, but this is a real
privilege grant. It survives upgrades until the marker is removed. The helper
log is `/var/log/golem-link-update.log`.

Turning any switch off in the Autopilot panel takes effect on the next loop
and is persisted; pre-1.3 configs migrate once to these defaults.

**Remote operations (1.4.0)**

Every paired device with *remote maintenance* granted gets a **Remote** button
on the Devices page. It is the same grant the update API uses (`allow_control`),
and every action is written to the audit log as `peer:<name>`:

- **Overview** — version, service state (`systemd` units or the Windows
  service), uptime, CPU/RAM/disk, task counts, database and log sizes, update
  state, and whether that node can roll back.
- **Logs** — the last 300 lines of that node's own `golem-link.log`, bounded to
  256 KB on the reading side.
- **Restart** — Windows uses the service wrapper; Linux exits cleanly and
  systemd (`Restart=always`) starts the agent again in a few seconds. The pair
  link reconnects through the existing backoff loop.
- **Roll back** — Windows runs a detached SYSTEM script that restores
  `<install>.previous`; Linux hands a `rollback` request to the same opt-in
  root helper that applies updates. The helper stops the service, swaps the two
  root-owned program folders, starts the restored build and **disables its
  update loop** so autopilot cannot reinstall the release you just rolled back.
  If the restored copy does not start, the helper puts the newer build back.

The helper's rollback is bounded the same way as its install: it never runs a
command named by the request, only restores `/opt/golem-link.previous` (a
root-owned folder the installer created). Rollback needs a helper from 1.4.0;
on an older helper the dashboard says so instead of guessing.

**Link health (1.4.0)**

Each peer link now samples its own latency every 10 s (application-level ping,
so metrics traffic cannot hide a degrading link) and reports the reconnect
count and the last connection error. The Network page shows both, and the
Devices card shows the error when a peer is offline.

**Heavy task results (1.4.0)**

Task updates larger than 512 KB travel over HTTPS instead of the WebSocket, so
a big result cannot trip the frame limit and take the link down. Results above
4 MB are stored with an explicit `truncated` marker and the original size
instead of being silently cut.

**Shipping a release**

```powershell
python packaging\windows\build_setup_exe.py
python packaging\linux\build_run.py
python packaging\make_update_manifest.py --version 1.7.0 ^
  --windows dist\GOLEM-LINK-Setup-Windows.exe ^
  --linux dist\GOLEM-LINK-Debian13.run --notes-file docs\RELEASE_NOTES_1.7.0.md
```

`dist/updates/` then holds `manifest.json` plus both artifacts. Copy it to a
node's data directory as `updates/`, or point `update.source_dir` at it. A
paired node with *Allow updates* on serves that manifest automatically; other
nodes only need to check.

**How each platform applies an update**

| Platform | Behaviour | Revert path |
| --- | --- | --- |
| Windows | The service runs the verified installer detached (`/Update /Silent /NoPair /NoBrowser`), stops, replaces the program folder and starts again. | `<install_dir>.previous` is kept; a failed start restores it. |
| Linux | The service runs unprivileged with `NoNewPrivileges`, so it **never** elevates. Without the opt-in helper it stages and verifies the artifact and shows one command: `sudo <artifact> --upgrade --no-auto-pair --no-firewall`. With `--allow-remote-updates` a root-owned oneshot unit re-verifies the staged artifact and runs that same command by itself. | The installer backs up `/opt/golem-link` and the unit, and restores both automatically on failure. |

**Permissions**

- *Allow updates on this node* (default on): peers may read the manifest and
  download the verified artifact.
- *Allow remote maintenance* (default **off**, per device): that device may ask
  this node to check, apply a staged update or restart. It is separate from
  task and file permissions and is enforced on the granting node.
- Remote actions use the same pinned-TLS peer channel, are limited to
  `check` / `apply` / `restart` / `rollback`, require confirmation for
  everything that changes the node, and are written to the audit log. The same
  grant opens the read-only diagnostics and log tail on `/peer/ops`.
- **Let peers maintain this node** (button on the Updates page) grants the
  inbound half to every currently paired device in one confirmation. Run it
  once per machine; without it, no peer can drive an update there even when
  its own dashboard offers the buttons.

**Automation**

```bash
golem-link update status
golem-link update check
golem-link update download
golem-link update apply --yes            # Windows launches it; Linux stages it
golem-link update rollback --yes         # restore the previous program folder
golem-link update fleet                  # every controllable peer, then this node
golem-link update fleet --apply --yes
golem-link control gl-peer123 check
golem-link control gl-peer123 apply --yes
golem-link control gl-peer123 rollback --yes
golem-link control self restart --yes
```

All commands talk to the local dashboard API (default port 8080) and accept
`--password` / `--port`. `apply` and `restart` refuse to run without `--yes`,
so an AI or script cannot silently change a machine.

## What changed in 1.7.0

- **"Paste from clipboard" works over plain HTTP.** A browser on the LAN will
  not read the clipboard without a secure context, so the page asks the agent
  (`POST /api/clipboard/read`) and the agent reads the logged-in desktop's
  clipboard directly or through the console-session token. Same switch, admin
  session and CSRF check as the other relay actions, and each read is audited.
- **An unreachable desktop is explained, not hidden.** When no one is logged
  on, or the Linux service cannot reach the X11/Wayland session, the response
  carries the reason; an oversized clipboard is refused instead of truncated.

## What changed in 1.6.1

- **Automatic copy works from the Windows service.** The console-session call
  is now resolved from `kernel32`, where `WTSGetActiveConsoleSessionId` actually
  lives, so a text received while the agent runs in session 0 reaches the
  logged-in desktop instead of failing with a 500.
- **A failed local copy reports, it does not crash.** "Copy here" now returns
  the platform error like every other relay action.

## What changed in 1.6.0

- **A copied text can cross the link.** The new Text Relay page sends short
  text to a paired computer that grants control; the receiving node stores it
  and copies it into the logged-in desktop clipboard. Only text travels — no
  files, no images — and every item shows whether it was copied automatically
  or why not.
- **Automatic copy reaches the real desktop.** The Windows service opens the
  active console session token and runs a hidden, STA PowerShell helper that
  sets the clipboard; when no user is logged on the item waits in the inbox.
  On Linux the agent uses the active X11/Wayland session when it can reach it,
  and otherwise the inbox's Copy button works in the browser's own session.
- **Manual fallbacks are explicit.** "Paste from clipboard" fills the compose
  box, "Copy here" puts the text on the local clipboard, and a failed automatic
  copy leaves a Copy button plus the real error instead of a silent loss.
- **Limits and permissions are enforced on both ends.** 256 KB per text by
  default, 50 items kept, sender and receiver both require the peer's control
  grant, and an older peer is reported as "runs an older GOLEM LINK" instead of
  a raw HTTP error. Relay and auto-copy are switches in Settings → Text relay.

## What changed in 1.5.0

- **The host protects itself automatically.** When this node stays above its
  host-light CPU/RAM limits while running its own task, the lowest-priority
  running task is stopped, requeued and placed on a healthy paired peer. Each
  task moves at most twice, operator-pinned targets are respected, and
  operating-system processes are never touched. The Tasks page shows a
  `preempted` event on the task.
- **Queue priority now ages.** A queued task collects up to 25 priority points
  while it waits (2 per minute by default), so low-priority work cannot starve
  behind a stream of newer high-priority submissions. FIFO still breaks ties.
- **The agent releases its own memory under pressure.** Above 80% RAM it runs a
  garbage collection and asks the OS allocator to reclaim the agent's own pages
  (`EmptyWorkingSet` on Windows, `malloc_trim` on Linux), at most once per
  two minutes. Other applications are never modified; the reclaimed total is
  shown in Settings → Diagnostics.
- **Heavy tasks no longer freeze the desktop.** Workers start below normal
  priority (Windows `BELOW_NORMAL_PRIORITY_CLASS`, Linux `nice 10`), and
  telemetry halves its cadence while CPU is at the warning level instead of
  competing with the work. Both are switchable in Settings → Scheduling.
- **Every new control is in the dashboard.** Settings exposes the move delay
  and per-task cap, the memory-relief threshold and cooldown, worker priority
  and the aging rate/cap; Diagnostics reports relief runs, reclaimed bytes,
  scheduler relief state and the current sampling interval.

## What changed in 1.4.1

- **The Remote panel says what it means.** A peer that predates 1.4.0 gets
  "still runs an older GOLEM LINK" instead of a raw `HTTP 404`, a `403` names
  the grant to turn on, and a failed diagnostics or log request now replaces
  the loader instead of fighting it.

## What changed in 1.4.0

- **Devices can be inspected, not just updated.** Each paired node offers a
  read-only Remote panel: version, service state, uptime, CPU/RAM/disk, task
  counts, update state and 300 lines of its own log — all over the pinned peer
  channel, gated by the same `allow_control` grant. No shell, no arbitrary
  commands.
- **Restart and rollback are now real remote actions.** `restart` exits cleanly
  and lets systemd (`Restart=always`) or the Windows service manager bring the
  node back. `rollback` restores the previous program folder; on Linux it runs
  through the same root helper as install and refuses if the helper predates
  the action. Restoring also pauses the node's own update loop, so autopilot
  cannot immediately re-upgrade the build you just reverted.
- **The link tells you why it broke.** Peers now carry `last_error`, a
  reconnect counter and `online_since`; a 10-second application ping keeps
  latency current instead of waiting for idle timeouts. The Devices and Network
  pages show the last failure in place of a silent "offline".
- **Heavy task results no longer break the WebSocket.** Task updates larger
  than 512 KB skip the WS frame limit and go over HTTP with a longer timeout,
  and results over 4 MB are replaced by an explicit `truncated` marker instead
  of a failed task or a closed socket.
- **Inbound capability pushes carry every grant.** A peer that changes its
  permissions over WebSocket now updates tasks, storage, update-serving and
  control on the other side at once, matching the HTTP capabilities path.

## What changed in 1.3.1

- **Linux can be maintained remotely.** `--allow-remote-updates` installs a
  root-owned oneshot unit plus a fixed, argument-free helper. Paired devices
  can now apply verified releases on Debian with no sudo command at all; the
  agent itself still never elevates. Opt-in and revocable with a marker file.
- **The probe is separate from the privilege.** The path unit only watches a
  request file; the helper re-checks path, SHA-256 and size, copies to a
  root-only directory, verifies the copy and executes that copy only.
- **Existing pairings follow the grant.** Enabling the flag also flips
  `allow_control` for devices already paired, and new pairings on such a node
  default to control on. Upgrades keep the marker until it is deleted.

## What changed in 1.3.0

- **Autopilot updates.** New `update.auto_stage` and `update.auto_install`
  switches are on by default: a newer release is downloaded and verified
  without a click, and on Windows it is installed and the service restarted
  with no interaction. Linux keeps its honest limit — it stages and shows the
  single `sudo` command because the service never elevates.
- **One-time migration.** Configs written before 1.3 have no `auto_stage` key;
  they are upgraded to the hands-free defaults once and then respected,
  including an explicit opt-out.
- **"Let peers maintain this node".** A confirmation on the Updates page
  grants remote maintenance to every paired device at once, so a fresh pair
  does not need per-device permission editing before fleet actions work.
- **Tasks were already hands-free.** Submitting with *Auto* places work on the
  least-loaded healthy machine immediately, and peers accept tasks by default;
  1.3.0 makes the same "no clicking on either machine" promise for updates.

## What changed in 1.2.0

- **Updates are part of the product.** A new Updates page checks a validated
  release manifest, downloads the platform artifact from a peer or from local
  disk, verifies SHA-256 + size, stages it and shows exactly what will happen.
  Staged, wrong-hash and interrupted states are persisted, so a crash during a
  download or an install is reported rather than silently retried.
- **Automatic updates with two honest modes.** Windows can install a verified
  release unattended and restart the service; Linux always waits for one
  operator `sudo` command because the service is unprivileged by design. Both
  keep a rollback copy and restore it if the new version fails to start.
- **Fleet maintenance from the app.** Check, apply and restart paired devices
  from the Updates page. Peers update first and the local node last, so the
  machine serving the artifact stays reachable during a rollout. Devices that
  did not grant remote control are skipped.
- **Two new per-device permissions.** *Let this device pull updates* (default
  on) and *Allow remote maintenance* (default off), exchanged through the
  capability handshake so both sides see what the other allows.
- **AI/automation surface.** `golem-link update …` and `golem-link control …`
  wrap the same dashboard API. Destructive actions require `--yes`; the
  dashboard requires a confirmation dialog.
- **Installer upgrade modes.** Windows `/Update` and Linux `--upgrade` replace
  the program without touching data, password or pairing; both verify the new
  version and roll back automatically if it does not come up.

## What changed in 1.1.0

- **The host stays light by default.** A new *Keep this machine light* guard
  (on by default) checks the host before every placement. When CPU is at or
  above 55% or RAM at or above 70% — both editable in Settings — and the paired
  machine is healthy, new tasks are placed there *immediately*, before they can
  start locally. If no peer can take the work it still runs locally, so nothing
  starves. The placement is recorded as an `offloaded` event in the task
  timeline so it is visible, not silent magic.
- **Placement happens in milliseconds.** Submitting a task now triggers the
  scheduler immediately instead of waiting for the next one-second tick; a
  lock keeps the background tick and the immediate pass from racing.
- **Busy peers are never offload targets.** Remote candidates are ignored when
  their pressure score is ≥ 80, so "keep the host light" cannot dump work onto
  an equally loaded machine.
- **The dashboard shows what weighs.** Resources now lists the top CPU and RAM
  processes (name, PID, utilisation, memory) from each node's live sample, so
  you can see the real culprit. Samples are cached for five seconds and cost
  ~0.04 s per two-second tick.
- **A hard-blocked host refuses even prepared work.** Critical CPU/RAM or a
  sustained thermal limit now stops queued work (local or accepted from a
  peer) from starting until the machine recovers.
- Honest limit, unchanged: GOLEM LINK schedules its own tasks. It never kills,
  suspends or migrates arbitrary operating-system processes.

## What changed in 1.0.2

- **The dashboard no longer freezes or stacks requests.** Every poll now has an
  in-flight guard, the live-metrics stream is in its own context (cards update
  without re-rendering the whole app), and task-event bursts are coalesced into
  one refresh instead of one per progress line. The dashboard woke every 2 s
  and re-fetched everything on every event in 1.0.1.
- **An expired session returns you to the login screen.** The WebSocket now
  accepts and closes with code `4401`, so the dashboard shows "Session
  expired" once instead of retrying an opaque HTTP 403 every few seconds
  forever.
- **mDNS discovery actually runs.** zeroconf calls handlers from its own
  thread; the coroutine was dropped ("never awaited") and peer callbacks never
  fired. It is now scheduled on the asyncio loop.
- **No more cp1252 crash loop in the Windows service log.** Alert messages
  contain "≥", which redirected service stdout could not encode; stdout/stderr
  and the rotating log are now UTF-8 with `errors="replace"`.
- **Temperatures show the GPU when the CPU is not exposed.** Windows does not
  give this process CPU sensors; nvidia-smi prints `[N/A]` on a GT 730, and the
  old float parse aborted the whole GPU list — hence `n/a`. Parsing is now
  tolerant, the card shows e.g. `71°C GPU`, Resources labels the source and
  explains that no CPU reading exists, and overload/thermal protection scores
  from the GPU temperature when platform sensors are absent.
- **First sample is ~0.8 s instead of ~6 s.** The hardware monitor probe is
  gated on the LibreHardwareMonitor process / admin rights and cached; the GPU
  query has a 5 s timeout and no console window.

## What changed in 1.0.1

- **Pairing stores the right identity.** 1.0.0 sent the *other* machine's
  OS/hostname/CPU inside the pairing request, so the approving node saved the
  wrong details for the new peer. Fixed, and every reconnect now re-reads
  `/peer/info` and repairs the stored row automatically — no re-pairing needed
  when both sides upgrade.
- **Task sharing is on after pairing.** `allow_tasks` now defaults to granted
  for newly paired peers; existing databases are migrated, except where the
  operator explicitly turned a peer's tasks off (that decision is kept).
- **Devices page tells the truth.** It shows what each side actually allows
  ("accepts tasks from this device" / "N runs my tasks") plus a one-line
  status when load sharing is blocked and why.
- **"Move queued work away from overloaded nodes" works.** A task pinned to a
  node whose pressure score is ≥ 80 (or that declares itself blocked) is
  re-placed by the normal policy and the event log records
  `rebalanced`. Running processes are still never migrated.
- **Task executors show names**, not hex node IDs.

## Ports

| Port | Protocol | Purpose | Exposure |
| --- | --- | --- | --- |
| 8080 | TCP | Dashboard + REST + WebSocket | LAN only |
| 8443 | TCP | Peer API, TLS-pinned | LAN only |
| 45873 | UDP | Discovery beacon | LAN only |
| 5353 | UDP | mDNS/DNS-SD | LAN only |

The application rejects non-LAN client addresses itself; the firewall recipes
add a second layer. See [docs/INSTALLATION.md](docs/INSTALLATION.md) for exact
`netsh`, `ufw` and `nftables` rules.

## CLI

```
golem-link --version
golem-link run [--data-dir PATH]
golem-link setup [--data-dir PATH] [--name NAME] [--password PASSWORD]
golem-link status [--data-dir PATH]
golem-link doctor [--data-dir PATH]
golem-link link init --peer IP[:PORT] [--password PW] [--wait SECONDS]
golem-link link approve --code 123456 [--password PW] [--wait SECONDS]
golem-link update status|check|download|apply [--yes] [--password PW] [--port 8080]
golem-link update fleet [--apply --yes]
golem-link control NODE check|apply|restart|status [--yes]
```

`status` prints paired peers, share count, task counters and open alerts from
local state. `doctor` prints identity, paths, plugin catalog, input-sharing
detection and ports without starting the agent. `link` drives pairing through
the local dashboard; it reads `GOLEM_ADMIN_PASSWORD` when `--password` is
omitted, so installers never put the password on a command line. `update` and
`control` use the same dashboard API as the Updates page and refuse
`apply`/`restart` without `--yes`.

## Configuration

Runtime configuration lives in `config.json` inside the data directory
(`%ProgramData%\GolemLink` on Windows, `/var/lib/golem-link` for the service,
`~/.local/share/golem-link` for a normal user). Secrets, the node key and the
TLS certificate live in `secrets/` with user-only permissions. The dashboard
writes this file atomically; manual edits are validated on load.
[config.example.json](config.example.json) documents every field.

## Documentation

- [INSTALLATION.md](docs/INSTALLATION.md) — packaging, services, firewall, upgrades, uninstall
- [TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) — discovery, pairing, tasks, transfers, input sharing
- [SECURITY.md](docs/SECURITY.md) — threat model, crypto, permissions, hardening, limitations
- [TEST_REPORT.md](docs/TEST_REPORT.md) — automated results, end-to-end QA log, known gaps

## Tests

```bash
pytest -q
```

The suite includes a real two-node loopback cluster test: two HTTPS peer
servers, real pairing, real remote worker subprocess, bidirectional
checksummed transfers and reconnection after a peer restart. 1.2.0 added 11
update tests; 1.3.0 added autopilot defaults, the status field and the one-time
config migration; 1.3.1 adds the Linux helper protocol (request write, missing
trigger, rejection result and the peer permission default); 1.4.0 adds the
remote-ops suite (rollback guards, scheduled Linux restart, diagnostics shape,
bounded log tail, complete capability pushes, HTTP fallback for large task
updates and truncated results; 1.5.0 adds queue aging, bounded running-task
preemption, memory relief, worker priority and adaptive sampling; 1.6.0 adds the clipboard text
relay with session clipboard writers and inbox, 1.6.1 fixes the Windows
console-session lookup, 1.7.0 adds agent-side clipboard reading for plain-HTTP
browsers; 99 tests total).

## Known limitations

- Input sharing wraps Deskflow/Input Leap; on Wayland, remote input depends on
  the compositor (X11 is the reliable choice, KDE/uinput generally works).
- Discovering a bare `<host>.local` name depends on mDNS being enabled on the
  client; the LAN IP always works and can be entered manually.
- The Windows service runs as `LocalSystem` so it can read operator-approved
  shares anywhere on disk. Share approval, per-peer permissions and
  Private-profile firewall rules bound that access.
- Running user-supplied Python tasks is off by default and must be enabled on
  the executing node.
- On Linux, a task's `memory_mb` becomes a hard address-space limit; on Windows
  it is only a scheduling reservation, because Windows exposes no equivalent
  portable per-process limit.
- The project has not been through a third-party security audit; the
  installers are unsigned unless the operator supplies a code-signing
  certificate.
- An update manifest is trusted because it travels over pinned TLS from a node
  you paired with, and every artifact is checked against it. There is no
  separate signature on the manifest itself; pin the dashboard fingerprint on
  each machine and distribute manifests only from machines you control.
- Automatic install is Windows-only. Linux deliberately requires one
  `sudo <artifact> --upgrade` per machine; the agent never elevates.
- Updates are full installers, not binary deltas, and the release folder must
  be placed on a node by the operator (or served by a paired node) before
  other machines can see it.
