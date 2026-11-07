"""Generic authoring and event acceptance through independent CLI processes.

These tests exercise disposable data only. They start no HTTP service, browser,
Grok process, or account connection; rendering inside a real host remains separate.
"""
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "bridge" / "cli.py"


class FoundationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp.name) / "data"
        self.fixture_dir = Path(self.temp.name) / "inputs"
        self.fixture_dir.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def cli(self, *args, status=None):
        child = subprocess.run([sys.executable, str(CLI), "--data-dir", str(self.data_dir), *map(str, args)],
                               capture_output=True, text=True, timeout=10)
        if status is None:
            self.assertEqual(child.returncode, 0, child.stderr)
            self.assertEqual(child.stderr, "")
            return json.loads(child.stdout)
        self.assertEqual(child.returncode, 2, child.stderr)
        self.assertEqual(child.stdout, "")
        error = json.loads(child.stderr)
        self.assertEqual(error["status"], status)
        return error

    def json_file(self, name, value):
        path = self.fixture_dir / name
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return path

    def create(self, title="Authored page"):
        bot = self.cli("add-bot", "--name", "Existing Bot association", "--external-id", "test-reference")["bot"]
        space = self.cli("create", "--bot", bot["id"], "--title", title)["space"]
        return bot, space

    def test_cli_authored_page_reopens_and_publish_is_atomic(self):
        self.assertEqual(self.cli("bots"), {"bots": []})
        contract = self.cli("contract")
        installation = contract["installation"]["installation_id"]
        self.assertEqual(self.cli("spaces"), {"spaces": []})
        bot, space = self.create()
        key = space["id"]
        initial = self.cli("get", "--space", key)
        self.assertEqual(initial["state"], {})
        self.assertEqual(initial["view_html"], "")
        self.assertEqual(initial["revision"], 0)
        self.assertEqual(self.cli("spaces", "--bot", bot["id"])["spaces"][0]["id"], key)

        project = Path(self.cli("project", "--space", key)["project_dir"])
        self.assertTrue(project.is_dir())
        self.assertEqual(project, self.data_dir.resolve() / "projects" / key)
        self.assertEqual(self.cli("contract", "--space", key)["project_dir"], str(project))
        source = project / "index.html"
        html = '<!doctype html><meta charset="utf-8"><title>Authored fixture</title><p>Saved: 中文 &amp; JSON</p><script>globalThis.authoredFixture=true;</script>'
        source.write_text(html, encoding="utf-8")
        state = {"record": {"enabled": False, "count": 3}, "values": [1, None, "text"], "label": "中文"}
        patch = self.json_file("initial.json", state)
        published = self.cli("publish", "--space", key, "--expected-revision", 0,
                             "--html-file", source, "--patch-file", patch)
        self.assertEqual(published["revision"], 1)

        # Each command is a new process. The published page survives edits to its
        # source file: the committed HTML snapshot is the render artifact.
        source.write_text("Different uncommitted source", encoding="utf-8")
        reopened = self.cli("get", "--space", key)
        self.assertEqual(reopened["state"], state)
        self.assertEqual(reopened["view_html"], html)
        self.assertEqual(reopened["revision"], 1)
        self.assertEqual(self.cli("doctor")["installation_id"], installation)
        publication = next(event for event in reopened["events"] if event["type"] == "space.published")
        self.assertEqual(publication["payload"]["sha256"], hashlib.sha256(html.encode()).hexdigest())

        replacement = self.json_file("replacement.json", {"record": {"count": 999}, "values": []})
        self.cli("publish", "--space", key, "--expected-revision", 0,
                 "--html-file", source, "--patch-file", replacement, status=409)
        self.assertEqual(self.cli("get", "--space", key), reopened)

        nested = {"value": 1}
        for _ in range(65):
            nested = {"nested": nested}
        deep = self.json_file("too-deep.json", nested)
        self.cli("publish", "--space", key, "--expected-revision", 1,
                 "--html-file", source, "--patch-file", deep, status=400)
        self.assertEqual(self.cli("get", "--space", key), reopened)

        # The input itself fits the CLI limit, but merging it into the existing
        # state exceeds the persisted-state limit inside the publication.
        large = self.json_file("too-large.json", {"large": "x" * (1024 * 1024 - 40)})
        self.assertLessEqual(len(large.read_bytes()), 1024 * 1024)
        self.cli("publish", "--space", key, "--expected-revision", 1,
                 "--html-file", source, "--patch-file", large, status=413)
        self.assertEqual(self.cli("get", "--space", key), reopened)

    def test_action_cas_replay_and_result_only_completion_are_observable_after_cursor(self):
        _, space = self.create()
        key = space["id"]
        payload = self.json_file("action.json", {"input": {"value": 1}, "literal": "$(must stay data)"})
        arguments = ("action", "--space", key, "--type", "custom.request", "--request-id", "stable-1",
                     "--expected-revision", 0, "--file", payload)
        action = self.cli(*arguments)["event"]
        changed = self.json_file("change.json", {"record": {"newer": True}})
        self.cli("patch", "--space", key, "--expected-revision", 0, "--file", changed)
        # An exact replay returns the original action despite the caller's old
        # revision. A new action with that stale revision must fail.
        self.assertEqual(self.cli(*arguments)["event"]["id"], action["id"])
        self.cli("action", "--space", key, "--type", "custom.request", "--request-id", "different",
                 "--expected-revision", 0, "--file", payload, status=409)
        other_payload = self.json_file("other-action.json", {"input": {"value": 2}})
        self.cli("action", "--space", key, "--type", "custom.request", "--request-id", "stable-1",
                 "--expected-revision", 1, "--file", other_payload, status=409)

        before = self.cli("get", "--space", key)
        cursor = max(event["seq"] for event in before["events"])
        arbitrary = {"values": [None, False, 0, 1.25, "text"], "nested": {"passed": True}}
        result = self.json_file("result.json", {"result": arbitrary})
        finish = ("event", "complete", "--space", key, "--event", action["id"],
                  "--expected-revision", 1, "--file", result)
        completed = self.cli(*finish)
        self.assertFalse(completed["replayed"])
        self.assertTrue(self.cli(*finish)["replayed"])
        authoritative = self.cli("event", "get", "--space", key, "--event", action["id"])["event"]
        self.assertEqual(authoritative["status"], "completed")
        self.assertEqual(authoritative["result"], arbitrary)
        after = self.cli("events", "--space", key, "--after", cursor)["events"]
        self.assertEqual(len(after), 1)
        self.assertEqual(after[0]["type"], "action.completed")
        self.assertEqual(after[0]["payload"]["event_id"], action["id"])
        self.assertEqual(after[0]["payload"]["result"], arbitrary)
        reread = self.cli("get", "--space", key)
        self.assertEqual(reread["state"], before["state"])
        self.assertEqual(reread["revision"], before["revision"])
        self.assertEqual(reread["pending_count"], 0)

    def test_scalar_results_and_failed_actions_preserve_json_types(self):
        _, space = self.create()
        key = space["id"]
        payload = self.json_file("payload.json", {})
        for index, value in enumerate((None, False, 0, "", [False, None])):
            with self.subTest(result=value):
                event = self.cli("action", "--space", key, "--type", "custom.request", "--request-id", str(index),
                                 "--expected-revision", 0, "--file", payload)["event"]
                result = self.json_file("result-{}.json".format(index), {"result": value})
                operation = "fail" if index == 1 else "complete"
                self.cli("event", operation, "--space", key, "--event", event["id"],
                         "--expected-revision", 0, "--file", result)
                reread = self.cli("event", "get", "--space", key, "--event", event["id"])["event"]
                self.assertEqual(reread["result"], value)
                self.assertIs(type(reread["result"]), type(value))
                self.assertEqual(reread["status"], "failed" if operation == "fail" else "completed")
        self.assertEqual(self.cli("get", "--space", key)["revision"], 0)

    def test_pending_work_survives_history_window_and_event_scope_is_enforced(self):
        bot, space = self.create()
        key = space["id"]
        payload = self.json_file("pending.json", {"input": "retain this action"})
        pending = self.cli("action", "--space", key, "--type", "custom.request", "--request-id", "long-lived",
                           "--expected-revision", 0, "--file", payload)["event"]
        for revision in range(55):
            patch = self.json_file("tick.json", {"tick": revision + 1})
            self.cli("patch", "--space", key, "--expected-revision", revision, "--file", patch)
        detail = self.cli("get", "--space", key)
        self.assertEqual(detail["pending_count"], 1)
        self.assertEqual(len(detail["events"]), 50)
        self.assertNotIn(pending["id"], [event["id"] for event in detail["events"]])
        listed = self.cli("events", "--space", key, "--status", "pending")["events"]
        self.assertEqual([event["id"] for event in listed], [pending["id"]])

        other = self.cli("create", "--bot", bot["id"], "--title", "Other Space")["space"]["id"]
        result = self.json_file("wrong-space-result.json", {"result": {"status": "must not commit"}, "patch": {"wrong": True}})
        self.cli("event", "get", "--space", other, "--event", pending["id"], status=404)
        self.cli("event", "complete", "--space", other, "--event", pending["id"],
                 "--expected-revision", 0, "--file", result, status=404)
        self.assertEqual(self.cli("get", "--space", other)["state"], {})
        self.assertEqual(self.cli("get", "--space", key)["state"], detail["state"])
        self.assertEqual(self.cli("event", "get", "--space", key, "--event", pending["id"])["event"]["status"], "pending")


if __name__ == "__main__":
    unittest.main()
