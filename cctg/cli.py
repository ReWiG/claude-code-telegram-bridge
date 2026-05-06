"""CLI for cctg — start, stop, status, daemon (foreground)."""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import subprocess
import sys

from cctg.config import load_config
from cctg.daemon import Daemon

DEFAULT_CONFIG = os.path.expanduser("~/.cctg/config.toml")


def get_config(config_path: str | None = None):
    path = config_path or os.environ.get("CCTG_CONFIG", DEFAULT_CONFIG)
    return load_config(path)


def cmd_start(args):
    """Launch daemon in background via systemd or nohup."""
    try:
        result = subprocess.run(
            ["systemctl", "--user", "start", "cctg"],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            print("✓ cctg started via systemd")
            return
    except FileNotFoundError:
        pass

    cfg = get_config(args.config)
    if os.path.exists(cfg.pid_file):
        with open(cfg.pid_file) as f:
            pid = int(f.read().strip())
        if os.path.exists(f"/proc/{pid}"):
            print(f"cctg already running (PID {pid})")
            return

    print("Starting cctg in background...")
    subprocess.Popen(
        [sys.executable, "-m", "cctg", "daemon", "--config", args.config or DEFAULT_CONFIG],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    print("✓ cctg started")


def cmd_stop(args):
    cfg = get_config(args.config)
    if os.path.exists(cfg.pid_file):
        with open(cfg.pid_file) as f:
            pid = int(f.read().strip())
        try:
            os.kill(pid, signal.SIGTERM)
            print(f"✓ cctg stopped (PID {pid})")
        except ProcessLookupError:
            print("cctg not running")
        try:
            os.unlink(cfg.pid_file)
        except OSError:
            pass
    else:
        try:
            subprocess.run(["systemctl", "--user", "stop", "cctg"], check=True)
            print("✓ cctg stopped via systemd")
        except (FileNotFoundError, subprocess.CalledProcessError):
            print("cctg not running (no PID file)")


def cmd_status(args):
    cfg = get_config(args.config)
    if os.path.exists(cfg.pid_file):
        with open(cfg.pid_file) as f:
            pid = int(f.read().strip())
        if os.path.exists(f"/proc/{pid}"):
            print(f"✓ cctg running (PID {pid})")
            return
    print("✗ cctg not running")


def cmd_daemon(args):
    """Run daemon in foreground (for systemd)."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    cfg = get_config(args.config)

    async def _run():
        daemon = Daemon(cfg)
        loop = asyncio.get_running_loop()
        stopped = False

        async def _shutdown():
            nonlocal stopped
            if not stopped:
                stopped = True
                await daemon.stop()

        def _signal_handler():
            loop.create_task(_shutdown())

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _signal_handler)
            except NotImplementedError:
                pass

        try:
            await daemon.start()
        except KeyboardInterrupt:
            pass
        finally:
            if not stopped:
                stopped = True
                await daemon.stop()

    asyncio.run(_run())


def cmd_install(args):
    """Run the installer script."""
    script = os.path.join(os.path.dirname(__file__), "..", "..", "install.sh")
    script = os.path.abspath(script)
    if os.path.exists(script):
        subprocess.run(["bash", script], check=True)
    else:
        print("install.sh not found. Run it from the repository root.")


def main():
    parser = argparse.ArgumentParser(prog="cctg", description="Claude Code Telegram Bridge")
    parser.add_argument("--config", "-c", help="Path to config.toml")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("start", help="Start daemon")
    sub.add_parser("stop", help="Stop daemon")
    sub.add_parser("status", help="Show daemon status")
    sub.add_parser("restart", help="Restart daemon")
    sub.add_parser("daemon", help="Run daemon in foreground")
    sub.add_parser("install", help="Run interactive installer")

    args = parser.parse_args()

    if args.command == "start":
        cmd_start(args)
    elif args.command == "stop":
        cmd_stop(args)
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "restart":
        cmd_stop(args)
        cmd_start(args)
    elif args.command == "daemon":
        cmd_daemon(args)
    elif args.command == "install":
        cmd_install(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
