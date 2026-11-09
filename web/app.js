"use strict";

const $ = (id) => document.getElementById(id);
const isObject = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
const uniqueId = () => crypto.randomUUID();
const model = {
  bots: [], spaces: [], bot: null, space: null, state: {}, revision: 0,
  events: [], pendingCount: 0, viewHTML: "", tab: "space", loading: false,
  pending: 0, navigation: 0, frameScope: null, frameDirty: true, sdkSource: "",
  polling: false, catalogPolling: false, noticeAction: null, createMode: "bot",
  activityFilter: "all", pendingEvents: [], pendingLoading: false,
  changeEpoch: 0, booting: false, pollTimer: null, catalogTimer: null, backgroundControllers: new Set(),
};
const nativeRequests = new Map();

function remember(key, value) { try { localStorage.setItem(`grok-spaces.${key}`, value); } catch { /* The server persists Space data. */ } }
function remembered(key) { try { return localStorage.getItem(`grok-spaces.${key}`); } catch { return null; } }
function selectionScope() { return model.space ? { spaceId: model.space.id, navigation: model.navigation } : null; }
function currentSelection(scope) { return scope && scope.navigation === model.navigation && model.space?.id === scope.spaceId && !model.loading && !model.booting; }
function current(scope) { return currentSelection(scope) && (!scope.epoch || model.frameScope === scope); }
function revisionOf(detail) { return Number(detail.revision ?? detail.space?.revision ?? 0); }
function eventSequence(events) { return Math.max(0, ...(events || []).map((event) => Number(event.seq) || 0)); }
function errorWithStatus(message, status, details = {}) {
  const error = new Error(message); error.status = status;
  for (const key of ["revision", "current_revision", "expected_revision"]) {
    if (Number.isInteger(details[key]) && details[key] >= 0) error[key] = details[key];
  }
  if (typeof details.code === "string") error.code = details.code;
  return error;
}
function backgroundPaused() { return document.hidden || window.__GROK_SPACES_HOST_ACTIVE__ === false; }
function connection(online) {
  $("connection").className = `connection ${online ? "online" : "offline"}`;
  $("connection").querySelector("span").textContent = online ? "Local" : "Offline";
}

async function api(path, options = {}) {
  const { background = false, ...requestOptions } = options;
  if (background && backgroundPaused()) throw new DOMException("Background polling is paused.", "AbortError");
  const controller = new AbortController();
  if (background) model.backgroundControllers.add(controller);
  const timeout = setTimeout(() => controller.abort(), 10000);
  try {
    const response = await fetch(path, { ...requestOptions, cache: "no-store", credentials: "same-origin", signal: controller.signal, headers: { "Content-Type": "application/json", ...requestOptions.headers } });
    let data;
    try { data = await response.json(); } catch { data = {}; }
    if (!response.ok) throw errorWithStatus(typeof data.error === "string" ? data.error : data.message || `Request failed (${response.status})`, response.status, data);
    connection(true);
    return data;
  } catch (error) {
    if (controller.signal.aborted && controller.signal.reason === "background-pause") throw new DOMException("Background polling is paused.", "AbortError");
    if (!error.status) connection(false);
    if (error.name === "AbortError") throw new Error("The local service did not respond. Check that Grok Spaces is running.");
    throw error;
  } finally { clearTimeout(timeout); model.backgroundControllers.delete(controller); }
}

function notice(message, { error = false, action = null, label = "重试" } = {}) {
  $("notice").hidden = false;
  $("notice").classList.toggle("error", error);
  $("notice-text").textContent = message;
  $("notice-action").hidden = !action;
  $("notice-action").textContent = label;
  model.noticeAction = action;
  window.SpaceShell?.onNotice(message, error);
}

window.addEventListener("spaces-native-result", (event) => {
  const result = event.detail;
  const request = result && nativeRequests.get(result.id);
  if (!request) return;
  clearTimeout(request.timeout);
  nativeRequests.delete(result.id);
  if (result.ok || result.cancelled) request.resolve(result);
  else request.reject(new Error(result.error || "The native operation failed."));
});

function nativeRequest(type, fields) {
  const bridge = window.webkit?.messageHandlers?.spacesNative;
  if (!bridge) return null;
  return new Promise((resolve, reject) => {
    const id = uniqueId();
    const timeout = setTimeout(() => { nativeRequests.delete(id); reject(new Error("The native operation did not respond.")); }, type === "export" ? 300000 : 10000);
    nativeRequests.set(id, { resolve, reject, timeout });
    try { bridge.postMessage({ type, id, ...fields }); }
    catch (error) { clearTimeout(timeout); nativeRequests.delete(id); reject(error); }
  });
}

async function copyText(text) {
  const native = nativeRequest("copy", { text });
  if (native) { await native; return; }
  try { if (navigator.clipboard?.writeText) { await navigator.clipboard.writeText(text); return; } } catch { /* Fall back in an ordinary browser. */ }
  const field = document.createElement("textarea");
  field.value = text;
  field.setAttribute("readonly", "");
  field.style.cssText = "position:fixed;left:-9999px;top:0";
  document.body.append(field);
  field.select();
  const copied = document.execCommand("copy");
  field.remove();
  if (!copied) throw new Error("暂时无法复制，请再试一次。");
}

function renderControls() {
  const busy = model.booting || model.loading || model.pending > 0;
  const botSelect = $("bot-select");
  const spaceSelect = $("space-select");
  botSelect.replaceChildren();
  spaceSelect.replaceChildren();
  if (!model.bots.length) botSelect.add(new Option("No bot selected", ""));
  model.bots.forEach((bot) => botSelect.add(new Option(bot.name || "Untitled bot", bot.id)));
  if (model.bot) botSelect.value = model.bot.id;
  if (!model.spaces.length) spaceSelect.add(new Option(model.loading ? "Loading…" : "No Spaces yet", ""));
  model.spaces.forEach((space) => spaceSelect.add(new Option(space.title || "Untitled Space", space.id)));
  if (model.space) spaceSelect.value = model.space.id;
  botSelect.disabled = busy || !model.bots.length;
  spaceSelect.disabled = busy || !model.spaces.length;
  $("add-space").disabled = busy || !model.bot;
  $("add-bot-action").disabled = busy;
  ["setup-action", "import-action", "export-action", "copy-data", "empty-setup", "empty-import"].forEach((id) => { $(id).disabled = busy || !model.space; });
  $("refresh-action").disabled = busy;
  $("save-status").textContent = model.booting || model.loading ? "载入中…" : model.pending ? "正在保存…" : "已保存";
  $("save-status").classList.toggle("unsaved", busy);
  $("tabs-row").hidden = !model.space;
  $("space-content").hidden = !model.space;
  $("workspace-empty").hidden = Boolean(model.space);
  $("empty-create").disabled = busy;
  $("empty-title").textContent = model.booting || model.loading ? "正在打开…" : model.bot ? "这个 Bot 暂无页面" : "尚未选择页面";
  $("empty-description").textContent = model.booting || model.loading ? "正在载入上次保存的内容。" : "在 Grok Bot 现有聊天中继续，页面会保存在这里。";
  $("empty-create").textContent = model.bot ? "新建页面" : "关联 Bot";
  window.SpaceShell?.render(model);
}

function renderData() {
  $("state-json").textContent = JSON.stringify(model.state, null, 2);
  $("data-revision").textContent = `版本 ${model.revision}`;
}

function eventLabel(event) {
  if (event.type === "request" || event.type === "ask_bot") return "交给 AI 的请求";
  if (event.type === "state.updated") return event.actor === "bot" ? "AI 更新了内容" : "内容已保存";
  if (event.type === "view.updated") return "页面已更新";
  if (event.type === "action.completed") return "AI 已完成请求";
  if (event.type === "action.failed") return "AI 未能完成请求";
  return String(event.type || "Action").replace(/[._-]/g, " ");
}

function renderEvents() {
  const pending = model.pendingCount;
  $("pending-count").hidden = !pending;
  $("pending-count").textContent = pending > 99 ? "99+" : String(pending);
  const list = $("activity-list");
  list.replaceChildren();
  const toolbar = document.createElement("div");
  toolbar.className = "activity-toolbar";
  [ ["all", "最近"], ["pending", `待处理（${pending}）`] ].forEach(([filter, label]) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "text-button";
    button.classList.toggle("selected", model.activityFilter === filter);
    button.textContent = label;
    button.addEventListener("click", () => { model.activityFilter = filter; renderEvents(); if (filter === "pending") loadPendingEvents(); });
    toolbar.append(button);
  });
  list.append(toolbar);
  const events = model.activityFilter === "pending" ? model.pendingEvents : model.events;
  if (!events.length) {
    const empty = document.createElement("p");
    empty.className = "activity-empty";
    empty.textContent = model.pendingLoading ? "正在载入…" : model.activityFilter === "pending" ? "现在没有待处理的请求。" : "这里会留下页面的每一次进展。";
    list.append(empty);
  }
  [...events].reverse().forEach((event) => {
    const row = document.createElement("div");
    row.className = "activity-item";
    const title = document.createElement("span");
    title.className = "event-title";
    title.textContent = eventLabel(event);
    const status = document.createElement("span");
    status.className = `event-status ${event.status || ""}`;
    status.textContent = event.status === "pending" ? "等待 AI" : event.status === "completed" ? "已完成" : event.status === "failed" ? "未完成" : "";
    const time = document.createElement("span");
    time.className = "event-time";
    const date = new Date(event.created_at);
    time.textContent = Number.isNaN(date.getTime()) ? "" : date.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
    row.append(title, status, time);
    const instruction = event.payload?.instruction || event.payload?.message;
    const result = event.result ?? event.payload?.result;
    [instruction, result == null ? null : typeof result === "string" ? result : JSON.stringify(result)].filter(Boolean).forEach((value) => {
      const text = document.createElement("div");
      text.className = "event-detail";
      text.textContent = String(value);
      row.append(text);
    });
    list.append(row);
  });
  if (model.activityFilter === "pending" && pending > events.length && !model.pendingLoading) {
    const more = document.createElement("p");
    more.className = "panel-hint";
    more.textContent = `Showing ${events.length} of ${pending} pending actions. The bot can query the remaining events.`;
    list.append(more);
  }
}

function clearFrame() {
  model.frameScope = null;
  model.frameDirty = true;
  const frame = document.createElement("iframe");
  frame.id = "space-frame";
  frame.title = "Space page";
  frame.setAttribute("sandbox", "allow-scripts");
  frame.referrerPolicy = "no-referrer";
  frame.hidden = true;
  $("space-frame").replaceWith(frame);
}

function stateMessage(scope, requestId) {
  return { type: "spaces:state", requestId, space_id: scope.spaceId, epoch: scope.epoch, state: model.state, revision: model.revision, space: { id: model.space.id, bot_id: model.space.bot_id, title: model.space.title } };
}

function postToFrame(scope, message) {
  if (!current(scope)) return;
  $("space-frame").contentWindow?.postMessage({ ...message, space_id: scope.spaceId, epoch: scope.epoch }, "*");
}

function broadcastState() { if (current(model.frameScope)) postToFrame(model.frameScope, stateMessage(model.frameScope)); }
function broadcastEvent(event) {
  const scope = model.frameScope;
  if (!current(scope)) return;
  const fingerprint = JSON.stringify(event);
  if (scope.notified.get(event.id) === fingerprint) return;
  scope.notified.set(event.id, fingerprint);
  if (scope.notified.size > 500) scope.notified.delete(scope.notified.keys().next().value);
  postToFrame(scope, { type: "spaces:event", event });
}

async function relayChangedEvents(events, scope, background = false) {
  if (!current(scope)) return;
  const changedOriginals = new Set(events.filter((event) => !["action.completed", "action.failed"].includes(event.type)).map((event) => event.id));
  for (const event of events) {
    if (!current(scope)) return;
    if (["action.completed", "action.failed"].includes(event.type) && event.payload?.event_id) {
      if (changedOriginals.has(event.payload.event_id)) continue;
      try {
        const result = await api(`/api/spaces/${encodeURIComponent(scope.spaceId)}/events/${encodeURIComponent(event.payload.event_id)}`, { background });
        if (current(scope)) broadcastEvent(result.event);
      } catch { /* The author can still read the immutable completion audit with events(). */ }
    } else broadcastEvent(event);
  }
}

function renderPage() {
  const hasHTML = Boolean(model.viewHTML);
  $("page-empty").hidden = hasHTML;
  $("space-frame").hidden = !hasHTML;
  if (!hasHTML) { clearFrame(); return; }
  model.frameDirty = true;
  if (model.tab !== "space" || !model.sdkSource || model.loading || model.booting) return;
  clearFrame();
  const scope = { spaceId: model.space.id, epoch: uniqueId(), navigation: model.navigation, notified: new Map() };
  model.frameScope = scope;
  const frame = $("space-frame");
  frame.hidden = false;
  const policy = "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; font-src 'none'; connect-src 'none'; media-src 'none'; frame-src 'none'; worker-src 'none'; object-src 'none'; form-action 'none'; base-uri 'none'";
  const config = JSON.stringify({ space_id: scope.spaceId, epoch: scope.epoch }).replace(/</g, "\\u003c");
  const sdk = model.sdkSource.replace(/<\/script/gi, "<\\/script");
  const html = `<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="${policy}"><meta name="viewport" content="width=device-width,initial-scale=1"><script>window.__SPACES_CONTEXT__=${config};<\/script><script>${sdk}<\/script></head><body>${model.viewHTML}</body></html>`;
  frame.addEventListener("load", () => { if (current(scope)) broadcastState(); });
  requestAnimationFrame(() => { if (current(scope)) { frame.srcdoc = html; model.frameDirty = false; } });
}

function applyDetail(detail, { refreshPage = false, background = false } = {}) {
  if (model.space?.id === detail.space.id && revisionOf(detail) < model.revision) return false;
  if (model.space?.id === detail.space.id && eventSequence(detail.events) < eventSequence(model.events)) return false;
  const previousScope = model.frameScope;
  const oldEvents = new Map(model.events.map((event) => [event.id, JSON.stringify(event)]));
  const changedPage = model.viewHTML !== (detail.view_html || "") || model.space?.id !== detail.space.id;
  model.space = detail.space;
  model.state = isObject(detail.state) ? detail.state : {};
  model.revision = revisionOf(detail);
  model.viewHTML = detail.view_html || "";
  model.events = Array.isArray(detail.events) ? detail.events : [];
  model.pendingCount = Number(detail.pending_count ?? model.events.filter((event) => event.status === "pending").length);
  renderControls();
  renderData();
  renderEvents();
  if (changedPage || refreshPage) renderPage();
  else broadcastState();
  if (previousScope && previousScope === model.frameScope) relayChangedEvents(model.events.filter((event) => oldEvents.get(event.id) !== JSON.stringify(event)), previousScope, background);
  return true;
}

function beginNavigation() {
  model.navigation += 1;
  model.loading = true;
  model.space = null;
  model.state = {};
  model.events = [];
  model.pendingEvents = [];
  model.pendingCount = 0;
  model.viewHTML = "";
  $("notice").hidden = true;
  clearFrame();
  renderControls();
  return model.navigation;
}

async function loadSpace(spaceId, token) {
  if (!spaceId) return;
  const detail = await api(`/api/spaces/${encodeURIComponent(spaceId)}`);
  if (token !== model.navigation) return;
  model.loading = false;
  applyDetail(detail, { refreshPage: true });
  remember(`space.${model.bot.id}`, spaceId);
}

async function chooseSpace(spaceId) {
  const token = beginNavigation();
  try { await loadSpace(spaceId, token); }
  finally { if (token === model.navigation) { model.loading = false; renderControls(); } }
}

async function chooseBot(botId, preferredSpaceId) {
  const token = beginNavigation();
  const bot = model.bots.find((entry) => entry.id === botId) || null;
  model.bot = bot;
  model.spaces = [];
  renderControls();
  try {
    if (!bot) return;
    const data = await api(`/api/spaces?bot_id=${encodeURIComponent(bot.id)}`);
    if (token !== model.navigation) return;
    model.spaces = data.spaces || [];
    remember("bot", bot.id);
    const saved = remembered(`space.${bot.id}`);
    if (preferredSpaceId && !model.spaces.some((entry) => entry.id === preferredSpaceId)) throw new Error("没有找到这个页面，请重新选择。");
    await loadSpace(preferredSpaceId || model.spaces.find((entry) => entry.id === saved)?.id || model.spaces[0]?.id, token);
  } finally { if (token === model.navigation) { model.loading = false; renderControls(); } }
}

async function openRequestedSpace() {
  const query = new URLSearchParams(location.search);
  if (!query.has("space_id") && !query.has("bot_id")) return false;
  const spaceId = query.get("space_id");
  const requestedBotId = query.get("bot_id");
  if (query.getAll("space_id").length !== 1 || !/^space_[a-f0-9]{32}$/.test(spaceId || "") || (requestedBotId !== null && (query.getAll("bot_id").length !== 1 || !/^bot_[a-f0-9]{32}$/.test(requestedBotId)))) throw new Error("The requested Space link is invalid.");
  const token = beginNavigation();
  try {
    const [bots, detail] = await Promise.all([api("/api/bots"), api(`/api/spaces/${encodeURIComponent(spaceId)}`)]);
    if (token !== model.navigation) return true;
    if (requestedBotId && requestedBotId !== detail.space.bot_id) throw new Error("This Space does not belong to the bot in the link.");
    const bot = (bots.bots || []).find((entry) => entry.id === detail.space.bot_id);
    if (!bot) throw new Error("The bot for this Space was not found locally.");
    model.bots = bots.bots || [];
    model.bot = bot;
    const spaces = await api(`/api/spaces?bot_id=${encodeURIComponent(bot.id)}`);
    if (token !== model.navigation) return true;
    model.spaces = spaces.spaces || [];
    if (!model.spaces.some((entry) => entry.id === spaceId)) throw new Error("The requested Space was not found under this bot.");
    model.loading = false;
    applyDetail(detail, { refreshPage: true });
    remember("bot", bot.id);
    remember(`space.${bot.id}`, spaceId);
    query.delete("space_id");
    query.delete("bot_id");
    history.replaceState(null, "", `${location.pathname}${query.size ? `?${query}` : ""}${location.hash}`);
    return true;
  } finally { if (token === model.navigation) { model.loading = false; renderControls(); } }
}

async function refreshCatalog({ reloadCurrent = false, background = false } = {}) {
  if (model.catalogPolling || model.loading || model.pending || (background && backgroundPaused())) return;
  model.catalogPolling = true;
  const token = model.navigation;
  const botId = model.bot?.id;
  const spaceId = model.space?.id;
  try {
    const data = await api("/api/bots", { background });
    if (token !== model.navigation) return;
    if (background && backgroundPaused()) return;
    model.bots = data.bots || [];
    if (!botId) {
      const saved = remembered("bot");
      const bot = model.bots.find((entry) => entry.id === saved) || model.bots[0];
      if (bot) await chooseBot(bot.id);
      else renderControls();
      return;
    }
    const spaces = await api(`/api/spaces?bot_id=${encodeURIComponent(botId)}`, { background });
    if (token !== model.navigation || model.bot?.id !== botId) return;
    model.spaces = spaces.spaces || [];
    model.bot = model.bots.find((entry) => entry.id === botId) || model.bot;
    renderControls();
    if (!spaceId && model.spaces.length) await chooseSpace(model.spaces[0].id);
    else if (reloadCurrent && spaceId) {
      const readEpoch = model.changeEpoch;
      const detail = await api(`/api/spaces/${encodeURIComponent(spaceId)}`, { background });
      if (token === model.navigation && model.space?.id === spaceId && readEpoch === model.changeEpoch && !model.pending) applyDetail(detail, { background });
    }
  } finally { model.catalogPolling = false; }
}

async function loadPendingEvents(background = false) {
  if (!model.space || model.pendingLoading || (background && backgroundPaused())) return;
  const token = model.navigation;
  const spaceId = model.space.id;
  const readEpoch = model.changeEpoch;
  model.pendingLoading = true;
  try {
    const data = await api(`/api/spaces/${encodeURIComponent(spaceId)}/events?status=pending`, { background });
    if (token === model.navigation && model.space?.id === spaceId && readEpoch === model.changeEpoch && !model.pending) model.pendingEvents = data.events || [];
  } catch (error) { if (token === model.navigation && error.name !== "AbortError") notice(error.message, { error: true }); }
  finally { model.pendingLoading = false; if (token === model.navigation) renderEvents(); }
}

async function poll() {
  if (backgroundPaused() || model.loading || model.pending || model.polling || !model.space) return;
  model.polling = true;
  const token = model.navigation;
  const spaceId = model.space.id;
  const readEpoch = model.changeEpoch;
  try {
    const query = new URLSearchParams({ known_revision: String(model.revision), known_seq: String(eventSequence(model.events)) });
    const detail = await api(`/api/spaces/${encodeURIComponent(spaceId)}?${query}`, { background: true });
    if (token === model.navigation && model.space?.id === spaceId && !model.pending && readEpoch === model.changeEpoch) {
      if (detail.unchanged === true) return;
      applyDetail(detail, { background: true });
      if (model.tab === "activity" && model.activityFilter === "pending") await loadPendingEvents(true);
    }
  } catch { /* The connection indicator reports outages; polling recovers. */ }
  finally { model.polling = false; }
}

async function mutate(scope, endpoint, method, body) {
  if (!current(scope)) throw errorWithStatus("This page is no longer the selected Space.", 409);
  model.changeEpoch += 1;
  model.pending += 1;
  renderControls();
  try {
    const result = await api(`/api/spaces/${encodeURIComponent(scope.spaceId)}/${endpoint}`, { method, body: JSON.stringify(body) });
    if (!current(scope)) throw errorWithStatus("The selected Space changed before the response arrived.", 409);
    return result;
  } finally { model.pending -= 1; renderControls(); }
}

async function patchState(scope, patch, expectedRevision) {
  if (!Number.isInteger(expectedRevision) || expectedRevision < 0) throw errorWithStatus("expected_revision must be the revision used to prepare this patch.", 400);
  if (expectedRevision !== model.revision) throw errorWithStatus(`The saved data changed: current revision is ${model.revision}. Read and reconcile before saving.`, 409,
    { code: "revision_conflict", revision: model.revision, current_revision: model.revision, expected_revision: expectedRevision });
  const result = await mutate(scope, "state", "PATCH", { patch, expected_revision: expectedRevision, actor: "user" });
  model.state = result.state;
  model.revision = Number(result.revision);
  model.space.revision = model.revision;
  if (result.event) model.events = [...model.events.filter((entry) => entry.id !== result.event.id), result.event].slice(-50);
  renderData();
  renderEvents();
  broadcastState();
  return stateMessage(scope);
}

async function recordAction(scope, type, payload, requestId, expectedRevision, { copy = false } = {}) {
  if (!Number.isInteger(expectedRevision) || expectedRevision < 0) throw errorWithStatus("expected_revision is required.", 400);
  const result = await mutate(scope, "actions", "POST", { type, payload, request_id: requestId, expected_revision: expectedRevision });
  if (result.event) {
    model.events = [...model.events.filter((event) => event.id !== result.event.id), result.event].slice(-50);
    renderEvents();
    broadcastEvent(result.event);
  }
  const readEpoch = model.changeEpoch;
  api(`/api/spaces/${encodeURIComponent(scope.spaceId)}`).then((detail) => {
    if (current(scope) && readEpoch === model.changeEpoch && !model.pending) applyDetail(detail);
  }).catch(() => {});
  if (copy && result.delivery === "manual" && result.prompt && result.event?.status === "pending" && current(scope)) {
    copyText(result.prompt).then(() => {
      if (current(scope)) notice("已复制，粘贴到当前 Bot 的聊天里就好。");
    }).catch((error) => {
      if (current(scope)) notice(`${error.message} The action is saved and awaiting the bot.`, { error: true });
    });
  }
  return result;
}

window.addEventListener("message", async (event) => {
  const scope = model.frameScope;
  if (!current(scope) || event.source !== $("space-frame").contentWindow || !isObject(event.data)) return;
  const message = event.data;
  if (!["spaces:get", "spaces:patch", "spaces:action", "spaces:events", "spaces:event:get"].includes(message.type)) return;
  if (message.space_id !== scope.spaceId || message.epoch !== scope.epoch) return;
  const requestId = message.requestId;
  if (typeof requestId !== "string" || !requestId || requestId.length > 200) return;
  const reply = (data) => postToFrame(scope, { ...data, requestId });
  try {
    if (message.type === "spaces:get") reply(stateMessage(scope));
    else if (message.type === "spaces:patch") {
      if (!isObject(message.patch) || JSON.stringify(message.patch).length > 256000) throw errorWithStatus("Provide a state patch object smaller than 256 KB.", 400);
      reply(await patchState(scope, message.patch, message.expected_revision));
    } else if (message.type === "spaces:action") {
      if (typeof message.action !== "string" || !message.action || message.action.length > 100) throw errorWithStatus("Provide an action name.", 400);
      if (!isObject(message.payload || {}) || JSON.stringify(message.payload || {}).length > 64000) throw errorWithStatus("Provide an action payload smaller than 64 KB.", 400);
      const result = await recordAction(scope, message.action, message.payload || {}, requestId, message.expected_revision, { copy: true });
      reply({ type: "spaces:result", ...result });
    } else if (message.type === "spaces:events") {
      const query = new URLSearchParams();
      if (message.after !== undefined) { if (!Number.isInteger(message.after) || message.after < 0) throw errorWithStatus("after must be a nonnegative sequence number.", 400); query.set("after", message.after); }
      if (message.status !== undefined) { if (!["pending", "completed", "failed"].includes(message.status)) throw errorWithStatus("Unknown event status.", 400); query.set("status", message.status); }
      const result = await api(`/api/spaces/${encodeURIComponent(scope.spaceId)}/events?${query}`);
      reply({ type: "spaces:events:result", ...result });
    } else {
      if (typeof message.event_id !== "string" || !/^event_[a-f0-9]{32}$/.test(message.event_id)) throw errorWithStatus("Provide a valid event ID.", 400);
      const result = await api(`/api/spaces/${encodeURIComponent(scope.spaceId)}/events/${encodeURIComponent(message.event_id)}`);
      reply({ type: "spaces:event:result", ...result });
    }
  } catch (error) {
    if (error.status === 409 && current(scope)) {
      const readEpoch = model.changeEpoch;
      try {
        const detail = await api(`/api/spaces/${encodeURIComponent(scope.spaceId)}`);
        if (current(scope) && readEpoch === model.changeEpoch && !model.pending) applyDetail(detail);
      } catch { /* A failed recovery read must not hide the original conflict. */ }
    }
    const details = {};
    for (const key of ["revision", "current_revision", "expected_revision"]) {
      if (Number.isInteger(error[key])) details[key] = error[key];
    }
    if (error.code) details.code = error.code;
    reply({ type: "spaces:error", error: error.message, status: error.status || 0, ...details });
    if (error.status === 409 && current(scope)) { broadcastState(); notice("内容刚刚更新，旧的改动没有覆盖新内容。请重新试一次。", { error: true }); }
  }
});

function setTab(tab) {
  model.tab = tab;
  ["space", "data", "activity"].forEach((name) => {
    $(`${name}-view`).hidden = name !== "space" && name !== tab;
    $(`${name}-tab`).setAttribute("aria-selected", String(name === tab));
    $(`${name}-tab`).tabIndex = name === tab ? 0 : -1;
  });
  if (tab === "space" && model.frameDirty && model.space) renderPage();
  if (tab === "activity" && model.activityFilter === "pending") loadPendingEvents();
}

async function copySetup() {
  if (!model.space || model.loading) return;
  const spaceId = model.space.id;
  const token = model.navigation;
  $("actions-menu").open = false;
  try {
    const result = await api(`/api/spaces/${encodeURIComponent(spaceId)}/setup-prompt`, { method: "POST", body: "{}" });
    if (token !== model.navigation || model.space?.id !== spaceId) return;
    await copyText(result.prompt);
    if (token === model.navigation) notice("已复制创作指令，粘贴到当前 Bot 的聊天里就好。");
  } catch (error) { if (token === model.navigation) notice(error.message, { error: true }); }
}

function openCreate(mode) {
  model.createMode = mode;
  const bot = mode === "bot";
  $("dialog-title").textContent = bot ? "和哪个 Bot 一起创作？" : "新建页面";
  $("dialog-description").textContent = bot ? "填入你在 Grok Bot 里使用的名字，之后这个 Bot 的页面会放在一起。" : "给这个新想法起个名字，AI 会帮你把它做出来。";
  $("create-name-label").textContent = bot ? "Bot 名称" : "页面名称";
  $("create-name").placeholder = bot ? "例如：黑123" : "例如：我的工作台";
  $("create-name").value = "";
  $("external-id").value = "";
  $("external-id-field").hidden = !bot;
  $("create-bot-field").hidden = bot || model.bots.length < 2;
  $("create-bot").replaceChildren(...model.bots.map((entry) => new Option(entry.name, entry.id, false, entry.id === model.bot?.id)));
  $("create-submit").textContent = bot ? "继续" : "创建页面";
  $("create-error").hidden = true;
  $("actions-menu").open = false;
  $("create-dialog").showModal();
  $("create-name").focus();
}

async function importHTML(file) {
  if (!file || !model.space || model.loading || model.pending) return;
  if (!/\.html?$/i.test(file.name) && file.type !== "text/html") throw new Error("请选择一个 HTML 页面文件。");
  if (file.size > 1024 * 1024) throw new Error("这个文件有些大，请使用 1 MB 以内的页面。");
  const spaceId = model.space.id;
  const token = model.navigation;
  const expectedRevision = model.revision;
  model.changeEpoch += 1;
  model.pending += 1;
  renderControls();
  try {
    const html = await file.text();
    if (token !== model.navigation) return;
    const detail = await api(`/api/spaces/${encodeURIComponent(spaceId)}/view`, { method: "PUT", body: JSON.stringify({ html, expected_revision: expectedRevision }) });
    if (token !== model.navigation || model.space?.id !== spaceId) return;
    if (detail.space) applyDetail(detail);
    else applyDetail(await api(`/api/spaces/${encodeURIComponent(spaceId)}`));
    setTab("space");
    notice("页面已经放进来了。");
  } finally { model.pending -= 1; renderControls(); }
}

async function exportSpace() {
  $("actions-menu").open = false;
  const text = JSON.stringify({ space: model.space, state: model.state, view_html: model.viewHTML, revision: model.revision, events: model.events }, null, 2);
  const filename = `${model.space.title.replace(/[^a-z0-9_-]/gi, "-") || "space"}.json`;
  const native = nativeRequest("export", { text, filename });
  if (native) { const result = await native; if (result.ok) notice("已导出页面副本。"); return; }
  const url = URL.createObjectURL(new Blob([text], { type: "application/json" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

$("create-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const name = $("create-name").value.trim();
  if (!name) return;
  $("create-submit").disabled = true;
  try {
    if (model.createMode === "bot") {
      const result = await api("/api/bots", { method: "POST", body: JSON.stringify({ name, bot_id: $("external-id").value.trim() }) });
      model.bots.push(result.bot);
      await chooseBot(result.bot.id);
    } else {
      const currentBotId = model.bot.id;
      const botId = $("create-bot").value || currentBotId;
      const result = await api("/api/spaces", { method: "POST", body: JSON.stringify({ bot_id: botId, title: name }) });
      if (model.bot?.id !== currentBotId) return;
      if (botId !== currentBotId) await chooseBot(botId, result.space.id);
      else { model.spaces.push(result.space); await chooseSpace(result.space.id); }
    }
    $("create-dialog").close();
  } catch (error) { $("create-error").textContent = error.message; $("create-error").hidden = false; }
  finally { $("create-submit").disabled = false; }
});

["space", "data", "activity"].forEach((tab) => $(`${tab}-tab`).addEventListener("click", () => setTab(tab)));
document.querySelector(".view-tabs").addEventListener("keydown", (event) => {
  if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
  event.preventDefault();
  const tabs = ["data", "activity"];
  setTab(tabs[(tabs.indexOf(model.tab) + 1) % 2]);
  $(`${model.tab}-tab`).focus();
});
$("bot-select").addEventListener("change", () => chooseBot($("bot-select").value).catch((error) => notice(error.message, { error: true })));
$("space-select").addEventListener("change", () => chooseSpace($("space-select").value).catch((error) => notice(error.message, { error: true })));
$("add-bot-action").addEventListener("click", () => openCreate("bot"));
$("add-space").addEventListener("click", () => openCreate("space"));
$("empty-create").addEventListener("click", () => openCreate(model.bot ? "space" : "bot"));
$("dialog-close").addEventListener("click", () => $("create-dialog").close());
$("notice-dismiss").addEventListener("click", () => { $("notice").hidden = true; });
$("notice-action").addEventListener("click", () => model.noticeAction?.());
$("setup-action").addEventListener("click", copySetup);
$("empty-setup").addEventListener("click", copySetup);
$("copy-data").addEventListener("click", () => copyText(JSON.stringify(model.state, null, 2)).then(() => notice("已复制保存的数据。")).catch((error) => notice(error.message, { error: true })));
$("refresh-action").addEventListener("click", () => { $("actions-menu").open = false; refreshCatalog({ reloadCurrent: true }).catch((error) => notice(error.message, { error: true })); });
$("export-action").addEventListener("click", () => exportSpace().catch((error) => notice(error.message, { error: true })));
function chooseHTMLFile() { $("actions-menu").open = false; $("html-file").click(); }
$("empty-import").addEventListener("click", chooseHTMLFile);
$("import-action").addEventListener("click", chooseHTMLFile);
$("html-file").addEventListener("change", () => { importHTML($("html-file").files[0]).catch((error) => notice(error.message, { error: true })); $("html-file").value = ""; });
let dragDepth = 0;
$("space-canvas").addEventListener("dragenter", (event) => { event.preventDefault(); dragDepth += 1; $("drop-overlay").hidden = false; });
$("space-canvas").addEventListener("dragover", (event) => { event.preventDefault(); event.dataTransfer.dropEffect = "copy"; });
$("space-canvas").addEventListener("dragleave", () => { dragDepth -= 1; if (dragDepth <= 0) $("drop-overlay").hidden = true; });
$("space-canvas").addEventListener("drop", (event) => { event.preventDefault(); dragDepth = 0; $("drop-overlay").hidden = true; importHTML(event.dataTransfer.files[0]).catch((error) => notice(error.message, { error: true })); });
document.addEventListener("click", (event) => { if (!$("actions-menu").contains(event.target)) $("actions-menu").open = false; });
function syncPolling(refresh = false) {
  if (backgroundPaused() || model.booting || document.documentElement.dataset.appReady !== "true") {
    clearInterval(model.pollTimer);
    clearInterval(model.catalogTimer);
    model.pollTimer = null;
    model.catalogTimer = null;
    model.backgroundControllers.forEach((controller) => controller.abort("background-pause"));
    return;
  }
  if (!model.pollTimer) model.pollTimer = setInterval(poll, 2000);
  if (!model.catalogTimer) model.catalogTimer = setInterval(() => refreshCatalog({ background: true }).catch(() => {}), 6000);
  if (refresh) { poll(); refreshCatalog({ background: true }).catch(() => {}); }
}
document.addEventListener("visibilitychange", () => syncPolling(true));
window.addEventListener("spaces-native-visibility", (event) => {
  if (typeof event.detail?.active !== "boolean") return;
  window.__GROK_SPACES_HOST_ACTIVE__ = event.detail.active;
  syncPolling(true);
});
window.addEventListener("beforeunload", (event) => { if (model.pending) { event.preventDefault(); event.returnValue = ""; } });

async function boot() {
  document.documentElement.dataset.appReady = "false";
  model.booting = true;
  renderControls();
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 10000);
  try {
    const response = await fetch("/space-sdk.js", { cache: "no-store", signal: controller.signal });
    if (!response.ok) throw new Error("The Space SDK could not be loaded.");
    model.sdkSource = await response.text();
    clearTimeout(timeout);
    if (!await openRequestedSpace()) await refreshCatalog();
    model.booting = false;
    renderControls();
    if (model.space && model.frameDirty) renderPage();
    document.documentElement.dataset.appReady = "true";
    syncPolling();
  } catch (error) { model.booting = false; renderControls(); notice(error.name === "AbortError" ? "The Space SDK did not load. Check that the local service is running." : error.message, { error: true, action: boot, label: "Reconnect" }); }
  finally { clearTimeout(timeout); }
}
boot();
