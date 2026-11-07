// Prepare only the explicit empty out/ui-test service. Never use a real database.
async (page) => {
  const origin = globalThis.UI_TEST.origin;
  const root = globalThis.UI_TEST.root;
  const health = await (await page.request.get(`${origin}/health`)).json();
  if (health.app !== "grok-spaces" || health.data_dir !== globalThis.UI_TEST.dataDir) throw new Error("Refusing to seed another service.");
  if ((await (await page.request.get(`${origin}/api/bots`)).json()).bots.length) throw new Error("Use a fresh isolated out/ui-test directory.");
  const bot = (await (await page.request.post(`${origin}/api/bots`, { data: { name: "UI Probe A" } })).json()).bot;
  const space = (await (await page.request.post(`${origin}/api/spaces`, { data: { bot_id: bot.id, title: "SDK Probe" } })).json()).space;
  await page.goto(`${origin}/?space_id=${space.id}&bot_id=${bot.id}`);
  await page.waitForFunction(() => document.documentElement.dataset.appReady === "true");
  await page.locator("#html-file").setInputFiles(`${root}/tests/ui_probe.html`);
  await page.waitForFunction(() => document.querySelector("#space-frame").srcdoc.length > 0);
  const frame = await (await page.locator("#space-frame").elementHandle()).contentFrame();
  await frame.waitForFunction(() => window.probe?.initial);
  return { bot: bot.id, space: space.id, fixture: "tests/ui_probe.html" };
}
