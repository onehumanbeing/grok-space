"""One-step init, locally editable source and Space-to-Bot rebinding."""
import json
import time
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bridge"))
from store import SpaceError, Store
import local_worker


class InitLocalSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="grok-space-init-")
        self.data = Path(self.temp.name) / "data"
        self.installation = Store(self.data).info()["installation_id"]

    def tearDown(self):
        self.temp.cleanup()

    def cli(self, *args, status=0):
        result = subprocess.run([sys.executable, str(ROOT / "bridge/cli.py"), "--data-dir", str(self.data), *args],
                                text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, status, result.stderr)
        return json.loads(result.stdout if status == 0 else result.stderr)

    def init(self, *extra):
        return self.cli("init", "--bot", "Home", "--create-bot", "--title", "Mi Home",
                        "--expect-installation", self.installation, *extra)

    def test_init_scaffolds_publishes_and_registers_in_one_call(self):
        result = self.init("--type", "refresh", "--type", "toggle")
        project = Path(result["project_dir"])
        self.assertEqual(result["files"], {"index.html": "created_starter", "handler.py": "created_starter"})
        self.assertTrue(result["published"])
        self.assertEqual(result["revision"], 1)
        self.assertEqual(result["worker"]["types"], ["refresh", "toggle"])
        self.assertIn('["refresh", "toggle"]', (project / "index.html").read_text())
        space = result["space"]["id"]
        self.assertEqual(Store(self.data).detail(space)["view_html"], (project / "index.html").read_text())
        # Rerunning never overwrites local edits or republishes.
        (project / "handler.py").write_text("# edited\n")
        again = self.cli("init", "--bot", "Home", "--space", space, "--expect-installation", self.installation, "--type", "refresh")
        self.assertEqual(again["files"]["handler.py"], "kept_existing")
        self.assertFalse(again["published"])
        self.assertEqual((project / "handler.py").read_text(), "# edited\n")
        self.cli("init", "--bot", "Home", "--space", space, "--expect-installation", "other", status=2)

    def test_handler_stub_runs_through_the_local_worker(self):
        result = self.init("--type", "refresh")
        space = result["space"]["id"]
        store = Store(self.data)
        event = store.action(space, "refresh", {}, "request-1", expected_revision=1)
        local_worker.dispatch(store, space, event["id"])
        for _ in range(100):
            current = store.event(space, event["id"])
            if current["status"] != "pending":
                break
            time.sleep(0.05)
        self.assertEqual(current["status"], "completed")
        self.assertEqual(current["result"], {"ok": True, "type": "refresh"})
        self.assertEqual(store.detail(space)["state"]["last_action"], "refresh")

    def test_local_source_edit_publishes_from_project(self):
        result = self.init()
        space, project = result["space"]["id"], Path(result["project_dir"])
        self.assertEqual(result["files"], {"index.html": "created_starter"})
        self.assertIsNone(result["worker"])
        (project / "index.html").write_text("<p>edited locally</p>")
        published = self.cli("publish", "--space", space, "--expected-revision", "1", "--from-project")
        self.assertEqual(published["revision"], 2)
        self.assertEqual(Store(self.data).detail(space)["view_html"], "<p>edited locally</p>")
        self.cli("publish", "--space", space, "--expected-revision", "1", "--from-project", status=2)
        self.cli("publish", "--space", space, "--expected-revision", "2", "--from-project", "--html-file", "/tmp/x.html", status=2)

    def test_open_exports_saved_page_once(self):
        opened = self.cli("open", "--bot", "Home", "--create-bot", "--title", "Imported")
        self.assertIsNone(opened["source_file"])
        space = opened["space"]["id"]
        Store(self.data).publish(space, "<p>saved</p>", 0)
        source = Path(self.cli("open", "--bot", "Home", "--space", space)["source_file"])
        self.assertEqual(source.read_text(), "<p>saved</p>")
        source.write_text("<p>local</p>")
        self.cli("open", "--bot", "Home", "--space", space)
        self.assertEqual(source.read_text(), "<p>local</p>")
        self.cli("publish", "--space", "space_" + "0" * 32, "--expected-revision", "0", "--from-project", status=2)

    def test_rebind_space_to_another_bot(self):
        store = Store(self.data)
        first, second = store.create_bot("First"), store.create_bot("Second")
        space = store.create_space(first["id"], "Shared")
        store.patch_state(space["id"], {"kept": True}, 0)
        moved = store.rebind_space(space["id"], second["id"])
        self.assertEqual(moved["bot_id"], second["id"])
        detail = store.detail(space["id"])
        self.assertEqual(detail["state"], {"kept": True})
        self.assertEqual(detail["revision"], 1)
        self.assertEqual(store.events(space["id"])[-1]["type"], "space.rebound")
        with self.assertRaises(SpaceError):
            store.rebind_space(space["id"], "bot_" + "0" * 32)
        with self.assertRaises(SpaceError):
            store.rebind_space(space["id"], {"id": 1})
        self.cli("open", "--bot", "First", "--space", space["id"], status=2)
        self.assertEqual(self.cli("open", "--bot", "First", "--space", space["id"], "--rebind")["space"]["bot_id"], first["id"])


if __name__ == "__main__":
    unittest.main()
