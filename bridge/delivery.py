"""Optional official-webhook transport; it does not execute or complete actions.

No live routine, provider grant or account pairing is established by this module.
Call deliver only after the action transaction commits. Accepted/ambiguous sends
are durable and never retried automatically. A Bot-owned routine must still read
the referenced local event and complete it through the existing CLI.
"""
import contextlib
import json
import os
import re
import sqlite3
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

KEY_ENV = "GROK_SPACE_WEBHOOK_KEY"
OFFICIAL_HOSTS = {"api2.cursor.sh"}


class DeliveryError(Exception):
    pass


def validate_url(url, allow_loopback=False):
    try:
        value = urlsplit(url)
        local = allow_loopback and value.scheme == "http" and value.hostname == "127.0.0.1"
        official = value.scheme == "https" and value.hostname in OFFICIAL_HOSTS and value.port in (None, 443)
        if not (local or official) or value.username or value.password or value.query or value.fragment:
            raise ValueError()
        if not re.fullmatch(r"/automations/webhook/[A-Za-z0-9_-]{1,200}", value.path):
            raise ValueError()
    except (TypeError, ValueError):
        raise DeliveryError("Use the current official Grok Bot webhook URL, without credentials, query or fragment.") from None
    return url


def validate_refs(value):
    required = {"installation_id", "bot_id", "space_id", "event_id", "operation"}
    if not isinstance(value, dict) or set(value) != required or not isinstance(value["operation"], str) or value["operation"] not in {"publish", "action"}:
        raise DeliveryError("Invalid delivery references")
    if not isinstance(value["installation_id"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value["installation_id"]):
        raise DeliveryError("Invalid installation reference")
    for name, prefix in (("bot_id", "bot_"), ("space_id", "space_"), ("event_id", "event_")):
        if not isinstance(value[name], str) or not re.fullmatch(prefix + r"[a-f0-9]{32}", value[name]):
            raise DeliveryError("Invalid delivery references")
    return value


class Delivery:
    def __init__(self, data_dir, provider="henry", worker=None, runner=None):
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.data_dir / "delivery.sqlite3"
        self.provider = str(provider)
        self.worker = Path(worker or Path(__file__).with_name("delivery_worker.py")).resolve()
        self.runner = runner or subprocess.run
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS bindings (
                    space_id TEXT PRIMARY KEY, bot_id TEXT NOT NULL, url TEXT NOT NULL,
                    secret_ref TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS deliveries (
                    event_id TEXT PRIMARY KEY, space_id TEXT NOT NULL, bot_id TEXT NOT NULL,
                    operation TEXT NOT NULL, status TEXT NOT NULL, code TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 1
                );
            """)
        os.chmod(self.path, 0o600)

    @contextlib.contextmanager
    def connect(self):
        db = sqlite3.connect(str(self.path), timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def configure(self, store, space_id, url, secret_ref):
        """Call only after secure native pairing; this method accepts no key."""
        validate_url(url)
        if not isinstance(secret_ref, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", secret_ref) or ".." in secret_ref:
            raise DeliveryError("Invalid secret reference")
        space = store.detail(space_id)["space"]
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO bindings VALUES (?,?,?,?,1)",
                       (space_id, space["bot_id"], url, secret_ref))
        return {"paired": True, "space_id": space_id, "bot_id": space["bot_id"], "host": urlsplit(url).hostname}

    def unpair(self, space_id):
        with self.connect() as db:
            db.execute("DELETE FROM bindings WHERE space_id=?", (space_id,))
        return {"paired": False, "space_id": space_id}

    def status(self, event_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM deliveries WHERE event_id=?", (event_id,)).fetchone()
        return self.result(row) if row else {"delivery": "manual", "event_id": event_id, "code": "not_attempted"}

    @staticmethod
    def result(row):
        return {"delivery": row["status"], "event_id": row["event_id"], "code": row["code"],
                "attempts": row["attempts"], "event_completed": False,
                "recovery": "Check the Bot and saved event. Accepted/uncertain/in-flight requests are never automatically resent; manual delivery remains available."}

    def deliver(self, store, space_id, event_id, operation="publish", retry_failed=False):
        """Call after Store.action commits, outside its transaction; safe to race."""
        event = store.event(space_id, event_id)
        space = store.detail(space_id)["space"]
        body = validate_refs({"installation_id": store.info()["installation_id"], "bot_id": space["bot_id"],
                              "space_id": space_id, "event_id": event_id, "operation": operation})
        if event["status"] != "pending":
            return {"delivery": "not_sent", "event_id": event_id, "code": "event_finished"}
        with self.connect() as db:
            binding = db.execute("SELECT * FROM bindings WHERE space_id=? AND enabled=1", (space_id,)).fetchone()
            if binding is None:
                return {"delivery": "manual", "event_id": event_id, "code": "not_paired"}
            if binding["bot_id"] != space["bot_id"]:
                raise DeliveryError("The saved Bot binding does not match this Space")
            old = db.execute("SELECT * FROM deliveries WHERE event_id=?", (event_id,)).fetchone()
            if old:
                if old["space_id"] != space_id or old["bot_id"] != space["bot_id"] or old["operation"] != operation:
                    raise DeliveryError("Event delivery references changed")
                if not retry_failed or old["status"] != "failed":
                    return self.result(old)
                db.execute("UPDATE deliveries SET status='in_flight',code='dispatching',attempts=attempts+1 WHERE event_id=?", (event_id,))
            else:
                db.execute("INSERT INTO deliveries VALUES (?,?,?,?,?,?,1)",
                           (event_id, space_id, space["bot_id"], operation, "in_flight", "dispatching"))
            binding = dict(binding)
        argv = [self.provider, "secret", "exec", "--project", "grok-space", "--env", "production",
                "--bind", KEY_ENV + "=" + binding["secret_ref"], "--", str(self.worker),
                "--url", binding["url"], "--refs-json", json.dumps(body, separators=(",", ":"))]
        status, code = "uncertain", "provider_worker_failed"
        try:
            # Never forward provider stderr, inherited model credentials or raw
            # child output to callers. The dedicated worker is the granted target.
            env = {key: value for key, value in os.environ.items()
                   if key in {"PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "TMPDIR"}}
            result = self.runner(argv, capture_output=True, text=True, timeout=15, env=env)
            if len(result.stdout) <= 2048:
                response = json.loads(result.stdout)
                allowed = {"accepted": {"http_200"}, "failed": {"http_rejected", "invalid_request", "missing_secret"},
                           "uncertain": {"network_uncertain"}}
                if isinstance(response, dict) and response.get("status") in allowed and response.get("code") in allowed[response["status"]]:
                    status, code = response["status"], response["code"]
                    if status == "accepted" and result.returncode != 0:
                        status, code = "uncertain", "provider_worker_failed"
        except FileNotFoundError:
            status, code = "failed", "provider_unavailable"
        except (OSError, subprocess.TimeoutExpired, ValueError, TypeError):
            status, code = "uncertain", "provider_worker_failed"
        with self.connect() as db:
            db.execute("UPDATE deliveries SET status=?,code=? WHERE event_id=?", (status, code, event_id))
            row = db.execute("SELECT * FROM deliveries WHERE event_id=?", (event_id,)).fetchone()
        return self.result(row)
