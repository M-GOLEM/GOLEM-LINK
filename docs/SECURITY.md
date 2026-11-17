# Security model

GOLEM LINK assumes a **trusted local network** and a threat model where the
main risks are: an unpaired device on the LAN, a malicious or compromised peer,
stolen dashboard credentials, and operator mistakes when exposing files or
compute. It is not designed to be exposed to the internet.

## Identity and pairing

- Each node creates a random `node_id` (`gl-` + 20 hex chars) and a long-lived
  **Ed25519** key on first run, stored in `<data_dir>/secrets/` (`0600`).
- Pairing starts with an **X25519** ephemeral exchange and an Ed25519 signature
  over `node_id|eph_public|req_nonce`. Both sides derive the same key with
  HKDF-SHA256 and display the same **six-digit code**. A human must see the same
  digits on both machines and approve the request locally on the receiving
  node; nothing is paired automatically.
- The pairing secret is stored per peer and used to authenticate later requests
  with HMAC-SHA256 tokens (`node|peer|expiry`, ten-minute lifetime, constant-
  time comparison).
- A node can only pair from a private/loopback/link-local address, with itself
  excluded, and only when no existing pairing with that node ID exists.

## Transport

- The peer API listens on TLS 8443 with a self-signed certificate generated
  locally. After pairing, clients **pin the peer's certificate**
  (`CERT_REQUIRED`, no hostname check because the certificate is pinned to the
  exact peer) and every request also carries an HMAC token.
- The only unauthenticated endpoint is `/peer/info`, which serves the node's
  public key, certificate, fingerprint, version and hardware facts. It contains
  no secrets and is usable only from LAN addresses.
- Discovery data (mDNS/beacon) is unauthenticated by design; it is treated as a
  hint. Fingerprints and certificates are verified during pairing and requests.

## Dashboard

- No default admin password exists. The first visitor must create one (minimum
  eight characters, scrypt N=2^15, r=8, p=1).
- Sessions are HMAC-SHA256 cookies (12-hour TTL, `HttpOnly`, `SameSite=Strict`)
  signed with `secrets/session.key`. Every state-changing request requires the
  per-session `X-CSRF-Token`.
- Two roles: **admin** (full control) and optional **viewer** (read-only).
  Viewer access is disabled until an admin sets a viewer password.
- Login failures are rate-limited per source address (five per five minutes).
  There is no account lockout and no distributed rate limiting.
- HTTP responses set `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
  `Referrer-Policy: no-referrer`, and the dashboard uses a self-only CSP.
- Requests whose client address is not private/loopback/link-local are rejected
  with HTTP 403 (`GOLEM LINK is LAN-only`) before any handler runs.

## Authorization and validation

- Every REST and WebSocket request body is validated with Pydantic v2 models;
  validation failures return field locations and messages, never internal
  objects.
- Admin-only operations are checked server-side (not just hidden in the UI):
  settings, share management, pairing approval, task cancellation, input
  layout, password changes.
- Task execution is **per-peer opt-in at pairing**: approving a pairing request
  grants that peer task submission by default, and either side can revoke it in
  Devices at any time. A revoked node cannot submit work even though it stays
  paired. Storage access is a separate permission. Changes are pushed to the
  owner and enforced on submission.
- Sharing is opt-in per directory with explicit approval recorded in
  `shares.approved_at`. There is no default share of `C:\` or `/`. Read-only and
  delete/overwrite flags are enforced on the server. Paths are resolved and must
  stay inside the share root (symlink-aware).
- Running user-supplied Python is disabled by default
  (`scheduling.allow_custom_python`) and must be enabled on the executing node.
- Uploaded files are written to a `.glpart` temporary file and only renamed
  into place after the size and SHA-256 checksum match; mismatch discards the
  partial file.

## Audit and storage at rest

- Security-relevant events (setup, login, failed login, pairing, share changes,
  settings changes, task lifecycle) are written to the SQLite `audit` and
  `task_events` tables, visible under Alerts/Settings. Audit history is pruned
  to `audit_history_limit` entries.
- Secrets (identity key, TLS key, session key, peer pairing secrets) are stored
  on disk. On Linux the data directory is `0700`/`0600` and the service uses
  `UMask=0077`; on Windows it inherits the `ProgramData` ACL while the service
  runs as `LocalSystem`. They are **not encrypted at rest** and do not use an OS
  keyring; protect the disk and backups accordingly.
- Backups of `<data_dir>` are equivalent to node credentials. Never post
  `secrets/` or the database publicly.

## Service accounts and host hardening

- **Debian:** `golem-link.service` runs as the unprivileged `golem-link` user
  with `NoNewPrivileges`, `PrivateTmp`, `ProtectSystem=full`,
  `ProtectKernelTunables`, `ProtectKernelModules`, `ProtectControlGroups`,
  `ProtectClock`, `ProtectHostname`, `RestrictSUIDSGID`, `LockPersonality` and
  `RestrictRealtime`. `ProtectHome` is intentionally off because approved
  shares may live in `/home`.
- **Windows:** the service runs as `LocalSystem` so it can read the operator's
  approved shares regardless of location. Firewall rules are restricted to
  `profile=private` and `remoteip=localsubnet`. Installers and scripts require
  administrator rights; no run-as passwords are stored.
- **Debian remote updates (1.3.1, opt-in):** `--allow-remote-updates` adds a
  root-owned `golem-link-update.path` → `golem-link-update.service` pair and a
  fixed, argument-free helper. The agent writes a request file; the helper
  independently rejects paths outside `<data>/updates/staged/<version>/`,
  re-checks SHA-256 and size, copies the artifact into root-only
  `/opt/golem-link-update`, verifies the copy and executes that copy as
  `--upgrade`. The agent itself still has no privilege and no way to change
  the helper.
  **Residual risk:** enabling this is a standing grant to apply a root install
  whenever a paired device with `allow_control` asks. A compromised peer or
  agent can therefore cause root code execution through a malicious installer
  that the agent itself accepted. It is off by default, LAN-only, pinned-TLS
  and HMAC-authenticated, audited, and revoked by deleting
  `/etc/golem-link/remote-updates.enabled` and disabling
  `golem-link-update.path`. Upgrades preserve the marker until it is removed.
- **Remote operations (1.4.0):** `/peer/ops/diagnostics` and
  `/peer/ops/logs` are read-only and gated by the same `allow_control` grant;
  they expose hardware counters, task counts, update state and at most 1000 log
  lines (256 KB tail). `/peer/ops/action` accepts only `restart` and
  `rollback`, both require `confirm: true`, and both are audited as
  `peer:<name>`. There is no shell, no file read outside the bounded log tail,
  and no arbitrary command.
  - `restart` schedules a clean `os._exit(0)` after ~1.2 s; the supervisor
    (systemd `Restart=always` or the Windows service) restarts the node. It does
    not run as root.
  - `rollback` restores `<install>.previous`. On Linux it is written as a
    bounded `{"action": "rollback", "nonce": ...}` request to the root helper and is
    refused unless that helper advertises the action (pre-1.4.0 helpers do not)
    and a real backup directory exists. On Windows a detached SYSTEM task moves
    the folders and starts the service, reverting to the newer build if the
    restore fails to start. Both paths then set
    `update.enabled/auto_stage/auto_install` to false on the restored node so
    autopilot cannot re-upgrade it immediately; re-enable from the Updates
    page once the node is healthy.
  - Residual risk: a peer with `allow_control` can now restart or revert a
    node, which is a denial-of-service and supply-chain lever even though it
    cannot execute arbitrary code. Revoke `allow_control` per device in Devices
    if that is not acceptable; the grant is enforced on the receiving node.
- The dashboard is never bound to a public interface by default, but binding is
  configurable (`dashboard.host`). Binding `0.0.0.0` is still LAN-guarded by the
  application, and the firewall recipes add a second layer.

## Reporting and limitations

- This release has not been independently audited, and the binaries are
  unsigned unless you sign them with your own certificate.
- Cookies do not set the `Secure` flag because the dashboard is plain HTTP on
  the LAN (TLS terminates only on the peer API). Use a reverse proxy with TLS if
  you need encrypted dashboard traffic.
- Discovery beacons and mDNS are visible to every device on the LAN; they carry
  node name, ID, fingerprint and version, but no files or credentials.
- Denial of service from a LAN device is possible in principle (the peer API and
  dashboard are always listening). The firewall scope and LAN guard are the
  mitigations; there is no connection-rate limiter on the peer API.
- Deleting a peer's row in the database does not rotate that peer's pairing
  secret on the other side; remove the pairing on both nodes.

Report suspected vulnerabilities privately to the maintainer before opening a
public issue. Include the version, platform, a minimal reproduction and whether
the attacker is on the LAN.
