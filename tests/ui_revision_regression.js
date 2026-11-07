// Deterministic stale-response and strict SDK regression, isolated port only.
async (page) => {
  const origin = globalThis.UI_TEST.origin;
  if (new URL(page.url()).origin !== origin) throw new Error("Use the isolated UI service.");
  const check = (value, message) => { if (!value) throw new Error(message); };
  await page.reload();
  await page.waitForFunction(() => document.documentElement.dataset.appReady === "true");
  const child = await (await page.locator("#space-frame").elementHandle()).contentFrame();
  await child.waitForFunction(() => window.probe?.initial);
  check(await child.evaluate(async () => { try { await spaces.patch({ omittedRevision: true }); return false; } catch (error) { return error.status === 400; } }), "Missing SDK revision did not reject locally.");
  const snapshot = await child.evaluate(() => spaces.get());
  await child.evaluate((revision) => spaces.action("request", { instruction: "Sequence regression fixture" }, { expected_revision: revision }), snapshot.revision);
  await page.evaluate(() => window.dispatchEvent(new CustomEvent("spaces-native-visibility", { detail: { active: false } })));
  const update = await page.request.patch(`${origin}/api/spaces/${snapshot.space.id}/state`, { data: { patch: { recoveryProbe: true }, expected_revision: snapshot.revision } });
  check(update.ok(), "External concurrent edit failed.");
  const recovered = await child.evaluate(async (revision) => {
    try { await spaces.patch({ shouldNotSave: true }, { expected_revision: revision }); return { rejected: false }; }
    catch (error) { return { rejected: error.status === 409, current_revision: error.current_revision, expected_revision: error.expected_revision, snapshot: await spaces.get() }; }
  }, snapshot.revision);
  check(recovered.rejected && recovered.snapshot.state.recoveryProbe && recovered.snapshot.revision > snapshot.revision && !recovered.snapshot.state.shouldNotSave, "Conflict recovery kept serving the old cached snapshot.");
  check(recovered.current_revision === recovered.snapshot.revision && recovered.expected_revision === snapshot.revision, "The SDK conflict error omitted the actual and expected revisions.");
  await page.evaluate(() => window.dispatchEvent(new CustomEvent("spaces-native-visibility", { detail: { active: true } })));
  const response = await page.request.get(`${origin}/api/spaces/${snapshot.space.id}`);
  const old = await response.json();
  const victim = old.events.find((event) => event.status === "pending");
  check(victim, "A pending test event is needed for the sequence regression.");
  const newer = structuredClone(old);
  const completed = newer.events.find((event) => event.id === victim.id);
  completed.status = "completed";
  completed.result = { sequenceProbe: true };
  newer.pending_count -= 1;
  newer.events.push({ seq: Math.max(...newer.events.map((event) => event.seq)) + 1, id: "event_ffffffffffffffffffffffffffffffff", space_id: snapshot.space.id, type: "action.completed", payload: { event_id: victim.id, status: "completed", result: completed.result }, actor: "bot", status: "completed", created_at: new Date().toISOString() });
  const guarded = await page.evaluate(({ old, newer, eventId }) => {
    applyDetail(newer);
    const acceptedOld = applyDetail(old);
    return { acceptedOld, status: model.events.find((event) => event.id === eventId)?.status, pending: model.pendingCount };
  }, { old, newer, eventId: victim.id });
  check(guarded.acceptedOld === false && guarded.status === "completed" && guarded.pending === newer.pending_count, "An older same-revision response rolled back the event result.");
  // Discard synthetic response ordering data; storage was never modified by it.
  await page.reload();
  await page.waitForFunction(() => document.documentElement.dataset.appReady === "true");
  return { passed: ["Missing expected_revision rejects with 400", "409 recovery reads fresh state before SDK rejection", "Same-revision older event sequence cannot roll back completion or pending count"], boundary: "Response-order regression is synthetic in the isolated parent DOM; the earlier local CLI completion test validates persistence separately." };
}
