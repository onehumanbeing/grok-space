"""One-shot Space setup: starter source, published page and worker registration.

The Bot only lays the plumbing; business logic is then edited locally in
project_dir and published with `publish --from-project`.
"""
import json
import os
import re
from pathlib import Path

from store import SpaceError

SOURCE_FILE = "index.html"
HANDLER_FILE = "handler.py"
ACTION_TYPE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,99}$")

STARTER_HTML = """<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{font:16px/1.55 -apple-system,BlinkMacSystemFont,sans-serif;color:#222;background:#fff}*{box-sizing:border-box}
body{margin:0;padding:24px 20px}main{max-width:560px;margin:0 auto}h1{font-size:22px;margin:0 0 8px}
p{margin:0 0 20px;color:#666}button{font:inherit;width:100%;min-height:44px;border:1px solid #222;border-radius:8px;
background:#222;color:#fff;margin:0 0 10px;cursor:pointer}button:disabled{opacity:.5}
pre{white-space:pre-wrap;overflow-wrap:anywhere;font:13px/1.6 ui-monospace,monospace;background:#f6f6f4;border-radius:8px;padding:12px}
#status{font-size:14px;color:#777;min-height:22px}
</style></head>
<body><main>
<h1>__TITLE__</h1><p>Starter page. Edit this file locally, then publish it with --from-project.</p>
<div id="actions"></div><p id="status" role="status"></p>
<pre id="state">Loading…</pre>
</main>
<script>
const ACTIONS = __ACTIONS__;
const el = id => document.getElementById(id);
let current;
ACTIONS.forEach(type => {
  const button = document.createElement("button");
  button.textContent = type;
  button.addEventListener("click", async () => {
    button.disabled = true;
    el("status").textContent = "Running " + type + "…";
    try {
      const snapshot = await spaces.get();
      const response = await spaces.action(type, {}, { expected_revision: snapshot.revision, requestId: crypto.randomUUID() });
      if (response.delivery === "manual") el("status").textContent = "Saved; no local handler is registered for " + type + ".";
    } catch (error) { el("status").textContent = error.message; }
    finally { button.disabled = false; }
  });
  el("actions").append(button);
});
spaces.subscribe(snapshot => { current = snapshot; el("state").textContent = JSON.stringify(snapshot.state, null, 2); });
spaces.subscribeEvents(event => {
  if (["completed", "failed"].includes(event.status) && ACTIONS.includes(event.type)) el("status").textContent = event.type + ": " + event.status;
});
spaces.get().then(snapshot => { el("state").textContent = JSON.stringify(snapshot.state, null, 2); });
</script></body></html>
"""

STARTER_HANDLER = '''#!/usr/bin/env python3
"""Local action handler. One function per action type; edit freely.

stdin: {"refs": {...}, "event": {"type", "payload", ...}, "snapshot": {"state", "revision"}}
stdout: {"result": ANY_JSON, "patch": OPTIONAL_MERGE_PATCH}
"""
import json
import sys


def handle(event, state):
    # Replace these stubs with real logic. Validate payloads before any side effect.
    return {"result": {"ok": True, "type": event["type"]}, "patch": {"last_action": event["type"]}}


HANDLERS = {__HANDLERS__}

request = json.load(sys.stdin)
handler = HANDLERS.get(request["event"]["type"])
if handler is None:
    raise SystemExit(2)
print(json.dumps(handler(request["event"], request["snapshot"]["state"]), allow_nan=False))
'''


def _write_new(path, text):
    """Create a file only if absent; never overwrite local edits."""
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as target:
        target.write(text)
    return True


def export_source(store, space_id):
    """Seed project_dir/index.html from the saved page so local edits start from it."""
    folder = store.project_directory(space_id, create=True)
    html = store.detail(space_id)["view_html"]
    created = bool(html) and _write_new(folder / SOURCE_FILE, html)
    return folder / SOURCE_FILE, created


def source_file(store, space_id):
    path = store.project_directory(space_id) / SOURCE_FILE
    if not path.is_file() or path.is_symlink():
        raise SpaceError("No local source. Run open or init for this Space first.", 404,
                         code="project_source_missing", source_file=str(path))
    return path


def init(store, space_id, types):
    import local_worker
    types = sorted(set(types or []))
    invalid = [value for value in types if not ACTION_TYPE.match(value)]
    if invalid:
        raise SpaceError("Action types must start with a letter and use letters, digits or _.:-", code="invalid_action_types")
    detail = store.detail(space_id)
    folder = store.project_directory(space_id, create=True)
    files = {}
    source, exported = export_source(store, space_id)
    if exported:
        files[SOURCE_FILE] = "exported_saved_page"
    else:
        starter = STARTER_HTML.replace("__TITLE__", _html_text(detail["space"]["title"])).replace("__ACTIONS__", json.dumps(types))
        files[SOURCE_FILE] = "created_starter" if _write_new(source, starter) else "kept_existing"
    published = False
    if not detail["view_html"]:
        store.publish(space_id, source.read_text(encoding="utf-8"), detail["revision"], None, "bot")
        published = True
    worker = None
    if types:
        handler = folder / HANDLER_FILE
        mapping = ", ".join("{!r}: handle".format(value) for value in types)
        files[HANDLER_FILE] = "created_starter" if _write_new(handler, STARTER_HANDLER.replace("__HANDLERS__", mapping)) else "kept_existing"
        worker = local_worker.register(store, space_id, handler, types)
    return {"project_dir": str(folder), "files": files, "published": published, "worker": worker}


def _html_text(value):
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
