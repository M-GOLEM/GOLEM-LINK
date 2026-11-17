#!/usr/bin/env bash
# GOLEM LINK @VERSION@ — one-file installer for Debian 13 (Debian 12 / Ubuntu 24.04+ also work).
#
# Usage:
#   sudo ./GOLEM-LINK-Debian13.run [options]
#
# Options:
#   --peer IP[:PORT]      pair with a specific machine (default: find it on the LAN)
#   --approve CODE        approve a pending pairing request with its six-digit code
#   --no-auto-pair        install only; do not look for the other machine
#   --password PW         admin dashboard password (min 8). GOLEM_ADMIN_PASSWORD works too
#   --name NAME           node name shown on the other machine (default: hostname)
#   --port PORT           dashboard port used to reach the local agent (default 8080)
#   --wait SECONDS        how long to wait for the other machine (default 300)
#   --firewall-cidr CIDR  LAN subnet for ufw rules (default: detected from the LAN address)
#   --no-firewall         do not touch ufw
#   --no-start            install, but do not start the systemd service
#   --allow-remote-updates
#                         install a root-owned helper so paired devices can
#                         apply verified updates without sudo (survives upgrades
#                         until you delete /etc/golem-link/remote-updates.enabled)
#   --check               inspect this file and the machine; change nothing
#   --upgrade             replace an existing installation; keeps data, does not
#                         pair, stops the service first and keeps the previous
#                         /opt/golem-link as /opt/golem-link.previous
#   --uninstall           stop and remove the agent (keeps /var/lib/golem-link)
#   --purge               with --uninstall: also delete identity, peers, shares and history
#   -h, --help            show this help
#
# What it does, in order: creates the golem-link system user, unpacks the
# bundled portable Python 3.13 (the machine needs no python3, pip, apt or
# network access), installs the agent offline from bundled wheels, completes
# the first-run admin password, opens the LAN-scoped firewall ports, enables
# the systemd service, waits for the dashboard, then pairs with the other
# machine: it is found automatically on the LAN (no arguments needed), or
# addressed with --peer / approved with --approve. The six-digit code is still
# compared by a human on the two machines before a peer is pinned.
set -euo pipefail

VERSION="@VERSION@"
APP_DIR=/opt/golem-link
DATA_DIR=/var/lib/golem-link
PY="$APP_DIR/python/bin/python3.13"
SITE="$APP_DIR/site"
BIN="$APP_DIR/bin/golem-link"
UNIT=/etc/systemd/system/golem-link.service
PAYLOAD_MARKER='#__GOLEM_LINK_PAYLOAD_BELOW__'
ORIG_ARGS=("$@")
WORK=""

MODE=install
PEER=""
APPROVE=""
PASSWORD="${GOLEM_ADMIN_PASSWORD:-}"
PASSWORD_SUPPLIED=0
NODE_NAME=""
PORT=8080
WAIT_S=300
FIREWALL=1
START=1
AUTO_PAIR=1
PURGE=0
CHECK=0
REMOTE_UPDATES=0
CIDR=""

say() { printf '%s\n' "$*"; }
die() { printf 'GOLEM LINK: %s\n' "$*" >&2; exit 1; }

usage() {
    cat <<'USAGE'
GOLEM LINK one-file installer for Debian 13.

Usage:
  sudo ./GOLEM-LINK-Debian13.run [options]

Options:
  --peer IP[:PORT]      pair with a specific machine (default: find it on the LAN)
  --approve CODE        approve a pending pairing request with its six-digit code
  --no-auto-pair        install only; do not look for the other machine
  --password PW         admin dashboard password (min 8). GOLEM_ADMIN_PASSWORD works too
  --name NAME           node name shown on the other machine (default: hostname)
  --port PORT           dashboard port used to reach the local agent (default 8080)
  --wait SECONDS        how long to wait for the other machine (default 300)
  --firewall-cidr CIDR  LAN subnet for ufw rules (default: detected)
  --no-firewall         do not touch ufw
  --no-start            install, but do not start the systemd service
  --allow-remote-updates
                        let paired devices apply verified updates via a root helper
  --check               inspect this file and the machine; change nothing
  --upgrade             replace an existing installation, keep data and rollback
  --uninstall           stop and remove the agent (keeps /var/lib/golem-link)
  --purge               with --uninstall: also delete identity, peers, shares and history
  -h, --help            show this help
USAGE
}

while [ $# -gt 0 ]; do
    case "$1" in
        -h|--help) usage; exit 0 ;;
        --check) CHECK=1 ;;
        --upgrade) MODE=upgrade ;;
        --uninstall) MODE=uninstall ;;
        --purge) PURGE=1 ;;
        --no-firewall) FIREWALL=0 ;;
        --no-start) START=0 ;;
        --allow-remote-updates) REMOTE_UPDATES=1 ;;
        --no-auto-pair) AUTO_PAIR=0 ;;
        --peer) [ $# -ge 2 ] || die "--peer needs an address"; PEER="$2"; shift ;;
        --peer=*) PEER="${1#*=}" ;;
        --approve) [ $# -ge 2 ] || die "--approve needs a code"; APPROVE="$2"; shift ;;
        --approve=*) APPROVE="${1#*=}" ;;
        --password) [ $# -ge 2 ] || die "--password needs a value"; PASSWORD="$2"; PASSWORD_SUPPLIED=1; shift ;;
        --password=*) PASSWORD="${1#*=}"; PASSWORD_SUPPLIED=1 ;;
        --name) [ $# -ge 2 ] || die "--name needs a value"; NODE_NAME="$2"; shift ;;
        --name=*) NODE_NAME="${1#*=}" ;;
        --port) [ $# -ge 2 ] || die "--port needs a value"; PORT="$2"; shift ;;
        --port=*) PORT="${1#*=}" ;;
        --wait) [ $# -ge 2 ] || die "--wait needs a value"; WAIT_S="$2"; shift ;;
        --wait=*) WAIT_S="${1#*=}" ;;
        --firewall-cidr) [ $# -ge 2 ] || die "--firewall-cidr needs a value"; CIDR="$2"; shift ;;
        --firewall-cidr=*) CIDR="${1#*=}" ;;
        *) die "unknown option: $1 (use --help)" ;;
    esac
    shift
done

case "$PORT" in ''|*[!0-9]*) die "--port must be a number" ;; esac
case "$WAIT_S" in ''|*[!0-9]*) die "--wait must be a number of seconds" ;; esac
if [ -n "$APPROVE" ]; then
    case "$APPROVE" in *[!0-9]*) die "--approve must be a six-digit number" ;; esac
    [ "${#APPROVE}" -eq 6 ] || die "--approve must be exactly six digits"
fi
[ -z "$PASSWORD" ] || [ "${#PASSWORD}" -ge 8 ] || die "the admin password must be at least 8 characters"
# Remote updates are root-owned opt-in: once enabled they survive upgrades.
if [ -f /etc/golem-link/remote-updates.enabled ]; then
    REMOTE_UPDATES=1
fi
if [ "$MODE" = upgrade ]; then
    [ -z "$PEER$APPROVE" ] || die "--upgrade does not pair; remove --peer/--approve"
    AUTO_PAIR=0
fi

payload_offset() {
    local line bytes
    line=$(grep -a -n -m1 "^${PAYLOAD_MARKER}$" "$0" | cut -d: -f1)
    [ -n "$line" ] || die "this file is truncated (payload marker missing); download it again"
    bytes=$(head -n "$line" "$0" | wc -c | tr -d ' ')
    printf '%s' "$bytes"
}

if [ "$CHECK" = 1 ]; then
    say "GOLEM LINK installer $VERSION"
    say "Host: $(uname -s) $(uname -m)"
    say "Bundled Python: CPython 3.13 (portable; no system python3 needed)"
    OFFSET=$(payload_offset)
    LIST=$(tail -c +$((OFFSET + 1)) "$0" | tar -tzf -) || die "the payload is corrupt; download the file again"
    say "Payload files: $(printf '%s\n' "$LIST" | wc -l | tr -d ' ')"
    say "Bundled wheels: $(printf '%s\n' "$LIST" | grep -c '^wheels/.*\.whl$')"
    printf '%s\n' "$LIST" | grep -q '^web/index.html$' || die "the dashboard is missing from the payload"
    printf '%s\n' "$LIST" | grep -q '^golem-link.service$' || die "the systemd unit is missing from the payload"
    printf '%s\n' "$LIST" | grep -q '^golem-link-apply-update$' || die "the remote-update helper is missing from the payload"
    printf '%s\n' "$LIST" | grep -q '^golem-link-update.path$' || die "the remote-update path unit is missing from the payload"
    printf '%s\n' "$LIST" | grep -q '^golem-link-update.service$' || die "the remote-update unit is missing from the payload"
    printf '%s\n' "$LIST" | grep -q '^python-runtime.tar.gz$' || die "the portable Python runtime is missing from the payload"
    say "Check complete: this is a valid GOLEM LINK installer."
    exit 0
fi

if [ "$(id -u)" -ne 0 ]; then
    if command -v sudo >/dev/null 2>&1; then
        say "Administrator rights are required; re-running with sudo..."
        exec sudo --preserve-env=GOLEM_ADMIN_PASSWORD -- "$0" "${ORIG_ARGS[@]}"
    fi
    die "run this file as root (sudo ./GOLEM-LINK-Debian13.run)"
fi

firewall_remove() {
    local cidr="$1"
    command -v ufw >/dev/null 2>&1 || return 0
    [ -n "$cidr" ] || return 0
    ufw --force delete allow from "$cidr" to any port 8080 proto tcp >/dev/null 2>&1 || true
    ufw --force delete allow from "$cidr" to any port 8443 proto tcp >/dev/null 2>&1 || true
    ufw --force delete allow from "$cidr" to any port 45873 proto udp >/dev/null 2>&1 || true
    ufw --force delete allow from "$cidr" to any port 5353 proto udp >/dev/null 2>&1 || true
    ufw reload >/dev/null 2>&1 || true
}

if [ "$MODE" = uninstall ]; then
    say "Removing GOLEM LINK..."
    if command -v systemctl >/dev/null 2>&1; then
        systemctl disable --now golem-link.service >/dev/null 2>&1 || true
        systemctl disable --now golem-link-update.path >/dev/null 2>&1 || true
        systemctl stop golem-link-update.service >/dev/null 2>&1 || true
    fi
    firewall_remove "$(sed -n 's/^GOLEM_FIREWALL_CIDR=//p' /etc/golem-link/install.env 2>/dev/null || true)"
    rm -f "$UNIT" /etc/systemd/system/golem-link-update.path /etc/systemd/system/golem-link-update.service
    if command -v systemctl >/dev/null 2>&1; then
        systemctl daemon-reload >/dev/null 2>&1 || true
    fi
    rm -rf "$APP_DIR" /usr/share/golem-link /etc/golem-link /opt/golem-link-update
    rm -f /var/log/golem-link-update.log
    if getent passwd golem-link >/dev/null 2>&1; then
        userdel golem-link >/dev/null 2>&1 || true
    fi
    if getent group golem-link >/dev/null 2>&1; then
        groupdel golem-link >/dev/null 2>&1 || true
    fi
    if [ "$PURGE" = 1 ]; then
        rm -rf "$DATA_DIR"
        say "Identity, peers, shares and history were deleted."
    else
        say "Data kept at $DATA_DIR (add --purge to delete it)"
    fi
    say "GOLEM LINK was removed."
    exit 0
fi

if [ "$MODE" = upgrade ]; then
    say "GOLEM LINK $VERSION installer (upgrade mode)"
else
    say "GOLEM LINK $VERSION installer"
fi
[ -f /etc/debian_version ] || say "warning: this file targets Debian 13; continuing anyway"

if [ "$MODE" = upgrade ]; then
    [ -x "$BIN" ] || die "--upgrade expects an existing installation in $APP_DIR"
    say "Stopping the running agent so its files can be replaced..."
    if command -v systemctl >/dev/null 2>&1; then
        systemctl stop golem-link.service >/dev/null 2>&1 || true
    fi
    say "Keeping the current installation for rollback: $APP_DIR.previous"
    rm -rf "$APP_DIR.previous"
    cp -a "$APP_DIR" "$APP_DIR.previous" \
        || die "could not back up $APP_DIR; nothing was replaced"
    UPGRADE_BACKED_UP=1
    if [ -f "$UNIT" ]; then
        cp -a "$UNIT" /etc/golem-link/golem-link.service.previous
    fi
fi

case "$(uname -m)" in
    x86_64|amd64) ;;
    *) die "this build is for x86_64 Debian (found $(uname -m)); use a matching build" ;;
esac
for tool in tar sha256sum grep; do
    command -v "$tool" >/dev/null 2>&1 || die "the base system lacks $tool; install coreutils and tar"
done

if [ "$START" = 0 ] && [ -n "$PEER$APPROVE" ]; then
    die "pairing needs the dashboard running; drop --no-start or pair later with golem-link link"
fi

UPGRADE_BACKED_UP=0
RESTORED=0

restore_previous() {
    say "The update did not complete; restoring the previous installation..."
    if command -v systemctl >/dev/null 2>&1; then
        systemctl stop golem-link.service >/dev/null 2>&1 || true
    fi
    rm -rf "$APP_DIR"
    if ! mv "$APP_DIR.previous" "$APP_DIR" 2>/dev/null; then
        say "warning: could not restore $APP_DIR automatically; it remains in $APP_DIR.previous"
    fi
    if [ -f /etc/golem-link/golem-link.service.previous ]; then
        install -m 0644 /etc/golem-link/golem-link.service.previous "$UNIT"
    fi
    if command -v systemctl >/dev/null 2>&1; then
        systemctl daemon-reload >/dev/null 2>&1 || true
        systemctl start golem-link.service >/dev/null 2>&1 || true
    fi
    say "Data in $DATA_DIR was never touched."
}

cleanup() {
    rc=$?
    [ -z "$WORK" ] || rm -rf "$WORK"
    if [ "$MODE" = upgrade ] && [ "$rc" -ne 0 ] && [ "$RESTORED" = 0 ]; then
        RESTORED=1
        if [ "$UPGRADE_BACKED_UP" = 1 ] && [ -d "$APP_DIR.previous" ]; then
            restore_previous
        else
            say "The update failed before anything was replaced; the existing installation is unchanged."
        fi
    fi
}

WORK=$(mktemp -d /tmp/golem-link-install.XXXXXX)
trap cleanup EXIT INT TERM
OFFSET=$(payload_offset)
say "Unpacking the agent..."
tail -c +$((OFFSET + 1)) "$0" | tar -xzf - -C "$WORK" \
    || die "the payload is corrupt; download the file again"
if [ -f "$WORK/SHA256SUMS" ]; then
    ( cd "$WORK" && sha256sum -c --quiet SHA256SUMS ) || die "payload checksum mismatch; download the file again"
fi
[ -d "$WORK/wheels" ] || die "the payload is missing its wheels"
[ -f "$WORK/python-runtime.tar.gz" ] || die "the payload is missing the portable Python runtime"

say "Creating the golem-link user and directories..."
getent group golem-link >/dev/null 2>&1 || addgroup --system golem-link
getent passwd golem-link >/dev/null 2>&1 || adduser --system --ingroup golem-link \
    --home "$DATA_DIR" --no-create-home --disabled-password \
    --gecos "GOLEM LINK" golem-link
install -d -o golem-link -g golem-link -m 0700 "$DATA_DIR"
chown golem-link:golem-link "$DATA_DIR"
install -d -m 0755 "$APP_DIR" /etc/golem-link /usr/share/golem-link

rm -rf "$APP_DIR/wheels"
install -d -m 0755 "$APP_DIR/wheels"
cp "$WORK"/wheels/*.whl "$APP_DIR/wheels/"

if [ ! -x "$PY" ] || [ ! -d "$SITE" ] || [ "$(cat "$APP_DIR/.golem-version" 2>/dev/null || true)" != "$VERSION" ]; then
    rm -rf "$APP_DIR/python" "$SITE"
    say "Unpacking the bundled Python 3.13 (no system Python, pip or network needed)..."
    tar -xzf "$WORK/python-runtime.tar.gz" -C "$APP_DIR" \
        || die "the bundled Python runtime is corrupt; download the file again"
    [ -x "$PY" ] || die "the bundled Python runtime did not unpack correctly"
    say "Installing the agent and its dependencies from the bundled wheels..."
    "$PY" - "$APP_DIR/wheels" "$SITE" <<'PY_INSTALL'
import pathlib
import sys
import zipfile

wheels = pathlib.Path(sys.argv[1])
site = pathlib.Path(sys.argv[2])
site.mkdir(parents=True, exist_ok=True)
count = 0
for wheel in sorted(wheels.glob("*.whl")):
    with zipfile.ZipFile(wheel) as archive:
        archive.extractall(site)
    count += 1
if count < 20:
    raise SystemExit(f"only {count} wheels were found")
print(f"Installed {count} wheels")
PY_INSTALL
    echo "$VERSION" > "$APP_DIR/.golem-version"
fi
"$PY" -m compileall -q "$SITE" >/dev/null 2>&1 || true

install -d -m 0755 "$APP_DIR/bin"
cat > "$BIN" <<EOF
#!/bin/sh
PYTHONPATH="$SITE" exec "$PY" -m golem_link.cli "\$@"
EOF
chmod 0755 "$BIN"
chmod -R a+rX "$APP_DIR"

install -d -m 0755 /usr/share/golem-link/web
rm -rf /usr/share/golem-link/web/*
cp -a "$WORK/web/." /usr/share/golem-link/web/

say "Completing the first-run setup..."
GENERATED=0
SETUP_STATE=new
if [ -z "$PASSWORD" ]; then
    PASSWORD=$("$PY" -c 'import secrets; print(secrets.token_urlsafe(12))')
    GENERATED=1
fi
SETUP_ARGS=(setup --data-dir "$DATA_DIR")
[ -z "$NODE_NAME" ] || SETUP_ARGS+=(--name "$NODE_NAME")
SETUP_OUT=$(GOLEM_ADMIN_PASSWORD="$PASSWORD" runuser -u golem-link -- \
    "$BIN" "${SETUP_ARGS[@]}" 2>&1) || true
case "$SETUP_OUT" in
    *"Setup complete"*) say "Admin dashboard password created." ;;
    *"already completed"*)
        SETUP_STATE=existing
        GENERATED=0
        say "Admin password already set; keeping the existing one."
        ;;
    *) die "first-run setup failed: $SETUP_OUT" ;;
esac
if [ "$SETUP_STATE" = existing ] && [ "$PASSWORD_SUPPLIED" = 0 ] && [ -n "$PEER$APPROVE" ]; then
    die "this node already has an admin password; re-run with --password (or GOLEM_ADMIN_PASSWORD) so pairing can log in"
fi
if [ "$GENERATED" = 1 ]; then
    printf '%s\n' "$PASSWORD" > "$DATA_DIR/initial-password.txt"
    chmod 0600 "$DATA_DIR/initial-password.txt"
fi

if [ "$REMOTE_UPDATES" = 1 ] && [ -f "$DATA_DIR/golem-link.db" ]; then
    say "Allowing already-paired devices to maintain this node..."
    runuser -u golem-link -- "$PY" - "$DATA_DIR/golem-link.db" <<'PY_DB' \
        || say "warning: could not pre-authorise paired devices; enable control per device in the dashboard"
import sqlite3
import sys

connection = sqlite3.connect(sys.argv[1], timeout=30)
try:
    connection.execute("UPDATE peers SET allow_control = 1")
    connection.commit()
except sqlite3.OperationalError:
    pass
finally:
    connection.close()
PY_DB
fi

install -m 0644 "$WORK/golem-link.service" "$UNIT"

if [ "$REMOTE_UPDATES" = 1 ]; then
    say "Installing the verified remote-update helper (root-owned, opt-in)..."
    sed "s|@DATA_DIR@|$DATA_DIR|g" "$WORK/golem-link-apply-update" \
        > "$WORK/golem-link-apply-update.rendered"
    install -d -m 0755 "$APP_DIR/bin"
    install -m 0755 "$WORK/golem-link-apply-update.rendered" "$APP_DIR/bin/golem-link-apply-update"
    sed "s|@DATA_DIR@|$DATA_DIR|g" "$WORK/golem-link-update.path" \
        > /etc/systemd/system/golem-link-update.path
    install -m 0644 "$WORK/golem-link-update.service" /etc/systemd/system/golem-link-update.service
    install -m 0644 /dev/null /etc/golem-link/remote-updates.enabled
    : > /var/log/golem-link-update.log
    chmod 0640 /var/log/golem-link-update.log
    if command -v systemctl >/dev/null 2>&1; then
        systemctl daemon-reload >/dev/null 2>&1 || true
    fi
fi

if [ "$FIREWALL" = 1 ] && [ "$MODE" = install ]; then
    if [ -z "$CIDR" ]; then
        CIDR=$(ip -4 -o addr show scope global 2>/dev/null | awk '{print $4; exit}' || true)
        ROUTE_DEV=$(ip -4 route show default 2>/dev/null | awk '{print $5; exit}' || true)
        if [ -n "$ROUTE_DEV" ]; then
            CIDR=$(ip -4 -o addr show dev "$ROUTE_DEV" scope global 2>/dev/null | awk '{print $4; exit}' || true)
        fi
        if [ -z "$CIDR" ]; then
            CIDR=$(ip -4 route show default 2>/dev/null | awk '{print $3; exit}' || true)
            [ -z "$CIDR" ] || CIDR="${CIDR%.*}.0/24"
        fi
    fi
    if [ -n "$CIDR" ]; then
        CIDR=$("$PY" - "$CIDR" <<'PY'
import ipaddress, sys
try:
    print(ipaddress.ip_network(sys.argv[1], strict=False))
except ValueError:
    pass
PY
)
    fi
    if [ -n "$CIDR" ] && command -v ufw >/dev/null 2>&1; then
        say "Opening LAN ports for $CIDR in ufw..."
        ufw allow from "$CIDR" to any port 8080 proto tcp comment 'GOLEM LINK dashboard' >/dev/null 2>&1 || true
        ufw allow from "$CIDR" to any port 8443 proto tcp comment 'GOLEM LINK peer API' >/dev/null 2>&1 || true
        ufw allow from "$CIDR" to any port 45873 proto udp comment 'GOLEM LINK discovery' >/dev/null 2>&1 || true
        ufw allow from "$CIDR" to any port 5353 proto udp comment 'mDNS' >/dev/null 2>&1 || true
        if ufw status 2>/dev/null | grep -q "Status: active"; then
            ufw reload >/dev/null 2>&1 || true
        else
            say "ufw is inactive; the rules will apply when it is enabled."
        fi
        printf 'GOLEM_FIREWALL_CIDR=%s\n' "$CIDR" > /etc/golem-link/install.env
    elif command -v ufw >/dev/null 2>&1; then
        say "warning: could not detect the LAN subnet; ufw was left unchanged (pass --firewall-cidr)"
    else
        say "ufw is not installed; open TCP 8080/8443 and UDP 45873/5353 to your subnet manually."
        say "The agent still refuses clients outside private/LAN addresses."
    fi
fi

SERVICE_OK=0
if [ "$START" = 1 ]; then
    if command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]; then
        say "Starting the service..."
        systemctl daemon-reload
        systemctl enable golem-link.service >/dev/null 2>&1 || true
        systemctl restart golem-link.service \
            || die "the service failed to start; inspect: journalctl -u golem-link -n 60"
        say "Waiting for the dashboard on 127.0.0.1:$PORT..."
        PYTHONPATH="$SITE" "$PY" - "$PORT" <<'PY' || die "the dashboard did not answer; inspect: journalctl -u golem-link -n 60"
import sys
from golem_link.link import LinkError, wait_for_service
try:
    wait_for_service(int(sys.argv[1]), 90.0)
except LinkError as exc:
    print(f"GOLEM LINK: {exc}", file=sys.stderr)
    raise SystemExit(1)
PY
        SERVICE_OK=1
    else
        say "systemd is not running here; start the agent manually with:"
        say "  GOLEM_LINK_DATA=$DATA_DIR $BIN run --data-dir $DATA_DIR"
    fi
else
    say "Installed but not started (--no-start). Start it with: systemctl start golem-link"
fi

if [ "$REMOTE_UPDATES" = 1 ] && command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]; then
    systemctl enable golem-link-update.path >/dev/null 2>&1 || true
    if [ "$START" = 1 ]; then
        systemctl start golem-link-update.path \
            || say "warning: could not start the remote-update trigger; run: systemctl start golem-link-update.path"
    fi
fi

PAIR_RC=0
if [ -n "$PEER" ]; then
    say ""
    say "Pairing with $PEER. Confirm the six-digit code on the other machine, then approve it there."
    set +e
    GOLEM_ADMIN_PASSWORD="$PASSWORD" runuser -u golem-link -- \
        "$BIN" link init --peer "$PEER" --port "$PORT" --wait "$WAIT_S"
    PAIR_RC=$?
    set -e
    if [ "$PAIR_RC" -ne 0 ]; then
        say "Pairing did not complete. Retry later with (it asks for the admin password):"
        say "  sudo runuser -u golem-link -- $BIN link init --peer $PEER"
    fi
elif [ -n "$APPROVE" ]; then
    say ""
    say "Approving the pending request with code $APPROVE..."
    set +e
    GOLEM_ADMIN_PASSWORD="$PASSWORD" runuser -u golem-link -- \
        "$BIN" link approve --code "$APPROVE" --port "$PORT" --wait "$WAIT_S"
    PAIR_RC=$?
    set -e
    if [ "$PAIR_RC" -ne 0 ]; then
        say "Approval did not complete. Retry later with:"
        say "  sudo runuser -u golem-link -- $BIN link approve --code $APPROVE"
    fi
elif [ "$AUTO_PAIR" = 1 ]; then
    if [ "$SERVICE_OK" != 1 ]; then
        say ""
        say "Pairing was skipped because the dashboard is not running (--no-start)."
        say "Start it, then run: sudo runuser -u golem-link -- $BIN link auto"
    elif [ -z "$PASSWORD" ]; then
        say ""
        say "Pairing was skipped: this node already has an admin password."
        say "Re-run with --password to pair automatically, or use the dashboard."
    else
        say ""
        say "Looking for the other GOLEM LINK machine on the LAN..."
        set +e
        GOLEM_ADMIN_PASSWORD="$PASSWORD" runuser -u golem-link -- \
            "$BIN" link auto --discovery-wait 12 --port "$PORT" --wait "$WAIT_S"
        PAIR_RC=$?
        set -e
        if [ "$PAIR_RC" -ne 0 ]; then
            say "Automatic pairing did not complete; open http://127.0.0.1:$PORT > Devices."
        fi
    fi
fi

LAN_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || true)
if [ -z "$LAN_IP" ]; then
    LAN_IP=$(ip -4 -o addr show scope global 2>/dev/null | awk '{print $4; sub(/\/.*/, "", $0); print $0; exit}' || true)
fi
say ""
if [ "$SERVICE_OK" = 1 ]; then
    say "GOLEM LINK $VERSION is installed and running."
else
    say "GOLEM LINK $VERSION is installed."
fi
say "Dashboard:  http://${LAN_IP:-<this-machine>}:$PORT   (LAN only)"
if [ "$GENERATED" = 1 ]; then
    say "Admin password: $PASSWORD"
    say "It is also saved at $DATA_DIR/initial-password.txt (root only)."
elif [ "$SETUP_STATE" = new ]; then
    say "Admin password: as supplied with --password."
fi
say "Service:    systemctl status golem-link"
if [ "$REMOTE_UPDATES" = 1 ]; then
    say "Updates:    paired devices may apply verified updates via the root helper"
    say "Revoke:     sudo systemctl disable --now golem-link-update.path && sudo rm /etc/golem-link/remote-updates.enabled"
fi
if [ "$MODE" = upgrade ]; then
    say "Rollback:   sudo systemctl stop golem-link && sudo rm -rf $APP_DIR && sudo mv $APP_DIR.previous $APP_DIR && sudo systemctl start golem-link"
    say "Data, identity and the admin password in $DATA_DIR were preserved."
fi
say "Uninstall:  sudo $0 --uninstall        (add --purge to delete data)"
[ "$PAIR_RC" -eq 0 ] || exit 3

exit 0
#__GOLEM_LINK_PAYLOAD_BELOW__
