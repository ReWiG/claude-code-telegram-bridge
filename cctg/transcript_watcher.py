"""Transcript watcher — finds and tails Claude Code JSONL transcript files."""
from __future__ import annotations

import json
import os
import time


class TranscriptWatcher:
    def __init__(self, transcript_path: str | None = None, *, cwd: str | None = None):
        self._cwd = cwd
        self._start_time = time.time()
        self._file_path: str | None = None
        self._position: int = 0
        self._last_tool_use: dict | None = None
        if transcript_path:
            self._file_path = transcript_path
            self._position = 0
            return
        # Project slug for scanning: /home/rig/azot -> -home-rig-azot
        if cwd:
            self._project_dir = os.path.expanduser(
                f"~/.claude/projects/{cwd.replace('/', '-')}"
            )
        else:
            self._project_dir = ""

    @property
    def last_tool_use(self) -> dict | None:
        """Most recent tool_use block seen in the transcript."""
        return self._last_tool_use

    @property
    def session_id(self) -> str | None:
        """Claude session ID extracted from the transcript filename."""
        if not self._file_path:
            return None
        return os.path.splitext(os.path.basename(self._file_path))[0]

    def _check_cwd(self, path: str) -> bool:
        """Read first 10 lines of a JSONL and check if cwd matches."""
        try:
            with open(path, "r") as f:
                for _ in range(10):
                    line = f.readline()
                    if not line:
                        break
                    try:
                        obj = json.loads(line.strip())
                    except json.JSONDecodeError:
                        continue
                    if obj.get("cwd") == self._cwd:
                        return True
        except OSError:
            pass
        return False

    def find_session_file(self) -> bool:
        """Scan project dir for a session JSONL whose cwd matches ours."""
        if self._file_path:
            return True
        if not os.path.isdir(self._project_dir):
            return False
        best = None
        best_mtime = 0
        for name in os.listdir(self._project_dir):
            if not name.endswith(".jsonl"):
                continue
            if name.startswith("agent-"):
                continue
            path = os.path.join(self._project_dir, name)
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            if mtime < self._start_time - 30:
                continue
            if mtime > best_mtime:
                best = path
                best_mtime = mtime
        if best and self._check_cwd(best):
            self._file_path = best
            self._position = 0
            return True
        return False

    def read_new_text(self) -> tuple[bool, str, dict | None]:
        """Read new messages since last call.
        Returns (flush, text, tool_use):
          - flush=True if a user message was seen (finalize current live msg)
          - text = concatenated assistant text blocks
          - tool_use = most recent tool_use block in this batch (or None)
        """
        if not self._file_path:
            return (False, "", None)
        try:
            size = os.path.getsize(self._file_path)
            if size <= self._position:
                return (False, "", None)
            with open(self._file_path, "r") as f:
                f.seek(self._position)
                raw = f.read()
                self._position = size
        except OSError:
            return (False, "", None)

        flush = False
        parts = []
        tool_use = None
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("type") == "user":
                flush = True
            elif obj.get("type") == "assistant":
                msg = obj.get("message", {})
                for block in msg.get("content", []):
                    bt = block.get("type", "")
                    if bt == "text":
                        t = block.get("text", "")
                        if t:
                            parts.append(t)
                    elif bt == "tool_use":
                        self._last_tool_use = block
                        tool_use = block
        return (flush, "\n".join(parts), tool_use)
