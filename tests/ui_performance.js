// Phase 1: inspect stable conditional polling; finish with ui_performance_result.js.
async (page) => {
  const origin = globalThis.UI_TEST.origin;
  if (new URL(page.url()).origin !== origin) throw new Error("Use the isolated UI service.");
  await page.reload();
  await page.waitForFunction(() => document.documentElement.dataset.appReady === "true");
  await page.evaluate(() => Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: async () => {} } }));
  const frame = await (await page.locator("#space-frame").elementHandle()).contentFrame();
  await frame.waitForFunction(() => window.probe?.initial);
  const created = await frame.evaluate(async () => {
    const snapshot = await spaces.get();
    const action = await spaces.action("request", { instruction: "Conditional poll result probe" }, { expected_revision: snapshot.revision, requestId: `conditional-${Date.now()}` });
    return { spaceId: snapshot.space.id, eventId: action.event.id, revision: snapshot.revision, instance: probe.instance };
  });
  await page.waitForFunction((eventId) => model.events.some((event) => event.id === eventId), created.eventId);
  const full = await page.request.get(`${origin}/api/spaces/${created.spaceId}`);
  await page.evaluate(() => {
    window.__stableChanges = 0;
    window.__stableObserver = new MutationObserver(() => { window.__stableChanges += 1; });
    window.__stableObserver.observe(document.querySelector("#space-content"), { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ["srcdoc"] });
  });
  let stable;
  for (let count = 0; count < 3; count += 1) {
    const response = await page.waitForResponse((response) => new URL(response.url()).pathname === `/api/spaces/${created.spaceId}` && new URL(response.url()).searchParams.has("known_revision"));
    if ((await response.json()).unchanged === true) { stable = response; break; }
  }
  if (!stable) throw new Error("Stable polling did not return an unchanged response.");
  const stableBytes = Number(stable.headers()["content-length"]);
  const fullBytes = Number(full.headers()["content-length"]);
  if (!(stableBytes < fullBytes / 5)) throw new Error("The unchanged response is not materially smaller.");
  const changes = await page.evaluate(() => { window.__stableObserver.disconnect(); return window.__stableChanges; });
  if (changes !== 0) throw new Error("A stable poll redrew the Space content.");
  await page.evaluate((action) => { window.__conditionalAction = action; }, created);
  return { ...created, stableBytes, fullBytes, contentMutations: changes };
}
