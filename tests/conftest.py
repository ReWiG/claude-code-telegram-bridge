"""Shared test fixtures for cctg."""
import os
import tempfile
import pytest


@pytest.fixture
def tmp_db_path():
    """Create a temporary SQLite database path."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    if os.path.exists(path):
        os.unlink(path)
