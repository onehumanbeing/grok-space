#!/usr/bin/env python3
"""Loopback-only UI and state API. Uses Python 3.9+ standard library."""
import argparse
import json
import mimetypes
import os
import re
import signal
import socket
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from prompts import action_prompt, setup_prompt
from contract import describe
from store import SpaceError, Store
from version import VERSION, BUILD_NUMBER
import local_worker

DEFAULT_DATA = Path.home() / "Library/Application Support/Grok Spaces"
# A 1 MiB decoded HTML string can expand sixfold through JSON escaping.
# Persisted HTML/state and action payloads have separate decoded-byte limits.
MAX_BODY = 6 * 1024 * 1024 + 64 * 1024


class Handler(BaseHTTPRequestHandler):
    server_version = "GrokSpaces/0.1"

    def log_message(self, format, *args):
        # Request bodies, query strings, imported HTML and Bot content stay out of logs.
        return

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-src 'self' about:; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
        super().end_headers()

    def _check_request(self, write=False):
        authority = "127.0.0.1:{}".format(self.server.server_port)
        if self.headers.get("Host") != authority:
            raise SpaceError("Invalid local host", 403)
        origin = self.headers.get("Origin")
        if origin is not None and origin != "http://" + authority:
            raise SpaceError("Cross-origin requests are not allowed", 403)
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            raise SpaceError("Cross-site requests are not allowed", 403)
        if write and self.headers.get("Content-Type", "").split(";", 1)[0].strip() != "application/json":
            raise SpaceError("Use application/json", 415)

    def _json_body(self):
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise SpaceError("Invalid Content-Length") from exc
        if size < 0 or size > MAX_BODY:
            raise SpaceError("Request too large", 413)
        try:
            data = json.loads(self.rfile.read(size), parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
        except (ValueError, UnicodeDecodeError, RecursionError) as exc:
            raise SpaceError("Invalid JSON") from exc
        if not isinstance(data, dict):
            raise SpaceError("Expected a JSON object")
        return data

    def _send(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _route(self):
        write = self.command != "GET"
        self._check_request(write)
        parsed = urlsplit(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        store = self.server.store
        if self.command == "GET" and path == "/health":
            return self._send(200, dict(app="grok-spaces", version=VERSION, build=BUILD_NUMBER, storage="sqlite", data_dir=str(store.data_dir), **store.info()))
        if self.command == "GET" and path == "/api/bots":
            return self._send(200, {"bots": store.bots()})
        if self.command == "GET" and path == "/api/contract":
            return self._send(200, describe(store, query.get("space_id", [None])[0]))
        if self.command == "POST" and path == "/api/bots":
            data = self._json_body()
            return self._send(201, {"bot": store.create_bot(data.get("name", ""), data.get("bot_id", ""))})
        if self.command == "GET" and path == "/api/spaces":
            return self._send(200, {"spaces": store.spaces(query.get("bot_id", [None])[0])})
        if self.command == "POST" and path == "/api/spaces":
            data = self._json_body()
            return self._send(201, {"space": store.create_space(data.get("bot_id"), data.get("title", ""))})
        match = re.fullmatch(r"/api/spaces/(space_[a-f0-9]{32})(?:/(state|view|actions|events|setup-prompt|worker))?", path)
        event_match = re.fullmatch(r"/api/spaces/(space_[a-f0-9]{32})/events/(event_[a-f0-9]{32})", path)
        if self.command == "GET" and event_match:
            return self._send(200, {"event": store.event(*event_match.groups())})
        if match:
            space_id, endpoint = match.groups()
            if self.command == "GET" and endpoint == "worker":
                return self._send(200, local_worker.status(store, space_id))
            if self.command == "GET" and endpoint is None:
                values = []
                for key in ("known_revision", "known_seq"):
                    raw = query.get(key, [None])[0]
                    try:
                        value = int(raw) if raw is not None else None
                    except ValueError as exc:
                        raise SpaceError(key + " must be a nonnegative integer") from exc
                    if value is not None and value < 0:
                        raise SpaceError(key + " must be a nonnegative integer")
                    values.append(value)
                return self._send(200, store.detail(space_id, *values))
            if self.command == "GET" and endpoint == "events":
                try:
                    after = int(query.get("after", ["0"])[0])
                except ValueError as exc:
                    raise SpaceError("after must be an integer") from exc
                return self._send(200, {"events": store.events(space_id, after, query.get("status", [None])[0])})
            if self.command == "DELETE" and endpoint is None:
                data = self._json_body()
                return self._send(200, store.delete_space(space_id, data.get("title")))
            if self.command == "PATCH" and endpoint is None:
                data = self._json_body()
                return self._send(200, {"space": store.rebind_space(space_id, data.get("bot_id"))})
            if self.command == "PATCH" and endpoint == "state":
                data = self._json_body()
                return self._send(200, store.patch_state(space_id, data.get("patch"), data.get("expected_revision"), "user"))
            if self.command == "PUT" and endpoint == "view":
                data = self._json_body()
                return self._send(200, store.set_view(space_id, data.get("html"), data.get("expected_revision"), "user"))
            if self.command == "POST" and endpoint == "actions":
                data = self._json_body()
                revision = data.get("expected_revision")
                if type(revision) is not int:
                    raise SpaceError("expected_revision must be an integer")
                event = store.action(space_id, data.get("type", "request"), data.get("payload", {}), data.get("request_id"), expected_revision=revision)
                worker = local_worker.dispatch(store, space_id, event["id"])
                return self._send(200, {"event": event, "prompt": action_prompt(store, space_id, event),
                                        "delivery": worker.get("delivery", "manual"), "worker": worker})
            if self.command == "POST" and endpoint == "setup-prompt":
                data = self._json_body()
                return self._send(200, {"prompt": setup_prompt(store, space_id, full=data.get("mode") == "full")})
        if self.command == "GET" and not path.startswith("/api/"):
            return self._static(path)
        raise SpaceError("Endpoint not found", 404)

    def _static(self, path):
        relative = "index.html" if path == "/" else path.lstrip("/")
        root = self.server.web_root
        candidate = (root / relative).resolve()
        if root not in candidate.parents or not candidate.is_file():
            raise SpaceError("File not found", 404)
        if candidate.suffix not in {".html", ".js", ".css", ".svg", ".png", ".ico"}:
            raise SpaceError("File type not served", 403)
        body = candidate.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", (mimetypes.guess_type(str(candidate))[0] or "application/octet-stream") + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle(self):
        self.connection.settimeout(10)
        try:
            self._route()
        except SpaceError as exc:
            self._send(exc.status, dict(error=str(exc), **exc.details))
        except (BrokenPipeError, ConnectionResetError, socket.timeout):
            return
        except Exception:
            print("Request failed; content omitted", file=sys.stderr, flush=True)
            self._send(500, {"error": "Local storage request failed"})

    do_GET = _handle
    do_POST = _handle
    do_PATCH = _handle
    do_DELETE = _handle
    do_PUT = _handle


def create_server(port, data_dir, seed=False):
    store = Store(data_dir)
    if seed:
        store.seed()
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    server.store = store
    server.web_root = Path(__file__).resolve().parent.parent.joinpath("web").resolve()
    local_worker.recover(store)
    return server


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description="Grok Spaces loopback service")
    parser.add_argument("--port", type=int, default=8789)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--seed-demo", action="store_true", help="Explicitly create a local sandbox association")
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("port must be 0–65535")
    def terminate(signum, frame):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, terminate)
    server = None
    try:
        server = create_server(args.port, args.data_dir, seed=args.seed_demo)
        print("Grok Spaces listening on 127.0.0.1:{}".format(server.server_port), flush=True)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        local_worker.shutdown()
        if server is not None:
            server.server_close()


if __name__ == "__main__":
    main()
