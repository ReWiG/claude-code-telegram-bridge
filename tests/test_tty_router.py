"""Tests for cctg.tty_router."""
import os
import tempfile
import pytest
from cctg.tty_router import TTYRouter


@pytest.fixture
def tmp_file():
    fd, path = tempfile.mkstemp()
    os.close(fd)
    yield path
    if os.path.exists(path):
        os.unlink(path)


class TestTTYRouter:
    def test_write_text_to_file(self, tmp_file):
        router = TTYRouter()
        router.write_text("hello world", tty_path=tmp_file)
        with open(tmp_file) as f:
            content = f.read()
        assert "hello world" in content

    def test_write_allow_to_tty(self, tmp_file):
        router = TTYRouter()
        router.write_response("allow", tty_path=tmp_file)
        with open(tmp_file) as f:
            content = f.read()
        assert "y" in content or "Y" in content

    def test_write_deny_to_tty(self, tmp_file):
        router = TTYRouter()
        router.write_response("deny", tty_path=tmp_file)
        with open(tmp_file) as f:
            content = f.read()
        assert "n" in content or "N" in content

    def test_write_allow_all_to_tty(self, tmp_file):
        router = TTYRouter()
        router.write_response("allow_all", tty_path=tmp_file)
        with open(tmp_file) as f:
            content = f.read()
        assert "a" in content or "A" in content

    def test_find_tty_from_proc_returns_none_for_nonexistent(self):
        router = TTYRouter()
        tty = router.find_tty(pid=99999999)
        assert tty is None

    def test_find_tty_from_proc(self):
        router = TTYRouter()
        tty = router.find_tty(pid=os.getpid())
        assert tty is None or tty.startswith("/dev/")
