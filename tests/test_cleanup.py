"""Tests for cctg.cleanup."""
import json
import os
import tempfile
import pytest
import pytest_asyncio
from cctg.cleanup import CleanupWorker
from cctg.db import Database


@pytest_asyncio.fixture
async def db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    database = Database(path)
    await database.init()
    yield database
    await database.close()
    if os.path.exists(path):
        os.unlink(path)


@pytest.fixture
def events_file():
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    os.close(fd)
    yield path
    for p in [path, path.replace(".jsonl", ".processing.jsonl")]:
        if os.path.exists(p):
            os.unlink(p)


@pytest.mark.asyncio
async def test_cleanup_expired_sessions(db):
    await db.add_session("s1", "p1", "/p1", "main", "/dev/pts/2", 999999)
    await db.set_session_status("s1", "exited")
    await db._conn.execute("UPDATE sessions SET exited_at = 1 WHERE session_id = 's1'")
    await db._conn.commit()

    worker = CleanupWorker(db, events_file="/tmp/nonexistent.jsonl")
    await worker.run_once()
    assert await db.get_session("s1") is None


@pytest.mark.asyncio
async def test_cleanup_processes_events_file(db, events_file):
    with open(events_file, "w") as f:
        f.write('{"type":"session_start","session_id":"abc","cwd":"/p","timestamp":1}\n')

    worker = CleanupWorker(db, events_file=events_file)
    await worker.run_once()

    s = await db.get_session("abc")
    assert s is not None
    assert not os.path.exists(events_file)


@pytest.mark.asyncio
async def test_cleanup_old_pending_events(db):
    await db.add_pending_event("s1", "notification", "test")
    events = await db.get_unprocessed_events()
    await db.mark_event_processed(events[0]["id"])
    await db._conn.execute("UPDATE pending_events SET created_at = 1 WHERE id = ?", (events[0]["id"],))
    await db._conn.commit()

    worker = CleanupWorker(db, events_file="/tmp/nonexistent.jsonl")
    await worker.run_once()
    remaining = await db.get_unprocessed_events()
    assert len(remaining) == 0


@pytest.mark.asyncio
async def test_mark_exited_for_missing_pids(db):
    await db.add_session("ghost", "p", "/p", "main", "/dev/pts/2", 99999999)
    worker = CleanupWorker(db, events_file="/tmp/nonexistent.jsonl")
    await worker.run_once()
    s = await db.get_session("ghost")
    assert s["status"] == "exited"
