"""Durable Space state and event ledger, shared by the UI and local Bot tools."""
from __future__ import annotations

import contextlib
import base64
import hashlib
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path


class SpaceError(Exception):
    def __init__(self, message, status=400, **details):
        super().__init__(message)
        self.status = status
        self.details = details


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def encode(value):
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError, RecursionError) as exc:
        raise SpaceError("Expected finite JSON data") from exc


def merge_patch(target, patch, _depth=0):
    """RFC 7396. State is always an object; nested null removes a field."""
    if _depth > 64:
        raise SpaceError("JSON Merge Patch nesting exceeds 64 levels")
    if not isinstance(patch, dict):
        return patch
    output = dict(target) if isinstance(target, dict) else {}
    for key, value in patch.items():
        if value is None:
            output.pop(key, None)
        else:
            output[key] = merge_patch(output.get(key), value, _depth + 1)
    return output


class Store:
    def __init__(self, data_dir):
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.data_dir / "spaces.sqlite3"
        with self.connect(snapshot=False) as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS bots (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL,
                    external_bot_id TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS bot_icons (
                    bot_id TEXT PRIMARY KEY REFERENCES bots(id), data_uri TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS spaces (
                    id TEXT PRIMARY KEY, bot_id TEXT NOT NULL REFERENCES bots(id),
                    title TEXT NOT NULL, state TEXT NOT NULL, view_html TEXT NOT NULL DEFAULT '',
                    revision INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL,
                    space_id TEXT NOT NULL REFERENCES spaces(id), type TEXT NOT NULL,
                    payload TEXT NOT NULL, actor TEXT NOT NULL,
                    status TEXT NOT NULL, result TEXT,
                    created_at TEXT NOT NULL, completed_at TEXT,
                    request_id TEXT, fingerprint TEXT, completion_fingerprint TEXT,
                    UNIQUE(space_id, request_id)
                );
                CREATE INDEX IF NOT EXISTS space_events ON events(space_id, seq);
            """)
            db.execute("INSERT OR IGNORE INTO meta VALUES ('installation_id', ?)", (str(uuid.uuid4()),))
            db.execute("INSERT OR IGNORE INTO meta VALUES ('schema_version', '1')")
        os.chmod(self.path, 0o600)

    @contextlib.contextmanager
    def connect(self, write=False, snapshot=True):
        db = sqlite3.connect(str(self.path), timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            if snapshot:
                db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def info(self):
        with self.connect() as db:
            return dict(db.execute("SELECT key, value FROM meta").fetchall())

    @staticmethod
    def _required(db, table, key):
        if table not in {"spaces", "bots", "events"}:
            raise ValueError("Unknown table")
        row = db.execute("SELECT * FROM " + table + " WHERE id=?", (key,)).fetchone()
        if row is None:
            raise SpaceError("{} not found".format(table[:-1].capitalize()), 404)
        return row

    @staticmethod
    def _space(row):
        return {key: row[key] for key in ("id", "bot_id", "title", "revision", "created_at", "updated_at")}

    @staticmethod
    def _event(row, compact=False):
        output = dict(row)
        output["payload"] = json.loads(output["payload"])
        output["result"] = json.loads(output["result"]) if output["result"] is not None else None
        for key in ("fingerprint", "completion_fingerprint"):
            output.pop(key, None)
        if compact and isinstance(output["payload"], dict) and output["type"] in {"state.updated", "space.published"}:
            patch = output["payload"].get("patch")
            if isinstance(patch, dict):
                serialized = encode(patch).encode()
                if len(serialized) > 4096:
                    output["payload"].pop("patch")
                    output["payload"]["patch_summary"] = {
                        "sha256": hashlib.sha256(serialized).hexdigest(),
                        "bytes": len(serialized), "field_count": len(patch),
                        "fields": [key[:100] for key in list(patch)[:20]],
                    }
        return output

    @staticmethod
    def _revision(row, expected):
        if isinstance(expected, bool) or not isinstance(expected, int):
            raise SpaceError("expected_revision must be an integer", 400)
        if row["revision"] != expected:
            raise SpaceError("Space changed: current revision is {}. Read and reconcile before saving.".format(row["revision"]),
                             409, code="revision_conflict", revision=row["revision"],
                             current_revision=row["revision"], expected_revision=expected)

    def bots(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT bots.*, COALESCE(bot_icons.data_uri,'') AS icon_url "
                    "FROM bots LEFT JOIN bot_icons ON bot_icons.bot_id=bots.id ORDER BY bots.created_at,bots.id")]

    def set_bot_icon(self, bot_id, path):
        """Local CLI only: use an existing Bot image without remote requests."""
        with Path(path).open("rb") as source:
            data = source.read(128 * 1024 + 1)
        if len(data) > 128 * 1024:
            raise SpaceError("Bot icon exceeds 128 KiB", 413)
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            mime = "image/png"
        elif data.startswith(b"\xff\xd8\xff"):
            mime = "image/jpeg"
        elif data.startswith((b"GIF87a", b"GIF89a")):
            mime = "image/gif"
        elif data.startswith(b"RIFF") and data[8:12] == b"WEBP":
            mime = "image/webp"
        else:
            raise SpaceError("Use a PNG, JPEG, GIF or WebP Bot icon")
        uri = "data:" + mime + ";base64," + base64.b64encode(data).decode("ascii")
        with self.connect(write=True) as db:
            self._required(db, "bots", bot_id)
            db.execute("INSERT OR REPLACE INTO bot_icons VALUES (?,?)", (bot_id, uri))
        return {"bot_id": bot_id, "icon_saved": True}

    def create_bot(self, name, external_bot_id=""):
        if not isinstance(name, str):
            raise SpaceError("Bot name must be text")
        name = name.strip()
        if not name or len(name) > 100:
            raise SpaceError("Bot name must contain 1–100 characters")
        if not isinstance(external_bot_id, str) or len(external_bot_id) > 200:
            raise SpaceError("Invalid external Bot reference")
        result = dict(id="bot_" + uuid.uuid4().hex, name=name,
                      external_bot_id=external_bot_id, created_at=timestamp())
        with self.connect(write=True) as db:
            db.execute("INSERT INTO bots VALUES (:id,:name,:external_bot_id,:created_at)", result)
        return result

    def resolve_bot(self, reference, create=False):
        """Use an exact ID or unique name; opt-in creation is atomic."""
        if not isinstance(reference, str) or not reference.strip() or len(reference.strip()) > 100:
            raise SpaceError("Bot reference must contain 1–100 characters")
        reference = reference.strip()
        with self.connect(write=True) as db:
            by_id = db.execute("SELECT * FROM bots WHERE id=?", (reference,)).fetchone()
            if by_id:
                return dict(by_id), False
            matches = db.execute("SELECT * FROM bots WHERE name=? ORDER BY created_at,id", (reference,)).fetchall()
            if len(matches) > 1:
                raise SpaceError("Multiple Bot associations have this name. Select an explicit local Bot ID.", 409,
                                 candidates=[dict(row) for row in matches])
            if matches:
                return dict(matches[0]), False
            if not create or reference.startswith("bot_"):
                raise SpaceError("Bot association not found. Choose an existing ID or explicitly create a local association.", 404,
                                 candidates=[dict(row) for row in db.execute("SELECT * FROM bots ORDER BY created_at,id")])
            bot = dict(id="bot_" + uuid.uuid4().hex, name=reference, external_bot_id="", created_at=timestamp())
            db.execute("INSERT INTO bots VALUES (:id,:name,:external_bot_id,:created_at)", bot)
            return bot, True

    def spaces(self, bot_id=None):
        with self.connect() as db:
            if bot_id:
                self._required(db, "bots", bot_id)
                rows = db.execute("SELECT * FROM spaces WHERE bot_id=? ORDER BY created_at, id", (bot_id,))
            else:
                rows = db.execute("SELECT * FROM spaces ORDER BY created_at, id")
            return [self._space(row) for row in rows]

    def create_space(self, bot_id, title):
        if not isinstance(title, str):
            raise SpaceError("Space title must be text")
        title = title.strip()
        if not title or len(title) > 150:
            raise SpaceError("Space title must contain 1–150 characters")
        now = timestamp()
        key = "space_" + uuid.uuid4().hex
        with self.connect(write=True) as db:
            self._required(db, "bots", bot_id)
            db.execute("INSERT INTO spaces VALUES (?,?,?,?,?,?,?,?)", (
                key, bot_id, title, encode({}), "", 0, now, now,
            ))
        return self.detail(key)["space"]

    def rebind_space(self, space_id, bot_id, actor="user"):
        """Move a Space to another local Bot label; state, HTML and history stay."""
        if not isinstance(bot_id, str):
            raise SpaceError("bot_id must be a local Bot ID")
        with self.connect(write=True) as db:
            row = self._required(db, "spaces", space_id)
            self._required(db, "bots", bot_id)
            if row["bot_id"] != bot_id:
                db.execute("UPDATE spaces SET bot_id=?, updated_at=? WHERE id=?", (bot_id, timestamp(), space_id))
                self._insert_event(db, space_id, "space.rebound", {"from_bot_id": row["bot_id"], "bot_id": bot_id}, actor)
            return self._space(self._required(db, "spaces", space_id))

    def resolve_space(self, bot_id, title):
        """Resolve/create an exact title atomically; never guess among duplicates."""
        if not isinstance(title, str) or not title.strip() or len(title.strip()) > 150:
            raise SpaceError("Space title must contain 1–150 characters")
        title = title.strip()
        with self.connect(write=True) as db:
            self._required(db, "bots", bot_id)
            matches = db.execute("SELECT * FROM spaces WHERE bot_id=? AND title=? ORDER BY created_at,id", (bot_id, title)).fetchall()
            if len(matches) > 1:
                raise SpaceError("Multiple Spaces have this title. Select an explicit Space ID.", 409,
                                 candidates=[self._space(row) for row in matches])
            if matches:
                return self._space(matches[0]), False
            now = timestamp()
            key = "space_" + uuid.uuid4().hex
            db.execute("INSERT INTO spaces VALUES (?,?,?,?,?,?,?,?)", (key, bot_id, title, encode({}), "", 0, now, now))
            return self._space(self._required(db, "spaces", key)), True

    def detail(self, space_id, known_revision=None, known_seq=None):
        with self.connect() as db:
            row = self._required(db, "spaces", space_id)
            cursor = db.execute("SELECT COALESCE(MAX(seq),0) FROM events WHERE space_id=?", (space_id,)).fetchone()[0]
            if known_revision == row["revision"] and known_seq == cursor:
                return dict(unchanged=True, revision=row["revision"], event_cursor=cursor)
            events = db.execute("SELECT * FROM (SELECT * FROM events WHERE space_id=? ORDER BY seq DESC LIMIT 50) ORDER BY seq", (space_id,))
            pending_count = db.execute("SELECT COUNT(*) FROM events WHERE space_id=? AND status='pending'", (space_id,)).fetchone()[0]
            return dict(space=self._space(row), state=json.loads(row["state"]),
                        view_html=row["view_html"], revision=row["revision"],
                        pending_count=pending_count, event_cursor=cursor,
                        events=[self._event(event, compact=True) for event in events])

    def events(self, space_id, after=0, status=None):
        if status not in {None, "pending", "completed", "failed"}:
            raise SpaceError("Invalid event status")
        with self.connect() as db:
            self._required(db, "spaces", space_id)
            if status is not None:
                return [self._event(row) for row in db.execute(
                    "SELECT * FROM events WHERE space_id=? AND seq>? AND status=? ORDER BY seq LIMIT 200", (space_id, after, status))]
            return [self._event(row) for row in db.execute(
                "SELECT * FROM events WHERE space_id=? AND seq>? ORDER BY seq LIMIT 200", (space_id, after))]

    def event(self, space_id, event_id):
        with self.connect() as db:
            row = self._required(db, "events", event_id)
            if row["space_id"] != space_id:
                raise SpaceError("Event not found in this Space", 404)
            return self._event(row)

    def _insert_event(self, db, space_id, event_type, payload, actor, status="completed", **extra):
        if not isinstance(event_type, str) or not event_type.strip() or len(event_type) > 100:
            raise SpaceError("Invalid action type")
        key = "event_" + uuid.uuid4().hex
        db.execute("""INSERT INTO events
            (id,space_id,type,payload,actor,status,created_at,completed_at,request_id,fingerprint)
            VALUES (?,?,?,?,?,?,?,?,?,?)""", (
                key, space_id, event_type, encode(payload), actor, status, timestamp(),
                timestamp() if status == "completed" else None,
                extra.get("request_id"), extra.get("fingerprint"),
            ))
        return self._event(self._required(db, "events", key))

    def patch_state(self, space_id, patch, expected_revision, actor="user"):
        if not isinstance(patch, dict):
            raise SpaceError("patch must be a JSON object")
        with self.connect(write=True) as db:
            row = self._required(db, "spaces", space_id)
            self._revision(row, expected_revision)
            state = merge_patch(json.loads(row["state"]), patch)
            serialized = encode(state)
            if len(serialized.encode()) > 1024 * 1024:
                raise SpaceError("State exceeds 1 MiB", 413)
            revision = row["revision"] + 1
            db.execute("UPDATE spaces SET state=?,revision=?,updated_at=? WHERE id=?", (serialized, revision, timestamp(), space_id))
            event = self._insert_event(db, space_id, "state.updated", {"patch": patch, "revision": revision}, actor)
        return dict(state=state, revision=revision, event=event)

    def set_view(self, space_id, html, expected_revision, actor="user"):
        return self.publish(space_id, html, expected_revision, actor=actor)

    def publish(self, space_id, html, expected_revision, patch=None, actor="bot"):
        """Publish code and optional state together, under one revision check."""
        if not isinstance(html, str) or len(html.encode()) > 1024 * 1024:
            raise SpaceError("HTML must be text no larger than 1 MiB")
        if patch is not None and not isinstance(patch, dict):
            raise SpaceError("patch must be a JSON object")
        with self.connect(write=True) as db:
            row = self._required(db, "spaces", space_id)
            self._revision(row, expected_revision)
            state = row["state"] if patch is None else encode(merge_patch(json.loads(row["state"]), patch))
            if len(state.encode()) > 1024 * 1024:
                raise SpaceError("State exceeds 1 MiB", 413)
            db.execute("UPDATE spaces SET view_html=?,state=?,revision=revision+1,updated_at=? WHERE id=?", (html, state, timestamp(), space_id))
            event_payload = {"sha256": hashlib.sha256(html.encode()).hexdigest(), "revision": row["revision"] + 1}
            if patch is not None:
                event_payload["patch"] = patch
            self._insert_event(db, space_id, "space.published" if patch is not None else "view.updated", event_payload, actor)
        return self.detail(space_id)

    def action(self, space_id, event_type, payload, request_id, actor="user", expected_revision=None):
        if not isinstance(request_id, str) or not request_id or len(request_id) > 200:
            raise SpaceError("request_id is required (1–200 characters)")
        if len(encode(payload).encode()) > 64 * 1024:
            raise SpaceError("Action payload exceeds 64 KiB", 413)
        fingerprint = hashlib.sha256(encode([event_type, payload]).encode()).hexdigest()
        with self.connect(write=True) as db:
            space = self._required(db, "spaces", space_id)
            old = db.execute("SELECT * FROM events WHERE space_id=? AND request_id=?", (space_id, request_id)).fetchone()
            if old:
                if old["fingerprint"] != fingerprint:
                    raise SpaceError("request_id was used for another action", 409)
                return self._event(old)
            if expected_revision is not None:
                self._revision(space, expected_revision)
            return self._insert_event(db, space_id, event_type, payload, actor, "pending", request_id=request_id, fingerprint=fingerprint)

    def complete(self, space_id, event_id, expected_revision, result, patch=None, failed=False):
        if patch is not None and not isinstance(patch, dict):
            raise SpaceError("patch must be a JSON object")
        fingerprint = hashlib.sha256(encode([expected_revision, result, patch, failed]).encode()).hexdigest()
        with self.connect(write=True) as db:
            event = self._required(db, "events", event_id)
            if event["space_id"] != space_id:
                raise SpaceError("Event not found in this Space", 404)
            if event["status"] != "pending":
                if event["completion_fingerprint"] == fingerprint:
                    return {"event": self._event(event), "replayed": True}
                raise SpaceError("Event already finished with another result", 409)
            space = self._required(db, "spaces", space_id)
            self._revision(space, expected_revision)
            if patch is not None:
                state = encode(merge_patch(json.loads(space["state"]), patch))
                if len(state.encode()) > 1024 * 1024:
                    raise SpaceError("State exceeds 1 MiB", 413)
                db.execute("UPDATE spaces SET state=?,revision=revision+1,updated_at=? WHERE id=?", (state, timestamp(), space_id))
            db.execute("UPDATE events SET status=?,result=?,completed_at=?,completion_fingerprint=? WHERE id=?", (
                "failed" if failed else "completed", encode(result), timestamp(), fingerprint, event_id))
            self._insert_event(db, space_id, "action.failed" if failed else "action.completed", {
                "event_id": event_id, "status": "failed" if failed else "completed", "result": result,
            }, "bot")
            return {"event": self._event(self._required(db, "events", event_id)), "replayed": False}

    def project_directory(self, space_id, create=False):
        with self.connect() as db:
            self._required(db, "spaces", space_id)
        folder = self.data_dir / "projects" / space_id
        if create:
            folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        return folder

    def seed(self):
        with self.connect(write=True) as db:
            if db.execute("SELECT 1 FROM bots LIMIT 1").fetchone():
                return
            bot_id = "bot_" + uuid.uuid4().hex
            now = timestamp()
            db.execute("INSERT INTO bots VALUES (?,?,?,?)", (bot_id, "Local sandbox", "", now))
            db.execute("INSERT INTO spaces VALUES (?,?,?,?,?,?,?,?)", (
                "space_" + uuid.uuid4().hex, bot_id, "My first Space",
                encode({}), "", 0, now, now,
            ))
