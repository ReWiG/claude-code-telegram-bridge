"""Periodic cleanup worker — processes events file, cleans old data, detects exited sessions."""
from __future__ import annotations

import json
import os
import logging

from cctg.db import Database

logger = logging.getLogger(__name__)


class CleanupWorker:
    def __init__(self, db: Database, events_file: str):
        self.db = db
        self.events_file = events_file

    async def run_once(self) -> None:
        await self._process_events_file()
        await self._detect_exited_sessions()
        await self._cleanup_old_data()

    async def _process_events_file(self) -> None:
        if not os.path.exists(self.events_file):
            return
        processing_file = self.events_file.replace(".jsonl", ".processing.jsonl")
        try:
            os.rename(self.events_file, processing_file)
        except OSError:
            return
        try:
            with open(processing_file, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    await self._handle_event(event)
        finally:
            if os.path.exists(processing_file):
                os.unlink(processing_file)

    async def _handle_event(self, event: dict) -> None:
        event_type = event.get("type", "")
        session_id = event.get("session_id", "")
        if event_type == "session_start":
            await self.db.add_session(
                session_id=session_id,
                project_name=event.get("project_name"),
                cwd=event.get("cwd", ""),
                branch=None, tty=None, pid=None,
            )
        elif event_type == "stop":
            logger.info("Stop event for session %s (stop_hook_active=%s) — ignored, using /proc detection instead",
                        session_id[:8], event.get("stop_hook_active"))
        elif event_type == "notification":
            await self.db.add_pending_event(
                session_id=session_id,
                event_type="notification",
                payload=event.get("message", ""),
            )

    async def _detect_exited_sessions(self) -> None:
        sessions = await self.db.list_active_sessions()
        for s in sessions:
            pid = s.get("pid")
            if pid and not self._pid_exists(pid):
                logger.info(f"Session {s['session_id'][:8]} PID {pid} gone, marking exited")
                await self.db.set_session_status(s["session_id"], "exited")

    def _pid_exists(self, pid: int) -> bool:
        return os.path.exists(f"/proc/{pid}")

    async def _cleanup_old_data(self) -> None:
        await self.db.cleanup_old_sessions(max_age_seconds=86400)
        await self.db.cleanup_old_events(max_age_seconds=3600)
        await self.db.cleanup_old_live_messages(max_age_seconds=86400)
