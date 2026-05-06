"""Session manager -- registry, attach/detach."""
from __future__ import annotations

import os

from cctg.db import Database


class SessionManager:
    def __init__(self, db: Database):
        self.db = db

    async def attach(self, session_id: str) -> None:
        s = await self.db.get_session(session_id)
        if s is None:
            raise ValueError(f"Session not found: {session_id}")
        await self.db.set_state("attached_session", session_id)

    async def detach(self) -> None:
        await self.db.set_state("attached_session", None)

    async def get_attached_session(self) -> dict | None:
        sid = await self.db.get_state("attached_session")
        if not sid:
            return None
        return await self.db.get_session(sid)

    def discover_and_update(self, session_id: str) -> None:
        """Discover PID and TTY for a session by scanning /proc for claude processes."""
        import asyncio
        try:
            for pid_str in os.listdir("/proc"):
                if not pid_str.isdigit():
                    continue
                try:
                    cmdline_path = f"/proc/{pid_str}/cmdline"
                    if not os.path.exists(cmdline_path):
                        continue
                    with open(cmdline_path, "rb") as f:
                        cmdline = f.read()
                    if b"claude" not in cmdline:
                        continue
                    fd_path = f"/proc/{pid_str}/fd/0"
                    if os.path.exists(fd_path):
                        link = os.readlink(fd_path)
                        if link.startswith("/dev/"):
                            asyncio.ensure_future(
                                self.db._conn.execute(
                                    "UPDATE sessions SET pid=?, tty=? WHERE session_id=?",
                                    (int(pid_str), link, session_id),
                                )
                            )
                            return
                except (OSError, PermissionError, ValueError):
                    continue
        except (OSError, PermissionError):
            pass
