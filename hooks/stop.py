#!/usr/bin/env python3
"""Stop hook — writes stop event to events file."""
import json
import os
import sys
import time


def main():
    data = json.load(sys.stdin)
    event = {
        "type": "stop",
        "session_id": data.get("session_id", ""),
        "stop_hook_active": data.get("stop_hook_active", False),
        "timestamp": int(time.time()),
    }

    events_file = os.environ.get("CCTG_EVENTS_FILE", os.path.expanduser("~/.cctg/data/cc-events.jsonl"))
    os.makedirs(os.path.dirname(events_file), exist_ok=True)
    with open(events_file, "a") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
