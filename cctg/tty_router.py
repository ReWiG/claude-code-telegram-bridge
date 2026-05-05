"""TTY Router — finds terminals and writes input to them."""
from __future__ import annotations

import os


class TTYRouter:
    RESPONSE_MAP = {
        "allow": "y",
        "deny": "n",
        "allow_all": "a",
    }

    def write_text(self, text: str, tty_path: str) -> bool:
        """Write arbitrary text to a TTY. Returns True on success."""
        try:
            with open(tty_path, "w") as f:
                f.write(text + "\r")
            return True
        except (OSError, PermissionError):
            return False

    def write_response(self, action: str, tty_path: str) -> bool:
        """Write a response action (allow/deny/allow_all) to a TTY."""
        mapped = self.RESPONSE_MAP.get(action, action)
        return self.write_text(mapped, tty_path)

    def find_tty(self, pid: int) -> str | None:
        """Find the TTY device for a given PID from /proc."""
        try:
            fd_path = f"/proc/{pid}/fd/0"
            if os.path.exists(fd_path):
                link = os.readlink(fd_path)
                if link.startswith("/dev/"):
                    return link
        except (OSError, FileNotFoundError):
            pass
        return None

    def find_tty_for_session(self, session_id: str) -> str | None:
        """Find TTY for a session by scanning /proc for claude processes."""
        try:
            for pid_str in os.listdir("/proc"):
                if not pid_str.isdigit():
                    continue
                try:
                    cmdline_path = f"/proc/{pid_str}/cmdline"
                    if os.path.exists(cmdline_path):
                        with open(cmdline_path, "rb") as f:
                            cmdline = f.read()
                        if b"claude" in cmdline:
                            fd_path = f"/proc/{pid_str}/fd/0"
                            if os.path.exists(fd_path):
                                link = os.readlink(fd_path)
                                if link.startswith("/dev/"):
                                    return link
                except (OSError, PermissionError):
                    continue
        except (OSError, PermissionError):
            pass
        return None
