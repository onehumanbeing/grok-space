#!/usr/bin/env python3
"""Disposable local author preview. Actions are JSON echoes, never executable."""
import argparse
import json
import re
import socket
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from store import SpaceError, Store

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "scripts/preview-assets"
LIMIT = 1024 * 1024


def read_html(path):
    with path.open("rb") as source:
        raw = source.read(LIMIT + 1)
    if len(raw) > LIMIT:
        raise ValueError("HTML must be no larger than 1 MiB")
    return raw.decode("utf-8")


class PreviewHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-src 'self' about:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        super().end_headers()

    def send_json(self, status, value):
        data = json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def body(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise SpaceError("Invalid Content-Length") from None
        if not 0 <= length <= 6 * LIMIT + 65536:
            raise SpaceError("Request too large", 413)
        try:
            value = json.loads(self.rfile.read(length), parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()))
        except (ValueError, UnicodeDecodeError, RecursionError):
            raise SpaceError("Expected finite JSON") from None
        if not isinstance(value, dict):
            raise SpaceError("Expected a JSON object")
        return value

    def route(self):
        authority = "127.0.0.1:" + str(self.server.server_port)
        if self.headers.get("Host") != authority:
            raise SpaceError("Invalid local host", 403)
        if self.headers.get("Origin") not in (None, "http://" + authority) or self.headers.get("Sec-Fetch-Site") == "cross-site":
            raise SpaceError("Cross-origin requests are not allowed", 403)
        if self.command != "GET" and self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            raise SpaceError("Use application/json", 415)
        parsed = urlsplit(self.path)
        path, query = parsed.path, parse_qs(parsed.query)
        store, space_id = self.server.store, self.server.space_id
        if self.command == "GET" and path == "/health":
            return self.send_json(200, {"app": "grok-space-preview", "mock": True, "transient": True, "space_id": space_id})
        if self.command == "GET" and path == "/api/preview":
            return self.send_json(200, dict(store.detail(space_id), mock=True))
        if self.command == "POST" and path == "/api/preview/reload":
            self.body()
            detail = store.detail(space_id)
            return self.send_json(200, store.set_view(space_id, read_html(self.server.html_path), detail["revision"], "preview"))
        match = re.fullmatch(r"/api/spaces/(space_[a-f0-9]{32})(?:/(state|actions|events))?", path)
        event_match = re.fullmatch(r"/api/spaces/(space_[a-f0-9]{32})/events/(event_[a-f0-9]{32})", path)
        if self.command == "GET" and event_match and event_match[1] == space_id:
            return self.send_json(200, {"event": store.event(space_id, event_match[2])})
        if match and match[1] == space_id:
            endpoint = match[2]
            if self.command == "GET" and endpoint is None:
                return self.send_json(200, store.detail(space_id))
            if self.command == "GET" and endpoint == "events":
                try:
                    after = int(query.get("after", ["0"])[0])
                except ValueError:
                    raise SpaceError("after must be an integer") from None
                if after < 0:
                    raise SpaceError("after must be nonnegative")
                return self.send_json(200, {"events": store.events(space_id, after, query.get("status", [None])[0])})
            if self.command == "PATCH" and endpoint == "state":
                data = self.body()
                return self.send_json(200, store.patch_state(space_id, data.get("patch"), data.get("expected_revision"), "preview"))
            if self.command == "POST" and endpoint == "actions":
                data = self.body()
                if type(data.get("expected_revision")) is not int:
                    raise SpaceError("expected_revision must be an integer")
                if not isinstance(data.get("payload", {}), dict):
                    raise SpaceError("payload must be an object")
                event = store.action(space_id, data.get("type"), data.get("payload", {}), data.get("request_id"), "preview", data["expected_revision"])
                if event["status"] == "pending":
                    result = {"mock": True, "action": event["type"], "payload": event["payload"]}
                    event = store.complete(space_id, event["id"], data["expected_revision"], result)["event"]
                return self.send_json(200, {"event": event, "delivery": "mock", "mock": True})
        static = {
            "/": (ASSETS / "index.html", "text/html"),
            "/preview.js": (ASSETS / "preview.js", "text/javascript"),
            "/preview.css": (ASSETS / "preview.css", "text/css"),
            "/space-sdk.js": (ROOT / "web/space-sdk.js", "text/javascript"),
        }
        if self.command == "GET" and path in static:
            file, mime = static[path]
            data = file.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mime + "; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            return self.wfile.write(data)
        raise SpaceError("Preview endpoint not found", 404)

    def handle_request(self):
        self.connection.settimeout(10)
        try:
            self.route()
        except SpaceError as error:
            self.send_json(error.status, dict(error=str(error), **error.details))
        except (BrokenPipeError, ConnectionResetError, socket.timeout):
            pass
        except (OSError, ValueError, UnicodeDecodeError):
            self.send_json(400, {"error": "The preview input could not be read."})

    do_GET = do_POST = do_PATCH = handle_request


def main():
    parser = argparse.ArgumentParser(description="Disposable local preview using the real SDK. Actions are mocked; no handlers run.")
    parser.add_argument("--html", type=Path, required=True)
    parser.add_argument("--state", type=Path, help="Optional initial JSON object; changes remain in the temporary preview only.")
    parser.add_argument("--port", type=int, default=0, help="Loopback port, 0 chooses a free port.")
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("port must be 0–65535")
    html = read_html(args.html.resolve())
    state = {}
    if args.state:
        with args.state.open("rb") as source:
            raw = source.read(LIMIT + 1)
        if len(raw) > LIMIT:
            parser.error("initial state exceeds 1 MiB")
        state = json.loads(raw, parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()))
        if not isinstance(state, dict):
            parser.error("initial state must be a JSON object")
    with tempfile.TemporaryDirectory(prefix="grok-space-preview-") as directory:
        store = Store(directory)
        bot = store.create_bot("Preview only")
        space = store.create_space(bot["id"], "Local preview")
        store.publish(space["id"], html, 0, state, "preview")
        server = ThreadingHTTPServer(("127.0.0.1", args.port), PreviewHandler)
        server.daemon_threads = True
        server.store, server.space_id, server.html_path = store, space["id"], args.html.resolve()
        print(json.dumps({"app": "grok-space-preview", "url": "http://127.0.0.1:" + str(server.server_port), "mock": True, "transient": True}), flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()


if __name__ == "__main__":
    main()
