"""Tests for cctg.config."""
import tempfile
import os
import pytest
from cctg.config import Config, load_config


def test_load_config_minimal():
    """Config with only required fields."""
    toml_content = b"""
[telegram]
token = "test-token"
chat_id = "12345"
"""
    with tempfile.NamedTemporaryFile(suffix=".toml", delete=False) as f:
        f.write(toml_content)
        path = f.name
    try:
        cfg = load_config(path)
        assert cfg.telegram_token == "test-token"
        assert cfg.telegram_chat_id == "12345"
        assert cfg.telegram_proxy is None
        assert cfg.install_dir is not None
        assert cfg.transcript_base is not None
        assert cfg.events_file.endswith("cc-events.jsonl")
        assert cfg.db_path.endswith("cctg.db")
        assert cfg.pid_file.endswith("cctg.pid")
    finally:
        os.unlink(path)


def test_load_config_with_proxy():
    """Config with optional proxy."""
    toml_content = b"""
[telegram]
token = "t"
chat_id = "c"
proxy = "socks5://127.0.0.1:10808"
"""
    with tempfile.NamedTemporaryFile(suffix=".toml", delete=False) as f:
        f.write(toml_content)
        path = f.name
    try:
        cfg = load_config(path)
        assert cfg.telegram_proxy == "socks5://127.0.0.1:10808"
    finally:
        os.unlink(path)


def test_config_defaults():
    """Default paths use install_dir."""
    toml_content = b"""
[telegram]
token = "t"
chat_id = "c"

[paths]
install_dir = "/home/user/.cctg"
"""
    with tempfile.NamedTemporaryFile(suffix=".toml", delete=False) as f:
        f.write(toml_content)
        path = f.name
    try:
        cfg = load_config(path)
        assert cfg.install_dir == "/home/user/.cctg"
        assert cfg.db_path == "/home/user/.cctg/data/cctg.db"
        assert cfg.events_file == "/home/user/.cctg/data/cc-events.jsonl"
        assert cfg.pid_file == "/home/user/.cctg/data/cctg.pid"
    finally:
        os.unlink(path)


def test_load_config_missing_file():
    """Missing file raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        load_config("/nonexistent/config.toml")


def test_load_config_missing_required():
    """Missing required field raises error."""
    toml_content = b"""
[telegram]
token = "t"
# missing chat_id
"""
    with tempfile.NamedTemporaryFile(suffix=".toml", delete=False) as f:
        f.write(toml_content)
        path = f.name
    try:
        with pytest.raises(ValueError, match="chat_id"):
            load_config(path)
    finally:
        os.unlink(path)
