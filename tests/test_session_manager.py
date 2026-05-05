"""Tests for cctg.session_manager."""
import os
import tempfile
import pytest
import pytest_asyncio
from cctg.db import Database
from cctg.session_manager import SessionManager


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


@pytest_asyncio.fixture
async def sm(db):
    return SessionManager(db)


@pytest.mark.asyncio
async def test_process_session_start(sm, db):
    event = {
        "type": "session_start",
        "session_id": "abc123",
        "project_name": "my-project",
        "cwd": "/home/user/my-project",
        "transcript_path": "/home/user/.claude/projects/my-project/abc123.jsonl",
        "timestamp": 1700000000,
    }
    await sm.process_event(event)
    s = await db.get_session("abc123")
    assert s is not None
    assert s["project_name"] == "my-project"
    assert s["cwd"] == "/home/user/my-project"
    assert s["status"] == "active"


@pytest.mark.asyncio
async def test_process_session_stop(sm, db):
    await db.add_session("abc123", "p", "/p", "main", "/dev/pts/2", 12345)
    event = {"type": "stop", "session_id": "abc123", "stop_hook_active": False, "timestamp": 1700000001}
    await sm.process_event(event)
    s = await db.get_session("abc123")
    assert s["status"] == "exited"


@pytest.mark.asyncio
async def test_process_notification(sm, db):
    await db.add_session("abc123", "p", "/p", "main", "/dev/pts/2", 12345)
    event = {"type": "notification", "session_id": "abc123", "message": "Permission needed", "timestamp": 1700000000}
    await sm.process_event(event)
    events = await db.get_unprocessed_events()
    assert len(events) == 1
    assert events[0]["type"] == "notification"


@pytest.mark.asyncio
async def test_attach_session(sm, db):
    await db.add_session("abc123", "p", "/p", "main", "/dev/pts/2", 12345)
    await sm.attach("abc123")
    assert await db.get_state("attached_session") == "abc123"
    assert await db.get_state("watch_active") is None


@pytest.mark.asyncio
async def test_attach_clears_previous_watch(sm, db):
    await db.add_session("s1", "p1", "/p1", "m", "/dev/pts/2", 1)
    await db.add_session("s2", "p2", "/p2", "d", "/dev/pts/3", 2)
    await sm.attach("s1")
    await db.set_state("watch_active", "1")
    await sm.attach("s2")
    assert await db.get_state("attached_session") == "s2"
    assert await db.get_state("watch_active") is None


@pytest.mark.asyncio
async def test_detach_session(sm, db):
    await db.add_session("abc123", "p", "/p", "main", "/dev/pts/2", 12345)
    await sm.attach("abc123")
    await sm.detach()
    assert await db.get_state("attached_session") is None
    assert await db.get_state("watch_active") is None


@pytest.mark.asyncio
async def test_start_track_requires_attach(sm):
    with pytest.raises(ValueError, match="attach"):
        await sm.start_tracking()


@pytest.mark.asyncio
async def test_start_track(sm, db):
    await db.add_session("abc123", "p", "/p", "main", "/dev/pts/2", 12345)
    await sm.attach("abc123")
    await sm.start_tracking()
    assert await db.get_state("watch_active") == "1"


@pytest.mark.asyncio
async def test_stop_track_requires_attach(sm):
    with pytest.raises(ValueError, match="attach"):
        await sm.stop_tracking()


@pytest.mark.asyncio
async def test_stop_track(sm, db):
    await db.add_session("abc123", "p", "/p", "main", "/dev/pts/2", 12345)
    await sm.attach("abc123")
    await sm.start_tracking()
    await sm.stop_tracking()
    assert await db.get_state("watch_active") is None


@pytest.mark.asyncio
async def test_get_attached_session(sm, db):
    await db.add_session("abc123", "p", "/p", "main", "/dev/pts/2", 12345)
    await sm.attach("abc123")
    s = await sm.get_attached_session()
    assert s is not None
    assert s["session_id"] == "abc123"


@pytest.mark.asyncio
async def test_get_attached_session_none(sm):
    s = await sm.get_attached_session()
    assert s is None


@pytest.mark.asyncio
async def test_try_attach_nonexistent(sm):
    with pytest.raises(ValueError, match="not found"):
        await sm.attach("nonexistent")


@pytest.mark.asyncio
async def test_discover_and_update_session_tty_pid(sm, db):
    """discover_and_update should not crash even if no TTY found."""
    await db.add_session("abc123", "proj", "/home/user/proj", "main", None, None)
    # This is best-effort, just verify it doesn't crash
    sm.discover_and_update("abc123")
    s = await db.get_session("abc123")
    # May or may not find TTY in CI, just verify no crash
