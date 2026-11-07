#!/bin/sh
# Build and install locally. No sudo, credential export, or Gatekeeper bypass.
set -eu
if [ "$(uname -s)" != Darwin ]; then
  printf '%s\n' '{"ok":false,"code":"unsupported_os","error":"Grok Spaces currently supports macOS only. Windows and Linux are unsupported."}' >&2
  exit 2
fi
if [ ! -x /usr/bin/python3 ]; then
  printf '%s\n' '{"ok":false,"code":"command_line_tools_required","error":"Install Apple Xcode Command Line Tools yourself, finish the system installer, then retry. No installation was started."}' >&2
  exit 2
fi
exec /usr/bin/python3 -E -s -B - "$0" "$@" <<'PY'
"""Local installer; isolated prefixes exercise the same installation transaction."""
import argparse
import ctypes
import datetime
import json
import os
import platform
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

SCRIPT = Path(sys.argv.pop(1)).resolve()
APP_NAME = "Grok Spaces.app"
BUNDLE_ID = "sh.henry.grok-spaces"
REPOSITORY = "onehumanbeing/grok-spaces"


class InstallError(Exception):
    def __init__(self, code, message, **details):
        super().__init__(message)
        self.code = code
        self.details = details


def run(argv, cwd=None, timeout=120, env=None):
    try:
        result = subprocess.run([str(item) for item in argv], cwd=cwd, text=True,
                                capture_output=True, timeout=timeout, env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise InstallError("command_unavailable", "A required local command could not finish; check prerequisites and retry.",
                           executable=Path(str(argv[0])).name) from exc
    if result.returncode:
        # Build/auth output may contain account information. Keep it out of the
        # installer result; do not print subprocess argv or credential values.
        raise InstallError("command_failed", "A required local command failed; no successful installation is claimed.",
                           executable=Path(str(argv[0])).name, exit_code=result.returncode)
    return result.stdout


def bundle_info(app):
    try:
        with (app / "Contents/Info.plist").open("rb") as source:
            info = plistlib.load(source)
    except (OSError, ValueError, plistlib.InvalidFileException) as exc:
        raise InstallError("invalid_app", "The app is missing a readable Info.plist.") from exc
    if info.get("CFBundleIdentifier") != BUNDLE_ID or info.get("CFBundleExecutable") != "GrokSpaces":
        raise InstallError("invalid_app", "This is not a Grok Spaces companion bundle.")
    return info


def verify_signature(app):
    try:
        run(["/usr/bin/codesign", "--verify", "--deep", "--strict", app], timeout=30)
    except InstallError as exc:
        raise InstallError("invalid_signature", "The local app signature could not be verified. Rebuild from the selected trusted source; do not bypass macOS security.") from exc


def running_target(app):
    """Compare kernel-reported executable paths, never truncated/forged argv."""
    executable = (app / "Contents/MacOS/GrokSpaces").resolve()
    try:
        library = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        list_pids = library.proc_listpids
        list_pids.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p, ctypes.c_int]
        list_pids.restype = ctypes.c_int
        pid_path = library.proc_pidpath
        pid_path.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint]
        pid_path.restype = ctypes.c_int
        size = list_pids(1, 0, None, 0)  # PROC_ALL_PIDS; result is bytes.
        if size <= 0:
            raise OSError("process enumeration unavailable")
        # Leave room for processes created between the sizing and reading calls.
        pids = (ctypes.c_int * (size // ctypes.sizeof(ctypes.c_int) + 512))()
        used = list_pids(1, 0, pids, ctypes.sizeof(pids))
        if used <= 0 or used >= ctypes.sizeof(pids):
            raise OSError("process enumeration incomplete")
    except (OSError, AttributeError) as exc:
        raise InstallError("process_check_failed", "The running-app check could not finish. No app was replaced; retry on this Mac.") from exc
    found = []
    for pid in pids[:used // ctypes.sizeof(ctypes.c_int)]:
        if pid <= 0:
            continue
        path = ctypes.create_string_buffer(4096)  # PROC_PIDPATHINFO_MAXSIZE
        if pid_path(pid, path, len(path)) > 0 and Path(os.fsdecode(path.value)).resolve() == executable:
            found.append(pid)
    return found


def require_stopped(app):
    if running_target(app):
        raise InstallError("app_running", "Quit Grok Spaces, then rerun the update. The installer will not close or replace a running app.")


def check_link(link, target):
    if not os.path.lexists(str(link)):
        return
    if not link.is_symlink() or link.resolve() != target.resolve():
        raise InstallError("cli_path_conflict", "The CLI path belongs to another file or installation; move it yourself or choose another --prefix.",
                           path=str(link))


def platform_info():
    version = platform.mac_ver()[0]
    try:
        major = int(version.split(".")[0])
    except ValueError:
        major = 0
    arch = platform.machine()
    if major < 14 or arch not in ("arm64", "x86_64"):
        raise InstallError("unsupported_platform", "macOS 14 or later on Apple Silicon or Intel is required. User runtime evidence covers macOS 26.5.2 arm64; CI build/smoke evidence covers macOS 15.8.2 arm64.",
                           detected_os=version, detected_arch=arch)
    if sys.version_info < (3, 9):
        raise InstallError("python_required", "Apple Command Line Tools must provide Python 3.9 or later.")
    evidence = ("verified_environment" if version == "26.5.2" and arch == "arm64" else
                "ci_verified_environment" if version == "15.8.2" and arch == "arm64" else "not_runtime_tested")
    return {"os": "macOS", "version": version, "arch": arch,
            "runtime_evidence": evidence,
            "minimum_deployment_target": "14.0", "windows": "unsupported", "linux": "unsupported"}


def preflight(args):
    environment = platform_info()
    run(["/usr/bin/xcode-select", "-p"], timeout=15)
    if args.app is None:
        run(["/usr/bin/xcrun", "--find", "swiftc"], timeout=30)
        compiler = run(["/usr/bin/xcrun", "swiftc", "--version"], timeout=30)
        match = re.search(r"Swift version (\d+)", compiler)
        if match is None or int(match.group(1)) < 6:
            raise InstallError("swift6_required", "A Swift 6 toolchain is required. Update Apple Xcode/Command Line Tools yourself, then retry.")
    else:
        bundle_info(args.app)
        verify_signature(args.app)
    local_source = args.source or SCRIPT.parent.parent
    if args.app is None and not args.repo and args.source is not None:
        if not all((local_source / name).exists() for name in ("bridge/server.py", "web", "native", "scripts/build-app.sh", "scripts/grok-space")):
            raise InstallError("source_missing", "The selected local source checkout is incomplete.")
        source_icon(local_source)
    if args.repo or args.source is None and args.app is None and not (SCRIPT.parent.parent / "bridge/server.py").is_file():
        if shutil.which("git") is None:
            raise InstallError("github_tools_required", "Git is required to fetch a public source revision. Private repository fallback also needs an existing authorized GitHub CLI login.")
    if args.target.is_symlink():
        raise InstallError("install_path_conflict", "The target app is a symlink. Choose a regular user-owned application directory.")
    if args.target.exists():
        bundle_info(args.target)
    check_link(args.link, args.target / "Contents/Resources/grok-space")
    require_stopped(args.target)
    return environment


def check(args):
    environment = platform_info()
    if args.target.is_symlink():
        raise InstallError("install_path_conflict", "The target app is a symlink. Choose a regular user-owned application directory.")
    if args.target.exists():
        check_link(args.link, args.target / "Contents/Resources/grok-space")
        with tempfile.TemporaryDirectory(prefix="grok-space-check-") as temporary:
            version = validate(args.target, Path(temporary))
        running = bool(running_target(args.target))
        return {"ok": True, "status": "installed_running_reusable" if running else "installed",
                "reusable": True, "running": running, "app": str(args.target),
                "cli": str(args.link if args.link.is_symlink() else args.target / "Contents/Resources/grok-space"),
                "cli_link_installed": args.link.is_symlink(), "version": version, "platform": environment,
                "notice": "The installed bundle and CLI passed an isolated temporary doctor check. No reinstall, launch, compiler check, GitHub access or user-data read was performed. This does not verify a Bot connection or running page."}
    environment = preflight(args)
    return {"ok": True, "status": "not_installed", "reusable": False, "ready_to_install": True,
            "app": str(args.target), "cli": str(args.link), "platform": environment,
            "source": "prebuilt_local" if args.app else "local_build",
            "notice": "Prerequisites passed. No build, clone, install, launch or user-data write was performed."}


def source_icon(source):
    # Older trusted revisions and fixture builders may not declare an icon.
    # A declared asset must come from the same source revision, never elsewhere.
    script = source / "scripts/build-app.sh"
    if "assets/grok-space.png" not in script.read_text(encoding="utf-8"):
        return None
    assets = source / "assets"
    icon = assets / "grok-space.png"
    if assets.is_symlink() or icon.is_symlink() or not icon.is_file():
        raise InstallError("source_asset_missing", "This source build requires its regular assets/grok-space.png logo. Restore it from the same revision and retry; no app was installed.")
    with icon.open("rb") as image:
        if image.read(8) != b"\x89PNG\r\n\x1a\n":
            raise InstallError("source_asset_invalid", "assets/grok-space.png is not a PNG image. Restore the original project asset and retry.")
    return icon


def source_tree(args, temporary):
    transport = None
    if args.repo:
        source = temporary / "checkout"
        git = shutil.which("git")
        # Public installs neither need nor consult GitHub credentials. Disable
        # config-based URL rewrites, helpers, prompts and inherited token/proxy env.
        public_env = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(temporary),
                      "LANG": "en_US.UTF-8", "GIT_TERMINAL_PROMPT": "0",
                      "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
                      "GIT_ASKPASS": "/usr/bin/false"}
        try:
            run([git, "clone", "--no-checkout", "https://github.com/" + args.repo + ".git", source],
                timeout=300, env=public_env)
            run([git, "fetch", "--depth", "1", "origin", args.ref], cwd=source, timeout=300, env=public_env)
            run([git, "checkout", "--detach", "FETCH_HEAD"], cwd=source, env=public_env)
            transport = "public_https"
        except InstallError:
            if source.exists():
                shutil.rmtree(source)  # Only this transaction's temporary checkout.
            try:
                if shutil.which("gh") is None:
                    raise InstallError("github_auth_required", "Private repository access requires an already authorized GitHub CLI.")
                run(["gh", "auth", "status"], timeout=30)
                run(["gh", "repo", "clone", args.repo, source, "--", "--no-checkout"], timeout=300)
                run([git, "fetch", "--depth", "1", "origin", args.ref], cwd=source, timeout=300)
                run([git, "checkout", "--detach", "FETCH_HEAD"], cwd=source)
                transport = "authorized_github_cli"
            except InstallError as exc:
                raise InstallError("source_fetch_failed", "The public repository/ref could not be fetched, and existing authorized private access was unavailable or failed. Check connectivity/ref/access yourself; no login was started.") from exc
    else:
        source = args.source or SCRIPT.parent.parent
    if not all((source / name).exists() for name in ("bridge/server.py", "web", "native", "scripts/build-app.sh", "scripts/grok-space")):
        raise InstallError("source_missing", "A complete source checkout is required. Use --repo onehumanbeing/grok-spaces --ref REF to fetch an authorized revision.")
    icon = source_icon(source)
    try:
        revision = run(["git", "rev-parse", "HEAD"], cwd=source, timeout=15).strip()
        dirty = bool(run(["git", "status", "--porcelain"], cwd=source, timeout=15).strip())
    except InstallError:
        revision, dirty = None, True
    build = temporary / "build"
    build.mkdir()
    # Copy only host sources. Runtime artifacts, user data, chat, credentials,
    # git configuration and business project directories never enter the build.
    for name in ("bridge", "web", "native", "scripts"):
        shutil.copytree(source / name, build / name,
                        ignore=shutil.ignore_patterns("__pycache__", ".DS_Store"))
    if icon is not None:
        (build / "assets").mkdir()
        shutil.copyfile(icon, build / "assets/grok-space.png")
    try:
        run(["/bin/bash", build / "scripts/build-app.sh"], cwd=build, timeout=600)
    except InstallError as exc:
        raise InstallError("build_failed", "The staged Swift build failed. Confirm Apple Swift 6/Command Line Tools and rebuild the selected source; the installed app was not replaced.") from exc
    return build / "out" / APP_NAME, {"kind": "local_build", "repository": args.repo,
                                      "transport": transport,
                                      "requested_ref": args.ref if args.repo else None,
                                      "source_commit": revision, "source_dirty": dirty}


def validate(app, temporary):
    info = bundle_info(app)
    executable = app / "Contents/MacOS/GrokSpaces"
    launcher = app / "Contents/Resources/grok-space"
    if not executable.is_file() or not os.access(str(executable), os.X_OK) or not launcher.is_file():
        raise InstallError("invalid_app", "The bundle is missing its executable or local CLI launcher.")
    verify_signature(app)
    # An isolated doctor identifies the packaged CLI without reading or seeding
    # the user's Application Support database.
    data_dir = temporary / ("doctor-" + uuid.uuid4().hex)
    try:
        result = json.loads(run([launcher, "--data-dir", data_dir, "doctor"], timeout=30))
    except (ValueError, InstallError) as exc:
        raise InstallError("doctor_failed", "The packaged CLI failed its isolated doctor check. Rebuild the selected source and retry.") from exc
    if result.get("app") != "grok-spaces" or not result.get("installation_id"):
        raise InstallError("doctor_failed", "The packaged CLI failed its isolated installation check.")
    return info.get("CFBundleShortVersionString", "unknown")


def swap(first, second):
    """Atomically exchange two directories on the same macOS filesystem."""
    library = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
    function = library.renameatx_np
    function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    function.restype = ctypes.c_int
    if function(-2, os.fsencode(first), -2, os.fsencode(second), 2):  # AT_FDCWD, RENAME_SWAP
        number = ctypes.get_errno()
        raise InstallError("atomic_replace_failed", "The atomic app exchange failed; the previous app has not been intentionally removed.", errno=number)


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    stage = path.with_name("." + path.name + "." + uuid.uuid4().hex)
    try:
        stage.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
        os.replace(str(stage), str(path))
    finally:
        if stage.exists():
            stage.unlink()


def install(args, environment):
    args.target.parent.mkdir(parents=True, exist_ok=True)
    args.link.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="grok-space-build-") as temporary_name:
        temporary = Path(temporary_name)
        if args.app:
            built, provenance = args.app, {"kind": "prebuilt_local", "source_commit": None}
        else:
            if args.source is None and not args.repo and not (SCRIPT.parent.parent / "bridge/server.py").is_file():
                args.repo = REPOSITORY
            built, provenance = source_tree(args, temporary)
        version = validate(built, temporary)
        stage_dir = Path(tempfile.mkdtemp(prefix=".grok-space-stage-", dir=str(args.target.parent)))
        staged = stage_dir / APP_NAME
        backup = None
        changed = False
        old_link = os.readlink(str(args.link)) if args.link.is_symlink() else None
        link_changed = False
        try:
            shutil.copytree(built, staged, symlinks=True)
            validate(staged, temporary)
            require_stopped(args.target)
            check_link(args.link, args.target / "Contents/Resources/grok-space")
            if args.target.exists():
                name = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8]
                backup = args.target.parent / ".grok-space-backups" / name / APP_NAME
                backup.parent.mkdir(parents=True)
                swap(args.target, staged)
                changed = True
                os.replace(str(staged), str(backup))
            else:
                os.replace(str(staged), str(args.target))
                changed = True
            validate(args.target, temporary)
            link_stage = args.link.with_name(".grok-space-" + uuid.uuid4().hex)
            try:
                link_stage.symlink_to(args.target / "Contents/Resources/grok-space")
                os.replace(str(link_stage), str(args.link))
                link_changed = True
            finally:
                if os.path.lexists(str(link_stage)):
                    link_stage.unlink()
            receipt = {"ok": True, "status": "installed", "app": str(args.target), "cli": str(args.link),
                       "version": version, "backup": str(backup) if backup else None,
                       "source": provenance, "platform": environment,
                       "data": "Existing Application Support data was not changed by the installer.",
                       "signing": "Locally built/ad-hoc signed; not a notarized public release.",
                       "launch": "not_requested"}
            atomic_json(args.prefix / ".local/share/grok-space/install.json", receipt)
        except Exception as exc:
            if changed:
                try:
                    require_stopped(args.target)
                    if backup and backup.exists():
                        swap(args.target, backup)
                    elif staged.exists():
                        swap(args.target, staged)
                    else:
                        failed = args.target.parent / ".grok-space-backups" / ("failed-" + uuid.uuid4().hex) / APP_NAME
                        failed.parent.mkdir(parents=True)
                        os.replace(str(args.target), str(failed))
                    if link_changed:
                        if old_link is None:
                            args.link.unlink()
                        else:
                            recovery_link = args.link.with_name(".grok-space-rollback-" + uuid.uuid4().hex)
                            recovery_link.symlink_to(old_link)
                            os.replace(str(recovery_link), str(args.link))
                except Exception:
                    raise InstallError("rollback_needs_attention", "Installation failed and automatic rollback could not finish. Quit the app and recover the retained previous bundle; no user data was removed.",
                                       app=str(args.target), backup=str(backup) if backup else None) from exc
                raise InstallError("install_rolled_back", "Installation verification failed; the previous installation was restored and user data was retained.") from exc
            raise
        finally:
            shutil.rmtree(stage_dir, ignore_errors=True)
    if not args.no_launch and args.prefix == Path.home().resolve():
        try:
            run(["/usr/bin/open", "-a", args.target], timeout=20)
            receipt["launch"] = "accepted_by_macos"
        except InstallError:
            receipt["launch"] = "not_opened"
            receipt["next_step"] = "Open the installed app yourself. If macOS blocks it, review Privacy & Security; no security setting was bypassed."
    elif args.prefix != Path.home().resolve():
        receipt["launch"] = "isolated_prefix_not_launched"
    try:
        atomic_json(args.prefix / ".local/share/grok-space/install.json", receipt)
    except OSError as exc:
        raise InstallError("receipt_update_failed", "The app was installed, but its final launch receipt could not be saved.",
                           app=str(args.target), launch=receipt["launch"]) from exc
    return receipt


def main():
    parser = argparse.ArgumentParser(description="Build/install Grok Spaces locally without sudo or account sign-in")
    parser.add_argument("--check", action="store_true", help="Reuse a healthy installed app, or check installation prerequisites; never read user data")
    parser.add_argument("--no-launch", action="store_true", help="Install without asking macOS to open the app")
    parser.add_argument("--prefix", type=Path, default=Path.home(), help="Installation root; isolated prefixes never auto-launch")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--source", type=Path, help="Build this existing local source checkout")
    source.add_argument("--repo", help="Fetch an already authorized GitHub owner/repository")
    source.add_argument("--app", type=Path, help="Use an already locally built, codesign-verifiable app")
    parser.add_argument("--ref", default="main", help="Git tag, branch or commit to fetch with --repo")
    args = parser.parse_args()
    args.prefix = args.prefix.expanduser().resolve()
    args.source = args.source.expanduser().resolve() if args.source else None
    args.app = args.app.expanduser().resolve() if args.app else None
    args.target = args.prefix / "Applications" / APP_NAME
    args.link = args.prefix / ".local/bin/grok-space"
    if args.repo and not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repo):
        raise InstallError("invalid_repository", "Use an explicit GitHub owner/repository.")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/+-]*", args.ref) or ".." in args.ref:
        raise InstallError("invalid_ref", "Use a Git tag, branch or commit without shell syntax.")
    if args.check:
        return check(args)
    return install(args, preflight(args))


if __name__ == "__main__":
    os.umask(0o077)
    try:
        print(json.dumps(main(), ensure_ascii=False))
    except InstallError as exc:
        print(json.dumps({"ok": False, "code": exc.code, "error": str(exc), **exc.details}), file=sys.stderr)
        sys.exit(2)
    except (OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "code": "local_install_error", "error": "A local installation file operation failed; existing user data was not intentionally changed."}), file=sys.stderr)
        sys.exit(2)
PY
