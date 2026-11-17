"""GOLEM LINK Windows installer (single-file exe).

Actions: extract the bundled agent, register the WinSW service, open the
Private-profile firewall ports, complete the first-run admin password and
optionally pair with another node. Uninstall with ``/Uninstall``.

No typing is required: double-click it and it installs, generates the admin
password, finds the other GOLEM LINK machine on the LAN and finishes pairing.
The six-digit code is still compared by a human and approved with one click in
the dashboard.

User input (all optional):
  /S or /Silent          unattended (the default is already non-interactive)
  /InstallDir=<path>     default %ProgramFiles%\\GOLEM LINK
  /Name=<node name>      default hostname
  /Password=<admin pw>   at least 8 characters (default: generated)
  /Pair=<ip[:port]>      pair with a specific machine instead of finding it
  /Approve=<code>        approve a pending request with this code
  /NoPair                install only; do not look for the other machine
  /NoBrowser             do not open the dashboard
  /Update                replace an existing installation (implies /NoPair
                         /NoBrowser); keeps data, password and the previous
                         program folder as a rollback
  /Uninstall             remove the service, firewall rules and files
  /Purge                 with /Uninstall: also delete C:\\ProgramData\\GolemLink
  /ExtractOnly=<path>    only unpack the payload (no service, no admin needed)
                         also accepted as --extract-only=<path>
"""

from __future__ import annotations

import ctypes
import json
import os
import secrets
import shutil
import socket
import string
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

SERVICE_NAME = "golem-link"
SERVICE_EXE = "golem-link-service.exe"
APP_EXE = "golem-link.exe"
DASHBOARD = "http://127.0.0.1:8080"
PROGRAM_FILES = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
PROGRAM_DATA = Path(os.environ.get("ProgramData", r"C:\ProgramData"))
DEFAULT_INSTALL = PROGRAM_FILES / "GOLEM LINK"
DATA_DIR = PROGRAM_DATA / "GolemLink"
PUBLIC_DESKTOP = Path(os.environ.get("PUBLIC", r"C:\Users\Public")) / "Desktop"
START_MENU = PROGRAM_DATA / "Microsoft" / "Windows" / "Start Menu" / "Programs"
SHORTCUT = "GOLEM LINK Dashboard.url"
FIREWALL_RULES = (
    ("GOLEM LINK Dashboard", 8080, "TCP"),
    ("GOLEM LINK Peer API", 8443, "TCP"),
    ("GOLEM LINK Discovery", 45873, "UDP"),
    ("GOLEM LINK mDNS", 5353, "UDP"),
)
KNOWN_FLAGS = {
    "/s", "/silent", "--silent", "/help", "--help", "/?", "-h",
    "/uninstall", "--uninstall", "-uninstall", "/purge", "--purge",
    "/nobrowser", "--nobrowser", "/nopair", "--nopair",
    "/noautopair", "--noautopair", "/update", "--update", "/upgrade", "--upgrade",
}
KNOWN_OPTIONS = (
    "installdir", "name", "password", "pair", "approve", "extractonly", "extract-only",
)


def validate_arguments() -> None:
    for item in sys.argv[1:]:
        low = item.lower()
        if low in KNOWN_FLAGS:
            continue
        if any(low.startswith(f"/{name}=") or low.startswith(f"--{name}=") for name in KNOWN_OPTIONS):
            continue
        raise SystemExit(
            f"Unknown option: {item}\n"
            "Nothing was installed. Use /Help to see the supported options."
        )


def say(message: str = "") -> None:
    print(message, flush=True)


def argv_lower() -> list[str]:
    return [item.lower() for item in sys.argv[1:]]


def flag(*names: str) -> bool:
    wanted = {name.lower() for name in names}
    return any(item in wanted for item in argv_lower())


def option(name: str, default: str = "") -> str:
    prefixes = (f"/{name.lower()}=", f"--{name.lower()}=")
    for item in sys.argv[1:]:
        low = item.lower()
        for prefix in prefixes:
            if low.startswith(prefix):
                return item[len(prefix):]
    return default


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def elevate() -> None:
    params = subprocess.list2cmdline(sys.argv[1:])
    code = ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1)
    if code <= 32:
        raise SystemExit(f"Could not request administrator rights (ShellExecute code {code}).")


def payload_archive() -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    archive = base / "installer-payload.zip"
    if not archive.is_file():
        raise SystemExit(f"Bundled payload is missing: {archive}")
    return archive


def extract_payload(target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    root = target.resolve()
    with zipfile.ZipFile(payload_archive()) as archive:
        for info in archive.infolist():
            destination = (target / info.filename).resolve()
            if root not in destination.parents and destination != root:
                raise SystemExit(f"Refusing unsafe payload path: {info.filename}")
        archive.extractall(target)
    say(f"Installed files: {target}")


def run(command: list[str], *, check: bool = False, timeout: float = 120) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, errors="replace", timeout=timeout
        )
    except FileNotFoundError:
        result = subprocess.CompletedProcess(command, 127, "", f"{command[0]} not found")
    except subprocess.TimeoutExpired:
        result = subprocess.CompletedProcess(command, 124, "", f"{command[0]} timed out")
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise SystemExit(f"{' '.join(command)} failed ({result.returncode}): {detail}")
    return result


def stop_service(install_dir: Path) -> None:
    service_exe = install_dir / SERVICE_EXE
    if service_exe.is_file():
        run([str(service_exe), "stop"], timeout=60)
        run([str(service_exe), "uninstall"], timeout=60)


def firewall(remove: bool = False) -> None:
    for name, port, protocol in FIREWALL_RULES:
        run(["netsh", "advfirewall", "firewall", "delete", "rule", f"name={name}"])
        if remove:
            continue
        result = run(
            [
                "netsh", "advfirewall", "firewall", "add", "rule",
                f"name={name}", "dir=in", "action=allow",
                f"protocol={protocol}", f"localport={port}",
                "profile=private", "remoteip=localsubnet", "enable=yes",
            ]
        )
        if result.returncode != 0:
            say(f"Warning: firewall rule '{name}' was not added: {result.stderr.strip()}")


def api(method: str, path: str, body: dict | None = None, timeout: float = 10.0) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(DASHBOARD + path, data=data, method=method)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read() or b"{}")


def wait_for_agent(timeout: float = 90.0, expected_version: str = "") -> dict:
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        try:
            status = api("GET", "/api/setup/status")
            if not expected_version or status.get("version") == expected_version:
                return status
            last = f"the agent still reports {status.get('version')}"
        except (urllib.error.URLError, OSError, ValueError) as exc:
            last = str(exc)
        time.sleep(1.0)
    raise SystemExit(f"The agent did not answer on {DASHBOARD} within {int(timeout)} s ({last}).")


def payload_version(app_exe: Path) -> str:
    result = run([str(app_exe), "--version"], timeout=60)
    text = (result.stdout or "").strip().split()
    return text[-1] if result.returncode == 0 and text else ""


def generated_password() -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(20))


def write_initial_password(password: str) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / "initial-admin-password.txt"
    path.write_text(
        "GOLEM LINK initial dashboard password (delete this file after signing in)\n"
        f"Password: {password}\n",
        encoding="utf-8",
    )
    return path


def complete_setup(status: dict) -> str:
    """Ensure an admin password exists. Returns the password for pairing."""
    supplied = option("password") or os.environ.get("GOLEM_ADMIN_PASSWORD", "")
    if not status.get("needs_setup"):
        if supplied:
            say("First-run setup was already completed; the existing password was kept.")
            return supplied
        say("First-run setup was already completed.")
        say("Pairing needs that password: re-run with /Password=<pw>, or pair from the dashboard.")
        return ""

    password = supplied or generated_password()
    if len(password) < 8:
        raise SystemExit("The admin password must be at least 8 characters.")

    node_name = option("name") or socket.gethostname()
    api("POST", "/api/setup", {"node_name": node_name, "password": password})
    say(f"First-run setup complete for '{node_name}'.")
    say("")
    say(f"  Dashboard password: {password}")
    say("")
    path = write_initial_password(password)
    say(f"Password saved for first sign-in at: {path}")
    return password


def make_shortcut() -> None:
    content = "[InternetShortcut]\r\nURL=http://127.0.0.1:8080/\r\n"
    for folder in (PUBLIC_DESKTOP, START_MENU):
        try:
            folder.mkdir(parents=True, exist_ok=True)
            (folder / SHORTCUT).write_text(content, encoding="ascii")
        except OSError as exc:
            say(f"Warning: could not create shortcut in {folder}: {exc}")
    say("Shortcuts created on the Public Desktop and in the Start Menu.")


def run_link(install_dir: Path, password: str) -> None:
    peer = option("pair")
    approve = option("approve")
    auto = not flag("/nopair", "--nopair", "/noautopair", "--noautopair")
    if not peer and not approve and not auto:
        return
    if not password:
        if peer or approve:
            say("Pairing needs the dashboard admin password; re-run with /Password=<pw>.")
        return
    app = str(install_dir / APP_EXE)
    if peer:
        command = [app, "link", "init", "--peer", peer]
    elif approve:
        command = [app, "link", "approve", "--code", approve]
    else:
        command = [app, "link", "auto", "--discovery-wait", "12", "--wait", "300"]
    say("")
    say(f"Linking: {' '.join(command[1:])}")
    child_env = dict(os.environ)
    child_env["GOLEM_ADMIN_PASSWORD"] = password
    result = subprocess.run(command, env=child_env)
    if result.returncode != 0:
        say("Pairing did not complete. Open the dashboard > Devices and pair from there.")


def open_dashboard() -> None:
    if flag("/nobrowser", "--nobrowser"):
        return
    try:
        os.startfile(DASHBOARD)  # type: ignore[attr-defined]
    except OSError:
        pass


def backup_previous(install_dir: Path) -> Path:
    backup = install_dir.with_name(install_dir.name + ".previous")
    say(f"Keeping the current version for rollback: {backup}")
    if backup.exists():
        shutil.rmtree(backup, ignore_errors=True)
    try:
        shutil.copytree(install_dir, backup)
    except OSError as exc:
        raise SystemExit(
            f"Could not back up {install_dir} ({exc}); nothing was changed."
        ) from exc
    return backup


def restore_previous(install_dir: Path, backup: Path) -> None:
    say("The updated agent did not come up; restoring the previous installation...")
    stop_service(install_dir)
    shutil.rmtree(install_dir, ignore_errors=True)
    if backup.exists():
        try:
            shutil.move(str(backup), str(install_dir))
        except OSError as exc:
            raise SystemExit(
                f"Restore failed ({exc}). The previous files are still in {backup}."
            ) from exc
    service_exe = install_dir / SERVICE_EXE
    if service_exe.is_file():
        run([str(service_exe), "install"])
        run([str(service_exe), "start"])
    say(f"The previous version was restored in {install_dir}.")
    say(f"Full install log: {DATA_DIR / 'updates' / 'install.log'}")


def install(update: bool = False) -> int:
    install_dir = Path(option("installdir") or DEFAULT_INSTALL)
    say("GOLEM LINK installer" + (" (update mode)" if update else ""))
    say(f"Install folder: {install_dir}")
    say("")
    data_existed = DATA_DIR.exists()
    if update and not (install_dir / APP_EXE).is_file():
        raise SystemExit(
            f"No GOLEM LINK installation was found in {install_dir}; /Update only "
            "replaces an installed copy. Run the installer without /Update for a "
            "first install."
        )
    if update and not data_existed:
        raise SystemExit(
            f"The data folder {DATA_DIR} is missing; refusing to update because the "
            "identity and admin password would not be preserved."
        )

    say("Stopping an existing GOLEM LINK service (if any)...")
    stop_service(install_dir)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    backup = backup_previous(install_dir) if update else None

    say("Extracting the agent...")
    extract_payload(install_dir)
    app_exe = install_dir / APP_EXE
    service_exe = install_dir / SERVICE_EXE
    if not app_exe.is_file() or not service_exe.is_file():
        raise SystemExit("The payload did not contain golem-link.exe and the service wrapper.")

    say("Opening firewall ports for the Private profile only (local subnet)...")
    firewall(remove=False)

    say("Registering the Windows service...")
    run([str(service_exe), "install"], check=True)
    start = run([str(service_exe), "start"], check=False)
    if start.returncode != 0:
        say(f"Warning: service start reported: {(start.stderr or start.stdout).strip()}")

    expected = payload_version(app_exe) if update else ""
    say("Waiting for the agent to start" + (f" (version {expected})..." if expected else "..."))
    try:
        status = wait_for_agent(expected_version=expected)
    except SystemExit:
        if backup is not None:
            restore_previous(install_dir, backup)
        raise
    password = complete_setup(status)
    make_shortcut()

    if update:
        say("")
        say(f"GOLEM LINK {status.get('version', '')} is installed and running.")
        say(f"Data, identity and the admin password were kept in {DATA_DIR}.")
        if backup is not None:
            say(f"Rollback copy: {backup}")
        say(f"Update log:   {DATA_DIR / 'updates' / 'install.log'}")
        return 0

    open_dashboard()
    run_link(install_dir, password)

    say("")
    say("GOLEM LINK is installed.")
    say(f"Dashboard: {DASHBOARD}  (LAN: http://<this-hostname>.local:8080)")
    say(f"Logs:      {DATA_DIR / 'logs'}")
    say(f"Service:   Get-Service {SERVICE_NAME}")
    if not data_existed:
        say("Data folder created. The identity key is unique to this machine; back it up if you reinstall.")
    return 0


def uninstall() -> int:
    install_dir = Path(option("installdir") or DEFAULT_INSTALL)
    say("Removing GOLEM LINK...")
    stop_service(install_dir)
    firewall(remove=True)
    for folder in (PUBLIC_DESKTOP, START_MENU):
        try:
            (folder / SHORTCUT).unlink(missing_ok=True)
        except OSError:
            pass
    if install_dir.exists():
        for _ in range(20):
            try:
                shutil.rmtree(install_dir)
                break
            except OSError:
                time.sleep(0.5)
        else:
            say(f"Warning: could not fully delete {install_dir}; close any open files and retry.")
    if flag("/purge", "--purge"):
        if DATA_DIR.exists():
            shutil.rmtree(DATA_DIR, ignore_errors=True)
        say(f"Data removed: {DATA_DIR}")
    else:
        say(f"Data kept at: {DATA_DIR} (use /Purge to delete it)")
    say("GOLEM LINK was removed.")
    return 0


def main() -> int:
    validate_arguments()
    extract_only = option("extractonly") or option("extract-only")
    if extract_only:
        extract_payload(Path(extract_only))
        return 0
    if flag("/help", "--help", "/?", "-h"):
        say(__doc__ or "")
        return 0
    if not is_admin():
        say("Administrator rights are required; opening an elevated copy...")
        elevate()
        return 0
    if flag("/uninstall", "--uninstall", "-uninstall"):
        return uninstall()
    if flag("/update", "--update", "/upgrade", "--upgrade"):
        return install(update=True)
    return install()


if __name__ == "__main__":
    raise SystemExit(main())
