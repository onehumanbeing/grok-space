"""Subprocess checks of the stdio MCP probe against real temporary SQLite files."""
import concurrent.futures
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bridge"))
from store import Store

URI = "ui://grok-spaces/probe"
MIME = "text/html;profile=mcp-app"


def request(key, method, **params):
    return dict(jsonrpc="2.0", id=key, method=method, params=params)


def initialize(key=1):
    return request(key, "initialize", protocolVersion="2025-11-25",
                   capabilities={"extensions": {"io.modelcontextprotocol/ui": {"mimeTypes": [MIME]}}},
                   clientInfo={"name": "offline-test", "version": "1"})


def call(key, name, **arguments):
    return request(key, "tools/call", name=name, arguments=arguments)


class McpProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data_dir = self.temp.name

    def tearDown(self):
        self.temp.cleanup()

    def exchange(self, *messages):
        wire = "".join((value if isinstance(value, str) else json.dumps(value)) + "\n" for value in messages)
        process = subprocess.run([sys.executable, str(ROOT / "bridge/mcp_probe.py"), "--data-dir", self.data_dir],
                                 input=wire, text=True, capture_output=True, timeout=10)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(process.stderr, "")
        output = [json.loads(line) for line in process.stdout.splitlines()]
        for response in output:
            self.assertEqual(response["jsonrpc"], "2.0")
            self.assertTrue("result" in response or "error" in response)
        return output

    def open_probe(self):
        return self.exchange(initialize(), call(2, "spaces_open_app"))[1]["result"]["structuredContent"]

    def test_three_process_roundtrip_resource_cas_and_persistence(self):
        first = self.exchange(initialize(), request(2, "tools/list"), call(3, "spaces_open_app"),
                              request(4, "resources/list"), request(5, "resources/read", uri=URI))
        self.assertEqual(first[0]["result"]["serverInfo"]["name"], "grok-spaces-mcp-probe")
        tools = {item["name"]: item for item in first[1]["result"]["tools"]}
        self.assertEqual(tools["spaces_open_app"]["_meta"]["ui"]["resourceUri"], URI)
        opened = first[2]["result"]["structuredContent"]
        self.assertTrue(opened["probe_only"])
        self.assertEqual(opened["state"], {"counter": 0})
        self.assertEqual(first[3]["result"]["resources"][0]["mimeType"], MIME)
        resource = first[4]["result"]["contents"][0]
        self.assertEqual(resource["uri"], URI)
        self.assertEqual(resource["mimeType"], MIME)
        self.assertGreater(len(resource["text"]), 500)
        self.assertEqual(resource["_meta"]["ui"]["csp"]["connectDomains"], [])
        key = opened["space_id"]

        second = self.exchange(initialize(), call(2, "spaces_get", space_id=key),
                               call(3, "spaces_patch", space_id=key, expected_revision=0, patch={"counter": 1}),
                               call(4, "spaces_patch", space_id=key, expected_revision=0, patch={"counter": 99}))
        self.assertEqual(second[1]["result"]["structuredContent"]["revision"], 0)
        self.assertEqual(second[2]["result"]["structuredContent"]["revision"], 1)
        self.assertTrue(second[3]["result"]["isError"])
        self.assertEqual(json.loads(second[3]["result"]["content"][0]["text"])["status"], 409)

        third = self.exchange(initialize(), call(2, "spaces_open_app", space_id=key), call(3, "spaces_get", space_id=key))
        reread = third[2]["result"]["structuredContent"]
        self.assertEqual(reread["space_id"], key)
        self.assertEqual(reread["state"], {"counter": 1})
        self.assertEqual(reread["revision"], 1)
        self.assertEqual(len(Store(self.data_dir).bots()), 1)

    def test_startup_does_not_seed_and_business_spaces_are_protected(self):
        self.exchange(initialize(), request(2, "tools/list"), request(3, "resources/read", uri=URI))
        store = Store(self.data_dir)
        self.assertEqual(store.bots(), [])
        self.assertEqual(store.spaces(), [])
        bot = store.create_bot("Existing real Bot")
        business = store.create_space(bot["id"], "Existing business Space")["id"]
        before = store.detail(business)
        denied = self.exchange(initialize(), call(2, "spaces_open_app", space_id=business),
                               call(3, "spaces_get", space_id=business),
                               call(4, "spaces_patch", space_id=business, expected_revision=0, patch={"counter": 8}))
        for response in denied[1:]:
            self.assertTrue(response["result"]["isError"])
            self.assertEqual(json.loads(response["result"]["content"][0]["text"])["status"], 403)
        self.assertEqual(store.detail(business), before)
        probe = self.open_probe()
        reopened = self.open_probe()
        self.assertEqual(probe["space_id"], reopened["space_id"])
        self.assertEqual(len(store.bots()), 2)

    def test_protocol_errors_and_notifications_do_not_execute_tools(self):
        notification = dict(jsonrpc="2.0", method="tools/call", params={"name": "spaces_open_app"})
        replies = self.exchange(request(1, "tools/list"), "{", initialize(2), notification,
                                dict(jsonrpc="2.0", method="notifications/initialized"),
                                request(3, "unknown/method"), call(4, "unknown_tool"),
                                request(5, "resources/read", uri="ui://missing"), call(6, "spaces_get"),
                                dict(jsonrpc="2.0", id=True, method="ping"), "[]", "NaN")
        self.assertEqual(len(replies), 10)
        self.assertEqual([value["error"]["code"] for value in replies if "error" in value],
                         [-32002, -32700, -32601, -32602, -32002, -32602, -32600, -32600, -32700])
        self.assertEqual(Store(self.data_dir).spaces(), [])

    def test_patch_requires_counter_only_and_integer_revision(self):
        probe = self.open_probe()
        key = probe["space_id"]
        messages = [initialize()]
        for index, arguments in enumerate((
                {"patch": {"counter": True}, "expected_revision": 0},
                {"patch": {"counter": 1}, "expected_revision": True},
                {"patch": {"note": "unrelated"}, "expected_revision": 0},
                {"patch": {"counter": -1}, "expected_revision": 0},
                {"patch": {"counter": 9007199254740992}, "expected_revision": 0}), start=2):
            messages.append(call(index, "spaces_patch", space_id=key, **arguments))
        for response in self.exchange(*messages)[1:]:
            self.assertEqual(response["error"]["code"], -32602)
        self.assertEqual(Store(self.data_dir).detail(key)["revision"], 0)

    def test_two_hosts_cannot_overwrite_the_same_revision(self):
        key = self.open_probe()["space_id"]
        def update(counter):
            return self.exchange(initialize(), call(2, "spaces_patch", space_id=key, expected_revision=0, patch={"counter": counter}))[1]["result"]
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            replies = list(pool.map(update, (10, 20)))
        self.assertEqual(sum(bool(value.get("isError")) for value in replies), 1)
        committed = Store(self.data_dir).detail(key)
        self.assertEqual(committed["revision"], 1)
        self.assertIn(committed["state"]["counter"], (10, 20))


if __name__ == "__main__":
    unittest.main()
