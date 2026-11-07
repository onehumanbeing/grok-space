"""Integration checks for persistence, independent Bot writes and unsafe requests."""
import concurrent.futures
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bridge"))
from server import create_server
from store import SpaceError, Store


class RoundTripTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.server = create_server(0, self.temp.name, seed=False)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.origin = "http://127.0.0.1:{}".format(self.server.server_port)
        self.bot = self.api("POST", "/api/bots", {"name": "Test Bot"})[1]["bot"]
        self.space = self.api("POST", "/api/spaces", {"bot_id": self.bot["id"], "title": "Round trip"})[1]["space"]
        self.path = "/api/spaces/" + self.space["id"]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def api(self, method, path, payload=None, headers=None):
        body = json.dumps(payload).encode() if payload is not None else None
        values = {"Content-Type": "application/json"}
        values.update(headers or {})
        request = Request(self.origin + path, data=body, headers=values, method=method)
        try:
            with urlopen(request, timeout=3) as response:
                return response.status, json.load(response)
        except HTTPError as exc:
            return exc.code, json.load(exc)

    def cli(self, *arguments):
        result = subprocess.run([sys.executable, str(ROOT / "bridge/cli.py"), "--data-dir", self.temp.name, *arguments], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_ui_action_bot_cli_reply_and_reopen(self):
        status, saved = self.api("PATCH", self.path + "/state", {"patch": {"items": [{"id": "1", "text": "Persist this", "done": False}]}, "expected_revision": 0})
        self.assertEqual(status, 200)
        _, action = self.api("POST", self.path + "/actions", {"type": "review", "payload": {"item": "1"}, "request_id": "once", "expected_revision": 1})
        self.assertEqual(action["delivery"], "manual")
        self.assertEqual(action["event"]["status"], "pending")
        remote = self.cli("get", "--space", self.space["id"])
        self.assertEqual(remote["revision"], saved["revision"])
        reply = Path(self.temp.name) / "reply.json"
        reply.write_text(json.dumps({"result": {"summary": "Read and completed through CLI"}, "patch": {"items": [{"id": "1", "text": "Persist this", "done": True}]}}))
        arguments = ["event", "complete", "--space", self.space["id"], "--event", action["event"]["id"], "--expected-revision", "1", "--file", str(reply)]
        first = self.cli(*arguments)
        second = self.cli(*arguments)
        self.assertFalse(first["replayed"])
        self.assertTrue(second["replayed"])
        _, reread = self.api("GET", self.path)
        self.assertTrue(reread["state"]["items"][0]["done"])
        self.assertEqual(reread["revision"], 2)
        self.assertEqual(reread["events"][-1]["status"], "completed")
        reopened = Store(self.temp.name).detail(self.space["id"])
        self.assertEqual(reopened, reread)

    def test_racing_edits_have_one_winner_and_no_lost_update(self):
        def edit(label):
            return self.api("PATCH", self.path + "/state", {"patch": {"note": label}, "expected_revision": 0})
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(edit, ("user edit", "bot edit")))
        self.assertEqual(sorted(status for status, _ in responses), [200, 409])
        _, detail = self.api("GET", self.path)
        winner = next(value["state"]["note"] for status, value in responses if status == 200)
        self.assertEqual(detail["state"]["note"], winner)
        self.assertEqual(detail["revision"], 1)

    def test_bad_completion_does_not_finish_event_or_modify_state(self):
        _, action = self.api("POST", self.path + "/actions", {"type": "review", "request_id": "once", "expected_revision": 0})
        store = Store(self.temp.name)
        store.patch_state(self.space["id"], {"note": "newer user edit"}, 0)
        with self.assertRaises(SpaceError) as caught:
            store.complete(self.space["id"], action["event"]["id"], 0, {"summary": "stale"}, {"note": "overwrite"})
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(store.event(self.space["id"], action["event"]["id"])["status"], "pending")
        self.assertEqual(store.detail(self.space["id"])["state"]["note"], "newer user edit")

    def test_cross_origin_rebinding_and_unsafe_content_types_are_rejected(self):
        for headers in ({"Origin": "https://untrusted.example"}, {"Origin": "null"}, {"Host": "untrusted.example"}, {"Content-Type": "text/plain"}):
            status, _ = self.api("PATCH", self.path + "/state", {"patch": {"note": "unsafe"}, "expected_revision": 0}, headers)
            self.assertIn(status, (403, 415))
        self.assertEqual(self.api("GET", self.path)[1]["revision"], 0)

    def test_actions_are_idempotent_and_explicitly_scoped(self):
        request = {"type": "review", "payload": {"value": 1}, "request_id": "once", "expected_revision": 0}
        first = self.api("POST", self.path + "/actions", request)[1]
        second = self.api("POST", self.path + "/actions", request)[1]
        self.assertEqual(first["event"]["id"], second["event"]["id"])
        request["payload"] = {"value": 2}
        self.assertEqual(self.api("POST", self.path + "/actions", request)[0], 409)
        other = Store(self.temp.name).create_space(self.bot["id"], "Other")
        with self.assertRaises(SpaceError):
            Store(self.temp.name).complete(other["id"], first["event"]["id"], 0, {})
        self.assertEqual(Store(self.temp.name).event(self.space["id"], first["event"]["id"])["status"], "pending")

    def test_conditional_sync_observes_result_only_completion_without_resending_html(self):
        # The decoded file is within its limit although its JSON wire form is
        # larger than the former 1.1 MiB request cap.
        html = '<!doctype html><p>' + '"' * 600000 + '</p>'
        self.assertEqual(self.api("PUT", self.path + "/view", {"html": html, "expected_revision": 0})[0], 200)
        patch = {"dataset": "x" * 8000}
        self.assertEqual(self.api("PATCH", self.path + "/state", {"patch": patch, "expected_revision": 1})[0], 200)
        _, action = self.api("POST", self.path + "/actions", {"type": "refresh", "payload": {}, "request_id": "conditional", "expected_revision": 2})
        _, detail = self.api("GET", self.path)
        audit = next(event for event in detail["events"] if event["type"] == "state.updated")
        self.assertNotIn("patch", audit["payload"])
        self.assertIn("patch_summary", audit["payload"])
        full_audit = self.api("GET", self.path + "/events/" + audit["id"])[1]["event"]
        self.assertEqual(full_audit["payload"]["patch"], patch)
        query = "?known_revision={}&known_seq={}".format(detail["revision"], detail["event_cursor"])
        _, unchanged = self.api("GET", self.path + query)
        self.assertTrue(unchanged["unchanged"])
        self.assertNotIn("view_html", unchanged)
        self.assertLess(len(json.dumps(unchanged)), 100)
        Store(self.temp.name).complete(self.space["id"], action["event"]["id"], 2, {"done": True})
        _, changed = self.api("GET", self.path + query)
        self.assertEqual(changed["revision"], 2)
        self.assertGreater(changed["event_cursor"], detail["event_cursor"])
        self.assertEqual(changed["pending_count"], 0)
        self.assertEqual(changed["view_html"], html)
        oversized = {"type": "refresh", "payload": {"large": "x" * 65536}, "request_id": "oversized", "expected_revision": 2}
        self.assertEqual(self.api("POST", self.path + "/actions", oversized)[0], 413)
        self.assertEqual(self.api("GET", self.path)[1], changed)


if __name__ == "__main__":
    unittest.main()
