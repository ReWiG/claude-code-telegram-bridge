"""Tests for cctg.db."""
import pytest
from cctg.db import Database


@pytest.mark.asyncio
async def test_init_creates_tables(tmp_db_path):
    db = Database(tmp_db_path)
    await db.init()

    cursor = await db._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    tables = [row[0] async for row in cursor]

    assert "sessions" in tables
    assert "state" in tables
    assert "pending_events" in tables
    assert "live_messages" in tables
    await db.close()


@pytest.mark.asyncio
async def test_add_session(tmp_db_path):
    db = Database(tmp_db_path)
    await db.init()

    await db.add_session(
        session_id="abc123",
        project_name="my-project",
        cwd="/home/user/my-project",
        branch="main",
        tty="/dev/pts/2",
        pid=12345,
    )

    session = await db.get_session("abc123")
    assert session is not None
    assert session["session_id"] == "abc123"
    assert session["project_name"] == "my-project"
    assert session["cwd"] == "/home/user/my-project"
    assert session["branch"] == "main"
    assert session["tty"] == "/dev/pts/2"
    assert session["pid"] == 12345
    assert session["status"] == "active"
    await db.close()


@pytest.mark.asyncio
async def test_list_active_sessions(tmp_db_path):
    db = Database(tmp_db_path)
    await db.init()

    await db.add_session("s1", "p1", "/p1", "main", "/dev/pts/2", 1)
    await db.add_session("s2", "p2", "/p2", "dev", "/dev/pts/3", 2)
    await db.set_session_status("s1", "exited")

    active = await db.list_active_sessions()
    assert len(active) == 1
    assert active[0]["session_id"] == "s2"
    await db.close()


@pytest.mark.asyncio
async def test_set_and_get_state(tmp_db_path):
    db = Database(tmp_db_path)
    await db.init()

    await db.set_state("attached_session", "abc123")
    await db.set_state("watch_active", "1")

    assert await db.get_state("attached_session") == "abc123"
    assert await db.get_state("watch_active") == "1"
    assert await db.get_state("nonexistent") is None
    await db.close()


@pytest.mark.asyncio
async def test_add_pending_event(tmp_db_path):
    db = Database(tmp_db_path)
    await db.init()

    await db.add_pending_event("s1", "notification", '{"message":"test"}')

    events = await db.get_unprocessed_events()
    assert len(events) == 1
    assert events[0]["session_id"] == "s1"
    assert events[0]["type"] == "notification"
    assert events[0]["processed"] == 0
    await db.close()


@pytest.mark.asyncio
async def test_mark_event_processed(tmp_db_path):
    db = Database(tmp_db_path)
    await db.init()

    await db.add_pending_event("s1", "notification", '{"m":"t"}')
    events = await db.get_unprocessed_events()
    await db.mark_event_processed(events[0]["id"])

    remaining = await db.get_unprocessed_events()
    assert len(remaining) == 0
    await db.close()


@pytest.mark.asyncio
async def test_live_message_operations(tmp_db_path):
    db = Database(tmp_db_path)
    await db.init()

    msg_id = await db.create_live_message("s1", 555, "hello")
    assert msg_id > 0

    msg = await db.get_live_message("s1")
    assert msg is not None
    assert msg["telegram_msg_id"] == 555
    assert msg["buffer"] == "hello"

    await db.update_live_message_buffer(msg["id"], "hello world")
    msg = await db.get_live_message("s1")
    assert msg["buffer"] == "hello world"

    await db.clear_live_message("s1")
    assert await db.get_live_message("s1") is None
    await db.close()


@pytest.mark.asyncio
async def test_cleanup_old_data(tmp_db_path):
    db = Database(tmp_db_path)
    await db.init()

    await db.add_session("old", "p", "/p", "m", "/dev/pts/2", 1)
    # Force old exited_at via direct SQL
    await db._conn.execute(
        "UPDATE sessions SET status='exited', exited_at=1 WHERE session_id='old'"
    )
    await db._conn.commit()

    await db.cleanup_old_sessions(max_age_seconds=86400)
    assert await db.get_session("old") is None
    await db.close()


@pytest.mark.asyncio
async def test_reset_on_startup(tmp_db_path):
    db = Database(tmp_db_path)
    await db.init()

    await db.add_session("s1", "p1", "/p1", "main", "/dev/pts/2", 1)
    await db.set_state("attached_session", "s1")
    await db.set_state("watch_active", "1")
    await db.add_pending_event("s1", "notification", '{"m":"t"}')
    await db.create_live_message("s1", 555, "buf")

    await db.reset_on_startup()

    assert await db.get_state("attached_session") is None
    assert await db.get_state("watch_active") is None
    assert len(await db.get_unprocessed_events()) == 0
    assert await db.get_live_message("s1") is None
    s = await db.get_session("s1")
    assert s["status"] == "exited"
    await db.close()
