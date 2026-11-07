#!/usr/bin/env python3
"""Five local command families for the existing Grok Bot; no account access."""
import argparse
import json
import os
import socket
import sqlite3
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import build_opener, HTTPRedirectHandler, ProxyHandler

from store import SpaceError, Store
from contract import capabilities, describe
from version import VERSION, BUILD_NUMBER

DEFAULT_DATA = Path.home() / "Library/Application Support/Grok Spaces"
PUBLIC_COMMANDS = {
    "doctor": "Verify this installation and describe the available interface",
    "open": "Resolve an explicit local Bot and stable Space reference",
    "read": "Read Spaces, state, authored source location or events",
    "publish": "Save HTML and/or state with an explicit revision",
    "action": "Create, inspect or finish a durable action",
}


class JsonParser(argparse.ArgumentParser):
    """Keep help and argument failures machine-readable, including bad argv."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.add_argument("--data-dir", type=Path, default=argparse.SUPPRESS,
                          help="Use an explicit local database directory")

    def error(self, message):
        # argparse messages can echo arbitrary user-provided JSON or secrets.
        raise SpaceError("Invalid arguments. Read command help for required options.",
                         code="invalid_arguments", help_command=self.prog + " --help")

    def format_help(self):
        options = []
        commands = {}
        for action in self._actions:
            if isinstance(action, argparse._SubParsersAction):
                allowed = PUBLIC_COMMANDS if self.prog == "grok-space" else action.choices
                for name in allowed:
                    if name in action.choices:
                        commands[name] = action.choices[name].description
            elif action.option_strings:
                options.append({"flags": action.option_strings, "required": action.required,
                                "description": action.help or "",
                                **({"choices": list(action.choices)} if action.choices else {})})
        groups = [{"required": group.required,
                   "flags": [action.option_strings for action in group._group_actions]}
                  for group in self._mutually_exclusive_groups]
        return json.dumps({"command": self.prog, "description": self.description,
                           "commands": commands, "options": options,
                           "exclusive_groups": groups}, ensure_ascii=False) + "\n"


def add_space(parser):
    parser.add_argument("--space", required=True, help="Stable local Space ID")


def add_revision(parser):
    parser.add_argument("--expected-revision", type=int, required=True,
                        help="Revision read before this write; conflicts require reconciliation")


def add_input(parser):
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--json", help="JSON object; prefer --file for long or untrusted content")
    source.add_argument("--file", type=Path, help="UTF-8 JSON object file, up to 1 MiB")


def read_text(path, label):
    with path.open("rb") as source:
        data = source.read(1024 * 1024 + 1)
    if len(data) > 1024 * 1024:
        raise SpaceError(label + " exceeds 1 MiB", 413)
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SpaceError(label + " must be UTF-8 text") from exc


def payload(args):
    text = read_text(args.file, "Input") if args.file else args.json
    if len(text.encode()) > 1024 * 1024:
        raise SpaceError("Input exceeds 1 MiB", 413)
    try:
        value = json.loads(text, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except RecursionError as exc:
        raise SpaceError("JSON input is nested too deeply", code="input_nesting_limit") from exc
    except ValueError as exc:
        raise SpaceError("Invalid JSON input") from exc
    if not isinstance(value, dict):
        raise SpaceError("Input must be a JSON object")
    return value


def add_event_operations(subparsers, include_status=False):
    get = subparsers.add_parser("get", description="Read one action with its authoritative result")
    add_space(get)
    get.add_argument("--event", required=True)
    if include_status:
        status = subparsers.add_parser("status", description="Read one action or list pending actions")
        add_space(status)
        status.add_argument("--event")
        status.add_argument("--after", type=int, default=0)
        status.add_argument("--status", choices=("pending", "completed", "failed"), default="pending")
    for operation in ("complete", "fail"):
        done = subparsers.add_parser(operation, description="Save the factual result and optional state patch")
        add_space(done)
        done.add_argument("--event", required=True)
        add_revision(done)
        add_input(done)


def parser():
    root = JsonParser(prog="grok-space", description="Local HTML/state foundation for existing Grok Bots")
    root.set_defaults(data_dir=Path(os.environ.get("GROK_SPACES_DATA_DIR", DEFAULT_DATA)))
    commands = root.add_subparsers(dest="command", required=True, metavar="{doctor,open,read,publish,action}")
    public = {name: commands.add_parser(name, help=description, description=description)
              for name, description in PUBLIC_COMMANDS.items()}
    public["doctor"].add_argument("--space", help="Include this Space's scoped authoring interface")
    public["doctor"].add_argument("--expect-installation", help="Fail if this is a different local installation")
    public["doctor"].add_argument("--summary", action="store_true", help="Return compact local health, version, Space count and Bot associations")
    opened = public["open"]
    opened.add_argument("--bot", required=True, help="Exact local Bot name or ID; never guessed")
    opened.add_argument("--create-bot", action="store_true", help="Explicitly register a missing local Bot label")
    opened.add_argument("--show", action="store_true", help="Ask macOS to show this Space in the companion app")
    opened.add_argument("--icon-file", type=Path, help="Use this local PNG/JPEG/GIF/WebP image for the Bot avatar (128 KiB max)")
    target = opened.add_mutually_exclusive_group(required=True)
    target.add_argument("--space", help="Reopen a stable Space ID belonging to this Bot")
    target.add_argument("--title", help="Resolve or create an exact Space title for this Bot")
    read = public["read"]
    read.add_argument("--space")
    read.add_argument("--bot", help="Exact local Bot name or ID; optionally scope a catalog read")
    read.add_argument("--html", action="store_true", help="Include saved HTML; requires --space")
    read.add_argument("--summary", action="store_true", help="Read field types/counts and revision without returning application data")
    read.add_argument("--field", action="append", default=[], help="Return only this top-level state field; repeat for multiple fields")
    read.add_argument("--events", action="store_true", help="Read an event page instead of state; requires --space")
    read.add_argument("--after", type=int, default=0)
    read.add_argument("--status", choices=("pending", "completed", "failed"))
    publish = public["publish"]
    add_space(publish)
    publish.add_argument("--html-file", type=Path, help="Self-contained UTF-8 HTML file")
    publish.add_argument("--patch-file", type=Path, help="JSON Merge Patch object; may be used without HTML")
    add_revision(publish)
    action = public["action"].add_subparsers(dest="action_command", required=True)
    create = action.add_parser("create", description="Record an idempotent pending action; does not wake a Bot")
    add_space(create)
    create.add_argument("--type", required=True)
    create.add_argument("--request-id", required=True)
    add_revision(create)
    add_input(create)
    add_event_operations(action, include_status=True)
    worker = action.add_parser("worker", description="Register and inspect trusted local code authored by Grok")
    operations = worker.add_subparsers(dest="worker_command", required=True)
    register = operations.add_parser("register", description="Allow one project-local Python handler for explicit action types")
    add_space(register)
    register.add_argument("--file", type=Path, required=True)
    register.add_argument("--type", action="append", required=True, dest="types")
    status = operations.add_parser("status", description="Read registration or one durable worker attempt")
    add_space(status)
    status.add_argument("--event")
    status.add_argument("--output", action="store_true", help="Explicitly read the saved worker output for reconciliation; requires --event")
    invoke = operations.add_parser("run", description="Run an unclaimed pending action once; never repeat a claimed action")
    add_space(invoke)
    invoke.add_argument("--event", required=True)

    # Only the five families above are advertised. Old successful response
    # shapes remain unchanged for existing integrations.
    contract = commands.add_parser("contract")
    contract.add_argument("--space")
    commands.add_parser("bots")
    bot = commands.add_parser("add-bot")
    bot.add_argument("--name", required=True)
    bot.add_argument("--external-id", default="")
    spaces = commands.add_parser("spaces")
    spaces.add_argument("--bot")
    create = commands.add_parser("create")
    create.add_argument("--bot", required=True)
    create.add_argument("--title", required=True)
    for operation in ("get", "project", "patch", "view", "events"):
        legacy = commands.add_parser(operation)
        add_space(legacy)
        if operation in ("patch", "view"):
            add_revision(legacy)
        if operation == "patch":
            add_input(legacy)
        if operation == "view":
            legacy.add_argument("--html-file", type=Path, required=True)
        if operation == "events":
            legacy.add_argument("--after", type=int, default=0)
            legacy.add_argument("--status", choices=("pending", "completed", "failed"))
    add_event_operations(commands.add_parser("event").add_subparsers(dest="event_command", required=True))
    return root


def normalize_legacy_action(argv):
    """Old flat `action --space ...` remains equivalent to `action create`."""
    argv = list(argv)
    index = 0
    while index < len(argv):
        if argv[index] == "--data-dir":
            index += 2
        elif argv[index].startswith("--data-dir="):
            index += 1
        else:
            break
    if index < len(argv) and argv[index] == "action":
        cursor = index + 1
        while cursor < len(argv) and (argv[cursor] == "--data-dir" or argv[cursor].startswith("--data-dir=")):
            cursor += 2 if argv[cursor] == "--data-dir" else 1
        if cursor < len(argv) and argv[cursor].startswith("-") and argv[cursor] not in ("-h", "--help"):
            argv.insert(index + 1, "create")
    return argv


def resolve_bot(store, reference, create=False):
    return store.resolve_bot(reference, create=create)


def refs(store, space):
    return {"installation_id": store.info()["installation_id"],
            "bot_id": space["bot_id"], "space_id": space["id"]}


def scoped_detail(store, space_id, bot=None):
    detail = store.detail(space_id)
    if bot is not None and detail["space"]["bot_id"] != bot["id"]:
        raise SpaceError("Space belongs to a different local Bot. Select the intended association.", 409)
    return detail


def companion_app():
    """Find the app in either a source checkout or a bundled launcher."""
    module = Path(__file__).resolve()
    for parent in module.parents:
        if parent.suffix == ".app" and (parent / "Contents/MacOS/GrokSpaces").is_file():
            return parent
    candidates = (module.parent.parent / "out/Grok Spaces.app",
                  Path.home() / "Applications/Grok Spaces.app", Path("/Applications/Grok Spaces.app"))
    return next((candidate for candidate in candidates if (candidate / "Contents/MacOS/GrokSpaces").is_file()), None)


def show_space(store, bot, space):
    if store.data_dir != DEFAULT_DATA.expanduser().resolve():
        raise SpaceError("--show requires the default data directory; this companion does not select custom databases.",
                         code="custom_data_dir_show_unsupported")
    app = companion_app()
    if app is None:
        raise SpaceError("Companion app not found. Build it with scripts/build-app.sh from the source checkout, then retry --show.",
                         404, code="companion_app_missing")
    url = "grok-space://open?" + urlencode({"space_id": space["id"], "bot_id": bot["id"]})
    launched = subprocess.run(["/usr/bin/open", "-a", str(app), url], capture_output=True, timeout=15)
    if launched.returncode:
        raise SpaceError("macOS did not accept the companion launch. Open the app directly and retry.",
                         503, code="companion_launch_failed")
    return {"window_opened": True, "window_status": "launch_accepted", "page_loaded": "unverified"}


def event_result(store, args, operation):
    if operation in ("get", "status") and getattr(args, "event", None):
        return {"event": store.event(args.space, args.event)}
    if operation == "status":
        detail = store.detail(args.space)
        return {"space_id": args.space, "pending_count": detail["pending_count"],
                "events": store.events(args.space, args.after, args.status)}
    if operation == "create":
        return {"event": store.action(args.space, args.type, payload(args), args.request_id, "bot", args.expected_revision)}
    data = payload(args)
    if "result" not in data:
        raise SpaceError("Completion input requires result; patch is optional")
    return store.complete(args.space, args.event, args.expected_revision,
                          data["result"], data.get("patch"), operation == "fail")


def health_summary(store, probe=False):
    spaces = store.spaces()
    counts = {}
    for item in spaces:
        counts[item["bot_id"]] = counts.get(item["bot_id"], 0) + 1
    bots = [{"id": bot["id"], "name": bot["name"],
             "space_count": counts.get(bot["id"], 0)} for bot in store.bots()]
    service = {"status": "not_checked"}
    if probe:
        class NoRedirect(HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None
        try:
            port = int(os.environ.get("GROK_SPACES_PORT", "8789"))
            if not 1024 <= port <= 65535:
                raise ValueError()
            opener = build_opener(ProxyHandler({}), NoRedirect())
            with opener.open("http://127.0.0.1:{}/health".format(port), timeout=0.3) as response:
                data = json.loads(response.read(16385))
            if (data.get("app") == "grok-spaces" and data.get("installation_id") == store.info()["installation_id"]
                    and Path(data.get("data_dir", "")).expanduser().resolve() == store.data_dir):
                service = {"status": "running", "version": data.get("version", "unknown"),
                           "version_matches_cli": data.get("version") == VERSION}
            else:
                service = {"status": "different_or_unverified_installation"}
        except (OSError, ValueError, TypeError, AttributeError, RecursionError):
            service = {"status": "not_running"}
    status = "attention" if (service["status"] == "different_or_unverified_installation"
                              or service.get("version_matches_cli") is False) else "ready"
    summary = "Grok Space {} · local storage OK · {} Spaces · Bots: {}".format(
        VERSION, len(spaces), ", ".join(bot["name"] for bot in bots) or "none")
    if probe:
        summary += " · service " + service["status"]
        if service.get("version_matches_cli") is False:
            summary += " (version {}; differs from CLI)".format(service["version"])
    return {"status": status, "storage": "readable", "space_count": len(spaces),
            "bot_count": len(bots), "bots": bots, "service": service, "summary": summary}


def run(args):
    store = Store(args.data_dir)
    operation = args.command
    if operation == "doctor":
        installation = store.info()
        if args.expect_installation is not None and args.expect_installation != installation["installation_id"]:
            raise SpaceError("This is a different local Space installation. Use the intended data directory.", 409,
                             code="installation_mismatch", installation_id=installation["installation_id"])
        health = health_summary(store, probe=args.summary)
        result = dict(app="grok-spaces", version=VERSION, build=BUILD_NUMBER,
                      summary=health["summary"], health=health,
                      machine=socket.gethostname(), data_dir=str(store.data_dir),
                    **installation, capabilities=capabilities(), interface=describe(store, args.space),
                    companion_app_available=companion_app() is not None,
                    verification={"local_store": "readable", "expected_installation":
                                  "matched" if args.expect_installation else "not_requested",
                                  "grok_bot_round_trip": "unverified"})
        if args.summary:
            return {key: result[key] for key in ("app", "version", "build", "summary", "health", "installation_id", "data_dir")}
        return result
    if operation == "open":
        if args.show and store.data_dir != DEFAULT_DATA.expanduser().resolve():
            raise SpaceError("--show requires the default data directory; omit --show for isolated local data.",
                             code="custom_data_dir_show_unsupported")
        bot, bot_created = resolve_bot(store, args.bot, create=args.create_bot)
        if args.icon_file:
            store.set_bot_icon(bot["id"], args.icon_file)
        if args.space:
            detail = scoped_detail(store, args.space, bot)
            created = False
        else:
            space, created = store.resolve_space(bot["id"], args.title)
            detail = store.detail(space["id"])
        window = show_space(store, bot, detail["space"]) if args.show else {"window_opened": False, "window_status": "not_requested"}
        return dict(space=detail["space"], refs=refs(store, detail["space"]), created=created,
                    bot_created=bot_created, project_dir=str(store.project_directory(detail["space"]["id"], create=True)), **window)
    if operation == "read":
        if (args.summary or args.field) and (not args.space or args.events or args.html):
            raise SpaceError("--summary/--field require --space and cannot be combined with --events or --html")
        if args.summary and args.field:
            raise SpaceError("Choose --summary or --field")
        bot = resolve_bot(store, args.bot)[0] if args.bot else None
        if (args.events or args.html) and not args.space:
            raise SpaceError("Reading HTML or events requires an explicit Space ID")
        if args.events and args.html:
            raise SpaceError("Read HTML and events separately; use --html or --events")
        if not args.events and (args.after != 0 or args.status is not None):
            raise SpaceError("Event cursors and status require --events and --space")
        if not args.space:
            return {"bots": [bot] if bot else store.bots(), "spaces": store.spaces(bot["id"] if bot else None)}
        if args.events:
            if bot:
                scoped_detail(store, args.space, bot)
            return {"space_id": args.space, "events": store.events(args.space, args.after, args.status)}
        detail = scoped_detail(store, args.space, bot)
        result = {key: detail[key] for key in ("state", "revision", "space", "pending_count")}
        if args.summary:
            names = {dict: "object", list: "array", str: "string", bool: "boolean",
                     int: "number", float: "number", type(None): "null"}
            result.pop("state")
            result["state_summary"] = {
                key: dict(type=names[type(value)], **({"count": len(value)} if isinstance(value, (dict, list, str)) else {}))
                for key, value in detail["state"].items()
            }
        elif args.field:
            missing = [key for key in args.field if key not in detail["state"]]
            if missing:
                raise SpaceError("Requested state field not found", 404, fields=missing)
            result["state"] = {key: detail["state"][key] for key in args.field}
        if args.html:
            result["view_html"] = detail["view_html"]
        return dict(result, refs=refs(store, detail["space"]), project_dir=str(store.project_directory(args.space)))
    if operation == "publish":
        if args.html_file is None and args.patch_file is None:
            raise SpaceError("Publish requires --html-file and/or --patch-file")
        patch = None
        if args.patch_file:
            args.file, args.json = args.patch_file, None
            patch = payload(args)
        if args.html_file is None:
            store.patch_state(args.space, patch, args.expected_revision, "bot")
            detail = store.detail(args.space)
        else:
            detail = store.publish(args.space, read_text(args.html_file, "HTML"), args.expected_revision, patch, "bot")
        return {"space": detail["space"], "revision": detail["revision"],
                "refs": refs(store, detail["space"]), "pending_count": detail["pending_count"]}
    if operation == "action":
        if args.action_command == "worker":
            import local_worker
            if args.worker_command == "register":
                return local_worker.register(store, args.space, args.file, args.types)
            if args.worker_command == "status":
                if args.output and not args.event:
                    raise SpaceError("--output requires --event")
                return local_worker.status(store, args.space, args.event, include_output=args.output)
            return local_worker.dispatch(store, args.space, args.event)
        return event_result(store, args, args.action_command)
    if operation == "contract":
        return describe(store, args.space)
    if operation == "bots":
        return {"bots": store.bots()}
    if operation == "add-bot":
        return {"bot": store.create_bot(args.name, args.external_id)}
    if operation == "spaces":
        return {"spaces": store.spaces(args.bot)}
    if operation == "create":
        return {"space": store.create_space(args.bot, args.title)}
    if operation == "get":
        return store.detail(args.space)
    if operation == "project":
        return {"space_id": args.space, "project_dir": str(store.project_directory(args.space, create=True))}
    if operation == "patch":
        return store.patch_state(args.space, payload(args), args.expected_revision, "bot")
    if operation == "view":
        return store.set_view(args.space, read_text(args.html_file, "HTML"), args.expected_revision, "bot")
    if operation == "events":
        return {"events": store.events(args.space, args.after, args.status)}
    if operation == "event":
        return event_result(store, args, args.event_command)
    raise SpaceError("Unknown operation")


def error_result(exc, args=None):
    status = exc.status
    details = dict(exc.details)
    code = details.pop("code", None) or {404: "not_found", 409: "conflict", 413: "input_too_large", 500: "local_storage_error"}.get(status, "invalid_input")
    recovery = [{"command": "grok-space --help", "reason": "Read the five supported command families"}]
    if args is not None and getattr(args, "space", None):
        recovery.insert(0, {"command": "read", "args": ["--space", args.space],
                            "reason": "Read current revision and reconcile before retrying; do not silently upgrade a stale write"})
    elif status in (404, 409):
        recovery.insert(0, {"command": "read", "args": [], "reason": "List local Bot and Space IDs; choose the intended explicit reference"})
    return dict(error=str(exc), status=status, code=code, recovery=recovery, **details)


def main(argv=None):
    os.umask(0o077)
    args = None
    try:
        args = parser().parse_args(normalize_legacy_action(sys.argv[1:] if argv is None else argv))
        result = run(args)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0
    except SpaceError as exc:
        print(json.dumps(error_result(exc, args), ensure_ascii=False, allow_nan=False), file=sys.stderr)
        return 2
    except (OSError, ValueError, sqlite3.Error, subprocess.TimeoutExpired):
        exc = SpaceError("Unable to read local Space data, input file or launch the companion", 500, code="local_io_error")
        print(json.dumps(error_result(exc, args)), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
