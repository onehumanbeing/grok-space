"""Exercise installation transactions in isolated prefixes, never user apps/data."""
import json
import ctypes
import os
import platform
import plistlib
import shlex
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts/install.sh"


@unittest.skipUnless(platform.system() == "Darwin", "The installer supports macOS only")
class InstallerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture_temp = tempfile.TemporaryDirectory(prefix="grok-installer-native-fixture-")
        cls.addClassCleanup(cls.fixture_temp.cleanup)
        folder = Path(cls.fixture_temp.name)
        source = folder / "fixture.c"
        source.write_text(
            '#include <stdlib.h>\n#include <unistd.h>\n'
            'int main(int argc, char **argv) {\n'
            '  unsigned int seconds = argc > 1 ? (unsigned int)strtoul(argv[1], 0, 10) : 0;\n'
            '  while (seconds) seconds = sleep(seconds);\n'
            '  return 0;\n}\n')
        cls.fixture_executable = folder / "owned-fixture"
        # Use our own executable, not a copied Apple platform binary whose
        # launch/signing behavior can differ between macOS runner versions.
        subprocess.run(['/usr/bin/xcrun', 'clang', '-O2', str(source), '-o', str(cls.fixture_executable)],
                       capture_output=True, text=True, check=True, timeout=60)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="grok-installer-test-")
        self.folder = Path(self.temp.name)
        self.prefix = self.folder / "isolated user"
        self.app = self.prefix / "Applications/Grok Spaces.app"
        self.link = self.prefix / ".local/bin/grok-space"

    def tearDown(self):
        self.temp.cleanup()

    def bundle(self, version, fail_after_install=False):
        app = self.folder / ("fixture-" + version + ".app")
        resources = app / "Contents/Resources"
        (app / "Contents/MacOS").mkdir(parents=True)
        (resources / "bridge").mkdir(parents=True)
        shutil.copyfile(self.fixture_executable, app / "Contents/MacOS/GrokSpaces")
        (app / "Contents/MacOS/GrokSpaces").chmod(0o755)
        with (app / "Contents/Info.plist").open("wb") as output:
            plistlib.dump({"CFBundleIdentifier": "sh.henry.grok-spaces", "CFBundleExecutable": "GrokSpaces",
                          "CFBundlePackageType": "APPL", "CFBundleShortVersionString": version}, output)
        launcher = resources / "grok-space"
        shutil.copy2(ROOT / "scripts/grok-space", launcher)
        launcher.chmod(0o755)
        cli = "import json,sys\nfrom pathlib import Path\n"
        if fail_after_install:
            cli += "if '/Applications/Grok Spaces.app/' in str(Path(__file__).resolve()): sys.exit(7)\n"
        cli += "print(json.dumps({'app':'grok-spaces','installation_id':'isolated-fixture','version':%r}))\n" % version
        (resources / "bridge/cli.py").write_text(cli)
        subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", str(app)],
                       capture_output=True, check=True)
        return app

    def assert_fixture_running(self, process, executable):
        """Diagnose only this test's known PID; never inspect user argv/content."""
        library = ctypes.CDLL('/usr/lib/libproc.dylib', use_errno=True)
        library.proc_pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint]
        library.proc_pidpath.restype = ctypes.c_int
        deadline = time.monotonic() + 3
        metadata = {}
        while time.monotonic() < deadline:
            buffer = ctypes.create_string_buffer(4096)
            ctypes.set_errno(0)
            length = library.proc_pidpath(process.pid, buffer, len(buffer))
            actual = Path(os.fsdecode(buffer.value)).resolve() if length > 0 else None
            metadata = {'fixture_pid': process.pid, 'returncode': process.poll(),
                        'expected_executable': str(executable.resolve()),
                        'kernel_executable': str(actual) if actual else None,
                        'proc_pidpath_errno': ctypes.get_errno() if length <= 0 else 0,
                        'os': platform.mac_ver()[0], 'arch': platform.machine()}
            if actual == executable.resolve() and process.poll() is None:
                break
            if process.poll() is not None:
                break
            time.sleep(0.05)
        # Safe CI evidence distinguishes an exited fixture, executable-identity
        # mismatch, and a genuine installer failure without process-list dumps.
        print('installer-fixture ' + json.dumps(metadata, sort_keys=True), flush=True)
        self.assertIsNone(process.poll(), json.dumps(metadata))
        self.assertEqual(metadata['kernel_executable'], str(executable.resolve()), json.dumps(metadata))

    def install(self, app, *extra, error=None, env=None):
        selected = ["--app", str(app)] if app else []
        result = subprocess.run([str(INSTALLER), "--prefix", str(self.prefix), *selected,
                                 "--no-launch", *extra], text=True, capture_output=True, timeout=60, env=env)
        self.assertEqual(result.returncode, 2 if error else 0, result.stderr)
        self.assertEqual(result.stdout if error else result.stderr, "")
        data = json.loads(result.stderr if error else result.stdout)
        if error:
            self.assertEqual(data["code"], error)
        return data

    def version(self):
        with (self.app / "Contents/Info.plist").open("rb") as source:
            return plistlib.load(source)["CFBundleShortVersionString"]

    def test_check_is_read_only_and_install_is_relocatable(self):
        built = self.bundle("1")
        check = self.install(built, "--check")
        self.assertEqual(check["status"], "not_installed")
        self.assertTrue(check["ready_to_install"])
        self.assertFalse(self.prefix.exists())
        installed = self.install(built)
        self.assertEqual(installed["launch"], "isolated_prefix_not_launched")
        receipt = json.loads((self.prefix / ".local/share/grok-space/install.json").read_text())
        self.assertEqual(receipt, installed)
        self.assertTrue(self.link.is_symlink())
        self.assertEqual(self.link.resolve(), (self.app / "Contents/Resources/grok-space").resolve())
        result = subprocess.run([str(self.link), "doctor"], text=True, capture_output=True, check=True)
        self.assertEqual(json.loads(result.stdout)["version"], "1")
        self.assertEqual(self.version(), "1")

    def test_update_keeps_old_bundle_and_preserves_data(self):
        first, second = self.bundle("1"), self.bundle("2")
        self.install(first)
        data = self.prefix / "Library/Application Support/Grok Spaces"
        data.mkdir(parents=True)
        sentinel = data / "spaces.sqlite3"
        sentinel.write_bytes(b"user data must remain byte-for-byte unchanged")
        before = sentinel.read_bytes()
        updated = self.install(second)
        self.assertEqual(self.version(), "2")
        self.assertEqual(sentinel.read_bytes(), before)
        backup = Path(updated["backup"])
        with (backup / "Contents/Info.plist").open("rb") as source:
            self.assertEqual(plistlib.load(source)["CFBundleShortVersionString"], "1")
        self.assertFalse(list((self.prefix / "Applications").glob(".grok-space-stage-*")))

    def test_post_install_failure_restores_previous_app_and_link(self):
        first, broken = self.bundle("1"), self.bundle("2", fail_after_install=True)
        self.install(first)
        original_link = os.readlink(str(self.link))
        self.install(broken, error="install_rolled_back")
        self.assertEqual(self.version(), "1")
        self.assertEqual(os.readlink(str(self.link)), original_link)
        result = subprocess.run([str(self.link), "doctor"], text=True, capture_output=True, check=True)
        self.assertEqual(json.loads(result.stdout)["version"], "1")

    def test_running_target_is_left_running_and_unchanged(self):
        first, second = self.bundle("1"), self.bundle("2")
        self.install(first)
        process = subprocess.Popen([str(self.app / "Contents/MacOS/GrokSpaces"), "30"])
        try:
            self.assert_fixture_running(process, self.app / "Contents/MacOS/GrokSpaces")
            self.install(second, error="app_running")
            self.assertIsNone(process.poll())
            self.assertEqual(self.version(), "1")
        finally:
            process.terminate()
            process.wait(timeout=5)

    def test_running_long_path_is_identified_by_executable_not_display_text(self):
        self.prefix = self.folder / ("long directory " * 12) / ("nested directory " * 12) / "isolated user"
        self.app = self.prefix / "Applications/Grok Spaces.app"
        self.link = self.prefix / ".local/bin/grok-space"
        first, second = self.bundle("long-1"), self.bundle("long-2")
        self.install(first)
        self.assertGreater(len(str(self.app)), 300)
        process = subprocess.Popen([str(self.app / "Contents/MacOS/GrokSpaces"), "30"])
        try:
            self.assert_fixture_running(process, self.app / "Contents/MacOS/GrokSpaces")
            check = self.install(None, "--check", "--repo", "unavailable/repository", env={
                "PATH": "/usr/bin:/bin", "HOME": str(self.folder)})
            self.assertEqual(check["status"], "installed_running_reusable")
            self.assertTrue(check["reusable"])
            self.install(second, error="app_running")
            self.assertEqual(self.version(), "long-1")
            self.assertIsNone(process.poll())
        finally:
            process.terminate()
            process.wait(timeout=5)

    def test_healthy_check_reuses_installation_without_source_or_user_data(self):
        built = self.bundle("reuse")
        self.install(built)
        data = self.prefix / "Library/Application Support/Grok Spaces"
        data.mkdir(parents=True)
        sentinel = data / "spaces.sqlite3"
        sentinel.write_bytes(b"not a database; never open this for an installation check")
        before = sentinel.stat()
        receipt = self.prefix / ".local/share/grok-space/install.json"
        previous_receipt = receipt.read_bytes()
        result = self.install(None, "--check", "--source", str(self.folder / "missing"), env={
            "PATH": "/usr/bin:/bin", "HOME": str(self.folder)})
        self.assertEqual(result["status"], "installed")
        self.assertTrue(result["reusable"])
        self.assertFalse(result["running"])
        self.assertEqual(sentinel.stat().st_mtime_ns, before.st_mtime_ns)
        self.assertEqual(sentinel.stat().st_atime_ns, before.st_atime_ns)
        self.assertEqual(receipt.read_bytes(), previous_receipt)
        self.assertFalse((self.prefix / "Applications/.grok-space-backups").exists())

    def test_argv_cannot_impersonate_running_installed_executable(self):
        built = self.bundle("argv")
        self.install(built)
        process = subprocess.Popen([str(self.app / "Contents/MacOS/GrokSpaces"), "30"], executable=str(self.fixture_executable))
        try:
            self.assert_fixture_running(process, self.fixture_executable)
            check = self.install(None, "--check")
            self.assertEqual(check["status"], "installed")
            self.assertFalse(check["running"])
            self.assertIsNone(process.poll())
        finally:
            process.terminate()
            process.wait(timeout=5)

    def test_unrelated_cli_path_is_not_replaced(self):
        built = self.bundle("1")
        self.link.parent.mkdir(parents=True)
        self.link.write_text("unrelated executable\n")
        self.install(built, error="cli_path_conflict")
        self.assertEqual(self.link.read_text(), "unrelated executable\n")
        self.assertFalse(self.app.exists())

    def test_invalid_bundle_does_not_replace_working_app(self):
        first, broken = self.bundle("1"), self.bundle("2")
        self.install(first)
        (broken / "Contents/Resources/bridge/cli.py").write_text("tampered")
        self.install(broken, error="invalid_signature")
        self.assertEqual(self.version(), "1")

    def test_source_build_uses_isolated_allowlist_and_leaves_source_outputs(self):
        built = self.bundle("source")
        source = self.folder / "source checkout"
        for name in ("bridge", "web", "native", "scripts", "artifacts", "out"):
            (source / name).mkdir(parents=True)
        (source / "bridge/server.py").write_text("# fixture\n")
        (source / "scripts/grok-space").write_text("# fixture\n")
        marker = source / "out/preserve.txt"
        marker.write_text("existing build output\n")
        (source / "artifacts/do-not-copy.txt").write_text("runtime artifact\n")
        (source / "scripts/build-app.sh").write_text(
            '#!/bin/sh\nset -eu\n[ ! -e artifacts ]\n[ ! -e out/preserve.txt ]\n'
            'mkdir -p out\n/usr/bin/ditto ' + shlex.quote(str(built)) + ' "out/Grok Spaces.app"\n')
        result = self.install(None, "--source", str(source))
        self.assertEqual(result["source"]["kind"], "local_build")
        self.assertEqual(self.version(), "source")
        self.assertEqual(marker.read_text(), "existing build output\n")
        self.assertFalse((source / "out/Grok Spaces.app").exists())

    def test_missing_source_fails_check_without_creating_installation(self):
        self.install(None, "--source", str(self.folder / "missing"), "--check", error="source_missing")
        self.assertFalse(self.prefix.exists())

    def test_public_fetch_needs_no_github_login_or_inherited_credentials(self):
        built = self.bundle("public")
        source = self.folder / "public source"
        for name in ("bridge", "web", "native", "scripts"):
            (source / name).mkdir(parents=True)
        (source / "bridge/server.py").write_text("# fixture\n")
        (source / "scripts/grok-space").write_text("# fixture\n")
        (source / "scripts/build-app.sh").write_text(
            '#!/bin/sh\nset -eu\nmkdir -p out\n/usr/bin/ditto ' + shlex.quote(str(built)) + ' "out/Grok Spaces.app"\n')
        tools = self.folder / "tools"
        tools.mkdir()
        calls = self.folder / "git-calls.jsonl"
        fake_git = tools / "git"
        fake_git.write_text(
            '#!/usr/bin/python3 -E\nimport json,os,shutil,sys\nfrom pathlib import Path\n'
            'args=sys.argv[1:]\n'
            'if args[0] in ("clone","fetch","checkout"):\n'
            ' assert os.environ.get("GIT_TERMINAL_PROMPT")=="0"\n'
            ' assert os.environ.get("GIT_CONFIG_GLOBAL")=="/dev/null"\n'
            ' assert os.environ.get("GIT_CONFIG_NOSYSTEM")=="1"\n'
            ' assert "INSTALL_TEST_SENTINEL" not in os.environ\n'
            ' with Path(%r).open("a") as f: f.write(json.dumps(args)+"\\n")\n'
            'if args[0]=="clone": shutil.copytree(%r,args[-1])\n'
            'elif args[:2]==["rev-parse","HEAD"]: print("a"*40)\n'
            % (str(calls), str(source)))
        fake_git.chmod(0o755)
        # If fallback is attempted, fail rather than consulting any real account.
        fake_gh = tools / "gh"
        fake_gh.write_text('#!/bin/sh\nexit 99\n')
        fake_gh.chmod(0o755)
        result = self.install(None, "--repo", "public-owner/public-source", "--ref", "v0.1.0", env={
            "PATH": str(tools) + ":/usr/bin:/bin", "HOME": str(self.folder), "INSTALL_TEST_SENTINEL": "must-not-inherit"})
        self.assertEqual(result["source"]["transport"], "public_https")
        self.assertEqual(result["source"]["source_commit"], "a" * 40)
        self.assertEqual(self.version(), "public")
        commands = [json.loads(line) for line in calls.read_text().splitlines()]
        self.assertEqual([command[0] for command in commands], ["clone", "fetch", "checkout"])
        self.assertIn("https://github.com/public-owner/public-source.git", commands[0])


if __name__ == "__main__":
    unittest.main()
