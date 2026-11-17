# GOLEM LINK 1.3.1 — Linux remote maintenance

1.3.1 closes the last manual step on Debian: a node installed with
`--allow-remote-updates` can install verified releases with no `sudo` command,
while the agent itself stays unprivileged.

## What changed

- **Opt-in root helper.** The Linux `.run` installer gained
  `--allow-remote-updates`. It installs a root-owned
  `golem-link-update.path` → `golem-link-update.service` pair and the fixed
  helper `/opt/golem-link/bin/golem-link-apply-update`. The path unit watches
  `<data_dir>/updates/remote-update.request`; the helper takes no arguments.
- **The helper only runs verified installers.** It rejects artifacts outside
  `<data_dir>/updates/staged/<version>/`, re-checks SHA-256 and size, copies the
  file into root-only `/opt/golem-link-update`, verifies the copy and runs
  *that copy* with `--upgrade --no-auto-pair --no-firewall`. It cannot execute
  anything else.
- **Existing pairings follow the grant.** Enabling the flag flips
  `allow_control` for already-paired devices; new pairings on such a node
  default to control on. The grant survives upgrades until
  `/etc/golem-link/remote-updates.enabled` is deleted.
- **Honest state reporting.** The new `remote_install` status field tells the
  dashboard whether the helper is available. If the path unit is disabled, the
  agent reports *“the root helper did not pick up the update request”* instead
  of hanging in *Installing*; helper rejections and installer exit codes are
  read from `remote-update.result` and shown after the restart.
- **Autopilot on Linux.** With the helper present, `auto_install` now applies
  the verified release on Linux too, not only on Windows.
- **Revoke path.** `sudo systemctl disable --now golem-link-update.path` and
  `sudo rm /etc/golem-link/remote-updates.enabled`. Uninstall removes the
  helper, units, log and marker.

## Residual risk (read this before enabling it)

While enabled, any paired device you granted remote maintenance can trigger a
root install of any artifact this node accepted. The request path is LAN-only,
pinned-TLS, HMAC-authenticated and audited, and the helper re-verifies the
artifact, but the grant is real. Leave it off if you do not want that trade.

## Install / upgrade

Fresh Debian install with the helper:

```bash
sudo ./GOLEM-LINK-Debian13.run --allow-remote-updates
```

An existing node upgrades with the same flag once (data, identity, password
and pairings are preserved):

```bash
sudo ./GOLEM-LINK-Debian13.run --upgrade --allow-remote-updates --no-auto-pair --no-firewall
```

After that, 1.3.1 upgrades keep the helper by themselves: the marker is
root-owned and survives every later `--upgrade`.

Windows is unchanged apart from the version: `/Update` still installs the
verified release detached and keeps `<install_dir>.previous`.
