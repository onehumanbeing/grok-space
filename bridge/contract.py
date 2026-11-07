"""The stable foundation contract given to the existing Grok Bot."""
from pathlib import Path

LAYOUT_GUIDANCE = """Design for the actual iframe CSS viewport, not the Mac screen. The default companion is about 420 CSS px wide; allow vertical scrolling and adapt when the window resizes. Do not shrink the whole application with zoom/transform, a fixed desktop min-width, or tiny text. Use readable body text (about 16px), essential labels at least 13px, and click targets about 44px; the visible icon itself may be smaller. Diagram-specific user zoom is allowed.
At narrow widths, prioritize the current task and reveal secondary controls progressively; avoid fixed many-column grids and oversized headers. Preserve the user's requested visual concept rather than turning every page into a list. For spatial dashboards, a compact overview plus a selected room/area and readable device controls can retain the floor plan without squeezing every control into it. Wider windows may show overview and detail together, using the same saved state and actions.
Actually render-check at 320, 420, 768 and 1280 CSS px before calling the layout verified: readable text, usable targets, no unintended horizontal scroll/clipping, and the current task's controls easy to reach. Check available height too. If rendering tools are unavailable, report the unverified sizes instead of claiming media queries prove usability. Validate device-control feedback without triggering real device actions during preparation."""


def capabilities():
    """Implementation facts, separate from account/device verification."""
    return {
        "local_store": {"available": True, "persistence": "SQLite", "revision_checks": True},
        "authoring": {"self_contained_html": True, "atomic_html_and_state": True,
                      "state_only_publish": True, "business_runtime": "authored by the existing Grok Bot"},
        "actions": {"durable": True, "idempotent": True, "results": "any finite JSON value",
                    "delivery": "registered local worker; otherwise manual", "automatic_bot_wake": False,
                    "local_worker": "CLI-registered project-local Python; host does not invoke a model, handler code may; trusted code, not an OS sandbox"},
        "companion": {"launch": "explicit open --show; default database only",
                      "launch_acknowledgement": "macOS accepted the request; page loading remains unverified"},
        "grok_native": {"html_publish": "unverified", "mcp_apps_account_gate": "unverified",
                        "macbook_local_execution": "unverified", "bot_round_trip": "unverified"},
    }


def describe(store, space_id=None):
    result = {
        "app": "grok-spaces", "contract_version": "1.4",
        "installation": store.info(), "data_dir": str(store.data_dir),
        "cli_path": str(Path(__file__).resolve().with_name("cli.py")),
        "purpose": "Host and persist HTML applications authored by the existing Grok Bot. No business application is built into the host.",
        "commands": {
            "doctor": "doctor [--space SPACE_ID] [--expect-installation INSTALLATION_ID] [--summary]: verify identity and interface; --summary returns compact version/Space/Bot counts and checks the matching loopback service without starting it",
            "open": "open --bot EXACT_NAME_OR_LOCAL_ID (--space SPACE_ID | --title TITLE) [--create-bot] [--show]: return stable Space metadata, refs and project_dir; --create-bot explicitly registers a local label; no state, HTML or history is returned",
            "read": "read [--bot EXACT_NAME_OR_LOCAL_ID] [--space SPACE_ID [--summary | --field KEY (repeatable) | --html | --events [--after SEQ] [--status pending|completed|failed]]]: prefer summary or selected fields for model efficiency; HTML/history are explicit",
            "publish": "publish --space SPACE_ID --expected-revision N [--html-file HTML] [--patch-file PATCH_JSON]: at least one file; HTML and state commit atomically when supplied together; returns space/revision/refs/pending_count only",
            "action": "action create --space SPACE_ID --type NAME --request-id ID --expected-revision N --file PAYLOAD_JSON; action get|status --space SPACE_ID [--event EVENT_ID]; action complete|fail --space SPACE_ID --event EVENT_ID --expected-revision N --file RESULT_JSON; action worker register --space SPACE_ID --file PROJECT_HANDLER_PY --type NAME (repeatable); action worker status|run --space SPACE_ID [--event EVENT_ID]",
        },
        "capabilities": capabilities(),
        "output": {"success": "One JSON object on stdout; help is JSON too",
                   "failure": "One JSON object on stderr, exit 2, with error/status/code/recovery; no raw argument echo",
                   "read": "Default: state, revision, space, refs, project_dir, pending_count. Prefer --summary for types/counts or repeated --field KEY for needed data. Add --html for view_html. --events returns a separate event page.",
                   "write": "open/publish return compact references and acknowledgements, not stored HTML, state or history. Read back the needed data explicitly."},
        "data": {
            "initial_state": {}, "patch": "RFC 7396 JSON Merge Patch; arrays replace; null removes a field",
            "concurrency": "Read revision before writing. A stale expected_revision is rejected with 409 plus current_revision and expected_revision. The current number is diagnostic, not permission to replay a stale write. Never rerun a side effect to resolve a result-commit conflict.",
            "publish": "HTML and optional state patch commit together under one revision. A patch-only publish preserves existing HTML.",
            "persistence": "SQLite; independent of window, HTML process and authored source files",
            "completion_input": {"result": "Any finite JSON value", "patch": "Optional JSON Merge Patch object"},
        },
        "html": {
            "entry": "One self-contained HTML file, up to 1 MiB. Bundle CSS/JS and embed images.",
            "layout": LAYOUT_GUIDANCE,
            "runtime": "Scripts-only iframe sandbox; no network, same-origin, parent DOM, filesystem or arbitrary command access",
            "sdk": "window.spaces is injected before authored HTML. Use get/patch/action/event/events and subscriptions; see web/README.md.",
            "sdk_writes": "patch(patch, {expected_revision: snapshot.revision}) and action(name, payload, {expected_revision: snapshot.revision}) require an explicit revision from the snapshot used to prepare the write. Omission is rejected; no implicit upgrade.",
            "state": "Keep durable application data in Space state, not iframe localStorage.",
        },
        "events": {
            "delivery": "Page actions dispatch to a registered local handler automatically after persistence. Unregistered types remain manual. No automatic Bot/model wake. Local worker claims are never automatically replayed.",
            "idempotency": "request_id deduplicates the same action. An identical completion is replay-safe; a different completion is refused.",
            "cursor": "after is an insertion sequence, not an update cursor. Completion also appends action.completed/action.failed with original event_id and result. Read the original event for its authoritative current status.",
            "pagination": "At most 200 events per page. Continue with after=last seq until an empty page.",
            "recent_detail": "The companion's recent detail may summarize audit patches larger than 4 KiB with bytes/hash/keys. Read --events or action get to retrieve complete event data. event_cursor covers action-only and result-only changes independently of state revision.",
        },
        "limits": {"html_bytes": 1048576, "state_bytes": 1048576,
                   "action_payload_bytes": 65536, "cli_json_bytes": 1048576,
                   "http_json_bytes": 6 * 1024 * 1024 + 64 * 1024},
        "identity": "Bot associations are explicit local labels, not authenticated Grok identities. Exact ID wins over an exact unique name; ambiguity requires selecting a candidate ID. Tools and private skills may be shared across Bots. Always pass the intended Space ID and verify the installation_id.",
        "window": "open resolves local references without showing a window by default. Explicit --show requests companion launch for the default database. Launch accepted is not proof of page loading or Grok write-back.",
    }
    if space_id is not None:
        detail = store.detail(space_id)
        result["space"] = detail["space"]
        result["project_dir"] = str(store.project_directory(space_id))
    return result
