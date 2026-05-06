#!/usr/bin/env python3
"""Notification hook — writes permission events for cctg bridge."""
import json
import os
import sys
import time

data = json.load(sys.stdin)
events_file = os.environ.get("CCTG_EVENTS_FILE", "")

if not events_file:
    sys.exit(0)

event = {
    "type": "notification",
    "session_id": data.get("session_id", ""),
    "message": data.get("message", ""),
    "notification_type": data.get("notification_type", ""),
    "timestamp": int(time.time()),
}

os.makedirs(os.path.dirname(events_file), exist_ok=True)
with open(events_file, "a") as f:
    f.write(json.dumps(event, ensure_ascii=False) + "\n")
