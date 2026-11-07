"use strict";
const frame = document.querySelector("#preview-frame");
let detail, sdk, scope;
const fingerprints = new Map();
const snapshot = () => ({ state: detail.state, revision: detail.revision, space: { id: detail.space.id, bot_id: detail.space.bot_id, title: detail.space.title } });
const cursor = (value) => Math.max(0, ...(value.events || []).map((event) => event.seq || 0));
const send = (message) => { if (scope) frame.contentWindow.postMessage({ ...message, space_id: scope.space_id, epoch: scope.epoch }, "*"); };
const state = (requestId) => send({ type: "spaces:state", requestId, ...snapshot() });
function showError(message) { const el = document.querySelector("#error"); el.hidden = !message; el.textContent = message; }
async function api(path, method = "GET", body) {
  const response = await fetch(path, { method, cache: "no-store", headers: { "Content-Type": "application/json" }, ...(body ? { body: JSON.stringify(body) } : {}) });
  const value = await response.json();
  if (!response.ok) { const error = new Error(value.error || "Preview request failed"); Object.assign(error, value, { status: response.status }); throw error; }
  return value;
}
function apply(value) {
  if (detail && (value.revision < detail.revision || cursor(value) < cursor(detail))) return;
  detail = value;
  state();
  (detail.events || []).forEach((event) => {
    const fingerprint = JSON.stringify(event);
    if (fingerprints.get(event.id) !== fingerprint) { fingerprints.set(event.id, fingerprint); send({ type: "spaces:event", event }); }
  });
}
function mount() {
  scope = { space_id: detail.space.id, epoch: crypto.randomUUID() };
  fingerprints.clear();
  const config = JSON.stringify(scope).replace(/</g, "\\u003c");
  const source = sdk.replace(/<\/script/gi, "<\\/script");
  const policy = "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; connect-src 'none'; frame-src 'none'; worker-src 'none'; object-src 'none'; form-action 'none'; base-uri 'none'";
  frame.srcdoc = '<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="' + policy + '"><script>window.__SPACES_CONTEXT__=' + config + ';<\/script><script>' + source + '<\/script></head><body>' + detail.view_html + '</body></html>';
}
frame.addEventListener("load", () => { if (detail) state(); });
window.addEventListener("message", async (event) => {
  const message = event.data;
  if (!scope || event.source !== frame.contentWindow || !message || message.space_id !== scope.space_id || message.epoch !== scope.epoch || typeof message.requestId !== "string" || !message.requestId || message.requestId.length > 200) return;
  if (!["spaces:get", "spaces:patch", "spaces:action", "spaces:event:get", "spaces:events"].includes(message.type)) return;
  const active = scope;
  const reply = (value) => { if (scope === active) send({ ...value, requestId: message.requestId }); };
  const base = "/api/spaces/" + scope.space_id;
  try {
    if (message.type === "spaces:get") { state(message.requestId); return; }
    if (["spaces:patch", "spaces:action"].includes(message.type) && (!Number.isInteger(message.expected_revision) || message.expected_revision < 0)) throw Object.assign(new Error("expected_revision is required"), { status: 400 });
    if (message.type === "spaces:patch") {
      await api(base + "/state", "PATCH", { patch: message.patch, expected_revision: message.expected_revision });
      const value = await api(base);
      if (scope !== active) return;
      apply(value); state(message.requestId);
    } else if (message.type === "spaces:action") {
      const value = await api(base + "/actions", "POST", { type: message.action, payload: message.payload || {}, request_id: message.requestId, expected_revision: message.expected_revision });
      const latest = await api(base);
      if (scope !== active) return;
      apply(latest); reply({ type: "spaces:result", ...value });
    } else if (message.type === "spaces:event:get") {
      if (!/^event_[a-f0-9]{32}$/.test(message.event_id || "")) throw Object.assign(new Error("Invalid event ID"), { status: 400 });
      reply({ type: "spaces:event:result", ...await api(base + "/events/" + message.event_id) });
    } else {
      const query = new URLSearchParams();
      if (message.after !== undefined) { if (!Number.isInteger(message.after) || message.after < 0) throw Object.assign(new Error("Invalid event cursor"), { status: 400 }); query.set("after", message.after); }
      if (message.status !== undefined) { if (!["pending", "completed", "failed"].includes(message.status)) throw Object.assign(new Error("Invalid event status"), { status: 400 }); query.set("status", message.status); }
      reply({ type: "spaces:events:result", ...await api(base + "/events?" + query) });
    }
  } catch (error) {
    if (scope !== active) return;
    if (error.status === 409) { try { apply(await api(base)); } catch { /* Keep the original conflict. */ } }
    reply({ type: "spaces:error", error: error.message, status: error.status || 0, code: error.code, revision: error.revision, current_revision: error.current_revision ?? error.revision ?? detail.revision, expected_revision: error.expected_revision });
  }
});
document.querySelector("#width").addEventListener("change", (event) => {
  frame.style.width = event.target.value + "px";
  document.querySelector("#dimensions").textContent = event.target.value + " CSS px";
});
document.querySelector("#reload").addEventListener("click", async () => {
  try { apply(await api("/api/preview/reload", "POST", {})); mount(); showError(""); }
  catch (error) { showError(error.message); }
});
(async () => {
  try { sdk = await (await fetch("/space-sdk.js")).text(); apply(await api("/api/preview")); mount(); document.documentElement.dataset.previewReady = "true"; }
  catch (error) { showError(error.message); }
})();
