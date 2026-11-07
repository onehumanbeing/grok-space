#!/usr/bin/env python3
"""Run the real bundled WebKit smoke path with disposable local data.

Usage: /usr/bin/python3 -E -s -B tests/native_smoke.py [--app APP] [--report JSON]
This is an explicit macOS check, not a unittest-discovery test. It never uses
LaunchServices, activates a window, opens Grok, or reads the user's Space data.
"""
import argparse
import ctypes
import http.client
import json
import os
from pathlib import Path
import platform
import plistlib
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent.parent


class SmokeFailure(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class BSDInfo(ctypes.Structure):
    # macOS sys/proc_info.h, PROC_PIDTBSDINFO: only PID, parent and start time
    # are used. The identity prevents cleanup from signalling a recycled PID.
    _fields_ = [(name, ctypes.c_uint32) for name in (
        "flags", "status", "xstatus", "pid", "ppid", "uid", "gid", "ruid",
        "rgid", "svuid", "svgid", "reserved")] + [
        ("comm", ctypes.c_char * 16), ("name", ctypes.c_char * 32),
    ] + [(name, ctypes.c_uint32) for name in (
        "nfiles", "pgid", "jobc", "tdev", "tpgid")] + [
        ("nice", ctypes.c_int32), ("start_sec", ctypes.c_uint64),
        ("start_usec", ctypes.c_uint64),
    ]


class OwnedProcesses:
    def __init__(self, parent):
        self.parent = parent
        self.children = {}
        self.library = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        self.library.proc_listpids.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p, ctypes.c_int]
        self.library.proc_listpids.restype = ctypes.c_int
        self.library.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int]
        self.library.proc_pidinfo.restype = ctypes.c_int

    def identity(self, pid):
        info = BSDInfo()
        if self.library.proc_pidinfo(pid, 3, 0, ctypes.byref(info), ctypes.sizeof(info)) != ctypes.sizeof(info):
            return None
        return (info.pid, info.ppid, info.start_sec, info.start_usec)

    def observe(self):
        pids = (ctypes.c_int * 1024)()
        used = self.library.proc_listpids(6, self.parent.pid, pids, ctypes.sizeof(pids))  # PROC_PPID_ONLY
        if used < 0 or used >= ctypes.sizeof(pids):
            raise SmokeFailure("process_check_failed", "Owned child-process enumeration failed.")
        for pid in pids[:max(0, used) // ctypes.sizeof(ctypes.c_int)]:
            identity = self.identity(pid)
            if identity and identity[1] == self.parent.pid:
                # Parent IDs change after app exit; retain immutable start time.
                self.children[pid] = (identity[0], identity[2], identity[3])

    def alive(self):
        found = []
        for pid, expected in self.children.items():
            current = self.identity(pid)
            if current and (current[0], current[2], current[3]) == expected:
                found.append(pid)
        return found

    def stop(self):
        for requested_signal in (signal.SIGTERM, signal.SIGKILL):
            alive = self.alive()
            if self.parent.poll() is None or alive:
                try:
                    os.killpg(self.parent.pid, requested_signal)
                except ProcessLookupError:
                    pass
            # Foundation may put its Python child in a separate process group.
            for pid in alive:
                current = self.identity(pid)
                if current and (current[0], current[2], current[3]) == self.children[pid]:
                    try:
                        os.kill(pid, requested_signal)
                    except ProcessLookupError:
                        pass
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                self.parent.poll()
                if self.parent.returncode is not None and not self.alive():
                    return
                time.sleep(0.05)


def port_open(port):
    with socket.socket() as connection:
        connection.settimeout(0.1)
        return connection.connect_ex(("127.0.0.1", port)) == 0


def owned_health(port, data):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=0.2)
    try:
        connection.request("GET", "/health")
        response = connection.getresponse()
        if response.status != 200:
            return False
        health = json.loads(response.read(65537))
        return health.get("app") == "grok-spaces" and Path(health.get("data_dir", "")).resolve() == data.resolve()
    except (OSError, ValueError, http.client.HTTPException):
        return False
    finally:
        connection.close()


def write_report(path, report):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix=".native-smoke-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(report, output, indent=2, ensure_ascii=False)
            output.write("\n")
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def verify_icon(app, info):
    """Check the declared bundle resource before starting any native process."""
    name = info.get("CFBundleIconFile")
    if not isinstance(name, str) or not name or name in (".", "..") or any(character in name for character in ("/", "\\", "\0")):
        raise SmokeFailure("icon_invalid_metadata", "CFBundleIconFile must name a regular icon file directly inside Resources.")
    resources = app / "Contents/Resources"
    icon = resources / name
    if resources.is_symlink() or icon.is_symlink() or not icon.is_file():
        raise SmokeFailure("icon_missing", "The declared bundle icon is missing or is not a regular Resources file.")
    try:
        descriptor = os.open(icon, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as source:
            metadata = os.fstat(source.fileno())
            header = source.read(8)
    except OSError as exc:
        raise SmokeFailure("icon_unreadable", "The declared bundle icon could not be read safely.") from exc
    if not stat.S_ISREG(metadata.st_mode) or len(header) != 8 or header[:4] != b"icns" or int.from_bytes(header[4:], "big") != metadata.st_size or metadata.st_size < 16:
        raise SmokeFailure("icon_invalid_icns", "The bundle icon does not have a valid ICNS magic and container length.")
    return {"filename": name, "bytes": metadata.st_size, "format": "icns"}


def smoke(app, timeout):
    if platform.system() != "Darwin":
        raise SmokeFailure("unsupported_platform", "The native WebKit smoke check requires macOS.")
    executable = app / "Contents/MacOS/GrokSpaces"
    try:
        with (app / "Contents/Info.plist").open("rb") as source:
            info = plistlib.load(source)
    except (OSError, ValueError, plistlib.InvalidFileException) as exc:
        raise SmokeFailure("app_missing", "Build the native app first or pass --app with a built Grok Spaces bundle.") from exc
    if info.get("CFBundleIdentifier") != "sh.henry.grok-spaces" or not executable.is_file() or not os.access(executable, os.X_OK):
        raise SmokeFailure("invalid_app", "The selected bundle is not an executable Grok Spaces app.")
    icon = verify_icon(app, info)
    with tempfile.TemporaryDirectory(prefix="grok-space-native-smoke-") as directory:
        temporary = Path(directory).resolve()
        data = temporary / "data"
        home = temporary / "home"
        scratch = temporary / "tmp"
        home.mkdir(mode=0o700)
        scratch.mkdir(mode=0o700)
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        native_report = temporary / "native.json"
        environment = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(home),
                       "TMPDIR": str(scratch) + "/", "LANG": "en_US.UTF-8",
                       "GROK_SPACES_DATA_DIR": str(data), "GROK_SPACES_PORT": str(port),
                       "GROK_SPACES_SMOKE_REPORT": str(native_report)}
        owned = OwnedProcesses(None)
        # No open(1), inherited account/proxy env, source-root override or input.
        process = subprocess.Popen([str(executable)], env=environment, cwd=str(temporary),
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, start_new_session=True)
        owned.parent = process
        health_seen = False
        deadline = time.monotonic() + timeout
        try:
            while process.poll() is None:
                owned.observe()
                if not health_seen:
                    health_seen = owned_health(port, data)
                if time.monotonic() >= deadline:
                    raise SmokeFailure("timeout", "The owned native smoke process exceeded its deadline.")
                time.sleep(0.04)
            try:
                native = json.loads(native_report.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise SmokeFailure("report_missing", "The owned native app did not write a valid smoke report.") from exc
            document = native.get("document", {})
            if process.returncode != 0 or native.get("ok") is not True or native.get("stage") != "webkit":
                raise SmokeFailure("native_failed", "The native WebKit smoke check did not succeed.")
            if document.get("appReady") is not True or document.get("hostActive") is not False:
                raise SmokeFailure("root_not_ready", "The isolated root was not ready with host activity suppressed.")
            if not health_seen or not owned.children:
                raise SmokeFailure("service_not_observed", "The isolated service identity and owned process were not observed.")
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline and (port_open(port) or owned.alive()):
                time.sleep(0.05)
            if port_open(port) or owned.alive():
                raise SmokeFailure("service_not_stopped", "The native app exited without stopping all observed owned processes and its listener.")
            return {"ok": True, "code": "native_smoke_passed", "app_version": info.get("CFBundleShortVersionString"),
                    "icon_verified": True, "icon": icon,
                    "health_identity_matched": True, "owned_service_observed": True,
                    "owned_processes_stopped": True, "listener_closed": True,
                    "app_exit_code": process.returncode, "isolated_data": True,
                    "window_activation_requested": False, "native": native}
        finally:
            owned.stop()
            process.wait(timeout=3)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, default=ROOT / "out/Grok Spaces.app")
    parser.add_argument("--report", type=Path, default=ROOT / "out/native-smoke.json")
    parser.add_argument("--timeout", type=float, default=45)
    args = parser.parse_args()
    if not 5 <= args.timeout <= 180:
        parser.error("--timeout must be between 5 and 180 seconds")
    started = time.monotonic()
    try:
        result = smoke(args.app.expanduser().resolve(), args.timeout)
    except SmokeFailure as exc:
        result = {"ok": False, "code": exc.code, "error": str(exc)}
    except KeyboardInterrupt:
        result = {"ok": False, "code": "interrupted", "error": "The smoke check was interrupted; only owned processes were stopped."}
    except (OSError, subprocess.SubprocessError, AttributeError):
        result = {"ok": False, "code": "smoke_unavailable", "error": "A local smoke prerequisite or owned-process operation failed."}
    result.update(format_version=1, check="native-webkit", elapsed_ms=round((time.monotonic() - started) * 1000))
    try:
        write_report(args.report.expanduser().resolve(), result)
    except OSError:
        print(json.dumps({"ok": False, "code": "report_write_failed"}), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
