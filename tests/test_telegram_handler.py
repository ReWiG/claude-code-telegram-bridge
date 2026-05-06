"""Tests for cctg.telegram_handler."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from cctg.telegram_handler import TelegramHandler


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.get_state = AsyncMock(return_value=None)
    db.set_state = AsyncMock()
    db.list_active_sessions = AsyncMock(return_value=[])
    db.get_session = AsyncMock(return_value=None)
    db.add_pending_event = AsyncMock()
    db.get_unprocessed_events = AsyncMock(return_value=[])
    db.mark_event_processed = AsyncMock()
    return db


@pytest.fixture
def mock_sm():
    sm = AsyncMock()
    sm.attach = AsyncMock()
    sm.detach = AsyncMock()
    sm.start_tracking = AsyncMock()
    sm.stop_tracking = AsyncMock()
    sm.is_tracking = AsyncMock(return_value=False)
    sm.get_attached_session = AsyncMock(return_value=None)
    return sm


@pytest.fixture
def mock_tty():
    tty = AsyncMock()
    tty.write_text = MagicMock(return_value=True)
    tty.write_response = MagicMock(return_value=True)
    tty.find_tty = MagicMock(return_value="/dev/pts/2")
    return tty


@pytest.fixture
def handler(mock_db, mock_sm, mock_tty):
    return TelegramHandler(
        token="test-token",
        chat_id="12345",
        db=mock_db,
        session_manager=mock_sm,
        tty_router=mock_tty,
        proxy=None,
    )


class TestCommandHandling:
    @pytest.mark.asyncio
    async def test_list_command_no_sessions(self, handler, mock_db):
        mock_db.list_active_sessions.return_value = []
        text, kb = await handler.handle_command("/list")
        assert "нет активных" in text.lower() or "0" in text

    @pytest.mark.asyncio
    async def test_list_command_with_sessions(self, handler, mock_db):
        mock_db.list_active_sessions.return_value = [
            {"session_id": "abc", "project_name": "proj", "cwd": "/p", "branch": "main", "tty": "/dev/pts/2", "started_at": 1700000000},
        ]
        mock_db.get_state = AsyncMock(side_effect=lambda k: {"attached_session": None, "watch_active": None}.get(k))
        text, kb = await handler.handle_command("/list")
        assert "/p" in text
        assert "abc" in text

    @pytest.mark.asyncio
    async def test_attach_command(self, handler, mock_db, mock_sm):
        mock_db.list_active_sessions.return_value = [
            {"session_id": "abc", "project_name": "proj", "cwd": "/p", "branch": "main", "tty": "/dev/pts/2", "started_at": 1700000000},
        ]
        mock_sm.attach.return_value = None
        text, kb = await handler.handle_command("/attach 1")
        assert "прикреплён" in text.lower()
        mock_sm.attach.assert_called_once()

    @pytest.mark.asyncio
    async def test_start_track_without_attach(self, handler, mock_sm):
        mock_sm.start_tracking.side_effect = ValueError("No session attached")
        text, kb = await handler.handle_command("/start_track")
        assert "attach" in text.lower() or "сесси" in text.lower()

    @pytest.mark.asyncio
    async def test_start_track_success(self, handler, mock_sm):
        text, kb = await handler.handle_command("/start_track")
        mock_sm.start_tracking.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_track(self, handler, mock_sm):
        text, kb = await handler.handle_command("/stop_track")
        mock_sm.stop_tracking.assert_called_once()

    @pytest.mark.asyncio
    async def test_detach(self, handler, mock_sm):
        text, kb = await handler.handle_command("/detach")
        mock_sm.detach.assert_called_once()

    @pytest.mark.asyncio
    async def test_status(self, handler, mock_db, mock_sm):
        mock_sm.is_tracking.return_value = False
        mock_sm.get_attached_session.return_value = None
        text, kb = await handler.handle_command("/status")
        assert "отслеживание" in text.lower() or "off" in text.lower()

    @pytest.mark.asyncio
    async def test_help(self, handler):
        text, kb = await handler.handle_command("/help")
        assert "/list" in text or "\U0001f4cb" in text

    @pytest.mark.asyncio
    async def test_unknown_text_forwarded_to_tty(self, handler, mock_db, mock_tty):
        mock_db.get_state = AsyncMock(side_effect=lambda k: {
            "attached_session": "abc123", "watch_active": "1"
        }.get(k))
        callback = AsyncMock(return_value=True)
        handler._input_callback = callback
        text, kb = await handler.handle_message("fix the bug")
        assert text == "✅"
        callback.assert_awaited_once_with("abc123", "fix the bug")

    @pytest.mark.asyncio
    async def test_text_without_watch_not_forwarded(self, handler, mock_db, mock_tty):
        mock_db.get_state = AsyncMock(return_value=None)
        callback = AsyncMock()
        handler._input_callback = callback
        text, kb = await handler.handle_message("fix the bug")
        assert text is None
        callback.assert_not_called()


class TestCallbackHandling:
    @pytest.mark.asyncio
    async def test_allow_callback(self, handler, mock_db, mock_tty):
        mock_db.get_session.return_value = {"session_id": "abc123", "tty": "/dev/pts/2"}
        await handler.handle_callback("allow|abc123")
        mock_tty.write_response.assert_called_once_with("allow", "/dev/pts/2")

    @pytest.mark.asyncio
    async def test_deny_callback(self, handler, mock_db, mock_tty):
        mock_db.get_session.return_value = {"session_id": "abc123", "tty": "/dev/pts/2"}
        await handler.handle_callback("deny|abc123")
        mock_tty.write_response.assert_called_once_with("deny", "/dev/pts/2")

    @pytest.mark.asyncio
    async def test_allow_all_callback(self, handler, mock_db, mock_tty):
        mock_db.get_session.return_value = {"session_id": "abc123", "tty": "/dev/pts/2"}
        await handler.handle_callback("allow_all|abc123")
        mock_tty.write_response.assert_called_once_with("allow_all", "/dev/pts/2")


class TestKeyboardBuilding:
    def test_keyboard_no_attach(self, handler):
        kb = handler.build_keyboard(attached=False, tracking=False)
        assert len(kb) > 0

    def test_keyboard_attached_not_tracking(self, handler):
        kb = handler.build_keyboard(attached=True, tracking=False)
        flat = [b.text for row in kb for b in row]
        assert any("Начать" in t for t in flat)

    def test_keyboard_attached_and_tracking(self, handler):
        kb = handler.build_keyboard(attached=True, tracking=True)
        flat = [b.text for row in kb for b in row]
        assert any("Остановить" in t for t in flat)
