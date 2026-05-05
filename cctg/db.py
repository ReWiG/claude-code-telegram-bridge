"""SQLite database layer for cctg."""
from __future__ import annotations

import time
import aiosqlite


class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._conn: aiosqlite.Connection | None = None

    async def init(self) -> None:
        self._conn = await aiosqlite.connect(self.db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                session_id   TEXT PRIMARY KEY,
                project_name TEXT,
                cwd          TEXT NOT NULL,
                branch       TEXT,
                tty          TEXT,
                pid          INTEGER,
                status       TEXT DEFAULT 'active',
                started_at   INTEGER NOT NULL,
                exited_at    INTEGER
            )
        """)
        await self._conn.execute("""
            CREATE TABLE IF NOT EXISTS state (
                key   TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        await self._conn.execute("""
            CREATE TABLE IF NOT EXISTS pending_events (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id  TEXT NOT NULL,
                type        TEXT NOT NULL,
                payload     TEXT NOT NULL,
                created_at  INTEGER NOT NULL,
                processed   INTEGER DEFAULT 0
            )
        """)
        await self._conn.execute("""
            CREATE TABLE IF NOT EXISTS live_messages (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id       TEXT NOT NULL,
                telegram_msg_id  INTEGER NOT NULL,
                buffer           TEXT DEFAULT '',
                created_at       INTEGER NOT NULL
            )
        """)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()

    async def add_session(
        self, session_id: str, project_name: str | None, cwd: str,
        branch: str | None, tty: str | None, pid: int | None,
    ) -> None:
        await self._conn.execute(
            """INSERT OR REPLACE INTO sessions
               (session_id, project_name, cwd, branch, tty, pid, started_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (session_id, project_name, cwd, branch, tty, pid, int(time.time())),
        )
        await self._conn.commit()

    async def get_session(self, session_id: str) -> dict | None:
        cursor = await self._conn.execute(
            "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def list_active_sessions(self) -> list[dict]:
        cursor = await self._conn.execute(
            "SELECT * FROM sessions WHERE status = 'active' ORDER BY started_at ASC"
        )
        return [dict(row) for row in await cursor.fetchall()]

    async def set_session_status(self, session_id: str, status: str) -> None:
        await self._conn.execute(
            "UPDATE sessions SET status = ?, exited_at = ? WHERE session_id = ?",
            (status, int(time.time()) if status == "exited" else None, session_id),
        )
        await self._conn.commit()

    async def cleanup_old_sessions(self, max_age_seconds: int = 86400) -> None:
        cutoff = int(time.time()) - max_age_seconds
        await self._conn.execute(
            "DELETE FROM sessions WHERE status = 'exited' AND exited_at IS NOT NULL AND exited_at < ?",
            (cutoff,),
        )
        await self._conn.commit()

    async def get_state(self, key: str) -> str | None:
        cursor = await self._conn.execute(
            "SELECT value FROM state WHERE key = ?", (key,)
        )
        row = await cursor.fetchone()
        return row["value"] if row else None

    async def set_state(self, key: str, value: str) -> None:
        await self._conn.execute(
            "INSERT OR REPLACE INTO state (key, value) VALUES (?, ?)", (key, value)
        )
        await self._conn.commit()

    async def add_pending_event(self, session_id: str, event_type: str, payload: str) -> None:
        await self._conn.execute(
            "INSERT INTO pending_events (session_id, type, payload, created_at) VALUES (?, ?, ?, ?)",
            (session_id, event_type, payload, int(time.time())),
        )
        await self._conn.commit()

    async def get_unprocessed_events(self) -> list[dict]:
        cursor = await self._conn.execute(
            "SELECT * FROM pending_events WHERE processed = 0 ORDER BY id ASC"
        )
        return [dict(row) for row in await cursor.fetchall()]

    async def mark_event_processed(self, event_id: int) -> None:
        await self._conn.execute(
            "UPDATE pending_events SET processed = 1 WHERE id = ?", (event_id,)
        )
        await self._conn.commit()

    async def cleanup_old_events(self, max_age_seconds: int = 3600) -> None:
        cutoff = int(time.time()) - max_age_seconds
        await self._conn.execute(
            "DELETE FROM pending_events WHERE processed = 1 AND created_at < ?",
            (cutoff,),
        )
        await self._conn.commit()

    async def create_live_message(self, session_id: str, telegram_msg_id: int, buffer: str = "") -> int:
        cursor = await self._conn.execute(
            "INSERT INTO live_messages (session_id, telegram_msg_id, buffer, created_at) VALUES (?, ?, ?, ?)",
            (session_id, telegram_msg_id, buffer, int(time.time())),
        )
        await self._conn.commit()
        return cursor.lastrowid

    async def get_live_message(self, session_id: str) -> dict | None:
        cursor = await self._conn.execute(
            "SELECT * FROM live_messages WHERE session_id = ? ORDER BY id DESC LIMIT 1",
            (session_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def update_live_message_buffer(self, msg_id: int, buffer: str) -> None:
        await self._conn.execute(
            "UPDATE live_messages SET buffer = ? WHERE id = ?", (buffer, msg_id)
        )
        await self._conn.commit()

    async def clear_live_message(self, session_id: str) -> None:
        await self._conn.execute(
            "DELETE FROM live_messages WHERE session_id = ?", (session_id,)
        )
        await self._conn.commit()

    async def cleanup_old_live_messages(self, max_age_seconds: int = 86400) -> None:
        cutoff = int(time.time()) - max_age_seconds
        await self._conn.execute(
            "DELETE FROM live_messages WHERE created_at < ?", (cutoff,)
        )
        await self._conn.commit()

    async def reset_on_startup(self) -> None:
        await self._conn.execute("DELETE FROM pending_events")
        await self._conn.execute("DELETE FROM live_messages")
        await self._conn.execute("DELETE FROM state")
        await self._conn.commit()
        # Note: sessions are NOT cleared — dead ones will be detected by _detect_exited_sessions
