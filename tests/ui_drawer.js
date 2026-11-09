// Side drawer, per-Space Bot choice and rebinding. Runs after ui_redesign in the
// same isolated store, which already holds two Bots.
async (page) => {
  const origin = globalThis.UI_TEST.origin;
  const passed = [];
  const check = (value, message) => { if (!value) throw new Error(message); };
  const api = async (path) => (await page.request.get(origin + path)).json();
  const bots = (await api("/api/bots")).bots;
  check(bots.length >= 2, "Expected two Bots from the redesign fixture.");
  const [first, second] = bots;
  await page.setViewportSize({ width: 420, height: 800 });
  await page.goto(origin);
  await page.waitForFunction(() => document.documentElement.dataset.appReady === "true");
  check(!await page.locator("#library-dialog").isVisible(), "Drawer is visible while closed.");
  await page.locator("#library-toggle").click();
  const box = await page.locator("#library-dialog").boundingBox();
  check(box.x === 0 && box.height >= 790, "Drawer is not a full-height left panel.");
  const groups = new Set((await api("/api/spaces")).spaces.map((space) => space.bot_id)).size;
  await page.waitForFunction((count) => document.querySelectorAll(".library-bot").length === count, groups);
  passed.push("Full-height left drawer grouped by Bot");

  await page.locator("#library-create").click();
  check(await page.locator("#create-bot-field").isVisible(), "Create dialog has no Bot choice.");
  const target = await page.locator("#create-bot").inputValue() === first.id ? second : first;
  await page.locator("#create-bot").selectOption(target.id);
  await page.locator("#create-name").fill("抽屉测试页面");
  await page.locator("#create-submit").click();
  await page.waitForFunction(() => !document.querySelector("#create-dialog").open);
  await page.waitForFunction(() => document.querySelector("#current-space-name").textContent === "抽屉测试页面");
  const created = (await api("/api/spaces")).spaces.find((space) => space.title === "抽屉测试页面");
  check(created.bot_id === target.id, "New Space was not attached to the chosen Bot.");
  passed.push("New Space connects to the chosen Bot");

  const other = target.id === first.id ? second : first;
  await page.locator("#library-toggle").click();
  const row = page.locator(".library-row").filter({ hasText: "抽屉测试页面" });
  await row.hover();
  await row.locator(".rebind-button").click();
  await page.locator("#rebind-bot").selectOption(other.id);
  await page.locator("#rebind-form .dialog-submit").click();
  await page.waitForFunction(() => !document.querySelector("#rebind-dialog").open);
  const moved = (await api("/api/spaces")).spaces.find((space) => space.id === created.id);
  check(moved.bot_id === other.id, "Rebind did not move the Space.");
  await page.waitForFunction(() => document.querySelector("#current-space-name").textContent === "抽屉测试页面");
  passed.push("Existing Space rebinds to another Bot and stays selected");

  await page.mouse.move(4, 400); await page.mouse.down(); await page.mouse.move(120, 405); await page.mouse.up();
  await page.waitForFunction(() => document.querySelector("#library-dialog").open);
  await page.keyboard.press("Escape");
  passed.push("Edge swipe opens the drawer");
  return { passed };
}
