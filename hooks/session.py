#!/usr/bin/env python3
"""SessionStart hook — writes session metadata for cctg bridge."""
import json
import os
import sys
import time

data = json.load(sys.stdin)
events_file = os.environ.get("CCTG_EVENTS_FILE", "")

if not events_file:
    sys.exit(0)

event = {
    "type": "session",
    "session_id": data.get("session_id", ""),
    "cwd": data.get("cwd", ""),
    "transcript_path": data.get("transcript_path", ""),
    "timestamp": int(time.time()),
}

os.makedirs(os.path.dirname(events_file), exist_ok=True)
with open(events_file, "a") as f:
    f.write(json.dumps(event, ensure_ascii=False) + "\n")
