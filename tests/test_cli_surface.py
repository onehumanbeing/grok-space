"""Subprocess checks for the small AI-facing interface and migration boundary."""
import concurrent.futures
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bridge"))
from store import Store
from prompts import action_prompt, setup_prompt
import cli as cli_module


class CliSurfaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="grok-space-cli-")
        self.folder = Path(self.temp.name)
        self.data = self.folder / "isolated data"

    def tearDown(self):
        self.temp.cleanup()

    def cli(self, *args, status=None, launcher=None, max_stdout=None):
        command = [str(launcher)] if launcher else [sys.executable, str(ROOT / "bridge/cli.py")]
        result = subprocess.run(command + ["--data-dir", str(self.data), *map(str, args)],
                                text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0 if status is None else 2, result.stderr)
        self.assertEqual(result.stderr if status is None else result.stdout, "")
        if max_stdout is not None:
            self.assertLessEqual(len(result.stdout.encode("utf-8")), max_stdout)
        value = json.loads(result.stdout if status is None else result.stderr)
        if status is not None:
            self.assertEqual(value["status"], status)
            self.assertTrue(value["code"])
            self.assertTrue(value["recovery"])
        return value

    def opened(self):
        return self.cli("open", "--bot", "Existing Bot", "--create-bot", "--title", "Authored Space")

    def json_file(self, name, value):
        path = self.folder / name
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return path

    def test_five_command_json_help_and_installation_verification(self):
        help_result = self.cli("--help")
        self.assertEqual(set(help_result["commands"]), {"doctor", "open", "read", "publish", "action"})
        self.assertFalse(self.data.exists(), "help must not initialize a database")
        self.assertEqual(set(self.cli("action", "--help")["commands"]), {"create", "get", "status", "complete", "fail", "worker"})
        self.assertEqual(self.cli("read"), {"bots": [], "spaces": []})
        doctor = self.cli("doctor")
        self.assertEqual(set(doctor["interface"]["commands"]), set(help_result["commands"]))
        self.assertFalse(doctor["capabilities"]["actions"]["automatic_bot_wake"])
        self.assertEqual(doctor["verification"]["grok_bot_round_trip"], "unverified")
        verified = self.cli("doctor", "--expect-installation", doctor["installation_id"])
        self.assertEqual(verified["verification"]["expected_installation"], "matched")
        wrong = self.cli("doctor", "--expect-installation", "another-installation", status=409)
        self.assertEqual(wrong["code"], "installation_mismatch")

    def test_compact_doctor_matches_only_its_local_service(self):
        from server import create_server
        opened = self.opened()
        server = create_server(0, self.data)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with patch.dict(cli_module.os.environ, {"GROK_SPACES_PORT": str(server.server_port)}):
                compact = self.cli("doctor", "--summary", max_stdout=4000)
                self.assertNotIn("interface", compact)
                self.assertEqual(compact["health"]["service"]["status"], "running")
                self.assertEqual(compact["health"]["space_count"], 1)
                self.assertEqual(compact["health"]["bots"][0]["id"], opened["refs"]["bot_id"])
                self.assertIn(compact["version"], compact["summary"])
                self.assertIn("Existing Bot", compact["summary"])
                with patch("server.VERSION", "0.0.0-test"):
                    mismatch = self.cli("doctor", "--summary")
                    self.assertEqual(mismatch["health"]["status"], "attention")
                    self.assertFalse(mismatch["health"]["service"]["version_matches_cli"])
                other = cli_module.health_summary(Store(self.folder / "other database"), probe=True)
                self.assertEqual(other["status"], "attention")
                self.assertEqual(other["service"]["status"], "different_or_unverified_installation")
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=3)

    def test_open_is_explicit_reuses_stable_ids_and_never_guesses(self):
        self.cli("open", "--bot", "Existing Bot", "--title", "Authored Space", status=404)
        self.assertEqual(self.cli("read"), {"bots": [], "spaces": []})
        opened = self.opened()
        self.assertTrue(opened["created"])
        self.assertTrue(opened["bot_created"])
        self.assertFalse(opened["window_opened"])
        self.assertFalse({"state", "view_html", "events"} & opened.keys())
        self.assertEqual(self.cli("read", "--space", opened["refs"]["space_id"])["state"], {})
        self.assertTrue(Path(opened["project_dir"]).is_dir())
        again = self.cli("open", "--bot", "Existing Bot", "--title", "Authored Space")
        self.assertEqual(again["refs"], opened["refs"])
        self.assertFalse(again["created"])
        self.assertFalse(again["bot_created"])
        bot_id, key = opened["refs"]["bot_id"], opened["refs"]["space_id"]
        self.assertEqual(self.cli("open", "--bot", bot_id, "--space", key)["refs"], opened["refs"])
        store = Store(self.data)
        duplicate = store.create_bot("Existing Bot")
        ambiguous = self.cli("open", "--bot", "Existing Bot", "--title", "Authored Space", status=409)
        self.assertEqual({bot["id"] for bot in ambiguous["candidates"]}, {bot_id, duplicate["id"]})
        self.cli("open", "--bot", duplicate["id"], "--space", key, status=409)
        self.cli("read", "--bot", duplicate["id"], "--space", key, status=409)
        store.create_space(bot_id, "Authored Space")
        ambiguous = self.cli("open", "--bot", bot_id, "--title", "Authored Space", status=409)
        self.assertEqual(len(ambiguous["candidates"]), 2)
        self.assertEqual(self.cli("open", "--bot", bot_id, "--space", key)["refs"], opened["refs"])
        # An isolated test database must never be accidentally shown using the
        # companion's default database, and rejection must precede creation.
        before = self.cli("read")
        error = self.cli("open", "--bot", "Do not create", "--create-bot", "--title", "Hidden", "--show", status=400)
        self.assertEqual(error["code"], "custom_data_dir_show_unsupported")
        self.assertEqual(self.cli("read"), before)

    def test_concurrent_open_resolves_one_bot_and_one_space(self):
        # Initialize SQLite once; the concurrent operations are the explicit
        # Bot/title resolution, not unrelated schema startup contention.
        self.cli("doctor")
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(lambda _: self.opened(), range(3)))
        self.assertEqual(len({result["refs"]["space_id"] for result in results}), 1)
        self.assertEqual(len({result["refs"]["bot_id"] for result in results}), 1)
        self.assertEqual(sum(result["created"] for result in results), 1)
        self.assertEqual(sum(result["bot_created"] for result in results), 1)

    def test_registered_worker_cli_result_and_explicit_output(self):
        opened = self.opened()
        key = opened["refs"]["space_id"]
        handler = Path(opened["project_dir"]) / "handler.py"
        handler.write_text('import json,sys\nm=json.load(sys.stdin)\nprint(json.dumps({"result":{"event":m["event"]["id"]}}))\n')
        registered = self.cli("action", "worker", "register", "--space", key,
                              "--file", handler, "--type", "count")
        self.assertTrue(registered["registered"])
        self.assertEqual(registered["runs"], [])
        source = self.json_file("action.json", {})
        event = self.cli("action", "create", "--space", key, "--type", "count",
                         "--request-id", "cli-count", "--expected-revision", "0", "--file", source)["event"]
        self.cli("action", "worker", "run", "--space", key, "--event", event["id"])
        status = self.cli("action", "worker", "status", "--space", key, "--event", event["id"])
        self.assertEqual(status["status"], "completed")
        self.assertNotIn("output", status)
        saved = self.cli("action", "worker", "status", "--space", key, "--event", event["id"], "--output")
        self.assertEqual(saved["output"]["result"], {"event": event["id"]})
        self.cli("action", "worker", "status", "--space", key, "--output", status=400)

    def test_publish_state_only_and_action_result_round_trip(self):
        opened = self.opened()
        key = opened["refs"]["space_id"]
        html = self.folder / "page.html"
        html.write_text("<!doctype html><p>Authored by the Bot</p>", encoding="utf-8")
        patch = self.json_file("initial.json", {"record": {"version": 1}})
        published = self.cli("publish", "--space", key, "--expected-revision", "0", "--html-file", html, "--patch-file", patch)
        self.assertEqual(set(published), {"space", "revision", "refs", "pending_count"})
        patch = self.json_file("next.json", {"record": {"version": 2}})
        updated = self.cli("publish", "--space", key, "--expected-revision", "1", "--patch-file", patch)
        self.assertEqual(updated["revision"], 2)
        saved = self.cli("get", "--space", key)
        self.assertEqual(saved["view_html"], html.read_text(encoding="utf-8"))
        self.assertEqual(saved["state"], {"record": {"version": 2}})
        self.assertEqual(set(self.cli("read", "--space", key)),
                         {"state", "revision", "space", "refs", "project_dir", "pending_count"})
        summary = self.cli("read", "--space", key, "--summary")
        self.assertNotIn("state", summary)
        self.assertEqual(summary["state_summary"], {"record": {"type": "object", "count": 1}})
        self.assertEqual(self.cli("read", "--space", key, "--field", "record")["state"], {"record": {"version": 2}})
        self.cli("read", "--space", key, "--field", "missing", status=404)
        self.cli("read", "--space", key, "--summary", "--html", status=400)
        stale = self.cli("publish", "--space", key, "--expected-revision", "1", "--patch-file", patch, status=409)
        self.assertEqual(stale["revision"], 2)
        self.assertEqual(stale["current_revision"], 2)
        self.assertEqual(stale["expected_revision"], 1)
        self.assertEqual(stale["code"], "revision_conflict")
        self.assertEqual(self.cli("get", "--space", key), saved)
        action_input = self.json_file("input.json", {"input": "literal $(not executed)"})
        create = ("action", "create", "--space", key, "--type", "custom.request", "--request-id", "stable", "--expected-revision", "2", "--file", action_input)
        event = self.cli(*create)["event"]
        self.assertEqual(self.cli("action", "status", "--space", key)["pending_count"], 1)
        self.assertEqual(self.cli("action", "get", "--space", key, "--event", event["id"])["event"], event)
        result_file = self.json_file("result.json", {"result": [False, 0, None]})
        complete = ("action", "complete", "--space", key, "--event", event["id"], "--expected-revision", "2", "--file", result_file)
        done = self.cli(*complete)
        self.assertFalse(done["replayed"])
        self.assertTrue(self.cli(*complete)["replayed"])
        self.assertEqual(self.cli(*create)["event"]["id"], event["id"])
        status = self.cli("action", "status", "--space", key, "--event", event["id"])["event"]
        self.assertEqual(status["result"], [False, 0, None])
        self.assertEqual(status["status"], "completed")
        self.assertEqual(self.cli("action", "status", "--space", key)["pending_count"], 0)
        audit = self.cli("read", "--space", key, "--events", "--after", event["seq"])["events"]
        self.assertEqual(audit[0]["payload"]["event_id"], event["id"])
        self.assertEqual(self.cli("read", "--space", key)["revision"], 2)
        self.assertIn("action complete", action_prompt(Store(self.data), key, event))
        prompt = setup_prompt(Store(self.data), key)
        self.assertIn("doctor --space", prompt)
        self.assertNotIn(" contract ", prompt)
        self.assertNotIn(" event complete ", prompt)

    def test_json_errors_do_not_echo_input_and_legacy_commands_still_work(self):
        raw = "argument-that-must-not-be-echoed"
        error = self.cli("read", "--unknown", raw, status=400)
        self.assertNotIn(raw, json.dumps(error))
        self.assertEqual(error["code"], "invalid_arguments")
        self.cli("read", "--events", status=400)
        self.cli("read", "--html", status=400)
        self.cli("read", "--status", "pending", status=400)
        opened = self.opened()
        key = opened["refs"]["space_id"]
        self.cli("read", "--space", key, "--html", "--events", status=400)
        self.cli("publish", "--space", key, "--expected-revision", "0", status=400)
        event = self.cli("action", "--space", key, "--type", "legacy", "--request-id", "legacy", "--expected-revision", "0", "--json", "{}")["event"]
        self.cli("event", "fail", "--space", key, "--event", event["id"], "--expected-revision", "0", "--json", '{"result":false}')
        self.assertIs(self.cli("event", "get", "--space", key, "--event", event["id"])["event"]["result"], False)
        self.assertEqual(len(self.cli("events", "--space", key)["events"]), 2)
        self.assertEqual(len(self.cli("bots")["bots"]), 1)
        self.assertEqual(len(self.cli("spaces")["spaces"]), 1)

    def test_large_publication_does_not_echo_html_state_or_history(self):
        opened = self.opened()
        key = opened["refs"]["space_id"]
        html = self.folder / "large.html"
        source = "<!doctype html><p>" + "saved HTML only " * 40000 + "</p>"
        html.write_text(source, encoding="utf-8")
        state = {"durable": "saved state only " * 20000}
        patch_file = self.json_file("large-state.json", state)
        acknowledgement = self.cli("publish", "--space", key, "--expected-revision", "0",
                                   "--html-file", html, "--patch-file", patch_file, max_stdout=2048)
        self.assertEqual(set(acknowledgement), {"space", "revision", "refs", "pending_count"})
        reopened = self.cli("open", "--bot", opened["refs"]["bot_id"], "--space", key, max_stdout=2048)
        self.assertFalse({"state", "view_html", "events"} & reopened.keys())
        snapshot = self.cli("read", "--space", key, max_stdout=len(json.dumps(state).encode()) + 2048)
        self.assertEqual(snapshot["state"], state)
        self.assertNotIn("view_html", snapshot)
        self.assertNotIn("events", snapshot)
        with_html = self.cli("read", "--space", key, "--html")
        self.assertEqual(with_html["view_html"], source)
        self.assertNotIn("events", with_html)
        history = self.cli("read", "--space", key, "--events")
        self.assertEqual(set(history), {"space_id", "events"})
        self.assertEqual(history["events"][-1]["payload"]["patch"], state)

    def test_excessively_nested_json_returns_structured_error_without_writing(self):
        key = self.opened()["refs"]["space_id"]
        before = self.cli("get", "--space", key)
        nested = self.folder / "nested.json"
        nested.write_text('{"input":' + '[' * 2000 + '0' + ']' * 2000 + '}', encoding="utf-8")
        attempts = [
            ("publish", "--space", key, "--expected-revision", "0", "--patch-file", nested),
            ("action", "create", "--space", key, "--type", "nested", "--request-id", "nested",
             "--expected-revision", "0", "--file", nested),
        ]
        for args in attempts:
            with self.subTest(command=args[0]):
                error = self.cli(*args, status=400)
                self.assertEqual(error["code"], "input_nesting_limit")
                self.assertEqual(self.cli("get", "--space", key), before)

    def test_relocatable_launcher_from_source_bundle_and_path_symlink(self):
        source = ROOT / "scripts/grok-space"
        self.assertEqual(self.cli("read", launcher=source), {"bots": [], "spaces": []})
        resources = self.folder / "Relocated App.app/Contents/Resources"
        resources.mkdir(parents=True)
        shutil.copytree(ROOT / "bridge", resources / "bridge", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy2(source, resources / "grok-space")
        bin_dir = self.folder / "bin"
        bin_dir.mkdir()
        link = bin_dir / "grok-space"
        link.symlink_to("../Relocated App.app/Contents/Resources/grok-space")
        self.assertEqual(self.cli("read", launcher=link), {"bots": [], "spaces": []})
        self.assertEqual(set(self.cli("--help", launcher=link)["commands"]), {"doctor", "open", "read", "publish", "action"})

    def test_show_launch_boundary_uses_argv_and_reports_only_acceptance(self):
        # Mock the OS launch boundary: tests never open an app or a user account.
        store = Store(self.data)
        app = self.folder / "Companion App.app"
        with patch.object(cli_module, "DEFAULT_DATA", self.data), \
                patch.object(cli_module, "companion_app", return_value=app), \
                patch.object(cli_module.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as launched:
            result = cli_module.show_space(store, {"id": "bot & one"}, {"id": "space?x=y"})
            self.assertEqual(launched.call_args.args[0], ["/usr/bin/open", "-a", str(app),
                             "grok-space://open?space_id=space%3Fx%3Dy&bot_id=bot+%26+one"])
            self.assertEqual(result, {"window_opened": True, "window_status": "launch_accepted", "page_loaded": "unverified"})
        with patch.object(cli_module, "DEFAULT_DATA", self.data), \
                patch.object(cli_module, "companion_app", return_value=None):
            with self.assertRaises(cli_module.SpaceError) as missing:
                cli_module.show_space(store, {"id": "bot"}, {"id": "space"})
            self.assertEqual(missing.exception.details["code"], "companion_app_missing")


if __name__ == "__main__":
    unittest.main()
