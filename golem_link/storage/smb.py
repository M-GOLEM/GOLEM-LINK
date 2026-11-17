"""SMB/Samba helper generation.

GOLEM LINK's own authenticated transfer API is the default file path between
nodes. These helpers are optional: they write ready-to-review scripts so the
operator can expose approved folders through the operating system's SMB stack
and map them as drives/network folders. Nothing is executed automatically.
"""

from __future__ import annotations

import secrets
from typing import Iterable

from ..config import ShareConfig


def windows_share_script(shares: Iterable[ShareConfig], account: str = "GolemLinkShare") -> str:
    password = secrets.token_urlsafe(18)
    lines = [
        "# GOLEM LINK - Windows SMB share setup (review before running, run as Administrator)",
        "# Creates a least-privilege local account and shares only the approved folders.",
        f"$account = '{account}'",
        f"$password = Read-Host -AsSecureString 'Password for {account} (generated: {password})'",
        "# The generated password above is shown once; store it safely.",
        "if (-not (Get-LocalUser -Name $account -ErrorAction SilentlyContinue)) {",
        "  New-LocalUser -Name $account -Password $password -PasswordNeverExpires -Description 'GOLEM LINK share account' | Out-Null",
        "}",
        "",
    ]
    for index, share in enumerate(shares, start=1):
        name = f"Golem-{share.name[:40]}".replace(" ", "-")
        access = "Read" if share.read_only else "Change"
        lines += [
            f"# share: {share.name} -> {share.path}",
            f"if (-not (Get-SmbShare -Name '{name}' -ErrorAction SilentlyContinue)) {{",
            f"  New-SmbShare -Name '{name}' -Path '{share.path}' -{access}Access \"$env:COMPUTERNAME\\$account\" | Out-Null",
            "}",
            f"Set-SmbShare -Name '{name}' -EncryptData $true | Out-Null",
            "",
        ]
    lines += [
        "# Firewall: allow SMB on Private profile only",
        "Enable-NetFirewallRule -DisplayGroup 'File and Printer Sharing' -Profile Private",
        "Write-Host 'Done. Store the share account password in a password manager.'",
    ]
    return "\n".join(lines)


def debian_samba_setup(shares: Iterable[ShareConfig], account: str = "golem-link") -> str:
    lines = [
        "#!/usr/bin/env bash",
        "# GOLEM LINK - Debian/Samba setup (review before running, run as root)",
        "set -euo pipefail",
        "apt-get update",
        "apt-get install -y samba",
        "id -u " + account + " >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin " + account,
        "mkdir -p /etc/golem-link",
        "cp /etc/samba/smb.conf /etc/samba/smb.conf.bak.$(date +%s)",
        "cat >> /etc/samba/smb.conf <<'GOLEM_SAMBA'",
        "[global]",
        "   server min protocol = SMB3",
        "   smb encrypt = required",
        "   hosts allow = 127.0.0.1 192.168. 10. 172.16. 172.17. 172.18. 172.19. 172.20. 172.21. 172.22. 172.23. 172.24. 172.25. 172.26. 172.27. 172.28. 172.29. 172.30. 172.31.",
        "",
    ]
    for index, share in enumerate(shares, start=1):
        share_name = f"Golem-{share.name[:40]}".replace(" ", "-")
        lines += [
            f"[{share_name}]",
            f"   path = {share.path}",
            f"   valid users = {account}",
            "   browseable = yes",
            "   read only = " + ("yes" if share.read_only else "no"),
            "   force user = " + account,
            "   create mask = 0660",
            "   directory mask = 0770",
            "",
        ]
    lines += [
        "GOLEM_SAMBA",
        "testparm -s",
        "systemctl restart smbd",
        f"echo 'Set the SMB password with: smbpasswd -a {account}'",
        "# Firewall (nftables/ufw): allow 445/tcp only from your LAN subnet.",
    ]
    return "\n".join(lines)


def debian_mount_script(remote_host: str, shares: Iterable[ShareConfig], account: str = "golem-share") -> str:
    lines = [
        "#!/usr/bin/env bash",
        "# GOLEM LINK - mount approved Windows shares on Debian (review before running, run as root)",
        "set -euo pipefail",
        "apt-get install -y cifs-utils",
        "install -d -m 0700 /etc/golem-link",
        "# Create /etc/golem-link/credentials with:  username=...  password=...  (mode 0600)",
        "cat > /etc/golem-link/credentials <<'CREDS'",
        f"username={account}",
        "password=REPLACE_WITH_PASSWORD",
        "CREDS",
        "chmod 600 /etc/golem-link/credentials",
        "install -d -m 0755 /mnt/golem",
    ]
    for index, share in enumerate(shares, start=1):
        name = f"Golem-{share.name[:40]}".replace(" ", "-")
        mount = f"/mnt/golem/{share.name.replace(' ', '_').lower()}"
        lines += [
            f"install -d -m 0755 '{mount}'",
            f"mount.cifs '//{remote_host}/{name}' '{mount}' -o credentials=/etc/golem-link/credentials,uid=1000,gid=1000,vers=3.1.1,seal,ro"
            if share.read_only
            else f"mount.cifs '//{remote_host}/{name}' '{mount}' -o credentials=/etc/golem-link/credentials,uid=1000,gid=1000,vers=3.1.1,seal",
        ]
    lines.append("# Add equivalent /etc/fstab entries if you want them mounted at boot.")
    return "\n".join(lines)


def windows_mount_script(remote_host: str, shares: Iterable[ShareConfig]) -> str:
    drives = "XYZWVUTSRQ"
    lines = [
        "# GOLEM LINK - map approved Debian shares on Windows (review, then run in cmd.exe)",
        "# Use cmdkey /add to store credentials in Windows Credential Manager (not plain text).",
        "cmdkey /add:" + remote_host + " /user:golem-link",
    ]
    for index, share in enumerate(shares):
        drive = drives[index % len(drives)]
        name = f"Golem-{share.name[:40]}".replace(" ", "-")
        lines.append(f'net use {drive}: \\\\{remote_host}\\{name} /persistent:yes')
    return "\r\n".join(lines)
