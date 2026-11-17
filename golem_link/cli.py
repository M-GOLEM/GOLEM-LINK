"""GOLEM LINK command line entry point."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import logging.handlers
import os
import sys
from pathlib import Path

from . import crypto
from .app import GolemNode, build_context
from .config import ConfigManager
from .version import VERSION


def _setup_logging(data_dir: Path, level: str) -> None:
    log_dir = data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    # A service host redirects stdout to a file, which on Windows defaults to
    # the ANSI code page; alert messages contain "≥" and used to turn every
    # log record into a UnicodeEncodeError traceback in the service log.
    if sys.stdout is not None:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if sys.stderr is not None:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    for handler in list(root.handlers):
        root.removeHandler(handler)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    root.addHandler(console)
    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / "golem-link.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
        errors="replace",
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)


def cmd_run(args: argparse.Namespace) -> int:
    node = GolemNode(args.data_dir)
    _setup_logging(node.ctx.cfg.data_dir, node.ctx.cfg.get().log_level)
    if node.ctx.auth.needs_setup():
        log = logging.getLogger("golem_link")
        log.warning(
            "First run: dashboard setup is required. Open http://localhost:%s and create the admin password.",
            node.ctx.cfg.get().dashboard.port,
        )
    try:
        asyncio.run(node.serve())
    except KeyboardInterrupt:
        pass
    return 0


def cmd_setup(args: argparse.Namespace) -> int:
    cfg = ConfigManager(args.data_dir)
    cfg.ensure_dirs()
    from .api.auth import DashboardAuth
    from .db import Database

    db = Database(cfg.db_path())
    auth = DashboardAuth(cfg, db)
    if not auth.needs_setup():
        print("Setup already completed.", file=sys.stderr)
        return 1
    password = args.password or os.environ.get("GOLEM_ADMIN_PASSWORD", "")
    if not password:
        import getpass

        password = getpass.getpass("Admin password: ")
        confirm = getpass.getpass("Confirm: ")
        if password != confirm:
            print("Passwords do not match.", file=sys.stderr)
            return 1
    try:
        auth.setup(args.name or "", password)
    except Exception as exc:
        print(f"Setup failed: {exc}", file=sys.stderr)
        return 1
    print("Setup complete. Admin password stored as a scrypt hash.")
    db.close()
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    cfg = ConfigManager(args.data_dir)
    from .db import Database

    db = Database(cfg.db_path())
    cfg_data = cfg.get()
    peers = db.list_peers()
    tasks = db.query("SELECT status, COUNT(*) AS n FROM tasks GROUP BY status")
    open_alerts = db.list_alerts(limit=1000, only_open=True)
    print(json.dumps(
        {
            "version": VERSION,
            "node_name": cfg_data.node_name,
            "data_dir": str(cfg.data_dir),
            "setup_complete": cfg_data.first_run_complete,
            "dashboard": f"http://localhost:{cfg_data.dashboard.port}",
            "peer_port": cfg_data.peer.port,
            "paired_peers": [{"name": p["name"], "node_id": p["node_id"], "status": p["status"]} for p in peers],
            "shares": len(cfg_data.shares),
            "tasks": {row["status"]: row["n"] for row in tasks},
            "open_alerts": len(open_alerts),
        },
        indent=2,
    ))
    db.close()
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    from .app import build_context

    ctx = build_context(args.data_dir)
    info = ctx.identity.info()
    diag = {
        "node": info,
        "data_dir": str(ctx.cfg.data_dir),
        "db": str(ctx.cfg.db_path()),
        "certificate": str(ctx.cfg.cert_path()),
        "shares": ctx.storage.list_share_views(),
        "plugins": ctx.scheduler.catalog(),
        "input_sharing": ctx.input_sharing.detect(),
        "ports": {"dashboard": ctx.cfg.get().dashboard.port, "peer": ctx.cfg.get().peer.port},
    }
    print(json.dumps(diag, indent=2, default=str))
    return 0


def _link_password(args: argparse.Namespace) -> str:
    password = args.password or os.environ.get("GOLEM_ADMIN_PASSWORD", "")
    if not password:
        if not sys.stdin.isatty():
            raise ValueError(
                "admin password required: pass --password or set GOLEM_ADMIN_PASSWORD"
            )
        import getpass

        password = getpass.getpass("Admin password: ")
    return password


def _link_client(args: argparse.Namespace):
    from .link import LocalDashboard, wait_for_service

    wait_for_service(args.port, args.ready_timeout)
    client = LocalDashboard(args.port)
    client.login(_link_password(args))
    return client


def cmd_link(args: argparse.Namespace) -> int:
    from .link import LinkError, discover, run_approve, run_auto, run_init

    try:
        client = _link_client(args)
        if args.link_command == "init":
            run_init(client, args.peer, args.wait)
        elif args.link_command == "approve":
            run_approve(client, args.code, args.wait)
        elif args.link_command == "discover":
            rows = discover(client, args.wait)
            print(json.dumps({"candidates": rows}, indent=2))
        else:
            run_auto(client, args.wait, discovery_s=args.discovery_wait)
    except (LinkError, ValueError) as exc:
        print(f"GOLEM LINK: {exc}", file=sys.stderr)
        return 2
    return 0


def cmd_update(args: argparse.Namespace) -> int:
    from .link import LinkError

    action = args.update_command
    if action in ("apply", "rollback") and not args.yes:
        print(
            "Installing or rolling back restarts this node. Re-run with --yes to confirm.",
            file=sys.stderr,
        )
        return 2
    try:
        client = _link_client(args)
        if action == "status":
            result = client.update_status()
        elif action == "check":
            result = client.update_check()
        elif action == "download":
            result = client.update_download()
        elif action == "apply":
            result = client.update_apply()
        elif action == "rollback":
            node_id = str(client.me().get("node_id") or "")
            if not node_id:
                raise ValueError("could not resolve this node's id")
            result = client.control_device(node_id, "rollback", confirm=True)
        elif action == "fleet":
            if args.apply:
                if not args.yes:
                    print(
                        "Applying an update to the fleet restarts nodes. Re-run with --yes.",
                        file=sys.stderr,
                    )
                    return 2
                result = client.update_fleet("apply")
            else:
                result = client.update_fleet_status()
        else:  # pragma: no cover - argparse restricts the choices
            raise ValueError(f"unknown update action: {action}")
    except (LinkError, ValueError) as exc:
        print(f"GOLEM LINK: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0


def cmd_control(args: argparse.Namespace) -> int:
    from .link import LinkError

    if args.action in ("apply", "restart", "rollback") and not args.yes:
        print(
            f"'{args.action}' changes the remote device. Re-run with --yes to confirm.",
            file=sys.stderr,
        )
        return 2
    try:
        client = _link_client(args)
        node_id = args.node
        if node_id == "self":
            node_id = str(client.me().get("node_id") or "")
            if not node_id:
                raise ValueError("could not resolve this node's id")
        result = client.control_device(node_id, args.action, confirm=args.yes)
    except (LinkError, ValueError) as exc:
        print(f"GOLEM LINK: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="golem-link", description="GOLEM LINK agent")
    parser.add_argument("--version", action="version", version=f"GOLEM LINK {VERSION}")
    sub = parser.add_subparsers(dest="command")

    run_p = sub.add_parser("run", help="run the agent, dashboard and peer API")
    run_p.add_argument("--data-dir", default=None)
    run_p.set_defaults(func=cmd_run)

    setup_p = sub.add_parser("setup", help="create the admin password without the UI")
    setup_p.add_argument("--data-dir", default=None)
    setup_p.add_argument("--name", default="")
    setup_p.add_argument("--password", default="")
    setup_p.set_defaults(func=cmd_setup)

    status_p = sub.add_parser("status", help="print node status from local state")
    status_p.add_argument("--data-dir", default=None)
    status_p.set_defaults(func=cmd_status)

    doctor_p = sub.add_parser("doctor", help="print hardware/software diagnostics")
    doctor_p.add_argument("--data-dir", default=None)
    doctor_p.set_defaults(func=cmd_doctor)

    link_p = sub.add_parser("link", help="pair with another node through the local dashboard")
    link_sub = link_p.add_subparsers(dest="link_command", required=True)

    link_init = link_sub.add_parser("init", help="start pairing and wait for approval")
    link_init.add_argument("--peer", required=True, help="peer IP or IP:port")
    link_init.add_argument("--password", default="")
    link_init.add_argument("--port", type=int, default=8080)
    link_init.add_argument("--wait", type=float, default=300.0)
    link_init.add_argument("--ready-timeout", type=float, default=30.0, dest="ready_timeout")
    link_init.set_defaults(func=cmd_link)

    link_approve = link_sub.add_parser("approve", help="approve a pending request by code")
    link_approve.add_argument("--code", required=True)
    link_approve.add_argument("--password", default="")
    link_approve.add_argument("--port", type=int, default=8080)
    link_approve.add_argument("--wait", type=float, default=120.0)
    link_approve.add_argument("--ready-timeout", type=float, default=30.0, dest="ready_timeout")
    link_approve.set_defaults(func=cmd_link)

    link_discover = link_sub.add_parser(
        "discover", help="list unpaired GOLEM LINK machines found on the LAN"
    )
    link_discover.add_argument("--wait", type=float, default=8.0)
    link_discover.add_argument("--password", default="")
    link_discover.add_argument("--port", type=int, default=8080)
    link_discover.add_argument("--ready-timeout", type=float, default=30.0, dest="ready_timeout")
    link_discover.set_defaults(func=cmd_link)

    link_auto = link_sub.add_parser(
        "auto", help="find the only other machine on the LAN and finish pairing with it"
    )
    link_auto.add_argument("--discovery-wait", type=float, default=12.0, dest="discovery_wait")
    link_auto.add_argument("--wait", type=float, default=300.0)
    link_auto.add_argument("--password", default="")
    link_auto.add_argument("--port", type=int, default=8080)
    link_auto.add_argument("--ready-timeout", type=float, default=30.0, dest="ready_timeout")
    link_auto.set_defaults(func=cmd_link)

    update_p = sub.add_parser(
        "update", help="check, stage and install verified updates (local or fleet)"
    )
    update_p.add_argument("--password", default="")
    update_p.add_argument("--port", type=int, default=8080)
    update_p.add_argument("--ready-timeout", type=float, default=30.0, dest="ready_timeout")
    update_p.set_defaults(update_command="status", func=cmd_update)
    update_sub = update_p.add_subparsers(dest="update_command")
    for name, help_text in (
        ("status", "print the current update state"),
        ("check", "ask this node and its peers for a newer release"),
        ("download", "fetch and verify the staged artifact"),
        ("apply", "install the staged update (requires --yes)"),
        ("rollback", "restore the previous program folder (requires --yes)"),
    ):
        child = update_sub.add_parser(name, help=help_text)
        child.add_argument("--password", default="")
        child.add_argument("--port", type=int, default=8080)
        child.add_argument("--ready-timeout", type=float, default=30.0, dest="ready_timeout")
        if name in ("apply", "rollback"):
            child.add_argument("--yes", action="store_true", help="confirm the restart")
        child.set_defaults(func=cmd_update)
    fleet_p = update_sub.add_parser(
        "fleet", help="per-node versions, or check/apply across every device"
    )
    fleet_p.add_argument("--apply", action="store_true", help="apply on every device")
    fleet_p.add_argument("--yes", action="store_true", help="confirm remote changes")
    fleet_p.add_argument("--password", default="")
    fleet_p.add_argument("--port", type=int, default=8080)
    fleet_p.add_argument("--ready-timeout", type=float, default=30.0, dest="ready_timeout")
    fleet_p.set_defaults(func=cmd_update)

    control_p = sub.add_parser(
        "control", help="send one maintenance action to this node or a paired device"
    )
    control_p.add_argument("node", help="node id, or 'self'")
    control_p.add_argument("action", choices=["check", "apply", "restart", "rollback", "status"])
    control_p.add_argument("--yes", action="store_true", help="confirm remote changes")
    control_p.add_argument("--password", default="")
    control_p.add_argument("--port", type=int, default=8080)
    control_p.add_argument("--ready-timeout", type=float, default=30.0, dest="ready_timeout")
    control_p.set_defaults(func=cmd_control)

    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
