"""Configuration reader for cctg."""
from __future__ import annotations

import os
from dataclasses import dataclass

try:
    import tomllib
except ImportError:
    import tomli as tomllib


@dataclass
class Config:
    telegram_token: str
    telegram_chat_id: str
    telegram_proxy: str | None = None
    install_dir: str = "~/.cctg"
    transcript_base: str = "~/.claude/projects"
    session_cleanup_seconds: int = 30

    @property
    def db_path(self) -> str:
        return os.path.join(self._expanded_install_dir(), "data", "cctg.db")

    @property
    def events_file(self) -> str:
        return os.path.join(self._expanded_install_dir(), "data", "cc-events.jsonl")

    @property
    def pid_file(self) -> str:
        return os.path.join(self._expanded_install_dir(), "data", "cctg.pid")

    def _expanded_install_dir(self) -> str:
        return os.path.expanduser(self.install_dir)


def load_config(path: str) -> Config:
    """Load and validate configuration from a TOML file."""
    if not os.path.exists(path):
        raise FileNotFoundError(f"Config file not found: {path}")

    with open(path, "rb") as f:
        data = tomllib.load(f)

    telegram = data.get("telegram", {})
    token = telegram.get("token", "")
    chat_id = telegram.get("chat_id", "")
    proxy = telegram.get("proxy")

    if not token:
        raise ValueError("telegram.token is required")
    if not chat_id:
        raise ValueError("telegram.chat_id is required")

    paths = data.get("paths", {})
    timing = data.get("timing", {})

    return Config(
        telegram_token=token,
        telegram_chat_id=chat_id,
        telegram_proxy=proxy,
        install_dir=paths.get("install_dir", "~/.cctg"),
        transcript_base=paths.get("transcript_base", "~/.claude/projects"),
        session_cleanup_seconds=timing.get("session_cleanup_seconds", 30),
    )
