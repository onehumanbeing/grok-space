# Isolated browser verification

The optional browser checks use the public Playwright library, not a private
agent skill or browser profile. Application installation does not require Node
or these development dependencies.

From any source checkout, with Node.js 20+ and Python 3.9+:

```sh
npm ci
npx playwright install chromium
npm run test:ui
```

The runner derives the checkout path from its own location, starts new loopback
services on free ports, and creates fresh test stores. It launches its own
Chromium and isolated contexts; it never attaches to a user's browser, Grok,
existing database or installed companion. It closes only the resources it
started, including after a failed assertion. No account login is required.

To run one area, or inspect a headed **owned test browser**:

```sh
npm run test:ui -- --suite=preview
npm run test:ui -- --suite=shell
npm run test:ui -- --suite=worker
npm run test:ui -- --suite=foundation
npm run test:ui -- --suite=preview --headed
```

The default is `--suite=all`. Set `UI_TEST_PYTHON` to an explicit compatible
interpreter if needed. The runner uses the system Python on macOS by default.
Playwright installation/reference: [official library documentation](https://playwright.dev/docs/library).

## Coverage and boundaries

- **Preview:** real SDK in the disposable mock preview; get/patch/action/event/
  events, subscriptions, strict revision errors, request replay, same-process
  reload, HTTP origin checks and iframe network blocking. The minimal starter
  is rendered at 320/360/420/768/1280 CSS px with readable body text and controls.
- **Shell:** real creation/import/control save/reload, page switching between
  explicit Bot labels, state isolation, advanced panels, pending actions,
  honest unconnected Pages status, connection instructions and avatar policy.
  Canvas/menu layouts are checked at 320/420/520/1280 CSS px.
- **Worker:** a fixture's actual SDK button saves an HTTP action, automatically
  executes one registered deterministic Python handler, and receives its
  committed result through the event subscription. Checks include saved filter
  retention, no iframe replacement, no manual prompt copying and reload.
  Replaying its request ID must leave both one durable claim and exactly one
  line in the owned project's execution-count file.
- **Foundation:** revision conflicts, explicit revision requirements, action
  replay, result-only completion outside recent history, pending counts,
  ownership-checked links, wrong-frame scope rejection, rapid navigation,
  stale response ordering and conditional synchronization. A separate owned CLI
  process completes one saved event; it does not pretend to be a live Bot.
  Document/native pause signals are simulated, not actual OS focus acceptance.

Clipboard writes are stubbed. Native clipboard, actual OS visibility/window
movement, live Grok authoring and business/device behavior require separate
acceptance. Internal fixtures are infrastructure tests and must never be
presented as the user's product page.

Results are written to `out/ui-results.json`; screenshots go to
`output/playwright/`. These ignored directories contain only owned test output.
Test stores remain under unique `out/ui-test-*`, `out/redesign-test-*` and
`out/worker-ui-test-*` directories for inspection. They are never reused as an
installed app's store. The preview's own temporary store is discarded on exit.

The `ui_*.js` files are trusted test functions invoked by `tests/ui_run.mjs`;
they expect its explicit `UI_TEST` context. They no longer require personal
checkout paths, fixed ports or a machine-local `playwright_cli.sh`.
