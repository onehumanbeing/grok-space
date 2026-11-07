// Continue the isolated UI foundation run after its event/CAS probes.
async (page) => {
  const origin = globalThis.UI_TEST.origin;
  const root = globalThis.UI_TEST.root;
  if (new URL(page.url()).origin !== origin) throw new Error("Use only the isolated 18792 session.");
  const check = (value, message) => { if (!value) throw new Error(message); };
  const api = async (path) => { const result = await page.request.get(`${origin}${path}`); if (!result.ok()) throw new Error(path); return result.json(); };
  const bots = (await api("/api/bots")).bots;
  const botA = bots.find((bot) => bot.name === "UI Probe A");
  const botB = bots.find((bot) => bot.name.startsWith("UI Probe B"));
  const spaceA = (await api(`/api/spaces?bot_id=${botA.id}`)).spaces[0];
  const spaceB = (await api(`/api/spaces?bot_id=${botB.id}`)).spaces[0];
  const frame = async () => {
    await page.waitForFunction(() => { const frame = document.querySelector("#space-frame"); return frame && !frame.hidden && frame.srcdoc.length > 0; });
    const result = await (await page.locator("#space-frame").elementHandle()).contentFrame();
    await result.waitForFunction(() => window.probe?.initial);
    return result;
  };
  const passed = [];
  await page.reload();
  await page.waitForFunction(() => document.documentElement.dataset.appReady === "true");
  await page.evaluate(() => Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: async (text) => { window.__testCopied = text; } } }));
  await page.locator("#library-toggle").click();
  await page.locator("#library-search").fill(spaceA.title);
  await page.locator("#library-list").getByText(spaceA.title, { exact: true }).click();
  await page.waitForFunction((id) => document.querySelector("#space-select").value === id && !document.querySelector("#space-select").disabled, spaceA.id);
  await page.locator("#library-toggle").click();
  await page.locator("#library-search").fill(spaceB.title);
  await page.locator("#library-list").getByText(spaceB.title, { exact: true }).click();
  await page.waitForFunction((id) => document.querySelector("#space-select").value === id && !document.querySelector("#space-select").disabled, spaceB.id);
  await (await frame()).waitForFunction(() => probe.initial?.state.owner === "B");
  check(!(await (await frame()).evaluate(() => spaces.get())).state.external, "A state leaked into B.");
  await page.locator("#actions-menu > summary").click();
  await page.locator("#advanced-action").click();
  check(await page.locator("#data-view").isVisible(), "Advanced data is unavailable.");
  await page.locator('[data-close="advanced-dialog"]').click();
  passed.push("Page-switch menu, advanced panel and two Bot state isolation");

  const context = await (await frame()).evaluate(() => ({ ...spaces.context, revision: probe.snapshots.at(-1).revision }));
  await (await frame()).evaluate(({ context, wrongSpace }) => {
    parent.postMessage({ type: "spaces:patch", requestId: "wrong-space", space_id: wrongSpace, epoch: context.epoch, expected_revision: context.revision, patch: { escaped: true } }, "*");
    parent.postMessage({ type: "spaces:patch", requestId: "wrong-epoch", space_id: context.space_id, epoch: "expired-epoch", expected_revision: context.revision, patch: { escaped: true } }, "*");
  }, { context, wrongSpace: spaceA.id });
  await page.waitForTimeout(200);
  check(!(await api(`/api/spaces/${spaceB.id}`)).state.escaped && !(await api(`/api/spaces/${spaceA.id}`)).state.escaped, "An invalid bridge scope wrote data.");
  const denied = await (await frame()).evaluate((origin) => fetch(origin + "/health").then(() => false, () => true), origin);
  check(denied, "Iframe network access was not blocked.");
  passed.push("Wrong Space/epoch rejected; iframe network blocked");

  await page.goto(`${origin}/?space_id=${spaceB.id}&bot_id=${botB.id}`);
  await page.waitForFunction((id) => document.documentElement.dataset.appReady === "true" && document.querySelector("#space-select").value === id, spaceB.id);
  check(!new URL(page.url()).searchParams.has("space_id"), "Processed target query remains in URL.");
  await page.reload();
  await page.waitForFunction((id) => document.documentElement.dataset.appReady === "true" && document.querySelector("#space-select").value === id, spaceB.id);
  await page.goto(`${origin}/?space_id=${spaceB.id}&bot_id=${botA.id}`);
  await page.waitForFunction(() => document.querySelector("#notice-text").textContent.includes("does not belong"));
  check(await page.locator("#space-content").isHidden(), "An ownership mismatch displayed another Space.");
  await page.goto(`${origin}/?space_id=${spaceB.id}&bot_id=${botB.id}`);
  await page.waitForFunction(() => document.documentElement.dataset.appReady === "true");
  await frame();
  passed.push("Target link ownership and reload selection");

  await page.route(`**/api/spaces?bot_id=${botA.id}`, async (route) => { await new Promise((resolve) => setTimeout(resolve, 250)); await route.continue(); });
  await page.evaluate(({ a, b }) => { window.__race = Promise.all([chooseBot(a), chooseBot(b)]); }, { a: botA.id, b: botB.id });
  await page.evaluate(() => window.__race);
  check(await page.locator("#bot-select").inputValue() === botB.id && await page.locator("#space-select").inputValue() === spaceB.id, "An older navigation response replaced the latest Bot.");
  await page.unroute(`**/api/spaces?bot_id=${botA.id}`);
  passed.push("Out-of-order navigation generation keeps the latest Bot");

  const requests = [];
  const collect = (request) => { if (request.url().includes("/api/")) requests.push(request.url()); };
  page.on("request", collect);
  await page.waitForTimeout(2400);
  check(requests.some((url) => new URL(url).pathname === `/api/spaces/${spaceB.id}`), "Current Space did not poll.");
  check(!requests.some((url) => new URL(url).pathname === `/api/spaces/${spaceA.id}`), "Inactive Space was polled.");
  await page.evaluate(() => Object.defineProperty(document, "hidden", { configurable: true, get: () => true }));
  await page.evaluate(() => document.dispatchEvent(new Event("visibilitychange")));
  requests.length = 0;
  await page.waitForTimeout(6600);
  check(requests.length === 0, "Hidden-document polling continued.");
  await page.evaluate(() => { delete document.hidden; document.dispatchEvent(new Event("visibilitychange")); });
  await page.waitForTimeout(100);
  await page.evaluate(() => window.dispatchEvent(new CustomEvent("spaces-native-visibility", { detail: { active: false } })));
  requests.length = 0;
  await page.waitForTimeout(6600);
  check(requests.length === 0, "Native-inactive polling continued.");
  await page.evaluate(() => window.dispatchEvent(new CustomEvent("spaces-native-visibility", { detail: { active: true } })));
  await page.waitForTimeout(100);
  check(requests.some((url) => new URL(url).pathname === `/api/spaces/${spaceB.id}`), "Native resume did not immediately refresh the current Space.");
  page.off("request", collect);
  passed.push("Only active Space polls; document/native pause gates stop HTTP and resume immediately (simulated visibility signals)");
  for (const width of [320, 420]) {
    await page.setViewportSize({ width, height: 800 });
    check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `Horizontal overflow at ${width}px`);
    await page.screenshot({ path: `${root}/output/playwright/foundation-final-${width}.png`, scale: "css" });
  }
  return { passed, boundary: "Isolated Chromium + local CLI result probe; native clipboard and true OS backgrounding are separate acceptance." };
}
