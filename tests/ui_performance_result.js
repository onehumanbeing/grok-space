// Phase 2: after CLI completes __conditionalAction without a state patch.
async (page) => {
  const action = await page.evaluate(() => window.__conditionalAction);
  if (!action) throw new Error("Run ui_performance.js and complete its action through the isolated CLI first.");
  const frame = await (await page.locator("#space-frame").elementHandle()).contentFrame();
  await frame.waitForFunction((eventId) => probe.events.some((event) => event.id === eventId && event.status === "completed" && event.result?.conditionalProbe === true), action.eventId);
  const snapshot = await frame.evaluate(() => spaces.get());
  if (snapshot.revision !== action.revision) throw new Error("Result-only completion changed the state revision.");
  if (await frame.evaluate(() => probe.instance) !== action.instance) throw new Error("Result-only completion rebuilt the page.");
  return { passed: "Conditional polling discovers result-only completion at the same state revision without rebuilding HTML", revision: snapshot.revision, eventId: action.eventId };
}
