"""CLI for cctg — start, stop, status, daemon (foreground)."""
from __future__ import annotations

import argparse
import asyncio
import atexit
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

    # Best-effort terminal cleanup at interpreter exit. Covers the case where
    # the main loop never reaches its `finally` (uncaught exception, hard
    # crash, etc.). The normal `finally` block also calls these, but it is
    # idempotent — running it twice is harmless.
    def _restore_terminal():
        try:
            sys.stdout.write(
                "\x1b[?25h"      # show cursor
                "\x1b[?1049l"    # leave alternate screen buffer
                "\x1b[0m"        # reset all attributes
                "\x1b[2J"        # erase entire screen
                "\x1b[H"         # move cursor to (1,1)
            )
            sys.stdout.flush()
        except (OSError, ValueError):
            pass
        try:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        except (termios.error, OSError, ValueError):
            pass

    atexit.register(_restore_terminal)

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
            _sock_send_all(sock,msg.encode())
    except (FileNotFoundError, ConnectionRefusedError, OSError) as e:
        print(f"[cctg] ⚠ Daemon not available ({e}) — session works without Telegram")

    events_offset = 0
    pty_buffer = ""  # recent PTY output for parsing permission dialog options
    last_sent_hash: str | None = None  # dedup by options content
    monitor_until = 0  # keep monitoring buffer until this timestamp
    try:
        while bridge.is_alive():
            # PTY master -> stdout only (local terminal)
            output = bridge.read_output()
            if output:
                sys.stdout.write(output)
                sys.stdout.flush()
                pty_buffer = (pty_buffer + output)[-3000:]

            # Continuous dialog monitoring: when a permission prompt fires,
            # we keep parsing the buffer and send updates whenever the
            # dialog changes (Ink replaces the active dialog on screen).
            # Each successful send extends the monitoring window.
            now = time.time()
            if sock and now < monitor_until:
                options, dialog = _parse_permission_dialog(pty_buffer)
                if options is not None:
                    h = str(options)
                    if h != last_sent_hash:
                        last_sent_hash = h
                        tu = watcher.last_tool_use or {}
                        payload = json.dumps({
                            "msg": dialog or "",
                            "tool_use": tu,
                            "pty_options": options,
                        }, ensure_ascii=False)
                        data = payload.encode()
                        _sock_send_all(sock, f"NOTIFY|{session_id}|{len(data)}\n".encode())
                        _sock_send_all(sock, data)
                        monitor_until = now + 1.5  # extend window

            # Transcript -> daemon (clean model output for Telegram)
            if sock and watcher.find_session_file():
                flush, text, tool_use = watcher.read_new_text()
                if flush:
                    try:
                        _sock_send_all(sock,f"FLUSH|{session_id}\n".encode())
                    except (BlockingIOError, BrokenPipeError, OSError):
                        pass
                if text:
                    try:
                        data = text.encode()
                        _sock_send_all(sock,f"OUTPUT|{session_id}|{len(data)}\n".encode())
                        _sock_send_all(sock,data)
                    except (BlockingIOError, BrokenPipeError, OSError):
                        pass

            # Events file — session starts and permission prompts
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
                                ev_type = ev.get("type", "")
                                if ev_type == "session":
                                    new_sid = ev.get("session_id", "")
                                    new_transcript = ev.get("transcript_path", "")
                                    if new_sid and new_sid != session_id:
                                        # Session changed (/new or /clear) — re-register
                                        try:
                                            _sock_send_all(sock,f"UNREGISTER|{session_id}\n".encode())
                                        except (BlockingIOError, BrokenPipeError, OSError):
                                            pass
                                        session_id = new_sid
                                        watcher = TranscriptWatcher(new_transcript) if new_transcript else TranscriptWatcher(cwd=cwd)
                                        msg = f"REGISTER|{session_id}|{cwd}|{bridge.child_pid}\n"
                                        try:
                                            _sock_send_all(sock,msg.encode())
                                        except (BlockingIOError, BrokenPipeError, OSError):
                                            pass
                                        print(f"\r\n[cctg] Session changed: {session_id[:8]}")
                                elif ev_type == "notification":
                                    if ev.get("notification_type") != "permission_prompt":
                                        continue
                                    # Start monitoring — dialog will be picked up by the
                                    # continuous monitor loop above
                                    monitor_until = time.time() + 3.0
                                    last_sent_hash = None
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
        # 1. Ask Claude Code to shut down gracefully. SIGHUP is the
        #    conventional "terminal disconnected" signal; TUI apps usually
        #    handle it by leaving alternate screen, showing cursor, etc.
        #    We give it up to ~1s, then fall through to a forced reset.
        if bridge.child_pid:
            try:
                os.kill(bridge.child_pid, signal.SIGHUP)
            except (OSError, ProcessLookupError):
                pass
            for _ in range(20):
                if not bridge.is_alive():
                    break
                time.sleep(0.05)

        # 2. Force the parent terminal back to a sane state. Claude Code
        #    (Ink) enters the alternate screen buffer (\e[?1049h) and hides
        #    the cursor (\e[?25l) on startup. When the child is killed by
        #    signal it does NOT clean these up — the alternate buffer stays
        #    active and the cursor stays hidden, so subsequent output ends
        #    up overlaid on top of the old TUI. Reset everything we know
        #    about BEFORE restoring termios so the escapes reach the tty
        #    unfiltered.
        try:
            sys.stdout.write(
                "\x1b[?25h"      # show cursor
                "\x1b[?1049l"    # leave alternate screen buffer
                "\x1b[0m"        # reset all attributes (colors, bold, etc.)
                "\x1b[2J"        # erase entire screen
                "\x1b[H"         # move cursor to (1,1)
            )
            sys.stdout.flush()
        except (OSError, ValueError):
            pass

        # 3. Restore original terminal attributes.
        try:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        except termios.error:
            pass

        # 4. Reset SIGWINCH to default so we don't keep mutating the (now
        #    closed) master fd if the parent shell sends a resize during
        #    interpreter shutdown.
        try:
            signal.signal(signal.SIGWINCH, signal.SIG_DFL)
        except (OSError, ValueError):
            pass

        bridge.stop()
        if sock:
            try:
                _sock_send_all(sock,f"UNREGISTER|{session_id}\n".encode())
                sock.close()
            except OSError:
                pass
        print(f"\r\n[cctg] Session {session_id[:8]} ended.")


def _clean_ansi(text: str) -> str:
    """Strip ANSI and normalise cursor movement to newlines."""
    import re
    clean = re.sub(r'\x1b\[\d+;\d+[Hf]', '\n', text)
    clean = re.sub(r'\x1b\[\d+[BE]', '\n', clean)
    clean = re.sub(r'\x1b\[\d+[GC]', ' ', clean)
    clean = re.sub(r'\x1b\[[\x30-\x3f]*[\x20-\x2f]*[\x40-\x7e]', '', clean)
    clean = re.sub(r'\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)', '', clean)
    clean = re.sub(r'\x1b[^\[\]][\x20-\x7e]', '', clean)
    clean = re.sub(r'  +', ' ', clean)
    return re.sub(r'\n{3,}', '\n\n', clean)


def _sock_send_all(sock, data: bytes) -> bool:
    """Send all bytes on a non-blocking socket, handling partial writes."""
    import errno
    total = 0
    while total < len(data):
        try:
            n = sock.send(data[total:])
            if n > 0:
                total += n
        except (BlockingIOError, InterruptedError):
            continue
        except OSError as e:
            if e.errno == errno.EAGAIN:
                continue
            return False
    return True


def _parse_permission_dialog(pty_output: str) -> tuple[list[str] | None, str | None]:
    """Parse the MOST RECENT permission dialog from PTY output.

    When multiple subagent permissions overlap, Ink re-renders dialogs
    and the buffer contains fragments of several.  We find the *last*
    group of consecutive numbered options and the text preceding it.

    Returns (option_labels, dialog_text).  Each is None if not found.
    """
    import re
    clean = _clean_ansi(pty_output)
    lines = clean.split('\n')

    # Collect all numbered option lines: (line_index, number, label)
    hits = []
    for i, raw in enumerate(lines):
        line = raw.rstrip('\r')
        # Number may have a dot (1.) or just space (1 Yes) — Ink varies
        m = re.match(r'[\s❯▶►▸▹▪▸•·]*([1-9])\.?\s*(.+)', line)
        if not m:
            continue
        label = m.group(2).strip()
        label = re.sub(r'^[○◉◯❍●✓✔☐☑☒▢▣◇◆◈◊⊕⊗⊙⊚⊛∙∘⋆∗]+', '', label).strip()
        # Filter out non-permission lines (status bar "1 0", etc.)
        if label and re.search(r'(?i)\b(yes|no|allow|deny|proceed|cancel|don\'?t)\b', label):
            hits.append((i, int(m.group(1)), label))

    if len(hits) < 2:
        return None, None

    # Group consecutive hits (gap ≤ 2 non-matching lines)
    groups = []
    cur = [hits[0]]
    for prev, h in zip(hits, hits[1:]):
        if h[0] - prev[0] <= 3:
            cur.append(h)
        else:
            groups.append(cur)
            cur = [h]
    groups.append(cur)

    # Take the LAST group (most recent dialog)
    last = groups[-1]
    if len(last) < 2:
        return None, None

    last.sort(key=lambda x: x[1])
    options = [lbl for _, _, lbl in last]

    # Extract dialog text: lines before this group, back to the previous
    # group (or buffer start), taking meaningful non-empty lines.
    prev_end = groups[-2][-1][0] if len(groups) > 1 else 0
    start = last[0][0]
    prefix_lines = lines[prev_end:start]
    meaningful = []
    for raw in prefix_lines:
        l = raw.rstrip('\r').strip()
        if l and not l.startswith('●') and not l.startswith('⏵') and '─' not in l:
            meaningful.append(l)
    dialog = '\n'.join(meaningful[-3:]) if meaningful else None

    return options, dialog


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
