// Internal-only icon fixture. Never present this as a Bot's business page.
async (page) => {
  const origin = globalThis.UI_TEST.origin;
  if (new URL(page.url()).origin !== origin) throw new Error("Use only isolated port 18797.");
  const health = await (await page.request.get(origin + "/health")).json();
  if (!health.data_dir.includes("/out/redesign-test")) throw new Error("Wrong isolated test store.");
  await page.waitForFunction(() => document.documentElement.dataset.appReady === "true");
  const check = (value, message) => { if (!value) throw new Error(message); };
  const raster = await page.evaluate(() => {
    const canvas = document.createElement("canvas");
    canvas.width = canvas.height = 2;
    const context = canvas.getContext("2d");
    context.fillStyle = "#e02c3b";
    context.fillRect(0, 0, 2, 2);
    return canvas.toDataURL("image/png");
  });
  const botId = await page.evaluate(() => model.bot.id);
  const render = (icon) => page.evaluate((icon) => SpaceShell.render({ ...model, bot: { ...model.bot, name: "头像测试", icon_url: icon } }), icon);
  const requests = [];
  page.on("request", (request) => { if (!request.url().startsWith(origin) && !request.url().startsWith("data:")) requests.push(request.url()); });
  await render(raster);
  await page.locator("#bot-avatar-image").waitFor({ state: "visible" });
  check(await page.locator("#bot-avatar-image").evaluate((image) => image.naturalWidth === 2), "Raster data URL did not load.");
  for (const source of ["https://invalid.example/avatar.png", "//invalid.example/avatar.png", "file:///tmp/avatar.png", "data:image/svg+xml;base64,PHN2Zy8+", "data:image/png;base64,PHN2Zy8+", "data:image/png;base64," + "A".repeat(350000)]) {
    await render(source);
    check(await page.locator("#bot-avatar-image").isHidden(), "An invalid source rendered.");
    check(await page.locator("#bot-avatar-fallback").textContent() === "头", "Initial fallback is incorrect.");
  }
  const iconPath = "/api/bots/" + botId + "/icon";
  await page.route(origin + iconPath, async (route) => route.fulfill({ status: 200, contentType: "image/png", body: Buffer.from(raster.split(",")[1], "base64") }));
  await render(iconPath);
  await page.locator("#bot-avatar-image").waitFor({ state: "visible" });
  check(await page.locator("#bot-avatar-image").getAttribute("src").then((src) => src.startsWith("data:image/png;base64,")), "The local image was not converted to safe data.");
  await page.unroute(origin + iconPath);
  await render("");
  await page.route(origin + iconPath, async (route) => route.fulfill({ status: 302, headers: { location: "https://invalid.example/avatar.png" } }));
  await render(iconPath);
  await page.waitForTimeout(200);
  check(await page.locator("#bot-avatar-image").isHidden(), "A redirect must keep the fallback.");
  check(requests.length === 0, "An icon made a remote request.");
  await page.unroute(origin + iconPath);
  for (const width of [320, 420, 1280]) {
    await page.setViewportSize({ width, height: 760 });
    check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "Icon caused toolbar overflow.");
    check(await page.locator("#bot-avatar").isVisible(), "Avatar disappeared in a narrow window.");
  }
  await render("");
  return { passed: ["Raster data URL", "Neutral initial fallback", "Remote/file/SVG/signature/size rejection", "Local raster route", "Redirect blocked without remote request", "Avatar visible at 320/420/1280"], boundary: "Internal isolated Chromium fixture; the real Grok avatar is supplied separately." };
}
