"""Offline delivery acceptance, isolation and duplicate-wake protection."""
import concurrent.futures
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bridge"))
from delivery import Delivery, DeliveryError, KEY_ENV
from delivery_worker import post_event
from store import Store, SpaceError


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "data")
        self.bot = self.store.create_bot("Disposable test association")
        self.space = self.store.create_space(self.bot["id"], "Delivery fixture")
        self.event = self.store.action(self.space["id"], "publish.request", {"private_data": "must stay local"},
                                       "request-1", expected_revision=0)
        self.requests = []
        self.http_status = 200
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                fixture.requests.append({"path": self.path, "authorization": self.headers.get("Authorization"),
                                         "body": json.loads(self.rfile.read(int(self.headers["Content-Length"])))})
                self.send_response(fixture.http_status)
                if fixture.http_status == 302:
                    self.send_header("Location", "/automations/webhook/redirect-target")
                self.end_headers()
                self.wfile.write(b"provider body must not reach caller")

            def log_message(self, *args):
                pass

        self.http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.local_url = "http://127.0.0.1:{}/automations/webhook/test-routine".format(self.http.server_port)
        self.calls = []

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def provider(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        self.assertEqual(argv[:3], ["henry", "secret", "exec"])
        self.assertEqual(argv[argv.index("--") + 1], str(ROOT / "bridge" / "delivery_worker.py"))
        self.assertNotIn(KEY_ENV, kwargs["env"])
        self.assertNotIn("HTTPS_PROXY", kwargs["env"])
        refs = json.loads(argv[argv.index("--refs-json") + 1])
        result = post_event(self.local_url, "fake-test-key", refs, _allow_loopback=True)
        return subprocess.CompletedProcess(argv, 0 if result["status"] == "accepted" else 2,
                                           json.dumps(result), "")

    def paired(self, runner=None):
        adapter = Delivery(self.store.data_dir, runner=runner or self.provider)
        adapter.configure(self.store, self.space["id"],
                          "https://api2.cursor.sh/automations/webhook/test-routine", "space-webhook")
        return adapter

    def deliver(self, adapter, **kwargs):
        return adapter.deliver(self.store, self.space["id"], self.event["id"], **kwargs)

    def test_unpaired_is_manual_without_provider(self):
        adapter = Delivery(self.store.data_dir, runner=lambda *a, **k: self.fail("No provider without pairing"))
        self.assertEqual(self.deliver(adapter)["delivery"], "manual")
        self.assertEqual(self.store.event(self.space["id"], self.event["id"])["status"], "pending")

    def test_http_200_is_pending_and_durable_duplicate_never_wakes_again(self):
        adapter = self.paired()
        with patch.dict(os.environ, {"HTTPS_PROXY": "http://127.0.0.1:1", KEY_ENV: "must-not-inherit"}):
            result = self.deliver(adapter)
        self.assertEqual(result["delivery"], "accepted")
        self.assertFalse(result["event_completed"])
        self.assertEqual(self.store.event(self.space["id"], self.event["id"])["status"], "pending")
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.requests[0]["authorization"], "Bearer fake-test-key")
        self.assertEqual(set(self.requests[0]["body"]), {"installation_id", "bot_id", "space_id", "event_id", "operation"})
        self.assertNotIn("private_data", json.dumps(self.requests[0]["body"]))
        reopened = Delivery(self.store.data_dir, runner=lambda *a, **k: self.fail("Duplicate wake"))
        self.assertEqual(self.deliver(reopened, retry_failed=True), result)
        self.assertEqual(reopened.status(self.event["id"]), result)

    def test_concurrent_dispatch_claims_event_once(self):
        first, second = self.paired(), self.paired()
        barrier = threading.Barrier(2)
        def dispatch(adapter):
            barrier.wait(timeout=2)
            return self.deliver(adapter)["delivery"]
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(dispatch, (first, second)))
        self.assertIn("accepted", outcomes)
        self.assertTrue(set(outcomes) <= {"accepted", "in_flight"})
        self.assertEqual(len(self.requests), 1)

    def test_uncertain_provider_output_is_redacted_and_not_retried(self):
        def failed(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 1, "fake-secret-value", "fake-secret-value")
        adapter = self.paired(failed)
        result = self.deliver(adapter)
        self.assertEqual(result["delivery"], "uncertain")
        self.assertNotIn("fake-secret", json.dumps(result))
        adapter.runner = lambda *a, **k: self.fail("Ambiguous attempts must not repeat")
        self.assertEqual(self.deliver(adapter, retry_failed=True), result)
        self.assertEqual(self.requests, [])

    def test_explicit_retry_only_after_known_rejection(self):
        adapter = self.paired()
        self.http_status = 401
        self.assertEqual(self.deliver(adapter)["delivery"], "failed")
        self.http_status = 200
        self.assertEqual(self.deliver(adapter)["delivery"], "failed")
        self.assertEqual(len(self.requests), 1)
        result = self.deliver(adapter, retry_failed=True)
        self.assertEqual((result["delivery"], result["attempts"]), ("accepted", 2))
        self.assertEqual(len(self.requests), 2)

    def test_server_error_is_ambiguous_and_cannot_repeat(self):
        adapter = self.paired()
        self.http_status = 500
        self.assertEqual(self.deliver(adapter)["delivery"], "uncertain")
        self.http_status = 200
        self.assertEqual(self.deliver(adapter, retry_failed=True)["delivery"], "uncertain")
        self.assertEqual(len(self.requests), 1)

    def test_redirect_is_not_followed_and_cross_space_event_is_rejected(self):
        self.http_status = 302
        adapter = self.paired()
        self.assertEqual(self.deliver(adapter)["delivery"], "failed")
        self.assertEqual(len(self.requests), 1)
        other = self.store.create_space(self.bot["id"], "Other Space")
        with self.assertRaises(SpaceError):
            adapter.deliver(self.store, other["id"], self.event["id"])
        with self.assertRaises(DeliveryError):
            adapter.configure(self.store, self.space["id"], "https://attacker.example/automations/webhook/test", "ref")

    def test_grant_target_is_executable_and_worker_errors_are_safe_json(self):
        worker = ROOT / "bridge" / "delivery_worker.py"
        refs = {"installation_id": self.store.info()["installation_id"], "bot_id": self.bot["id"],
                "space_id": self.space["id"], "event_id": self.event["id"], "operation": "publish"}
        env = {"PATH": "/usr/bin:/bin", KEY_ENV: "fake-secret-not-to-print"}
        child = subprocess.run([str(worker), "--url", "https://unapproved.example/test", "--refs-json", json.dumps(refs)],
                               env=env, capture_output=True, text=True, timeout=5)
        self.assertEqual(child.returncode, 2)
        self.assertEqual(child.stderr, "")
        self.assertEqual(json.loads(child.stdout), {"status": "failed", "code": "invalid_request"})
        self.assertNotIn("fake-secret", child.stdout)


if __name__ == "__main__":
    unittest.main()
