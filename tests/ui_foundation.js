// Run through the public isolated tests/ui_run.mjs runner.
// The current Space must contain tests/ui_probe.html. Start ui_completion_driver.py
// beside this run. This is not a live Bot test.
async (page) => {
  const root = globalThis.UI_TEST.root;
  const origin = globalThis.UI_TEST.origin;
  if (new URL(page.url()).origin !== origin) throw new Error("Use only the isolated port 18792 test session.");
  const passed = [];
  const check = (value, message) => { if (!value) throw new Error(message); };
  const api = async (path, method = "GET", data) => {
    const response = await page.request.fetch(`${origin}${path}`, { method, ...(data ? { data } : {}) });
    const body = await response.json();
    if (!response.ok()) throw new Error(`${method} ${path}: ${response.status()} ${JSON.stringify(body)}`);
    return body;
  };
  const frame = async () => {
    await page.waitForFunction(() => { const frame = document.querySelector("#space-frame"); return frame && !frame.hidden && frame.srcdoc.length > 0; });
    const result = await (await page.locator("#space-frame").elementHandle()).contentFrame();
    await result.waitForFunction(() => window.probe?.initial);
    return result;
  };
  await page.waitForFunction(() => document.documentElement.dataset.appReady === "true");
  await frame();
  // Clipboard writes are stubbed in this isolated browser; native clipboard is separate acceptance.
  const stubClipboard = () => page.evaluate(() => Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: async (text) => { window.__testCopied = text; } } }));
  await stubClipboard();
  let active = await frame();
  const initial = await active.evaluate(() => spaces.get());
  const spaceA = initial.space.id;
  const botA = initial.space.bot_id;
  let detail = await api(`/api/spaces/${spaceA}`);
  const html = detail.view_html;
  const instance = await active.evaluate(() => probe.instance);

  const staleRevision = detail.revision;
  await api(`/api/spaces/${spaceA}/state`, "PATCH", { patch: { external: "retained" }, expected_revision: detail.revision });
  await active.waitForFunction(() => probe.snapshots.at(-1)?.state.external === "retained");
  check(await active.evaluate(() => probe.instance) === instance, "A state-only update rebuilt the HTML frame.");
  const rejected = await active.evaluate(async (revision) => {
    try { await spaces.patch({ forbiddenOverwrite: true }, { expected_revision: revision }); return 0; } catch (error) { return error.status; }
  }, staleRevision);
  check(rejected === 409, "Stale iframe writes must reject with 409.");
  check(!(await api(`/api/spaces/${spaceA}`)).state.forbiddenOverwrite, "A stale write reached storage.");
  passed.push("SDK state subscription, stale CAS rejection, unchanged HTML instance");

  const actionId = `ui-replay-${Date.now()}`;
  const missingRevision = await active.evaluate(async () => { try { await spaces.patch({ disallowed: true }); return 0; } catch (error) { return error.status; } });
  check(missingRevision === 400, "SDK mutation without an explicit revision was allowed.");
  const action = await active.evaluate(async (requestId) => { const snapshot = await spaces.get(); return spaces.action("request", { instruction: "Isolated event result test" }, { requestId, expected_revision: snapshot.revision }); }, actionId);
  check(action.delivery === "manual" && action.event.status === "pending", "An action must begin pending with manual delivery.");
  detail = await api(`/api/spaces/${spaceA}`);
  const oldRevision = detail.revision;
  await api(`/api/spaces/${spaceA}/state`, "PATCH", { patch: { afterAction: true }, expected_revision: detail.revision });
  const replay = await active.evaluate(({ requestId, revision }) => spaces.action("request", { instruction: "Isolated event result test" }, { requestId, expected_revision: revision }), { requestId: actionId, revision: oldRevision });
  check(replay.event.id === action.event.id, "Action replay created a different event.");
  check((await api(`/api/spaces/${spaceA}/events`)).events.filter((event) => event.request_id === actionId).length === 1, "Action replay duplicated a ledger row.");
  detail = await api(`/api/spaces/${spaceA}`);
  let revision = detail.revision;
  for (let index = 0; index < 52; index += 1) revision = (await api(`/api/spaces/${spaceA}/state`, "PATCH", { patch: { auditProbe: index }, expected_revision: revision })).revision;
  revision = (await api(`/api/spaces/${spaceA}/state`, "PATCH", { patch: { uiDriverReady: action.event.id }, expected_revision: revision })).revision;
  await active.waitForFunction((expected) => probe.snapshots.at(-1)?.revision === expected, revision);
  check(!(await api(`/api/spaces/${spaceA}`)).events.some((event) => event.id === action.event.id), "The event-result test must use an action outside the recent 50.");
  await active.waitForFunction((eventId) => probe.events.some((event) => event.id === eventId && event.status === "completed" && event.result?.verified === "local CLI only"), action.event.id, { timeout: 30000 });
  check((await api(`/api/spaces/${spaceA}`)).revision === revision, "A result-only completion unexpectedly changed the state revision.");
  const readEvent = await active.evaluate((eventId) => spaces.event(eventId), action.event.id);
  check(readEvent.status === "completed", "SDK event(id) did not return completion.");
  const eventPage = await active.evaluate((after) => spaces.events({ after }), action.event.seq);
  check(eventPage.events.some((event) => event.type === "action.completed" && event.payload.event_id === action.event.id), "The creation cursor missed completion audit.");
  passed.push("Action idempotency across revisions, result-only completion outside recent 50, event APIs");

  for (let index = 0; index < 55; index += 1) await api(`/api/spaces/${spaceA}/actions`, "POST", { type: "ui-pending-probe", payload: { index }, request_id: `${actionId}-pending-${index}`, expected_revision: revision });
  await page.waitForFunction(() => document.querySelector("#pending-count").textContent === "55");
  await page.locator("#actions-menu > summary").click();
  await page.locator("#advanced-action").click();
  await page.locator("#activity-tab").click();
  await page.getByRole("button", { name: "待处理（55）", exact: true }).click();
  await page.waitForFunction(() => document.querySelectorAll("#activity-list .event-status.pending").length === 55);
  passed.push("Authoritative pending count and pending list beyond recent 50");
  await page.locator('[data-close="advanced-dialog"]').click();

  const botB = (await api("/api/bots", "POST", { name: `UI Probe B ${Date.now()}` })).bot;
  const spaceB = (await api("/api/spaces", "POST", { bot_id: botB.id, title: "Second SDK Probe" })).space;
  await api(`/api/spaces/${spaceB.id}/view`, "PUT", { html, expected_revision: 0 });
  await api(`/api/spaces/${spaceB.id}/state`, "PATCH", { patch: { owner: "B" }, expected_revision: 1 });
  await page.locator("#library-toggle").click();
  await page.locator("#library-search").fill("Second SDK Probe");
  await page.locator("#library-list").getByText("Second SDK Probe", { exact: true }).click();
  await page.waitForFunction((spaceId) => document.querySelector("#space-select").value === spaceId && !document.querySelector("#space-select").disabled, spaceB.id);
  active = await frame();
  await active.waitForFunction(() => probe.initial?.state.owner === "B");
  check(!(await active.evaluate(() => spaces.get())).state.external, "Bot A state leaked into Bot B.");
  await page.locator("#library-toggle").click();
  await page.locator("#library-search").fill("SDK Probe");
  await page.locator("#library-list").getByText("SDK Probe", { exact: true }).click();
  await page.waitForFunction((spaceId) => document.querySelector("#space-select").value === spaceId && !document.querySelector("#space-select").disabled, spaceA);
  active = await frame();
  await active.waitForFunction(() => probe.initial?.state.external === "retained");
  check(!(await active.evaluate(() => spaces.get())).state.owner, "Bot B state leaked into Bot A.");
  passed.push("External directory discovery, real page-switch menu and two Bot isolation");

  await page.goto(`${origin}/?space_id=${spaceB.id}&bot_id=${botB.id}`);
  await page.waitForFunction((spaceId) => document.documentElement.dataset.appReady === "true" && document.querySelector("#space-select").value === spaceId, spaceB.id);
  check(!new URL(page.url()).searchParams.has("space_id"), "A processed target link was not cleared.");
  await page.reload();
  await page.waitForFunction((spaceId) => document.documentElement.dataset.appReady === "true" && document.querySelector("#space-select").value === spaceId, spaceB.id);
  await page.goto(`${origin}/?space_id=${spaceB.id}&bot_id=${botA}`);
  await page.waitForFunction(() => document.querySelector("#notice-text").textContent.includes("does not belong"));
  check(await page.locator("#space-content").isHidden(), "An invalid ownership link displayed another Space.");
  await page.goto(`${origin}/?space_id=${spaceB.id}&bot_id=${botB.id}`);
  await page.waitForFunction(() => document.documentElement.dataset.appReady === "true");
  await stubClipboard();
  active = await frame();
  await active.waitForFunction(() => probe.initial);
  passed.push("Target link ownership validation and saved selection after reload");

  const requested = [];
  const recordRequest = (request) => { if (request.url().includes("/api/")) requested.push(request.url()); };
  page.on("request", recordRequest);
  await page.waitForTimeout(2500);
  check(requested.some((url) => new URL(url).pathname === `/api/spaces/${spaceB.id}`), "The active Space was not polled.");
  check(!requested.some((url) => new URL(url).pathname === `/api/spaces/${spaceA}`), "An inactive Space was polled.");
  // Exercise the visibility guard without relying on headless OS tab behavior.
  await page.evaluate(() => Object.defineProperty(document, "hidden", { configurable: true, get: () => true }));
  requested.length = 0;
  await page.waitForTimeout(6600);
  check(requested.length === 0, "Polling continued while document.hidden was true.");
  await page.evaluate(() => { delete document.hidden; document.dispatchEvent(new Event("visibilitychange")); });
  page.off("request", recordRequest);
  passed.push("Only the active Space polls; hidden-document guard stops both polling timers (simulated visibility)");

  for (const width of [320, 420]) {
    await page.setViewportSize({ width, height: 800 });
    check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `Horizontal overflow at ${width}px.`);
    await page.screenshot({ path: `${root}/output/playwright/foundation-final-${width}.png`, scale: "css" });
  }
  return { passed, spaceA, botA, spaceB: spaceB.id, botB: botB.id, boundary: "Isolated Chromium + local CLI; no live Grok Bot, native clipboard, or real OS backgrounding verified." };
}
