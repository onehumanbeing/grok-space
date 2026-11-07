# Minimal author starter

This is an infrastructure example, not a product page or a required design.
Copy and replace it with the application the user requested. It demonstrates
one saved text value, strict revision checks and an action/result subscription.
The layout starts at 420 CSS px, uses one column and readable 16px body text,
and keeps controls at least 44px high.

## Preview before publishing

From the repository root, with Python 3.9+:

```sh
./scripts/preview-space --html examples/minimal/index.html --state examples/minimal/state.json
```

Open the printed loopback URL in a browser. The preview uses the real author
SDK and a disposable store. It supports get/patch/action/event/events and both
subscriptions. Every action returns an explicitly marked JSON mock response:
**no Python handler, model, shell command or device operation runs**.
Use its width selector to inspect 320, 360, 420, 768 and 1280 CSS px. Reload HTML
rereads the source file and retains this run's state. Browser reload also keeps
this run's state; stopping the process discards it. The preview never selects an
installed app's database and does not publish anything.

## Publish to an explicitly selected Space

Ask the owning Bot to run `grok-space open --bot BOT_ID --space SPACE_ID`.
Copy the example files into that returned `project_dir`, then adapt them.
Read the actual current revision before publishing; replace the placeholders:

```sh
grok-space read --space SPACE_ID --summary
grok-space publish --space SPACE_ID --expected-revision REVISION --html-file /absolute/project/index.html --patch-file /absolute/project/state.json
```

Use the initial state patch only for a new Space or an intended data change.
For a visual-only update, omit `--patch-file` to preserve the user's saved data.
On 409, read and reconcile the edit; do not automatically upgrade its revision.

The preview deliberately does not run `handler.py`. To test real deterministic
execution in an authorized development Space, inspect the file and register
only its example action type:

```sh
grok-space action worker register --space SPACE_ID --file /absolute/project/handler.py --type example.run
```

This handler uses Python's standard library and returns an uppercase message
plus an optional state patch. Registration trusts local code to execute as the
current OS user; it is not a device or account permission. See the
[worker contract](../../docs/local-workers.md) and
[SDK/layout contract](../../web/README.md). Never test real device controls
merely to inspect a layout.
