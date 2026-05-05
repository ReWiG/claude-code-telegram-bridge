"""Transcript watcher — reads Claude Code .jsonl transcript files, filters thinking."""
from __future__ import annotations

import json
import os


def filter_thinking(content: list[dict]) -> list[dict]:
    """Remove thinking blocks from message content."""
    return [block for block in content if block.get("type") != "thinking"]


def format_block(block: dict) -> str:
    """Format a content block for Telegram display."""
    block_type = block.get("type", "")
    if block_type == "text":
        return block.get("text", "")
    elif block_type == "tool_use":
        name = block.get("name", "tool")
        inp = block.get("input", {})
        if isinstance(inp, dict):
            command = inp.get("command", "")
            if command:
                return f"\U0001f527 {name}: {command}"
            path = inp.get("file_path", "")
            if path:
                return f"\U0001f527 {name}: {path}"
        return f"\U0001f527 {name}"
    return ""


class TranscriptWatcher:
    def __init__(self, transcript_base: str):
        self.base = os.path.expanduser(transcript_base)
        self._offsets: dict[str, int] = {}

    def read_new_lines(self, filepath: str, offset: int | None = None) -> list[str]:
        """Read new assistant messages from a transcript file, filtering thinking.
        Returns list of formatted text blocks ready for Telegram."""
        if filepath in self._offsets:
            offset = self._offsets[filepath]
        if offset is None:
            offset = 0

        if not os.path.exists(filepath):
            return []

        size = os.path.getsize(filepath)
        if offset >= size:
            return []

        results = []
        with open(filepath, "r") as f:
            f.seek(offset)
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue

                msg_type = data.get("type", "")
                if msg_type != "assistant":
                    continue

                message = data.get("message", {})
                content = message.get("content", [])
                filtered = filter_thinking(content)

                parts = [format_block(b) for b in filtered if format_block(b)]
                if parts:
                    results.append("\n".join(parts))

            self._offsets[filepath] = f.tell()

        return results

    def reset_offset(self, filepath: str) -> None:
        self._offsets.pop(filepath, None)
