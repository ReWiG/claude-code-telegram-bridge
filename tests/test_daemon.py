"""Tests for cctg.daemon."""
import os
import pytest
from unittest.mock import AsyncMock, MagicMock
from cctg.daemon import Daemon
from cctg.config import Config


@pytest.fixture
def config():
    return Config(
        telegram_token="test-token",
        telegram_chat_id="12345",
        install_dir="/tmp/test-cctg",
        transcript_base="/tmp/test-transcripts",
        session_cleanup_seconds=30,
    )


@pytest.fixture
def daemon(config):
    d = Daemon(config)
    d.db = AsyncMock()
    d.session_manager = AsyncMock()
    d.transcript_watcher = MagicMock()
    d.tty_router = MagicMock()
    d.telegram = AsyncMock()
    d.cleanup_worker = AsyncMock()
    return d


class TestDaemon:
    @pytest.mark.asyncio
    async def test_init_creates_dirs(self, config, tmp_path):
        config.install_dir = str(tmp_path / ".cctg")
        d = Daemon(config)
        await d._ensure_dirs()
        assert os.path.isdir(os.path.join(str(tmp_path / ".cctg"), "data"))

    def test_write_pid(self, config, tmp_path):
        config.install_dir = str(tmp_path / ".cctg")
        os.makedirs(os.path.join(str(tmp_path / ".cctg"), "data"), exist_ok=True)
        d = Daemon(config)
        d._write_pid()
        pid_file = os.path.join(str(tmp_path / ".cctg"), "data", "cctg.pid")
        assert os.path.exists(pid_file)
        with open(pid_file) as f:
            assert int(f.read()) == os.getpid()

    @pytest.mark.asyncio
    async def test_process_transcript_for_tracking(self, daemon):
        daemon.session_manager.is_tracking = AsyncMock(return_value=True)
        daemon.session_manager.get_attached_session = AsyncMock(return_value={
            "session_id": "abc123", "cwd": "/p", "project_name": "proj",
        })
        daemon.transcript_watcher.read_new_lines.return_value = ["Hello, world."]
        daemon.db.get_live_message = AsyncMock(return_value=None)
        daemon.db.create_live_message = AsyncMock(return_value=1)
        daemon.telegram.send_message = AsyncMock(return_value=555)

        # Create transcript file so that os.path.exists passes
        transcript_path = os.path.join(
            os.path.expanduser(daemon.config.transcript_base),
            "proj",
            "abc123.jsonl",
        )
        os.makedirs(os.path.dirname(transcript_path), exist_ok=True)
        with open(transcript_path, "w") as f:
            f.write("test")

        try:
            await daemon._poll_transcripts()
            daemon.telegram.send_message.assert_called_once()
        finally:
            os.unlink(transcript_path)
            os.rmdir(os.path.dirname(transcript_path))

    @pytest.mark.asyncio
    async def test_process_transcript_empty_when_not_tracking(self, daemon):
        daemon.session_manager.is_tracking = AsyncMock(return_value=False)
        await daemon._poll_transcripts()
        daemon.telegram.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_process_notification_events(self, daemon):
        daemon.db.get_unprocessed_events = AsyncMock(return_value=[
            {"id": 1, "session_id": "abc123", "type": "notification", "payload": "Permission needed"},
        ])
        daemon.db.get_session = AsyncMock(return_value={
            "session_id": "abc123", "cwd": "/p",
        })
        daemon.telegram.send_permission_prompt = AsyncMock(return_value=600)

        await daemon._process_pending_events()
        daemon.telegram.send_permission_prompt.assert_called_once()
        daemon.db.mark_event_processed.assert_called_once_with(1)
