# Space host and author SDK

This is a generic local host. It does not define the page's business model or
implement an application for the Bot. A Space starts with `{}` and no HTML.
The owning Bot writes its application in the directory returned by the local
CLI's `open --bot ID --space ID` command, then publishes its HTML and optional state patch.
The host supplies storage, revision checks, an action ledger, and a sandbox.

## Preview before publication

From a source checkout, run:

```sh
./scripts/preview-space --html examples/minimal/index.html --state examples/minimal/state.json
```

Open the printed loopback URL. Replace the file paths with your authored HTML
and optional initial JSON object. This development-only helper uses the real
`space-sdk.js` in an opaque sandbox and a newly created temporary store. Its
visible banner identifies **mock actions**. Actions return a marked JSON echo;
no handler, shell command, model or device control executes. No existing Bot,
Space or installed database is selected. This is not a sixth product CLI family.

Get, patch, action, event, events and both subscription methods are available.
Writes still require the revision actually read; missing revisions reject and
stale revisions conflict. Browser reload retains temporary state for this
process. Reload HTML rereads the authored file while retaining that state.
Stopping preview discards the store; no source/state file is written back.
Use the width selector to inspect 320, 360, 420, 768 and 1280 CSS px without
scaling the app. This is layout/protocol preparation, not proof of a real worker,
device, Bot connection or native Pages publication.

The [minimal starter](../examples/minimal/README.md) includes self-contained HTML,
initial state, a separately registered standard-library worker and the real
publish command. It is an infrastructure example, not a required design or a
business application supplied by the host.

## Layout and interaction requirements

Design for the iframe's actual **CSS viewport**, including its available height;
the Mac's screen resolution is not the page width. Start with the approximately
420px companion, allow natural vertical scrolling, and reflow when it resizes.
Use readable body text around 16px, essential labels at least 13px, and clickable
areas around 44px. Icons may look smaller than their hit areas. Never squeeze a
desktop layout into the side window by shrinking text, scaling/zooming the whole
application, or imposing a desktop `min-width`.

Give the current task priority and progressively reveal secondary controls.
Avoid fixed many-column device grids and headers that consume the working area.
This is not a requirement to turn every application into a list. Preserve the
user's requested visualization: a spatial dashboard may keep a compact overview,
let the user select a room/area, and put its readable controls nearby. The diagram
itself may support user-controlled zoom. Independent/fullscreen windows can show
overview and detail together with the same saved state and action bindings.

Before reporting layout verification, actually render the authored page at
320, 420, 768 and 1280 CSS px. Check text, hit areas, viewport height, unintended
horizontal scrolling/clipping and access to the current task's controls. Media
queries alone are not verification; report sizes that could not be rendered.
Verify pending/result feedback with a harmless fixture; layout preparation must
not trigger real device controls.

## Author API

The host injects `window.spaces` before the page's own scripts. Do not load a
remote SDK or make HTTP calls from the page. HTML, scripts and styles must be
self-contained; images can use data URLs.

```js
const snapshot = await spaces.get();
// snapshot = { state: {...}, revision: 12, space: { id, bot_id, title } }

// JSON Merge Patch: null removes a field; an array replaces the whole array.
const saved = await spaces.patch({ selectedMode: "review" }, {
  expected_revision: snapshot.revision,
});
// saved has the same shape as snapshot.

const unsubscribe = spaces.subscribe(({ state, revision, space }) => {
  render(state);
});

const request = await spaces.action("request", {
  instruction: "Review this Space and record your result.",
}, {
  expected_revision: saved.revision,
  requestId: "a-stable-unique-id-for-this-logical-action",
});
// request = { event, prompt, delivery: "manual" | "local", worker?: {...} }
// The action is persisted. "local" means a registered local handler was selected;
// observe the final event status/result rather than treating queued as completed.
// "manual" copies a prompt for the existing Grok Bot chat; clipboard success
// is separate from completion. No chat message is sent by the host.

const event = await spaces.event(request.event.id);
// event.status: pending | completed | failed; event.result contains the Bot result.

const page = await spaces.events({ after: 0, status: "pending" });
// page = { events: [...] }, at most 200, in ascending seq order.

const unsubscribeEvents = spaces.subscribeEvents((event) => {
  if (event.id === request.event.id) showResult(event.status, event.result);
});
```

- `get()` returns the host's current saved snapshot. State subscriptions receive
  a snapshot when available and subsequent state changes. They do not receive
  transient control values; the application decides what to persist.
- `patch(patch, options)` and `action(name, payload, options)` use
  **required** `options.expected_revision`. Pass the revision of the snapshot
  used to prepare the edit, especially after asynchronous work. An omitted or
  invalid revision rejects with status 400 before sending a request. The SDK
  never fills in a newer revision or retries a stale write automatically.
- A stale write rejects with `error.status === 409`. Read the latest state,
  reconcile the application-level edit, and submit a newly prepared patch.
  Do not blindly retry the original patch with a newer revision.
  When supplied, `error.current_revision`, `error.expected_revision` and
  `error.code` describe the conflict; they are not permission to promote the
  stale edit to that newer revision.
- `options.requestId` is optional. The SDK creates a UUID if absent. Reuse a
  stable request ID when retrying the **same logical action** after a timeout.
  Same ID and same payload return the same event, even if the revision later
  changes. Same ID with a different action or payload is rejected. Concurrent
  SDK requests must have distinct in-flight IDs.
- `event(id)` reads one action's current result. `events({after,status})` reads
  up to 200 ledger entries. For another page, pass the last returned `seq` as
  `after`. `after` is a **creation cursor**, not a modification cursor: completing
  an action updates its original row and creates an `action.completed` or
  `action.failed` audit entry containing `event_id`, `status`, and `result`.
- `subscribeEvents(listener)` returns an unsubscribe function. Notifications
  reflect polling of the selected Space; they are not a durable replay stream.
  Result-only completions are notified even when the state revision is unchanged.
  Use `event(id)` for authoritative status and `events()` for recovery/history.
- SDK requests time out after 15 seconds. A timeout does not prove a mutation
  failed: read back saved state or retry an action with its original request ID.
- `spaces.context` is a frozen `{space_id, epoch}`. The host replaces the iframe
  on Space changes or HTML publication. Requests and subscriptions belong to
  that frame only; an unloaded frame cannot write to the newly selected Space.

## Raw message protocol

The SDK wraps `parent.postMessage`. Raw protocol support remains available for
authors who need it. Every request must contain:

```js
const context = spaces.context;
parent.postMessage({
  type: "spaces:patch",
  requestId: "save-unique-id",
  space_id: context.space_id,
  epoch: context.epoch,
  expected_revision: snapshot.revision,
  patch: { selectedMode: "review" },
}, "*");
```

Supported requests:

| Request type | Additional fields | Correlated response |
| --- | --- | --- |
| `spaces:get` | — | `spaces:state` with `state`, `revision`, `space` |
| `spaces:patch` | `patch`, `expected_revision` | `spaces:state` |
| `spaces:action` | `action`, `payload`, `expected_revision` | `spaces:result` with `event`, `prompt`, `delivery` |
| `spaces:event:get` | `event_id` | `spaces:event:result` with `event` |
| `spaces:events` | optional `after`, `status` | `spaces:events:result` with `events` |

Errors use `spaces:error` with `error` and numeric `status`. Every response
echoes `requestId`, `space_id`, and `epoch`. Unsolicited state updates use
`spaces:state`; event notifications use `spaces:event` with `event`. The raw
action's `requestId` is also its durable `request_id` in the event ledger.

The iframe has an opaque origin. `*` is therefore necessary as the target origin.
Receivers must check `event.source === parent` plus their Space ID and epoch.
The host checks the exact iframe window, Space ID, and epoch before using the
selected Space API; it never routes based on a caller-supplied Space alone.

## Host behavior and boundaries

The host keeps a compact toolbar above the Bot-authored canvas. Page switching
is a small searchable menu; JSON and activity are inside More → Advanced.
HTML import, JSON export, connection instructions and native Pages availability
are secondary menu items. There is no built-in business page or separate AI
composer: the user continues in their existing Grok Bot chat. Bot associations
are explicit local labels, not authenticated Grok identities. Service availability
does not prove that a Bot is connected. Native Pages publication is not connected.

The optional Bot metadata field `icon_url` supplies its toolbar avatar. The host
accepts a base64 PNG/JPEG/WebP/GIF data URL (up to 256 KiB decoded), or exactly
`/api/bots/<the-current-bot-id>/icon`. It checks raster signatures and MIME types;
SVG, arbitrary paths, remote URLs, redirects and credentialed icon requests are
rejected. Missing or invalid icons use a neutral initial from the Bot name.

HTML runs with `sandbox="allow-scripts"`, without same-origin privileges.
The iframe CSP permits inline scripts/styles and data images; direct network,
external resources, nested frames and forms are blocked. The bridge exposes
only data and action APIs, without filesystem or command execution. A record in
the action ledger does not authorize external side effects.

Actions remain pending until the owning Bot saves a real completion or
failure. SDK actions persist first and return the exact context prompt;
for manual delivery the user sends it in Grok Bot. Registered local handlers
execute their allowed action types and save completion through the same ledger;
local delivery does not copy a manual prompt. The host does not send chat messages,
reconfigure accounts, or claim that clipboard success proves a Bot connection.

The optional native `spacesNative` bridge handles clipboard, file export and
the owned window's companion, standalone, fullscreen and display controls.
Only the native host's actual display list is shown. Browser-only hosts hide
these window controls. `spaces-native-window` publishes the current mode/list;
window requests wait for `spaces-native-result` with their matching ID.
The UI waits for a matching native result before displaying success. Browser
clipboard/download fallbacks are used only when the native bridge is absent.

Automatic HTTP polling pauses when the document is hidden or the native host
sets `window.__GROK_SPACES_HOST_ACTIVE__` to `false`. The native host signals
changes with `spaces-native-visibility` and `{active:boolean}` in event detail.
Pausing clears both polling timers and aborts background GETs; resuming refreshes
the selected Space and directory. Initial loading and explicit user operations
are not cancelled by this background-only gate.
