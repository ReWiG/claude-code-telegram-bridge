"""Main daemon — PTY session manager and Telegram bridge."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time

from cctg.config import Config
from cctg.db import Database
from cctg.session_manager import SessionManager
from cctg.tty_router import TTYRouter
from cctg.telegram_handler import TelegramHandler

logger = logging.getLogger(__name__)


class Daemon:
    def __init__(self, config: Config):
        self.config = config
        self.db: Database | None = None
        self.session_manager: SessionManager | None = None
        self.tty_router: TTYRouter | None = None
        self.telegram: TelegramHandler | None = None
        self._running = False
        self._bridge_writers: dict[str, asyncio.StreamWriter] = {}

    async def start(self) -> None:
        await self._ensure_dirs()
        self.db = Database(self.config.db_path)
        await self.db.init()
        await self.db.reset_on_startup()
        self.session_manager = SessionManager(self.db)
        self.tty_router = TTYRouter()
        self.telegram = TelegramHandler(
            token=self.config.telegram_token,
            chat_id=self.config.telegram_chat_id,
            db=self.db,
            session_manager=self.session_manager,
            tty_router=self.tty_router,
            proxy=self.config.telegram_proxy,
        )
        self.telegram.set_input_callback(self.inject_input)
        self.telegram.set_response_callback(self.inject_response)
        await self.telegram.start()
        self._write_pid()
        self._running = True
        logger.info("Daemon started")
        # Start Unix socket server for bridge connections
        self._socket_path = os.path.join(
            os.path.expanduser(self.config.install_dir), "data", "cctg.sock"
        )
        if os.path.exists(self._socket_path):
            os.unlink(self._socket_path)
        self._socket_server = await asyncio.start_unix_server(
            self._handle_bridge_connection, self._socket_path
        )
        logger.info("Bridge socket listening on %s", self._socket_path)
        await self._main_loop()

    async def stop(self) -> None:
        self._running = False
        if hasattr(self, '_socket_server') and self._socket_server:
            self._socket_server.close()
        if hasattr(self, '_socket_path') and os.path.exists(self._socket_path):
            os.unlink(self._socket_path)
        for writer in self._bridge_writers.values():
            writer.close()
        if self.telegram:
            await self.telegram.stop()
        if self.db:
            await self.db.close()
        logger.info("Daemon stopped")

    async def inject_input(self, session_id: str, text: str) -> bool:
        """Send text to a session's PTY via Unix socket (with Enter appended)."""
        writer = self._bridge_writers.get(session_id)
        if not writer:
            return False
        try:
            writer.write(f"INPUT|{text}\n".encode())
            await writer.drain()
            return True
        except (BrokenPipeError, ConnectionResetError, OSError):
            return False

    async def inject_response(self, session_id: str, text: str) -> bool:
        """Send a single-char response to PTY (no Enter appended)."""
        writer = self._bridge_writers.get(session_id)
        if not writer:
            return False
        try:
            writer.write(f"RESP|{text}\n".encode())
            await writer.drain()
            return True
        except (BrokenPipeError, ConnectionResetError, OSError):
            return False

    async def _handle_bridge_connection(self, reader, writer):
        session_id = None
        try:
            while self._running:
                line = await reader.readline()
                if not line:
                    break
                line = line.decode().strip()
                if line.startswith("REGISTER|"):
                    _, session_id, cwd, pid_str = line.split("|")
                    await self.db.add_session(
                        session_id=session_id,
                        project_name=os.path.basename(cwd),
                        cwd=cwd, branch=None, tty=None, pid=int(pid_str),
                    )
                    self._bridge_writers[session_id] = writer
                    logger.info("Session %s registered (%s PID %s)", session_id[:8], cwd, pid_str)
                elif line.startswith("FLUSH|"):
                    _, sid = line.split("|", 1)
                    await self.db.clear_live_message(sid)
                elif line.startswith("NOTIFY|"):
                    _, sid, length = line.split("|")
                    data = await reader.readexactly(int(length))
                    payload = json.loads(data.decode("utf-8", errors="replace"))
                    # Only forward if this session is attached
                    attached_id = await self.db.get_state("attached_session")
                    if attached_id != sid:
                        continue
                    s = await self.db.get_session(sid)
                    if s:
                        await self.telegram.send_permission_prompt(
                            s, payload.get("msg", ""), payload.get("tool_use"),
                            payload.get("pty_options"),
                        )
                elif line.startswith("OUTPUT|") and session_id:
                    _, sid, length = line.split("|")
                    data = await reader.readexactly(int(length))
                    text = data.decode("utf-8", errors="replace")
                    await self._handle_session_output(sid, text)
                elif line.startswith("UNREGISTER|"):
                    _, sid = line.split("|", 1)
                    self._bridge_writers.pop(sid, None)
                    await self.db.set_session_status(sid, "exited")
                    logger.info("Session %s unregistered", sid[:8])
                    # Auto-detach if was attached
                    attached_id = await self.db.get_state("attached_session")
                    if attached_id == sid:
                        await self.session_manager.detach()
                        self.telegram.clear_active_perm()
                        s = await self.db.get_session(sid)
                        cwd = s["cwd"] if s else "?"
                        await self.telegram.send_message(
                            f"\U0001f50c <b>Сессия закрыта</b>\n\U0001f4c1 {cwd}\n\n"
                            "Привязка автоматически снята.",
                            reply_markup=self.telegram._build_kb(attached=False),
                        )
        except (asyncio.IncompleteReadError, ConnectionResetError, BrokenPipeError):
            pass
        finally:
            if session_id:
                self._bridge_writers.pop(session_id, None)
                # Mark session as exited if not already
                s = await self.db.get_session(session_id)
                if s and s["status"] == "active":
                    await self.db.set_session_status(session_id, "exited")
                    logger.info("Session %s disconnected (no UNREGISTER)", session_id[:8])
                    # Auto-detach
                    attached_id = await self.db.get_state("attached_session")
                    if attached_id == session_id:
                        await self.session_manager.detach()
                        self.telegram.clear_active_perm()
                        cwd = s["cwd"]
                        await self.telegram.send_message(
                            f"🔌 <b>Сессия закрыта</b>\n📁 {cwd}\n\n"
                            "Привязка автоматически снята.",
                            reply_markup=self.telegram._build_kb(attached=False),
                        )
            writer.close()

    async def _handle_session_output(self, session_id: str, text: str) -> None:
        """Forward session output to Telegram if attached."""
        attached_id = await self.db.get_state("attached_session")
        if attached_id != session_id:
            return
        # Strip ANSI escape sequences for Telegram
        import re
        # CSI: ESC [ param* interm* final (catches \e[?1049h, \e[2J, etc.)
        text = re.sub(r'\x1b\[[\x30-\x3f]*[\x20-\x2f]*[\x40-\x7e]', '', text)
        # OSC: ESC ] ... (BEL|ST) — e.g. \e]0;title\a, \e]8;;link\a
        text = re.sub(r'\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)', '', text)
        # Other escape sequences: ESC + char (e.g. ESC c for reset)
        text = re.sub(r'\x1b[^\[\]][\x20-\x7e]', '', text)
        if not text.strip():
            return
        live = await self.db.get_live_message(session_id)
        s = await self.db.get_session(session_id)
        cwd = s["cwd"] if s else "?"
        if live:
            new_buffer = live["buffer"] + text
            if len(new_buffer) > 4000:
                new_buffer = new_buffer[-4000:]
            ok = await self.telegram.edit_message(
                live["telegram_msg_id"],
                f"\U0001f4ac <b>Claude Code (#{session_id[:8]} {cwd})</b>\n\n{new_buffer}"
            )
            if ok:
                await self.db.update_live_message_buffer(live["id"], new_buffer)
            else:
                await self.db.clear_live_message(session_id)
                msg_id = await self.telegram.send_message(
                    f"\U0001f4ac <b>Claude Code (#{session_id[:8]} {cwd})</b>\n\n{text}"
                )
                if msg_id:
                    await self.db.create_live_message(session_id, msg_id, text)
        else:
            msg_id = await self.telegram.send_message(
                f"\U0001f4ac <b>Claude Code (#{session_id[:8]} {cwd})</b>\n\n{text}"
            )
            if msg_id:
                await self.db.create_live_message(session_id, msg_id, text)

    async def _main_loop(self) -> None:
        cleanup_interval = self.config.session_cleanup_seconds
        archive_interval = 3600  # GC exited sessions / live_messages once an hour
        last_cleanup = 0
        last_archive = 0
        while self._running:
            try:
                now = time.time()
                if now - last_cleanup >= cleanup_interval:
                    await self._cleanup_stale_sessions()
                    last_cleanup = now
                if now - last_archive >= archive_interval:
                    await self.db.cleanup_old_sessions()
                    await self.db.cleanup_old_live_messages()
                    last_archive = now
                await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"Main loop error: {e}", exc_info=True)
                await asyncio.sleep(5)

    async def _cleanup_stale_sessions(self) -> None:
        """Mark sessions as exited if their bridge writer is disconnected or PID is dead."""
        for sid, writer in list(self._bridge_writers.items()):
            if writer.is_closing():
                self._bridge_writers.pop(sid, None)
                s = await self.db.get_session(sid)
                if s and s["status"] == "active":
                    await self._mark_session_exited(sid, s["cwd"], reason="writer closed")

        # Bridge may have died without sending UNREGISTER (e.g. kill -9 on the
        # launch process). Detect by checking if the session's PID is still alive.
        for s in await self.db.list_active_sessions():
            sid = s["session_id"]
            if sid in self._bridge_writers:
                continue  # handled above
            pid = s.get("pid")
            if pid and not os.path.exists(f"/proc/{pid}"):
                await self._mark_session_exited(sid, s["cwd"], reason=f"PID {pid} dead")

    async def _mark_session_exited(self, sid: str, cwd: str, reason: str) -> None:
        await self.db.set_session_status(sid, "exited")
        logger.info("Session %s stale (%s)", sid[:8], reason)
        attached_id = await self.db.get_state("attached_session")
        if attached_id == sid:
            await self.session_manager.detach()
            self.telegram.clear_active_perm()
            await self.telegram.send_message(
                f"🔌 <b>Сессия закрыта</b>\n📁 {cwd}\n\n"
                "Привязка автоматически снята.",
                reply_markup=self.telegram._build_kb(attached=False),
            )

    async def _ensure_dirs(self) -> None:
        install_dir = os.path.expanduser(self.config.install_dir)
        os.makedirs(os.path.join(install_dir, "data"), exist_ok=True)

    def _write_pid(self) -> None:
        with open(self.config.pid_file, "w") as f:
            f.write(str(os.getpid()))
