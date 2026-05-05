"""Integration smoke test — verifies all components wire together."""
import json
import os
import tempfile
import pytest

from cctg.db import Database
from cctg.session_manager import SessionManager
from cctg.transcript_watcher import TranscriptWatcher, filter_thinking, format_block


@pytest.mark.asyncio
async def test_full_session_lifecycle():
    """Simulate: start → attach → track → notification → stop."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    try:
        db = Database(db_path)
        await db.init()
        await db.reset_on_startup()

        sm = SessionManager(db)

        event = {
            "type": "session_start",
            "session_id": "abc12345",
            "project_name": "test-project",
            "cwd": "/tmp/test-project",
            "transcript_path": "/tmp/.claude/projects/test-project/abc12345.jsonl",
            "timestamp": 1700000000,
        }
        await sm.process_event(event)
        s = await db.get_session("abc12345")
        assert s is not None
        assert s["status"] == "active"

        await sm.attach("abc12345")
        assert await db.get_state("attached_session") == "abc12345"

        await sm.start_tracking()
        assert await db.get_state("watch_active") == "1"

        await db.add_pending_event("abc12345", "notification", "Permission needed")
        events = await db.get_unprocessed_events()
        assert len(events) == 1
        await db.mark_event_processed(events[0]["id"])

        await sm.stop_tracking()
        assert await db.get_state("watch_active") is None

        await db.set_session_status("abc12345", "exited")
        s = await db.get_session("abc12345")
        assert s["status"] == "exited"

        await db.close()
    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)


class TestThinkingFilter:
    def test_realistic_assistant_message(self):
        content = [
            {"type": "thinking", "thinking": "Let me analyze the user's request..."},
            {"type": "text", "text": "Я нашёл проблему в парсере."},
            {"type": "tool_use", "name": "Read", "input": {"file_path": "/home/user/project/parser.py"}},
            {"type": "thinking", "thinking": "The parser.py file has the issue on line 42."},
            {"type": "text", "text": "Вот исправление для parser.py:42"},
        ]
        result = filter_thinking(content)
        assert len(result) == 3
        assert all(b["type"] != "thinking" for b in result)


class TestTranscriptFormatting:
    def test_format_text_blocks(self):
        assert "hello" in format_block({"type": "text", "text": "hello world"})

    def test_format_tool_use_with_command(self):
        result = format_block({"type": "tool_use", "name": "Bash", "input": {"command": "pytest"}})
        assert "Bash" in result and "pytest" in result

    def test_format_tool_use_with_file(self):
        result = format_block({"type": "tool_use", "name": "Read", "input": {"file_path": "/tmp/x.py"}})
        assert "Read" in result
