#!/usr/bin/env python3
"""Notification hook — writes permission/question events to events file."""
import json
import os
import sys
import time


def main():
    data = json.load(sys.stdin)
    event = {
        "type": "notification",
        "session_id": data.get("session_id", ""),
        "message": data.get("message", ""),
        "timestamp": int(time.time()),
    }

    events_file = os.environ.get("CCTG_EVENTS_FILE", os.path.expanduser("~/.cctg/data/cc-events.jsonl"))
    os.makedirs(os.path.dirname(events_file), exist_ok=True)
    with open(events_file, "a") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
