"""Trusted local handlers; no page/HTTP endpoint may register an executable.

Python -I isolates interpreter configuration, not filesystem/network access.
At most two handlers run and 32 wait per dispatcher process. Claims are durable:
an accepted event is never executed again, including after an uncertain crash.
"""
from __future__ import annotations

import json
import os
import selectors
import signal
import stat
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from store import SpaceError, encode, timestamp

TIMEOUT_SECONDS = 60
MAX_OUTPUT_BYTES = 1024 * 1024
_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="grok-local-worker")
_CAPACITY = threading.BoundedSemaphore(34)
_SCHEMA_LOCK = threading.Lock()
_SCHEMAS = set()
_STOPPING = threading.Event()
_ACTIVE_LOCK = threading.Lock()
_ACTIVE = {}


def _schema(store):
    key = str(store.path)
    with _SCHEMA_LOCK:
        if key in _SCHEMAS:
            return
        with store.connect(snapshot=False) as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS local_workers (
                    space_id TEXT PRIMARY KEY REFERENCES spaces(id),
                    handler TEXT NOT NULL, action_types TEXT NOT NULL, registered_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS local_worker_runs (
                    event_id TEXT PRIMARY KEY REFERENCES events(id),
                    space_id TEXT NOT NULL REFERENCES spaces(id), status TEXT NOT NULL,
                    revision INTEGER NOT NULL, created_at TEXT NOT NULL, started_at TEXT,
                    finished_at TEXT, code TEXT, output_json TEXT
                );
            """)
        _SCHEMAS.add(key)


def _project_fd(store, space_id, create=False):
    if not isinstance(space_id, str) or not space_id.startswith("space_") or "/" in space_id or ".." in space_id:
        raise SpaceError("Invalid Space reference")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    parent = os.open(str(store.data_dir), flags)
    try:
        for part in ("projects", space_id):
            if create:
                try:
                    os.mkdir(part, 0o700, dir_fd=parent)
                except FileExistsError:
                    pass
            child = os.open(part, flags, dir_fd=parent)
            os.close(parent)
            parent = child
        return parent
    except OSError:
        os.close(parent)
        raise SpaceError("Space project directory is unavailable or contains a symlink", 400,
                         code="unsafe_project_path") from None


def _handler_fd(store, space_id, handler, create=False):
    project = store.data_dir / "projects" / space_id
    candidate = Path(handler).expanduser()
    try:
        relative = candidate.relative_to(project) if candidate.is_absolute() else candidate
    except ValueError:
        raise SpaceError("Handler must be inside this Space project", 400, code="handler_outside_project") from None
    if not relative.parts or any(part in {"..", "."} for part in relative.parts) or relative.suffix != ".py":
        raise SpaceError("Handler must be a project-local Python file", 400, code="unsafe_handler_path")
    project_fd = _project_fd(store, space_id, create=create)
    directory = os.dup(project_fd)
    try:
        for part in relative.parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        fd = os.open(relative.parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > 1024 * 1024:
            os.close(fd)
            raise SpaceError("Handler must be a regular Python file no larger than 1 MiB", 400,
                             code="unsafe_handler_file")
        return fd, project_fd, relative.as_posix()
    except OSError:
        os.close(project_fd)
        raise SpaceError("Handler is unavailable or contains a symlink", 400, code="unsafe_handler_path") from None
    except Exception:
        os.close(project_fd)
        raise
    finally:
        os.close(directory)


def register(store, space_id, file, types):
    """CLI-only registration. Does not execute code; existing claims stay final."""
    _schema(store)
    store.detail(space_id)
    if not isinstance(types, (list, tuple)) or not types or len(types) > 100 or any(
        not isinstance(value, str) or not value.strip() or len(value) > 100 for value in types
    ):
        raise SpaceError("Provide 1–100 explicit action types", 400, code="invalid_action_types")
    fd, project_fd, handler = _handler_fd(store, space_id, file, create=True)
    os.close(fd)
    os.close(project_fd)
    with store.connect(write=True) as db:
        db.execute("""INSERT INTO local_workers VALUES (?,?,?,?)
            ON CONFLICT(space_id) DO UPDATE SET handler=excluded.handler,
            action_types=excluded.action_types, registered_at=excluded.registered_at""",
                   (space_id, handler, encode(sorted(set(types))), timestamp()))
    return status(store, space_id)


def _summary(row):
    return {key: row[key] for key in ("event_id", "space_id", "status", "revision", "created_at", "started_at", "finished_at", "code")} | {
        "delivery": "local", "output_available": row["output_json"] is not None,
    }


def status(store, space_id, event_id=None, include_output=False):
    _schema(store)
    with store.connect() as db:
        store._required(db, "spaces", space_id)
        registration = db.execute("SELECT * FROM local_workers WHERE space_id=?", (space_id,)).fetchone()
        result = {"registered": registration is not None, "delivery": "local" if registration else "manual"}
        if registration:
            result.update(handler=registration["handler"], types=json.loads(registration["action_types"]))
        if event_id:
            event = store._required(db, "events", event_id)
            if event["space_id"] != space_id:
                raise SpaceError("Event not found in this Space", 404)
            row = db.execute("SELECT * FROM local_worker_runs WHERE event_id=?", (event_id,)).fetchone()
            result.update(_summary(row) if row else {"event_id": event_id, "status": "not_dispatched"})
            if include_output:
                result["output"] = json.loads(row["output_json"]) if row and row["output_json"] is not None else None
        else:
            result["runs"] = [_summary(row) for row in db.execute(
                "SELECT * FROM local_worker_runs WHERE space_id=? ORDER BY created_at DESC LIMIT 50", (space_id,))]
        return result


def dispatch(store, space_id, event):
    """Claim synchronously, run asynchronously. Never retries a claimed event."""
    _schema(store)
    if _STOPPING.is_set():
        return {"delivery": "local", "status": "deferred", "code": "worker_stopped", "accepted": False}
    event_id = event.get("id") if isinstance(event, dict) else event
    if not _CAPACITY.acquire(blocking=False):
        return {"delivery": "local", "status": "deferred", "code": "worker_queue_full", "accepted": False}
    submitted = False
    try:
        with store.connect(write=True) as db:
            item = store._required(db, "events", event_id)
            if item["space_id"] != space_id:
                raise SpaceError("Event not found in this Space", 404)
            old = db.execute("SELECT * FROM local_worker_runs WHERE event_id=?", (event_id,)).fetchone()
            if old:
                return dict(_summary(old), accepted=True, replayed=True)
            registration = db.execute("SELECT * FROM local_workers WHERE space_id=?", (space_id,)).fetchone()
            if registration is None or item["type"] not in json.loads(registration["action_types"]):
                return {"delivery": "manual", "status": "unregistered", "accepted": False}
            if item["status"] != "pending":
                return {"delivery": "local", "status": item["status"], "accepted": False}
            space = store._required(db, "spaces", space_id)
            db.execute("INSERT INTO local_worker_runs(event_id,space_id,status,revision,created_at) VALUES (?,?,?,?,?)",
                       (event_id, space_id, "queued", space["revision"], timestamp()))
            payload = {"refs": {"installation_id": store.info()["installation_id"], "bot_id": space["bot_id"],
                                "space_id": space_id, "event_id": event_id,
                                "project_dir": str(store.data_dir / "projects" / space_id),
                                "expected_revision": space["revision"]},
                       "event": {key: item[key] for key in ("id", "type", "request_id")},
                       "snapshot": {"state": json.loads(space["state"]), "revision": space["revision"]}}
            payload["event"]["payload"] = json.loads(item["payload"])
            handler = registration["handler"]
        _POOL.submit(_run, store, space_id, event_id, handler, payload)
        submitted = True
        return {"delivery": "local", "status": "queued", "event_id": event_id, "accepted": True, "replayed": False}
    except RuntimeError:
        _record(store, event_id, "failed", "worker_unavailable")
        return {"delivery": "local", "status": "failed", "code": "worker_unavailable", "accepted": False}
    finally:
        if not submitted:
            _CAPACITY.release()


def _record(store, event_id, state, code=None, output=None):
    with store.connect(write=True) as db:
        row = db.execute("SELECT space_id,status FROM local_worker_runs WHERE event_id=?", (event_id,)).fetchone()
        db.execute("UPDATE local_worker_runs SET status=?,code=?,output_json=COALESCE(?,output_json),finished_at=? WHERE event_id=?",
                   (state, code, encode(output) if output is not None else None, timestamp(), event_id))
        if row and state in {"failed", "needs_reconcile"} and row["status"] != state:
            store._insert_event(db, row["space_id"], "worker." + state,
                                {"event_id": event_id, "status": state, "code": code}, "worker")


def read_output(store, space_id, event_id):
    """Explicit local-CLI recovery read. Never include this in routine HTTP status."""
    summary = status(store, space_id, event_id)
    with store.connect() as db:
        row = db.execute("SELECT output_json FROM local_worker_runs WHERE event_id=? AND space_id=?", (event_id, space_id)).fetchone()
        return {"event_id": event_id, "status": summary["status"], "code": summary.get("code"),
                "output": json.loads(row["output_json"]) if row and row["output_json"] is not None else None}


class _WorkerFailure(Exception):
    pass


def _kill_group(process):
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        pass


def _bounded_io(process, body):
    output = bytearray()
    total = 0
    offset = 0
    deadline = time.monotonic() + TIMEOUT_SECONDS
    selector = selectors.DefaultSelector()
    streams = (process.stdin, process.stdout, process.stderr)
    try:
        for stream, name, events in ((process.stdin, "in", selectors.EVENT_WRITE),
                                     (process.stdout, "out", selectors.EVENT_READ),
                                     (process.stderr, "err", selectors.EVENT_READ)):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, events, name)
        while selector.get_map():
            if time.monotonic() >= deadline:
                raise _WorkerFailure("worker_timeout")
            for key, _ in selector.select(min(.1, max(0, deadline - time.monotonic()))):
                stream = key.fileobj
                if key.data == "in":
                    try:
                        offset += os.write(stream.fileno(), body[offset:offset + 8192])
                    except BlockingIOError:
                        continue
                    except BrokenPipeError:
                        offset = len(body)
                    if offset == len(body):
                        selector.unregister(stream)
                        stream.close()
                else:
                    try:
                        chunk = os.read(stream.fileno(), 8192)
                    except BlockingIOError:
                        continue
                    if not chunk:
                        selector.unregister(stream)
                        stream.close()
                        continue
                    total += len(chunk)
                    if total > MAX_OUTPUT_BYTES:
                        raise _WorkerFailure("worker_output_limit")
                    if key.data == "out":
                        output.extend(chunk)
        remaining = max(0, deadline - time.monotonic())
        try:
            code = process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            raise _WorkerFailure("worker_timeout") from None
        if code:
            raise _WorkerFailure("worker_exit")
        return bytes(output)
    finally:
        selector.close()
        _kill_group(process)
        for stream in streams:
            if not stream.closed:
                stream.close()


def _run(store, space_id, event_id, handler, payload):
    fd = project_fd = None
    process = None
    try:
        if _STOPPING.is_set():
            raise _WorkerFailure("worker_shutdown")
        with store.connect(write=True) as db:
            db.execute("UPDATE local_worker_runs SET status='running',started_at=? WHERE event_id=? AND status='queued'",
                       (timestamp(), event_id))
        fd, project_fd, _ = _handler_fd(store, space_id, handler)
        # macOS fdescfs cannot chdir('/dev/fd/N'). Change directory inside the
        # isolated child via its inherited descriptor; no threaded preexec_fn.
        bootstrap = ("import os,sys\n"
                     "os.fchdir(int(sys.argv[1]))\n"
                     "with os.fdopen(int(sys.argv[2]),'rb') as f: source=f.read(1048577)\n"
                     "if len(source)>1048576: sys.exit(65)\n"
                     "name=sys.argv[3];sys.argv=[name]\n"
                     "exec(compile(source,name,'exec'),{'__name__':'__main__','__file__':name})\n")
        process = subprocess.Popen(["/usr/bin/python3", "-I", "-B", "-c", bootstrap, str(project_fd), str(fd), handler],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   cwd="/", pass_fds=(fd, project_fd),
                                   start_new_session=True, env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LANG": "en_US.UTF-8"})
        with _ACTIVE_LOCK:
            if _STOPPING.is_set():
                _kill_group(process)
                raise _WorkerFailure("worker_shutdown")
            _ACTIVE[event_id] = process
        raw = _bounded_io(process, encode(payload).encode())
        try:
            output = json.loads(raw, parse_constant=lambda value: (_ for _ in ()).throw(ValueError()))
            if not isinstance(output, dict) or "result" not in output or set(output) - {"result", "patch"}:
                raise ValueError()
            if "patch" in output and not isinstance(output["patch"], dict):
                raise ValueError()
            encode(output)
        except (ValueError, UnicodeDecodeError, RecursionError, SpaceError):
            raise _WorkerFailure("worker_invalid_output") from None
        try:
            store.complete(space_id, event_id, payload["snapshot"]["revision"], output["result"], output.get("patch"))
        except SpaceError as exc:
            if exc.status == 409:
                _record(store, event_id, "needs_reconcile", "needs_reconcile", output)
                return
            raise _WorkerFailure("worker_completion_rejected") from None
        _record(store, event_id, "completed", output=output)
    except (_WorkerFailure, SpaceError, OSError) as exc:
        code = str(exc) if isinstance(exc, _WorkerFailure) else "worker_unavailable"
        try:
            store.complete(space_id, event_id, payload["snapshot"]["revision"], {"code": code}, failed=True)
        except SpaceError:
            pass
        _record(store, event_id, "failed", code)
    except Exception:
        _record(store, event_id, "failed", "worker_internal_error")
    finally:
        with _ACTIVE_LOCK:
            _ACTIVE.pop(event_id, None)
        if process is not None:
            _kill_group(process)
        for handle in (fd, project_fd):
            if handle is not None:
                os.close(handle)
        _CAPACITY.release()
        if not _STOPPING.is_set():
            recover(store)


def recover(store):
    """Only never-claimed pending actions; uncertain/queued/running claims are final."""
    _schema(store)
    with store.connect() as db:
        pending = []
        for row in db.execute("""
            SELECT e.space_id,e.id,e.type,w.action_types FROM events e JOIN local_workers w ON e.space_id=w.space_id
            LEFT JOIN local_worker_runs r ON e.id=r.event_id
            WHERE e.status='pending' AND r.event_id IS NULL ORDER BY e.seq
        """):
            if row["type"] in json.loads(row["action_types"]):
                pending.append((row["space_id"], row["id"]))
                if len(pending) >= 200:
                    break
    return {"delivery": "local", "dispatched": [dispatch(store, space_id, event_id) for space_id, event_id in pending]}


def shutdown(wait=True):
    """Normal server shutdown: stop launches and kill only tracked child groups.

    SIGKILL/crash cannot run this cleanup; durable claims still prevent replay.
    """
    _STOPPING.set()
    with _ACTIVE_LOCK:
        active = list(_ACTIVE.values())
    for process in active:
        _kill_group(process)
    _POOL.shutdown(wait=wait)
