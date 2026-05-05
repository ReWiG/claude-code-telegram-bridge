"""Tests for cctg.transcript_watcher."""
import json
import os
import tempfile
import pytest
from cctg.transcript_watcher import TranscriptWatcher, filter_thinking


class TestFilterThinking:
    def test_filters_thinking_blocks(self):
        content = [
            {"type": "thinking", "thinking": "Hmm, let me think about this..."},
            {"type": "text", "text": "Here is the answer."},
            {"type": "thinking", "thinking": "More thinking..."},
            {"type": "tool_use", "name": "read", "input": {"path": "/x"}},
        ]
        result = filter_thinking(content)
        assert len(result) == 2
        assert result[0]["type"] == "text"
        assert result[1]["type"] == "tool_use"

    def test_preserves_all_text(self):
        content = [
            {"type": "text", "text": "First."},
            {"type": "text", "text": "Second."},
        ]
        result = filter_thinking(content)
        assert len(result) == 2

    def test_empty_content(self):
        result = filter_thinking([])
        assert result == []

    def test_only_thinking(self):
        content = [
            {"type": "thinking", "thinking": "..."},
            {"type": "thinking", "thinking": "..."},
        ]
        result = filter_thinking(content)
        assert result == []


class TestTranscriptWatcher:
    @pytest.fixture
    def tmp_transcript_dir(self):
        d = tempfile.mkdtemp()
        yield d
        import shutil
        shutil.rmtree(d, ignore_errors=True)

    def test_parse_assistant_message(self, tmp_transcript_dir):
        filepath = os.path.join(tmp_transcript_dir, "test.jsonl")
        watcher = TranscriptWatcher(tmp_transcript_dir)

        line = json.dumps({
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "thinking", "thinking": "internal"},
                    {"type": "text", "text": "Hello, I can help."},
                ]
            }
        })
        with open(filepath, "w") as f:
            f.write(line + "\n")

        result = watcher.read_new_lines(filepath, 0)
        assert len(result) == 1
        text = result[0]
        assert "Hello, I can help." in text
        assert "internal" not in text

    def test_parse_user_message_is_empty(self, tmp_transcript_dir):
        filepath = os.path.join(tmp_transcript_dir, "test.jsonl")
        watcher = TranscriptWatcher(tmp_transcript_dir)

        line = json.dumps({
            "type": "user",
            "message": {"role": "user", "content": [{"type": "text", "text": "Fix bug"}]}
        })
        with open(filepath, "w") as f:
            f.write(line + "\n")

        result = watcher.read_new_lines(filepath, 0)
        assert len(result) == 0

    def test_respects_offset(self, tmp_transcript_dir):
        filepath = os.path.join(tmp_transcript_dir, "test.jsonl")
        watcher = TranscriptWatcher(tmp_transcript_dir)

        lines = [
            json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "First."}]}}),
            json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "Second."}]}}),
        ]
        with open(filepath, "w") as f:
            for line in lines:
                f.write(line + "\n")

        result = watcher.read_new_lines(filepath, len(lines[0]) + 1)
        assert len(result) == 1
        assert "Second." in result[0]

    def test_tool_use_formatted(self, tmp_transcript_dir):
        filepath = os.path.join(tmp_transcript_dir, "test.jsonl")
        watcher = TranscriptWatcher(tmp_transcript_dir)

        line = json.dumps({
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "name": "Bash", "input": {"command": "ls -la"}}
                ]
            }
        })
        with open(filepath, "w") as f:
            f.write(line + "\n")

        result = watcher.read_new_lines(filepath, 0)
        assert len(result) == 1
        assert "Bash" in result[0] or "ls" in result[0]

    def test_no_file_returns_empty(self, tmp_transcript_dir):
        watcher = TranscriptWatcher(tmp_transcript_dir)
        result = watcher.read_new_lines("/nonexistent/path.jsonl", 0)
        assert result == []
