#!/usr/bin/env python3
"""Complete one isolated UI probe using the real local CLI, never a live Bot."""
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("GROK_SPACE_UI_DATA_DIR", ROOT / "out/ui-test")).resolve()
ORIGIN = os.environ.get("GROK_SPACE_UI_ORIGIN", "http://127.0.0.1:18792")


def get(path):
    with urllib.request.urlopen(ORIGIN + path, timeout=5) as response:
        return json.load(response)


def main():
    parsed = urlsplit(ORIGIN)
    if DATA.parent != (ROOT / "out").resolve() or not DATA.name.startswith("ui-test"):
        raise RuntimeError("Use only an owned out/ui-test* store")
    if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not parsed.port:
        raise RuntimeError("Use only an isolated loopback origin")
    health = get("/health")
    if health.get("app") != "grok-spaces" or Path(health.get("data_dir", "")).resolve() != DATA.resolve():
        raise RuntimeError("Refusing to use a service outside the isolated UI test directory")
    deadline = time.monotonic() + 50
    while time.monotonic() < deadline:
        for space in get("/api/spaces")["spaces"]:
            detail = get("/api/spaces/" + space["id"])
            event_id = detail["state"].get("uiDriverReady")
            if not isinstance(event_id, str):
                continue
            event = get("/api/spaces/" + space["id"] + "/events/" + event_id)["event"]
            if event["status"] != "pending" or event["payload"].get("instruction") != "Isolated event result test":
                continue
            if any(row["id"] == event_id for row in detail["events"]):
                raise RuntimeError("The probe action must be older than the recent 50")
            result = subprocess.run([
                "/usr/bin/python3", str(ROOT / "bridge/cli.py"), "--data-dir", str(DATA),
                "action", "complete", "--space", space["id"], "--event", event_id,
                "--expected-revision", str(detail["revision"]),
                "--json", json.dumps({"result": {"verified": "local CLI only"}}),
            ], check=True, capture_output=True, text=True)
            print(result.stdout.strip())
            return 0
        time.sleep(0.15)
    raise RuntimeError("Timed out waiting for the isolated UI completion probe")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
