#!/usr/bin/env python3
"""Prepare an empty owned store for real HTTP/SDK worker verification."""
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bridge"))
from store import Store
import local_worker

name = sys.argv[1] if len(sys.argv) > 1 else "worker-ui-test"
data = (ROOT / "out" / name).resolve()
if data.parent != (ROOT / "out").resolve() or not data.name.startswith("worker-ui-test"):
    raise RuntimeError("Use only an owned out/worker-ui-test* directory.")
store = Store(data)
if store.bots():
    raise RuntimeError("Use a fresh owned out/worker-ui-test store; do not reset an existing one.")
bot = store.create_bot("Internal worker verification")
space = store.create_space(bot["id"], "Internal fixture")
project = store.project_directory(space["id"], create=True)
handler = project / "handler.py"
shutil.copyfile(ROOT / "tests/ui_worker_handler.py", handler)
store.set_view(space["id"], (ROOT / "tests/ui_worker_fixture.html").read_text(), 0, "user")
local_worker.register(store, space["id"], handler, ["compute_stats"])
print(json.dumps({"bot_id": bot["id"], "space_id": space["id"], "data_dir": str(data), "url": "http://127.0.0.1:18798/?bot_id=" + bot["id"] + "&space_id=" + space["id"]}))
