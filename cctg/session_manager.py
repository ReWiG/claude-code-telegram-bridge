"""Session manager -- registry, attach/detach, tracking control."""
from __future__ import annotations

from cctg.db import Database


class SessionManager:
    def __init__(self, db: Database):
        self.db = db

    async def process_event(self, event: dict) -> None:
        event_type = event.get("type", "")
        session_id = event.get("session_id", "")

        if event_type == "session_start":
            await self.db.add_session(
                session_id=session_id,
                project_name=event.get("project_name"),
                cwd=event.get("cwd", ""),
                branch=None,
                tty=None,
                pid=None,
            )
        elif event_type == "stop":
            await self.db.set_session_status(session_id, "exited")
        elif event_type == "notification":
            await self.db.add_pending_event(
                session_id=session_id,
                event_type="notification",
                payload=event.get("message", ""),
            )

    async def attach(self, session_id: str) -> None:
        s = await self.db.get_session(session_id)
        if s is None:
            raise ValueError(f"Session not found: {session_id}")
        await self.db.set_state("attached_session", session_id)
        await self.db.set_state("watch_active", None)

    async def detach(self) -> None:
        await self.db.set_state("attached_session", None)
        await self.db.set_state("watch_active", None)

    async def start_tracking(self) -> None:
        attached = await self.db.get_state("attached_session")
        if not attached:
            raise ValueError("No session attached. Use /attach first.")
        await self.db.set_state("watch_active", "1")

    async def stop_tracking(self) -> None:
        attached = await self.db.get_state("attached_session")
        if not attached:
            raise ValueError("No session attached. Use /attach first.")
        await self.db.set_state("watch_active", None)

    async def is_tracking(self) -> bool:
        return await self.db.get_state("watch_active") == "1"

    async def get_attached_session(self) -> dict | None:
        sid = await self.db.get_state("attached_session")
        if not sid:
            return None
        return await self.db.get_session(sid)
