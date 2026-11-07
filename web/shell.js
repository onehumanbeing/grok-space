"use strict";
(() => {
  const el = (id) => document.getElementById(id);
  let current = null;
  let allSpaces = [];
  let allBots = [];
  let windowState = window.__GROK_SPACES_WINDOW__ || null;
  let noticeTimer = null;
  let avatarKey = "";
  let avatarGeneration = 0;
  let avatarController = null;
  const iconLimit = 256 * 1024;
  const close = (id) => { if (el(id).open) el(id).close(); };
  const show = (id) => { el("actions-menu").open = false; if (!el(id).open) el(id).showModal(); };
  const create = (mode) => { close("library-dialog"); openCreate(mode); };

  function rasterType(bytes) {
    if (bytes.length >= 8 && [137, 80, 78, 71, 13, 10, 26, 10].every((value, index) => bytes[index] === value)) return "image/png";
    if (bytes.length >= 3 && bytes[0] === 255 && bytes[1] === 216 && bytes[2] === 255) return "image/jpeg";
    if (bytes.length >= 6 && ["GIF87a", "GIF89a"].includes(String.fromCharCode(...bytes.slice(0, 6)))) return "image/gif";
    if (bytes.length >= 12 && String.fromCharCode(...bytes.slice(0, 4)) === "RIFF" && String.fromCharCode(...bytes.slice(8, 12)) === "WEBP") return "image/webp";
    return null;
  }

  function safeIconData(value) {
    if (typeof value !== "string" || value.length > Math.ceil(iconLimit / 3) * 4 + 40) return null;
    const match = /^data:(image\/(?:png|jpeg|webp|gif));base64,([A-Za-z0-9+/]+={0,2})$/.exec(value);
    if (!match) return null;
    try {
      const binary = atob(match[2]);
      if (binary.length > iconLimit) return null;
      const header = Uint8Array.from(binary.slice(0, 12), (character) => character.charCodeAt(0));
      return rasterType(header) === match[1] ? value : null;
    } catch { return null; }
  }

  async function renderAvatar(bot) {
    const name = typeof bot?.name === "string" ? bot.name.trim() : "";
    const source = typeof bot?.icon_url === "string" ? bot.icon_url : "";
    const key = JSON.stringify([bot?.id || "", name, source]);
    if (key === avatarKey) return;
    avatarKey = key;
    const generation = ++avatarGeneration;
    avatarController?.abort();
    avatarController = null;
    const image = el("bot-avatar-image");
    const fallback = el("bot-avatar-fallback");
    const showFallback = () => { image.hidden = true; image.removeAttribute("src"); fallback.hidden = false; };
    image.onload = null;
    image.onerror = null;
    showFallback();
    fallback.textContent = Array.from(name)[0] || "·";
    el("library-toggle").title = name ? name + " · 切换页面" : "切换页面";
    let data = safeIconData(source);
    if (!data && /^bot_[a-f0-9]{32}$/.test(bot?.id || "") && source === "/api/bots/" + bot.id + "/icon") {
      const controller = new AbortController();
      avatarController = controller;
      const timeout = setTimeout(() => controller.abort(), 5000);
      try {
        const response = await fetch(source, { credentials: "omit", redirect: "error", signal: controller.signal, cache: "force-cache" });
        const type = response.headers.get("content-type")?.split(";")[0].trim();
        if (!response.ok || !["image/png", "image/jpeg", "image/webp", "image/gif"].includes(type) || Number(response.headers.get("content-length")) > iconLimit) return;
        const bytes = new Uint8Array(await response.arrayBuffer());
        if (bytes.length > iconLimit || rasterType(bytes) !== type) return;
        let binary = "";
        bytes.forEach((value) => { binary += String.fromCharCode(value); });
        data = "data:" + type + ";base64," + btoa(binary);
      } catch { return; }
      finally { clearTimeout(timeout); if (avatarController === controller) avatarController = null; }
    }
    if (!data || generation !== avatarGeneration) return;
    image.onload = () => { if (generation === avatarGeneration) { image.hidden = false; fallback.hidden = true; } };
    image.onerror = () => { if (generation === avatarGeneration) showFallback(); };
    image.src = data;
  }

  function renderLibrary() {
    const list = el("library-list");
    list.replaceChildren();
    const query = el("library-search").value.trim().toLocaleLowerCase();
    let count = 0;
    allBots.forEach((bot) => {
      const spaces = allSpaces.filter((space) => space.bot_id === bot.id && (!query || space.title.toLocaleLowerCase().includes(query) || bot.name.toLocaleLowerCase().includes(query)));
      if (!spaces.length) return;
      const group = document.createElement("section");
      group.className = "library-group";
      const heading = document.createElement("p");
      heading.className = "library-bot";
      heading.textContent = bot.name;
      group.append(heading);
      spaces.forEach((space) => {
        count += 1;
        const button = document.createElement("button");
        button.type = "button";
        button.className = "library-item";
        button.classList.toggle("selected", current?.space?.id === space.id);
        const glyph = document.createElement("span");
        glyph.className = "page-glyph";
        glyph.setAttribute("aria-hidden", "true");
        const name = document.createElement("span");
        name.className = "item-name";
        name.textContent = space.title;
        button.append(glyph, name);
        if (current?.space?.id === space.id) {
          const check = document.createElement("span");
          check.className = "library-check";
          check.textContent = "✓";
          button.append(check);
        }
        button.addEventListener("click", async () => {
          close("library-dialog");
          if (current?.space?.id === space.id) return;
          try {
            if (current?.bot?.id === bot.id) await chooseSpace(space.id);
            else await chooseBot(bot.id, space.id);
          } catch (error) { notice(error.message, { error: true }); }
        });
        group.append(button);
      });
      list.append(group);
    });
    if (!count) {
      const empty = document.createElement("p");
      empty.className = "library-empty";
      empty.textContent = query ? "没有找到这个页面" : "暂无已保存的页面";
      list.append(empty);
    }
    el("library-create").textContent = current?.bot ? "＋ 新建页面" : "＋ 关联 Bot";
  }

  async function openLibrary() {
    show("library-dialog");
    el("library-search").value = "";
    allBots = current?.bots || [];
    allSpaces = current?.spaces || [];
    renderLibrary();
    try {
      const [bots, spaces] = await Promise.all([api("/api/bots"), api("/api/spaces")]);
      allBots = bots.bots || [];
      allSpaces = spaces.spaces || [];
      if (current) current.bots = allBots;
      renderLibrary();
    } catch { el("library-list").textContent = "暂时没能载入，请稍后重试。"; }
  }

  function openAdvanced(tab = "data") {
    if (!current?.space) return;
    setTab(tab);
    show("advanced-dialog");
  }

  function renderWindow() {
    const modes = el("window-modes");
    modes.replaceChildren();
    const icons = {
      companion: '<rect x="3" y="3" width="23" height="17" rx="2"/><path d="M18 3v17"/>',
      standalone: '<rect x="3" y="3" width="23" height="17" rx="2"/><path d="M3 7h23"/>',
      fullscreen: '<path d="M11 3H3v6m15-6h8v6M3 14v6h8m15-6v6h-8"/>',
    };
    [["companion", "侧窗"], ["standalone", "独立窗口"], ["fullscreen", "全屏"]].forEach(([value, label]) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "mode-button";
      button.classList.toggle("selected", windowState?.mode === value);
      button.setAttribute("aria-pressed", String(windowState?.mode === value));
      button.innerHTML = '<svg viewBox="0 0 29 23" aria-hidden="true">' + icons[value] + "</svg>";
      button.append(document.createTextNode(label));
      button.addEventListener("click", () => windowAction("mode", value));
      modes.append(button);
    });
    const displays = el("display-list");
    displays.replaceChildren();
    if (!windowState?.displays?.length) { displays.textContent = "正在读取显示器…"; return; }
    windowState.displays.forEach((display) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "display-item";
      button.classList.toggle("selected", String(windowState.display_id) === String(display.id));
      const name = document.createElement("span");
      name.textContent = display.name;
      const detail = document.createElement("small");
      detail.textContent = display.is_builtin ? "内建显示器" : display.is_main ? "主显示器" : "";
      button.append(name, detail);
      button.addEventListener("click", () => windowAction("display", display.id));
      displays.append(button);
    });
  }

  async function windowAction(action, value) {
    el("window-feedback").textContent = "";
    try {
      const response = nativeRequest("window", { action, ...(value !== undefined ? { value } : {}) });
      if (!response) return;
      const result = await response;
      if (result.window) windowState = result.window;
      renderWindow();
    } catch { el("window-feedback").textContent = "这次没能切换，请再试一次。"; }
  }

  window.SpaceShell = {
    render(model) {
      current = model;
      renderAvatar(model.bot);
      el("current-space-name").textContent = model.space?.title || "我的空间";
      el("pages-action").disabled = !model.space;
      el("advanced-action").disabled = !model.space;
      el("window-toggle").hidden = !window.webkit?.messageHandlers?.spacesNative;
      if (model.pendingCount && !model.pending && !model.loading) el("save-status").textContent = "等待 AI";
      if (el("library-dialog").open) renderLibrary();
    },
    onNotice(message, error) {
      clearTimeout(noticeTimer);
      if (!error) noticeTimer = setTimeout(() => { el("notice").hidden = true; }, 5000);
    },
  };
  el("library-toggle").addEventListener("click", openLibrary);
  el("library-search").addEventListener("input", renderLibrary);
  el("library-create").addEventListener("click", () => create(current?.bot ? "space" : "bot"));
  el("add-bot-action").addEventListener("click", () => close("library-dialog"), { capture: true });
  el("connection-action").addEventListener("click", () => show("connection-dialog"));
  el("advanced-action").addEventListener("click", () => openAdvanced());
  el("save-status").addEventListener("click", () => { if (current?.pendingCount) openAdvanced("activity"); });
  el("advanced-dialog").addEventListener("close", () => setTab("space"));
  el("pages-action").addEventListener("click", () => show("pages-dialog"));
  el("window-toggle").addEventListener("click", () => { renderWindow(); show("window-dialog"); windowAction("get"); });
  window.addEventListener("spaces-native-window", (event) => { if (event.detail && Array.isArray(event.detail.displays)) { windowState = event.detail; renderWindow(); } });
  document.querySelectorAll("[data-close]").forEach((button) => button.addEventListener("click", () => close(button.dataset.close)));
  document.querySelectorAll("dialog").forEach((dialog) => dialog.addEventListener("click", (event) => {
    if (event.target !== dialog) return;
    const box = dialog.getBoundingClientRect();
    if (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom) dialog.close();
  }));
  if (typeof model !== "undefined") window.SpaceShell.render(model);
})();
