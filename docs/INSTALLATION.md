# Installation and operations

GOLEM LINK is installed per machine. You need two machines on the same subnet.
Both run the same version; there is no server component.

## Requirements

| | Windows 10 desktop | Debian 13 laptop |
| --- | --- | --- |
| OS | Windows 10 1809+ x64 | Debian 13 (trixie) x64 |
| Runtime | Bundled by the installer (PyInstaller onefile + WinSW) | Bundled by the installer (portable CPython 3.13 — no python3, pip or apt needed) |
| Extra | PowerShell 5.1, LocalSystem service | systemd, avahi-daemon recommended |
| Build tools | Python 3.11+, Node.js 18+, network once (PyInstaller, WinSW) | Python 3.11+ and network once (portable CPython + manylinux wheels) |

Open only these ports, and only to the local subnet:

| Port | Proto | Purpose |
| --- | --- | --- |
| 8080 | TCP | Dashboard / REST / WebSocket |
| 8443 | TCP | Peer API (TLS) |
| 45873 | UDP | Discovery beacon |
| 5353 | UDP | mDNS / DNS-SD |

The application rejects non-LAN client addresses itself; the firewall rules
below add a second layer.

## Single-file installers (recommended)

Both installers are self-contained: no Python or Node.js installation is
needed on the target. The Linux file carries its own portable CPython and
installs the agent from bundled wheels, so a bare Debian 13 with no Python,
no pip and no internet access works. Both create the service, generate and
show the admin password, open the LAN firewall, then find the other machine on
the LAN and finish pairing without any typed input. The six-digit code still
has to match on both screens and the approval is one click in the dashboard.

### Windows: `GOLEM-LINK-Setup-Windows.exe`

Build (Windows build machine, Python 3.11+ and Node.js 18+):

```powershell
python packaging\windows\build_setup_exe.py
# or: powershell -ExecutionPolicy Bypass -File packaging\windows\build-installer.ps1
```

The exe embeds a 30 MB payload (agent + WinSW + service XML). Running it:

| Option | Meaning |
| --- | --- |
| *(no options)* | install, generate the password, find the other machine and finish pairing |
| `/S` or `/Silent` | unattended; identical to the default (kept for scripting) |
| `/InstallDir=<path>` | default `%ProgramFiles%\GOLEM LINK` |
| `/Name=<node name>` | default hostname |
| `/Password=<pw>` | admin password (min 8); otherwise a generated one is printed and saved |
| `/Pair=<ip[:port]>` | pair with a specific machine instead of finding it |
| `/Approve=<code>` | approve a pending request with this six-digit code |
| `/NoPair` | install only; do not look for the other machine |
| `/NoBrowser` | do not open the dashboard |
| `/Uninstall` | stop and remove the service, firewall rules and files |
| `/Purge` | with `/Uninstall`: also delete `%ProgramData%\GolemLink` |
| `--extract-only=<path>` | unpack the payload only; no admin, no service |
| `/Help` | show this list |

Unknown options are refused before elevation, so a mistyped flag cannot start
an install. The service account is `LocalSystem` (needed to read approved
share paths); the firewall rules use `profile=private remoteip=localsubnet`.

### Debian 13: `GOLEM-LINK-Debian13.run`

Build (any machine, Python 3.11+ and network once for the runtime and wheels):

```bash
python packaging/linux/build_run.py
# -> dist/GOLEM-LINK-Debian13.run (portable CPython 3.13 + manylinux x86_64 wheels)
```

The build downloads the official `python-build-standalone` CPython 3.13.18
(stripped, x86_64, sha256-pinned), prunes the parts the agent never uses
(test suites, IDLE, Tk, pip/ensurepip) and appends it to the installer, which
is why the file is ~41 MB.

Run on the laptop:

```bash
chmod +x GOLEM-LINK-Debian13.run
./GOLEM-LINK-Debian13.run --check               # inspect, change nothing
sudo ./GOLEM-LINK-Debian13.run                  # install + find + pair
sudo ./GOLEM-LINK-Debian13.run --peer 192.168.1.20   # specific machine
```

| Option | Meaning |
| --- | --- |
| *(no options)* | install, then find the only other machine and finish pairing |
| `--peer IP[:PORT]` | pair with a specific machine instead of finding it |
| `--approve CODE` | install, then approve a pending request with this code |
| `--no-auto-pair` | install only; do not look for the other machine |
| `--password PW` | admin password (min 8); `GOLEM_ADMIN_PASSWORD` works too |
| `--name NAME` | node name shown on the other machine |
| `--port PORT` | dashboard port used to reach the local agent (default 8080) |
| `--wait SECONDS` | how long to wait for the other machine (default 300) |
| `--firewall-cidr CIDR` | subnet for ufw rules (default: the LAN interface) |
| `--no-firewall` / `--no-start` | skip ufw / skip the systemd start |
| `--allow-remote-updates` | install the root-owned helper so paired devices can apply verified updates without sudo |
| `--check` | verify the payload and print the host facts |
| `--uninstall` / `--purge` | remove the agent; `--purge` also deletes data |

The installer:

1. requires only `tar`, `sha256sum` and `grep` on the machine — no python3,
   pip, apt calls or network access;
2. creates the `golem-link` system user and `/var/lib/golem-link` (0700);
3. unpacks the bundled portable Python to `/opt/golem-link/python` and
   installs the agent's dependencies plus `golem_link` by unzipping the
   bundled wheels into `/opt/golem-link/site` (no pip, no virtualenv), writing
   the launcher `/opt/golem-link/bin/golem-link`;
4. installs the dashboard at `/usr/share/golem-link/web` and the unit at
   `/etc/systemd/system/golem-link.service`;
5. completes the first-run password (`golem-link setup`); a generated password
   is printed and written to `/var/lib/golem-link/initial-password.txt` (0600);
6. adds four ufw rules scoped to the detected LAN subnet (never enables ufw,
   so a remote session is not cut off) and records the subnet in
   `/etc/golem-link/install.env` for clean removal;
7. enables and restarts the service, waits for `/api/setup/status`, then runs
   `link auto`: it listens 12 s for GOLEM LINK machines on the LAN, and when
   exactly one answers it either starts the pairing request (printing the
   six-digit code) or, if that machine already asked first, prints that
   request's code and waits for the approval.

Pairing exit codes: `0` installed (paired, or nothing to pair with yet), `1` a
check or configuration error, `3` installed but a pairing request was started
and the other machine did not approve in time. When that happens the agent
keeps running and pairing can be retried with
`sudo runuser -u golem-link -- /opt/golem-link/bin/golem-link link auto`
(it asks for the admin password).

`link auto` is also available on Windows
(`"%ProgramFiles%\GOLEM LINK\golem-link.exe" link auto`), and
`link discover --wait 8` prints the unpaired machines the agent has seen as
JSON for scripting.

### Pairing workflow

1. Run the installer on both machines. Each installs, generates its password
   and listens 12 s for the other. If the other machine is not installed yet,
   this step simply ends; the running agent keeps advertising.
2. The machine that finds the other one prints `PAIRING CODE: 123456` and
   waits. The other machine shows the same request and code in the dashboard
   (`http://localhost:8080` → Devices / Pairing), which the installer opens.
3. Compare the six digits on both screens, then click **Approve** in the
   dashboard. From a terminal the same step is
   `link approve --code 123456` (Linux: as the `golem-link` user).
4. The initiator reports `Link is online: <name> (<node_id>)` when the pinned
   TLS channel is up. If only pairing is saved, it prints what to check
   (LAN, Private firewall profile, TCP 8443).

If both machines already advertise when an installer runs, the request is
started by that installer; if an incoming request is already waiting, it is
shown instead of creating a second one. When several machines answer, the
installer refuses to guess and points to the dashboard.

The code comparison is mandatory by design: no installer approves pairing on
its own. The automation removes typing, not the human check.

## Windows 10: build and install (from source)

Building needs Python 3.11+, Node.js 18+ and network access once for
PyInstaller and WinSW. Inno Setup 6 (`ISCC.exe`) is only needed for the
optional `-Inno` path.

```powershell
cd F:\GOLEM-LINK
powershell -ExecutionPolicy Bypass -File packaging\windows\build-installer.ps1
```

The script:

1. builds the dashboard if `apps\dashboard\dist` is missing;
2. installs PyInstaller into the active environment and produces
   `dist\GOLEM-LINK\golem-link.exe` with the dashboard embedded, plus
   `dist\GOLEM-LINK-Portable-Windows.zip`;
3. downloads WinSW v2.12.0 into `build\windows\WinSW-x64.exe` (pass
   `-WinSWChecksum <sha256>` to pin it, or place the file there yourself);
4. compiles `dist\GOLEM-LINK-Setup-Windows.exe` from the single-file
   PyInstaller definition (`build_setup_exe.py`); add `-Inno` to compile
   `golem-link.iss` with ISCC instead.

Then run the setup. It installs to `%ProgramFiles%\GOLEM LINK`, creates
Desktop and Start-menu shortcuts, and registers these tasks:

- **Service** — registers `golem-link` with WinSW as an automatic,
  delayed-start service under `LocalSystem`, log rollover under
  `%ProgramData%\GolemLink\logs`.
- **Firewall** — four inbound rules for the four ports above, `profile=private
  remoteip=localsubnet`.

After install, open <http://localhost:8080>.

### Service management

```powershell
$svc = "$env:ProgramFiles\GOLEM LINK\golem-link-service.exe"
& $svc status
& $svc restart
Get-Service golem-link
Get-Content "$env:ProgramData\GolemLink\logs\golem-link.out.log" -Tail 50
```

If the service was removed by hand, register it again from the install folder:

```powershell
& "$env:ProgramFiles\GOLEM LINK\golem-link-service.exe" install
& "$env:ProgramFiles\GOLEM LINK\golem-link-service.exe" start
```

### Manual firewall commands

Equivalent to what the installer runs (review before executing):

```powershell
foreach ($r in @(
  @{ n = "GOLEM LINK Dashboard"; p = "TCP"; port = 8080 },
  @{ n = "GOLEM LINK Peer API";  p = "TCP"; port = 8443 },
  @{ n = "GOLEM LINK Discovery"; p = "UDP"; port = 45873 },
  @{ n = "GOLEM LINK mDNS";      p = "UDP"; port = 5353 }
)) {
  Remove-NetFirewallRule -DisplayName $r.n -ErrorAction SilentlyContinue
  New-NetFirewallRule -DisplayName $r.n -Direction Inbound -Action Allow `
    -Protocol $r.p -LocalPort $r.port -Profile Private -RemoteAddress LocalSubnet
}
```

### Uninstall

Run the installer again with `/Uninstall`. It stops and unregisters the WinSW
service, deletes the four firewall rules and the shortcuts, and removes
`%ProgramFiles%\GOLEM LINK`. `%ProgramData%\GolemLink` (identity key, database,
pairings, logs) is kept intentionally so a reinstall resumes the same node;
add `/Purge` to delete it too:

```powershell
.\GOLEM-LINK-Setup-Windows.exe /Uninstall /Purge
```

The service can also be removed by hand with
`golem-link-service.exe stop` followed by `golem-link-service.exe uninstall`.

## Debian 13: build and install (from source / .deb)

The single-file `.run` above is the recommended path and needs no dpkg and no
Python. To build a Debian package instead (it bundles the same wheels and unit
but still relies on the target's `python3` ≥ 3.11 and `python3-venv`):

```bash
sudo apt install python3 python3-venv python3-pip dpkg-dev nodejs npm
cd /path/to/GOLEM-LINK
bash packaging/linux/build-deb.sh
sudo apt install ./build/GOLEM-LINK-Debian13.deb
```

What the package does:

- installs the agent to `/opt/golem-link` and the dashboard to
  `/usr/share/golem-link/web`;
- ships every Python wheel inside the package and builds the virtualenv in
  `postinst` **offline**;
- creates the `golem-link` system user and `/var/lib/golem-link` (mode 0700);
- installs and starts `golem-link.service` on first install (not on upgrade);
- exposes `/usr/bin/golem-link` for the CLI.

Service controls:

```bash
sudo systemctl status golem-link
sudo journalctl -u golem-link -f
sudo systemctl restart golem-link
sudo -u golem-link golem-link status --data-dir /var/lib/golem-link
```

### Firewall

Use `packaging/linux/firewall-ufw.sh` (review it first) or this nftables
equivalent. Both restrict the ports to one LAN subnet:

```bash
LAN=192.168.1.0/24   # change to your subnet
sudo nft add table inet golem-link
sudo nft add chain inet golem-link input '{ type filter hook input priority 0; policy drop; }'
sudo nft add rule inet golem-link input ct state established,related accept
sudo nft add rule inet golem-link input iif lo accept
sudo nft add rule inet golem-link input ip saddr $LAN tcp dport { 8080, 8443 } accept
sudo nft add rule inet golem-link input ip saddr $LAN udp dport { 45873, 5353 } accept
```

Keep the rules in `/etc/nftables.conf` if you want them to survive reboot.

### Reaching the dashboard by name

Each node publishes `<hostname>.local` over mDNS. To get the documented
`http://golem-link.local:8080`, name the Debian laptop:

```bash
sudo hostnamectl set-hostname golem-link
```

Windows 10 resolves `.local` names natively; Debian needs `libnss-mdns` (a
recommended dependency) and avahi-daemon. If name resolution is unreliable,
use the LAN IP printed by `golem-link doctor` or shown under Network.

### Uninstall

```bash
sudo apt remove golem-link       # stops and disables the service, keeps data
sudo apt purge golem-link        # also removes /opt/golem-link, /var/lib/golem-link and the user
```

For the `.run` install, use `sudo ./GOLEM-LINK-Debian13.run --uninstall`
(add `--purge` to delete `/var/lib/golem-link`). It also deletes the ufw rules
recorded at install time.

## Portable development install

```bash
python3 -m venv .venv
. .venv/bin/activate                  # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cd apps/dashboard && npm ci && npm run build && cd ../..
pytest -q
golem-link run --data-dir ./dev-data
```

Use two different data directories and different ports when running two nodes
on one machine; set `peer.port`, `dashboard.port` and `discovery.beacon_port`
in each `config.json`, and add the peer manually as `127.0.0.1:<peer_port>`
while discovery stays off.

## Configuration

`config.json` is created on first use in the data directory:

| Platform | Data directory |
| --- | --- |
| Windows installer / service | `%ProgramData%\GolemLink` |
| Debian package | `/var/lib/golem-link` |
| Manual run | `$GOLEM_LINK_DATA`, else `~/.local/share/golem-link` (Linux) / `%ProgramData%\GolemLink` (Windows) |

The dashboard writes the file atomically and validates it on load. Manual edits
require a service restart. `config.example.json` documents every field; the
important groups are:

- `dashboard` — bind host/port, session TTL, login rate limit.
- `peer` — peer API bind host/port.
- `discovery` — mDNS/beacon switches, beacon port/interval, `manual_peers`
  (`["192.168.1.23:8443"]`), reconnect interval, offline threshold.
- `scheduling` — policy (`balanced`, `local_first`, `remote_first`),
  concurrency, default timeout, retries/backoff, CPU/RAM reservations,
  enabled plugins, `allow_custom_python` (off by default).
- `thresholds` — warning/critical CPU, RAM, disk-free, latency, packet loss;
  set `temp_warn_c`/`temp_crit_c` only when the hardware reports real
  temperatures. The emergency thermal policy refuses new work after
  `thermal_sustained_samples` critical samples.
- `update` — `enabled` (serve the manifest and accept checks), `auto_check`
  (default on), `auto_stage` (download and SHA-256-verify without a click,
  default on), `auto_install` (apply the verified artifact unattended; Windows
  restarts the service, Linux needs the opt-in root helper or stays at
  *ready*, default on), `channel`,
  `check_interval_s`, `source_dir` (folder holding `manifest.json`; empty uses
  `<data_dir>/updates`). A config written before 1.3.0 has no `auto_stage` key
  and is migrated to these defaults once; existing values are kept afterwards.
- `input_sharing` — enable flag, `deskflow`/`input-leap`, server node, screen
  layout, clipboard sharing.
- `shares` — written by the dashboard after explicit approval; never pre-seed a
  whole drive.

Back up a node by stopping its service and copying the data directory. The
`secrets/` folder contains the node identity and paired-peer keys: restoring it
preserves the node ID and existing pairings. Treat backups as secrets.

## Upgrades

- **Windows** — run the newer installer, or `GOLEM-LINK-Setup-Windows.exe
  /Update /Silent` to upgrade an existing service in place. `/Update` stops the
  service, copies the program folder to `<install_dir>.previous`, reinstalls,
  pairs again only if `/Pair` is passed and restarts. Inno removes the previous
  files and service registration when run interactively; data is preserved.
- **Debian** — `sudo apt install ./GOLEM-LINK-Debian13-<newer>.deb`; `postinst`
  rebuilds the venv when the version changes and keeps the service running.
  The `.run` installer upgrades with `sudo ./GOLEM-LINK-Debian13.run --upgrade
  --no-auto-pair --no-firewall`, which backs up `/opt/golem-link` to
  `/opt/golem-link.previous` and the unit to
  `/etc/golem-link/golem-link.service.previous` and restores them if the new
  version fails to start.

From 1.2.0 the dashboard can do this for you: see **Updates** in the README for
the manifest format, the `GOLEM-LINK-Setup-Windows.exe /Update` automation
flags, the Linux staging + one-`sudo` flow, per-device permissions
(`allow_updates`, `allow_control`) and the CLI (`update`, `control`). Pairing
survives upgrades on both platforms because identities are stored in the data
directory.

From 1.3.0 the whole check → verify → install path is automatic by default on
both platforms (`auto_stage` + `auto_install`), with the Linux `sudo` step as
the only manual action. The Updates page also has *Let peers maintain this
node*: one confirmation that grants `allow_control` to every paired device so
fleet actions work without editing permissions per device (run it once per
machine).

From 1.3.1 a Debian node installed with `--allow-remote-updates` removes that
last manual action: `golem-link-update.path` watches
`<data_dir>/updates/remote-update.request`, and the root-owned
`golem-link-apply-update` helper re-verifies the staged artifact and runs the
installer. Enabling the flag also flips `allow_control` for already-paired
devices and makes new pairings default to it. It survives upgrades; revoke it
with `sudo systemctl disable --now golem-link-update.path && sudo rm
/etc/golem-link/remote-updates.enabled`. The helper log is
`/var/log/golem-link-update.log`, and the request/result files live in
`<data_dir>/updates/`.

### Upgrading from 1.0.0 to 1.0.1

Both machines must run 1.0.1 for the pairing fix to matter; 1.0.0 peers still
work, they just keep the wrong metadata. After the upgrade the agent re-reads
the peer's real OS/hostname/CPU on the next reconnect and repairs its database
row, so the Devices page becomes correct without re-pairing. The database
migration also re-enables task sharing for peers the operator never explicitly
restricted; a peer switched off by hand in 1.0.0 stays off.

