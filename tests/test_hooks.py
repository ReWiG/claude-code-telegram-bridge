"""Tests for Claude Code hooks."""
import json
import os
import sys
import subprocess
import pytest


EVENTS_FILE = "/tmp/test-cc-events.jsonl"


@pytest.fixture(autouse=True)
def clean_events():
    if os.path.exists(EVENTS_FILE):
        os.unlink(EVENTS_FILE)
    yield
    if os.path.exists(EVENTS_FILE):
        os.unlink(EVENTS_FILE)


def read_events():
    if not os.path.exists(EVENTS_FILE):
        return []
    with open(EVENTS_FILE) as f:
        return [json.loads(line) for line in f if line.strip()]


def run_hook(script, stdin_data):
    """Run a hook script with given stdin and EVENTS_FILE env var."""
    env = os.environ.copy()
    env["CCTG_EVENTS_FILE"] = EVENTS_FILE
    result = subprocess.run(
        [sys.executable, script],
        input=json.dumps(stdin_data),
        capture_output=True,
        text=True,
        env=env,
    )
    return result


class TestSessionHook:
    def test_session_start_writes_event(self):
        result = run_hook("hooks/session.py", {
            "session_id": "abc123",
            "transcript_path": "/home/user/.claude/projects/my-project/abc123.jsonl",
            "cwd": "/home/user/my-project",
            "hook_event_name": "SessionStart",
            "source": "startup",
        })
        assert result.returncode == 0
        events = read_events()
        assert len(events) == 1
        assert events[0]["type"] == "session_start"
        assert events[0]["session_id"] == "abc123"
        assert events[0]["action"] == "startup"
        assert events[0]["project_name"] == "my-project"
        assert events[0]["cwd"] == "/home/user/my-project"


class TestNotifyHook:
    def test_notification_writes_event(self):
        result = run_hook("hooks/notify.py", {
            "session_id": "abc123",
            "transcript_path": "/home/user/.claude/projects/proj/abc123.jsonl",
            "cwd": "/home/user/proj",
            "hook_event_name": "Notification",
            "message": "Claude needs permission to run: rm -rf /tmp/test",
        })
        assert result.returncode == 0
        events = read_events()
        assert len(events) == 1
        assert events[0]["type"] == "notification"
        assert events[0]["session_id"] == "abc123"
        assert "permission" in events[0]["message"]
