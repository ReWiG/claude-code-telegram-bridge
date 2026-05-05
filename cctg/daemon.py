"""Main daemon — async event loop tying all components together."""
from __future__ import annotations

import asyncio
import logging
import os
import signal

from cctg.config import Config
from cctg.db import Database
from cctg.session_manager import SessionManager
from cctg.transcript_watcher import TranscriptWatcher
from cctg.tty_router import TTYRouter
from cctg.telegram_handler import TelegramHandler
from cctg.cleanup import CleanupWorker

logger = logging.getLogger(__name__)


class Daemon:
    def __init__(self, config: Config):
        self.config = config
        self.db: Database | None = None
        self.session_manager: SessionManager | None = None
        self.transcript_watcher: TranscriptWatcher | None = None
        self.tty_router: TTYRouter | None = None
        self.telegram: TelegramHandler | None = None
        self.cleanup_worker: CleanupWorker | None = None
        self._running = False

    async def start(self) -> None:
        await self._ensure_dirs()

        self.db = Database(self.config.db_path)
        await self.db.init()
        await self.db.reset_on_startup()

        self.session_manager = SessionManager(self.db)
        self.transcript_watcher = TranscriptWatcher(self.config.transcript_base)
        self.tty_router = TTYRouter()
        self.cleanup_worker = CleanupWorker(self.db, self.config.events_file)

        self.telegram = TelegramHandler(
            token=self.config.telegram_token,
            chat_id=self.config.telegram_chat_id,
            db=self.db,
            session_manager=self.session_manager,
            tty_router=self.tty_router,
            proxy=self.config.telegram_proxy,
        )
        await self.telegram.start()

        self._write_pid()
        self._running = True
        logger.info("Daemon started")

        await self._main_loop()

    async def stop(self) -> None:
        self._running = False
        if self.telegram:
            await self.telegram.stop()
        if self.db:
            await self.db.close()
        logger.info("Daemon stopped")

    async def _main_loop(self) -> None:
        cleanup_interval = self.config.session_cleanup_seconds
        ticks = 0

        while self._running:
            try:
                await self.cleanup_worker.run_once()
                await self._process_pending_events()
                await self._poll_transcripts()

                ticks += 1
                if ticks % cleanup_interval == 0:
                    await self.cleanup_worker._detect_exited_sessions()

                await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"Main loop error: {e}", exc_info=True)
                await asyncio.sleep(5)

    async def _process_pending_events(self) -> None:
        events = await self.db.get_unprocessed_events()
        for event in events:
            if event["type"] == "notification":
                session = await self.db.get_session(event["session_id"])
                if session:
                    await self.telegram.send_permission_prompt(session, event["payload"])
            await self.db.mark_event_processed(event["id"])

    async def _poll_transcripts(self) -> None:
        if not await self.session_manager.is_tracking():
            return

        attached = await self.session_manager.get_attached_session()
        if not attached:
            return

        sid = attached["session_id"]
        project_name = attached.get("project_name", "")
        transcript_path = os.path.join(
            os.path.expanduser(self.config.transcript_base),
            project_name,
            f"{sid}.jsonl",
        )

        if not os.path.exists(transcript_path):
            return

        new_texts = self.transcript_watcher.read_new_lines(transcript_path)
        if not new_texts:
            return

        combined = "\n".join(new_texts)
        live = await self.db.get_live_message(sid)

        if live:
            new_buffer = live["buffer"] + "\n" + combined
            if len(new_buffer) > 4000:
                new_buffer = new_buffer[-4000:]
            ok = await self.telegram.edit_message(
                live["telegram_msg_id"],
                f"\U0001f4ac <b>Claude Code (#{sid[:8]} {attached['cwd']})</b>\n\n{new_buffer}"
            )
            if ok:
                await self.db.update_live_message_buffer(live["id"], new_buffer)
            else:
                await self.db.clear_live_message(sid)
                msg_id = await self.telegram.send_message(
                    f"\U0001f4ac <b>Claude Code (#{sid[:8]} {attached['cwd']})</b>\n\n{combined}"
                )
                if msg_id:
                    await self.db.create_live_message(sid, msg_id, combined)
        else:
            msg_id = await self.telegram.send_message(
                f"\U0001f4ac <b>Claude Code (#{sid[:8]} {attached['cwd']})</b>\n\n{combined}"
            )
            if msg_id:
                await self.db.create_live_message(sid, msg_id, combined)

    async def _ensure_dirs(self) -> None:
        install_dir = os.path.expanduser(self.config.install_dir)
        os.makedirs(os.path.join(install_dir, "data"), exist_ok=True)

    def _write_pid(self) -> None:
        with open(self.config.pid_file, "w") as f:
            f.write(str(os.getpid()))
