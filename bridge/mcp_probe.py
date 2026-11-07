#!/usr/bin/env python3
"""Offline MCP Apps probe. Stdio is newline-delimited JSON-RPC only."""
import argparse
import json
import os
import sqlite3
import sys
import uuid
from pathlib import Path

from store import SpaceError, Store, encode, timestamp

RESOURCE_URI = "ui://grok-spaces/probe"
MIME_TYPE = "text/html;profile=mcp-app"
DEFAULT_DATA = Path.home() / "Library/Application Support/Grok Spaces"
MAX_LINE = 2 * 1024 * 1024
MAX_COUNTER = 9007199254740991
DEFAULT_KEY = "mcp_probe.default_space_id"
OWNER_PREFIX = "mcp_probe.owned."
MCP_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")

HTML = r'''<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Grok Spaces MCP Apps probe</title>
<style>
  :root{color-scheme:light dark;font:15px system-ui,sans-serif}
  body{margin:0;padding:20px;background:var(--color-background-primary,#fff);color:var(--color-text-primary,#111)}
  h1{font-size:18px;margin:0 0 8px}p{line-height:1.5}output{display:block;font-size:44px;font-variant-numeric:tabular-nums;margin:18px 0}
  button{font:inherit;padding:8px 14px;margin:0 8px 8px 0;border:1px solid currentColor;border-radius:7px;background:transparent;color:inherit}
  button:disabled{opacity:.4}code{overflow-wrap:anywhere}#status{min-height:3em}small{display:block;line-height:1.6}
  @media(prefers-color-scheme:dark){body{background:var(--color-background-primary,#111);color:var(--color-text-primary,#fff)}}
</style>
<h1>MCP Apps probe</h1>
<p>This sandbox changes only its dedicated probe counter. Existing Spaces are protected.</p>
<small>Space: <code id="space">Waiting for an explicit tool result</code><br>Revision: <span id="revision">—</span></small>
<output id="counter" aria-label="Probe counter">—</output>
<button id="read" disabled>Reload</button><button id="increment" disabled>Increment</button>
<p id="status" role="status" aria-live="polite">Connecting to the MCP Apps host…</p>
<small>Tool calls persist in SQLite. This app does not send chat messages or wake a Bot.</small>
<script>
(() => {
  'use strict';
  const status = document.getElementById('status');
  const read = document.getElementById('read');
  const increment = document.getElementById('increment');
  const pending = new Map();
  let nextId = 0, ready = false, busy = false, current = null;
  function controls() { read.disabled = increment.disabled = !ready || busy || !current; }
  function notify(method, params) { window.parent.postMessage({jsonrpc:'2.0',method,params}, '*'); }
  function request(method, params) {
    const id = 'spaces-probe-' + (++nextId);
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => { pending.delete(id); reject(new Error('Host request timed out. Reload before retrying a write.')); }, 15000);
      pending.set(id, {resolve, reject, timer});
      window.parent.postMessage({jsonrpc:'2.0',id,method,params}, '*');
    });
  }
  function accept(result) {
    if (result && result.isError) {
      const message = (result.content || []).find(item => item.type === 'text');
      throw new Error(message ? message.text : 'Tool call failed');
    }
    const value = result && result.structuredContent;
    if (!value || value.probe_only !== true || typeof value.space_id !== 'string' || !value.space_id ||
        !Number.isSafeInteger(value.revision) || !value.state || !Number.isSafeInteger(value.state.counter)) {
      throw new Error('No valid, explicitly scoped probe state was returned.');
    }
    current = value;
    document.getElementById('space').textContent = value.space_id;
    document.getElementById('revision').textContent = String(value.revision);
    document.getElementById('counter').textContent = String(value.state.counter);
    controls();
  }
  async function invoke(name, args) {
    if (!ready || busy || !current) return;
    busy = true; controls(); status.textContent = 'Waiting for a committed tool result…';
    try {
      accept(await request('tools/call', {name, arguments:args}));
      status.textContent = 'Saved state read from the shared probe database.';
    } catch (error) { status.textContent = String(error.message || error); }
    finally { busy = false; controls(); }
  }
  window.addEventListener('message', event => {
    if (event.source !== window.parent) return;
    const message = event.data;
    if (!message || message.jsonrpc !== '2.0') return;
    if ('id' in message && !('method' in message)) {
      const waiting = pending.get(message.id);
      if (!waiting) return;
      clearTimeout(waiting.timer); pending.delete(message.id);
      if (message.error) waiting.reject(new Error(message.error.message || 'Host error'));
      else if ('result' in message) waiting.resolve(message.result);
      else waiting.reject(new Error('Invalid host response'));
      return;
    }
    if (message.method === 'ui/notifications/tool-result') {
      try { accept(message.params); status.textContent = 'Probe tool result received. Reload to check later Bot edits.'; }
      catch (error) { status.textContent = String(error.message || error); }
    } else if (message.method === 'ui/notifications/tool-cancelled') {
      status.textContent = 'The host cancelled the tool call. No completion is assumed.';
    } else if ('id' in message && 'method' in message) {
      window.parent.postMessage({jsonrpc:'2.0',id:message.id,error:{code:-32601,message:'Method not supported by this probe'}}, '*');
    }
  });
  read.addEventListener('click', () => invoke('spaces_get', {space_id:current.space_id}));
  increment.addEventListener('click', () => {
    if (current.state.counter >= Number.MAX_SAFE_INTEGER) { status.textContent = 'Counter limit reached.'; return; }
    invoke('spaces_patch', {space_id:current.space_id,expected_revision:current.revision,patch:{counter:current.state.counter + 1}});
  });
  if (window.parent === window) { status.textContent = 'Open this resource inside an MCP Apps host. No standalone bridge is available.'; return; }
  request('ui/initialize', {
    protocolVersion:'2026-01-26',appInfo:{name:'Grok Spaces probe',version:'0.1.0'},
    appCapabilities:{availableDisplayModes:['inline']}
  }).then(result => {
    if (!result || result.protocolVersion !== '2026-01-26') throw new Error('The host did not negotiate MCP Apps 2026-01-26.');
    notify('ui/notifications/initialized', {});
    ready = !!(result.hostCapabilities && result.hostCapabilities.serverTools);
    controls();
    status.textContent = ready ? 'Connected. Waiting for the scoped open-tool result.' : 'The host did not advertise server-tool calls. Rendering only.';
    notify('ui/notifications/size-changed', {height:document.documentElement.scrollHeight});
  }).catch(error => { status.textContent = String(error.message || error); });
})();
</script></html>'''


class RpcError(Exception):
    def __init__(self, code, message, data=None):
        super().__init__(message)
        self.code, self.data = code, data


def object_params(value):
    if not isinstance(value, dict):
        raise RpcError(-32602, "Parameters must be a JSON object")
    return value


def fields(value, required=(), optional=()):
    object_params(value)
    if set(required) - set(value) or set(value) - set(required) - set(optional):
        raise RpcError(-32602, "Missing or unknown parameters")


def space_id(value):
    if not isinstance(value, str) or not value or len(value) > 200:
        raise RpcError(-32602, "space_id must be an explicit nonempty string")
    return value


def schema(properties, required=()):
    return dict(type="object", properties=properties, required=list(required), additionalProperties=False)


def tool_definitions():
    reference = {"type": "string", "minLength": 1, "maxLength": 200}
    return [
        dict(name="spaces_open_app", description="Open the isolated MCP Apps probe. Omitting space_id explicitly creates/reopens its dedicated probe Space; existing business Spaces are refused.",
             inputSchema=schema({"space_id": reference}),
             _meta={"ui": {"resourceUri": RESOURCE_URI, "visibility": ["model", "app"]}}),
        dict(name="spaces_get", description="Read an explicitly identified MCP probe Space. Cannot read business Spaces.",
             inputSchema=schema({"space_id": reference}, ("space_id",)),
             annotations={"readOnlyHint": True}),
        dict(name="spaces_patch", description="Set only the isolated probe counter using an expected revision. A stale revision fails; this does not wake a Bot.",
             inputSchema=schema({"space_id": reference,
                                 "expected_revision": {"type": "integer", "minimum": 0},
                                 "patch": schema({"counter": {"type": "integer", "minimum": 0, "maximum": MAX_COUNTER}}, ("counter",))},
                                ("space_id", "expected_revision", "patch")),
             annotations={"destructiveHint": False}),
    ]


class Probe:
    def __init__(self, data_dir):
        self.store = Store(data_dir)
        self.initialized = False

    def _owned(self, key):
        space_id(key)
        with self.store.connect() as db:
            marker = db.execute("SELECT value FROM meta WHERE key=?", (OWNER_PREFIX + key,)).fetchone()
            if marker is None or marker[0] != "1":
                raise SpaceError("Only a dedicated MCP Apps probe Space is accessible", 403)
        return key

    def _open_default(self):
        # Reserve the probe and its ownership atomically, including concurrent hosts.
        with self.store.connect(write=True) as db:
            row = db.execute("SELECT value FROM meta WHERE key=?", (DEFAULT_KEY,)).fetchone()
            if row:
                key = row[0]
                owned = db.execute("SELECT value FROM meta WHERE key=?", (OWNER_PREFIX + key,)).fetchone()
                if owned and owned[0] == "1" and db.execute("SELECT 1 FROM spaces WHERE id=?", (key,)).fetchone():
                    return key
                raise SpaceError("The recorded probe is unavailable; no replacement was created", 409)
            bot, key, now = "bot_" + uuid.uuid4().hex, "space_" + uuid.uuid4().hex, timestamp()
            db.execute("INSERT INTO bots (id,name,external_bot_id,created_at) VALUES (?,?,?,?)",
                       (bot, "MCP Apps probe", "", now))
            db.execute("INSERT INTO spaces (id,bot_id,title,state,view_html,revision,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
                       (key, bot, "MCP Apps probe", encode({"counter": 0}), "", 0, now, now))
            db.executemany("INSERT INTO meta VALUES (?,?)", [(DEFAULT_KEY, key), (OWNER_PREFIX + key, "1")])
            return key

    def _snapshot(self, key):
        detail = self.store.detail(self._owned(key))
        counter = detail["state"].get("counter")
        if type(counter) is not int or not 0 <= counter <= MAX_COUNTER:
            raise SpaceError("Probe counter is invalid; no automatic repair was performed", 409)
        return dict(probe_only=True, space_id=key, space=detail["space"],
                    state={"counter": counter}, revision=detail["revision"])

    def call(self, name, args):
        object_params(args)
        if name == "spaces_open_app":
            fields(args, optional=("space_id",))
            key = self._open_default() if "space_id" not in args else self._owned(space_id(args["space_id"]))
        elif name == "spaces_get":
            fields(args, required=("space_id",))
            key = self._owned(space_id(args["space_id"]))
        elif name == "spaces_patch":
            fields(args, required=("space_id", "expected_revision", "patch"))
            key = self._owned(space_id(args["space_id"]))
            fields(args["patch"], required=("counter",))
            counter, revision = args["patch"]["counter"], args["expected_revision"]
            if type(counter) is not int or not 0 <= counter <= MAX_COUNTER:
                raise RpcError(-32602, "counter must be a nonnegative safe integer")
            if type(revision) is not int or revision < 0:
                raise RpcError(-32602, "expected_revision must be a nonnegative integer")
            self.store.patch_state(key, {"counter": counter}, revision, actor="mcp-probe")
        else:
            raise RpcError(-32602, "Unknown tool")
        data = self._snapshot(key)
        return dict(content=[dict(type="text", text=encode(data))], structuredContent=data)

    def dispatch(self, method, params):
        object_params(params)
        if method == "initialize":
            if self.initialized:
                raise RpcError(-32600, "Already initialized")
            version = params.get("protocolVersion")
            if not isinstance(version, str) or not isinstance(params.get("capabilities"), dict) or not isinstance(params.get("clientInfo"), dict):
                raise RpcError(-32602, "initialize requires protocolVersion, capabilities and clientInfo")
            self.initialized = True
            return dict(protocolVersion=version if version in MCP_VERSIONS else MCP_VERSIONS[0],
                        capabilities={"tools": {}, "resources": {}, "extensions": {"io.modelcontextprotocol/ui": {"mimeTypes": [MIME_TYPE]}}},
                        serverInfo={"name": "grok-spaces-mcp-probe", "version": "0.1.0"},
                        instructions="Isolated counter probe only. Get/patch require a probe space_id. No chat wakeup or sidebar integration is provided.")
        if not self.initialized:
            raise RpcError(-32002, "Server not initialized")
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": tool_definitions()}
        if method == "tools/call":
            fields(params, required=("name",), optional=("arguments", "_meta"))
            if not isinstance(params["name"], str):
                raise RpcError(-32602, "Tool name must be a string")
            try:
                return self.call(params["name"], params.get("arguments", {}))
            except SpaceError as exc:
                return dict(isError=True, content=[dict(type="text", text=encode(dict(error=str(exc), status=exc.status, **exc.details)))])
        if method == "resources/list":
            return {"resources": [dict(uri=RESOURCE_URI, name="Grok Spaces MCP Apps probe", mimeType=MIME_TYPE,
                                       description="Sandboxed counter UI; uses host tools only.")]}
        if method == "resources/read":
            fields(params, required=("uri",), optional=("_meta",))
            if params["uri"] != RESOURCE_URI:
                raise RpcError(-32002, "Resource not found")
            return {"contents": [dict(uri=RESOURCE_URI, mimeType=MIME_TYPE, text=HTML,
                                      _meta={"ui": {"prefersBorder": True, "csp": {"connectDomains": [], "resourceDomains": [], "frameDomains": [], "baseUriDomains": []}}})]}
        raise RpcError(-32601, "Method not found")

    def handle(self, message):
        request_id = None
        try:
            if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
                raise RpcError(-32600, "Invalid Request")
            if "id" not in message:
                # Notifications never execute tools or change probe state.
                return None
            request_id = message["id"]
            if type(request_id) not in (int, str):
                request_id = None
                raise RpcError(-32600, "Invalid request ID")
            result = self.dispatch(message["method"], message.get("params", {}))
            return dict(jsonrpc="2.0", id=request_id, result=result)
        except RpcError as exc:
            error = dict(code=exc.code, message=str(exc))
            if exc.data is not None:
                error["data"] = exc.data
            return dict(jsonrpc="2.0", id=request_id, error=error)
        except (OSError, ValueError, sqlite3.Error, SpaceError):
            return dict(jsonrpc="2.0", id=request_id, error=dict(code=-32603, message="Unable to access probe data"))


def emit(value):
    sys.stdout.write(encode(value) + "\n")
    sys.stdout.flush()


def serve(probe):
    stream = sys.stdin.buffer
    while True:
        line = stream.readline(MAX_LINE + 1)
        if not line:
            return
        if len(line) > MAX_LINE:
            while not line.endswith(b"\n"):
                line = stream.readline(MAX_LINE + 1)
                if not line:
                    break
            emit(dict(jsonrpc="2.0", id=None, error=dict(code=-32700, message="JSON message exceeds 2 MiB")))
            continue
        try:
            message = json.loads(line.decode("utf-8"), parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
        except (UnicodeError, ValueError):
            emit(dict(jsonrpc="2.0", id=None, error=dict(code=-32700, message="Parse error")))
            continue
        response = probe.handle(message)
        if response is not None:
            emit(response)


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__, add_help=False)
    parser.add_argument("--data-dir", type=Path, default=Path(os.environ.get("GROK_SPACES_DATA_DIR", DEFAULT_DATA)))
    parser.add_argument("-h", "--help", action="store_true")
    args = parser.parse_args()
    if args.help:
        parser.print_help(file=sys.stderr)
        return 0
    try:
        serve(Probe(args.data_dir))
        return 0
    except BrokenPipeError:
        return 0
    except (OSError, sqlite3.Error, SpaceError):
        print("mcp-probe: unable to open local probe storage", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
