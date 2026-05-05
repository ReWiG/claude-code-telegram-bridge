#!/usr/bin/env python3
"""SessionStart hook — writes session metadata to events file."""
import json
import os
import sys
import time


def main():
    data = json.load(sys.stdin)
    session_id = data.get("session_id", "")
    transcript_path = data.get("transcript_path", "")
    cwd = data.get("cwd", "")

    project_name = None
    parts = transcript_path.rsplit("/", 2)
    if len(parts) >= 2:
        project_name = parts[-2]

    event = {
        "type": "session_start",
        "action": data.get("source", "startup"),
        "session_id": session_id,
        "project_name": project_name,
        "cwd": cwd,
        "transcript_path": transcript_path,
        "timestamp": int(time.time()),
    }

    events_file = os.environ.get("CCTG_EVENTS_FILE", os.path.expanduser("~/.cctg/data/cc-events.jsonl"))
    os.makedirs(os.path.dirname(events_file), exist_ok=True)
    with open(events_file, "a") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
