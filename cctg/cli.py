"""CLI for cctg — start, stop, status, daemon (foreground)."""
from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import logging
import os
import select
import signal
import socket
import subprocess
import sys
import termios
import time
import tty
import uuid

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


def cmd_launch(args):
    """Launch a Claude Code session via PTY bridge."""
    cfg = get_config(args.config)
    profile = args.profile
    session_id = str(uuid.uuid4())

    # Find ccs
    ccs_path = os.path.expanduser("~/.nvm/versions/node/v24.11.1/bin/ccs")
    if not os.path.exists(ccs_path):
        ccs_path = "ccs"

    cmd = [ccs_path, profile]
    cwd = os.getcwd()

    from cctg.pty_bridge import PTYBridge
    from cctg.transcript_watcher import TranscriptWatcher

    # Per-session events file: the hook (hooks/session.py) writes session info here.
    # CCTG_EVENTS_FILE is set in the child's environment so the hook knows where to write.
    events_file = f"/tmp/cctg-events-{os.getpid()}.jsonl"
    for p in (events_file,):
        try:
            os.remove(p)
        except OSError:
            pass

    bridge = PTYBridge(cwd=cwd, extra_env={"CCTG_EVENTS_FILE": events_file})
    bridge.start(cmd)

    # Wait for the hook to fire and write session info. Timeout: 8 seconds.
    transcript_path = None
    for _ in range(80):
        bridge.read_output()  # drain PTY output, don't let ccs block
        try:
            with open(events_file, "r") as f:
                for line in f:
                    try:
                        data = json.loads(line.strip())
                    except (json.JSONDecodeError, ValueError):
                        continue
                    if data.get("session_id"):
                        session_id = data["session_id"]
                        transcript_path = data.get("transcript_path", "")
                        break
        except OSError:
            pass
        if transcript_path:
            break
        time.sleep(0.1)
    # Keep events_file around — Notification hook writes permission prompts here.
    # We'll poll it in the main loop alongside the transcript.

    watcher = TranscriptWatcher(transcript_path) if transcript_path else TranscriptWatcher(cwd=cwd)

    print(f"[cctg] Session ID: {session_id}")
    print(f"[cctg] PID:       {bridge.child_pid}")
    print(f"[cctg] CWD:       {cwd}")

    # Save and set terminal to raw mode
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    new_settings = termios.tcgetattr(fd)
    # Disable echo, canonical mode, and CR→NL mapping on input.
    # ICRNL must be off so that Enter produces \r (not \n) —
    # ccs/Ink expects \r as the Enter key in raw mode.
    new_settings[0] = new_settings[0] & ~(termios.ICRNL)
    new_settings[3] = new_settings[3] & ~(termios.ECHO | termios.ICANON)
    new_settings[6][termios.VMIN] = 1
    new_settings[6][termios.VTIME] = 0
    termios.tcsetattr(fd, termios.TCSANOW, new_settings)

    # Handle window resize
    def _handle_winch(signum, frame):
        if bridge.master_fd:
            try:
                size = fcntl.ioctl(fd, termios.TIOCGWINSZ, b"\x00" * 8)
                fcntl.ioctl(bridge.master_fd, termios.TIOCSWINSZ, size)
            except OSError:
                pass

    signal.signal(signal.SIGWINCH, _handle_winch)
    # Set initial window size
    _handle_winch(None, None)

    # Connect to daemon
    SOCKET_PATH = os.path.expanduser("~/.cctg/data/cctg.sock")
    sock = None
    try:
        if os.path.exists(SOCKET_PATH):
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.connect(SOCKET_PATH)
            sock.setblocking(False)
            msg = f"REGISTER|{session_id}|{cwd}|{bridge.child_pid}\n"
            sock.send(msg.encode())
    except (FileNotFoundError, ConnectionRefusedError, OSError) as e:
        print(f"[cctg] ⚠ Daemon not available ({e}) — session works without Telegram")

    events_offset = 0
    notify_sent: set[str] = set()  # dedup tool_use IDs sent to daemon
    try:
        while bridge.is_alive():
            # PTY master -> stdout only (local terminal)
            output = bridge.read_output()
            if output:
                sys.stdout.write(output)
                sys.stdout.flush()

            # Transcript -> daemon (clean model output for Telegram)
            if sock and watcher.find_session_file():
                flush, text, tool_use = watcher.read_new_text()
                if flush:
                    try:
                        sock.send(f"FLUSH|{session_id}\n".encode())
                    except (BlockingIOError, BrokenPipeError, OSError):
                        pass
                if text:
                    try:
                        data = text.encode()
                        sock.send(f"OUTPUT|{session_id}|{len(data)}\n".encode())
                        sock.send(data)
                    except (BlockingIOError, BrokenPipeError, OSError):
                        pass

            # Events file -> only permission_prompt (filters out auto-accepted tools)
            if sock:
                try:
                    size = os.path.getsize(events_file)
                    if size > events_offset:
                        with open(events_file, "r") as f:
                            f.seek(events_offset)
                            for line in f:
                                try:
                                    ev = json.loads(line.strip())
                                except (json.JSONDecodeError, ValueError):
                                    continue
                                if ev.get("type") != "notification":
                                    continue
                                if ev.get("notification_type") != "permission_prompt":
                                    continue
                                tu = watcher.last_tool_use
                                if tu:
                                    tu_id = tu.get("id", "")
                                    if tu_id and tu_id not in notify_sent:
                                        notify_sent.add(tu_id)
                                        payload = json.dumps({"msg": ev.get("message", ""), "tool_use": tu}, ensure_ascii=False)
                                        data = payload.encode()
                                        sock.send(f"NOTIFY|{session_id}|{len(data)}\n".encode())
                                        sock.send(data)
                            events_offset = f.tell()
                except OSError:
                    pass

            # stdin -> PTY master
            r, _, _ = select.select([sys.stdin], [], [], 0.05)
            if r:
                data = os.read(fd, 1024)
                if data:
                    os.write(bridge.master_fd, data)

            # Daemon socket -> PTY master
            if sock:
                r, _, _ = select.select([sock], [], [], 0)
                if r:
                    try:
                        msg = sock.recv(4096)
                        if msg and msg.startswith(b"RESP|"):
                            # Single-char response (y/n/a) — no Enter appended
                            text = msg[5:].rstrip(b'\n')
                            if text:
                                os.write(bridge.master_fd, text)
                        elif msg and msg.startswith(b"INPUT|"):
                            # Full text input — append Enter
                            text = msg[6:].rstrip(b'\n')
                            if text:
                                os.write(bridge.master_fd, text + b"\r")
                    except (BlockingIOError, BrokenPipeError, OSError):
                        pass

    except KeyboardInterrupt:
        pass
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        bridge.stop()
        if sock:
            try:
                sock.send(f"UNREGISTER|{session_id}\n".encode())
                sock.close()
            except OSError:
                pass
        print(f"\r\n[cctg] Session {session_id[:8]} ended.")


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
    launch_parser = sub.add_parser("launch", help="Launch Claude Code session via PTY")
    launch_parser.add_argument("profile", help="CCS profile name (e.g., lanit, deepseek)")
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
    elif args.command == "launch":
        cmd_launch(args)
    elif args.command == "daemon":
        cmd_daemon(args)
    elif args.command == "install":
        cmd_install(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
