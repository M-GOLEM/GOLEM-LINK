#!/usr/bin/env bash
# Review before running: opens the GOLEM LINK ports to the local subnet only.
# Run on the Debian machine with sudo, after installing GOLEM LINK.
#
# The agent itself also rejects non-LAN clients; this script keeps the ports
# closed to routed/VPN traffic at the firewall layer.
set -euo pipefail

LAN_CIDR="${1:-}"
if [ -z "$LAN_CIDR" ]; then
    LAN_CIDR="$(ip -4 route show default | awk '{print $3; exit}')/24"
fi
echo "Using LAN subnet: $LAN_CIDR"
command -v ufw >/dev/null || { echo "ufw is not installed; use nftables/firewalld equivalents from docs/INSTALLATION.md" >&2; exit 1; }

sudo ufw allow from "$LAN_CIDR" to any port 8080 proto tcp comment 'GOLEM LINK dashboard'
sudo ufw allow from "$LAN_CIDR" to any port 8443 proto tcp comment 'GOLEM LINK peer API'
sudo ufw allow from "$LAN_CIDR" to any port 45873 proto udp comment 'GOLEM LINK discovery'
sudo ufw allow from "$LAN_CIDR" to any port 5353 proto udp comment 'mDNS'
sudo ufw reload
sudo ufw status numbered
