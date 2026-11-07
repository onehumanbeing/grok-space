/* Grok Spaces author SDK. Injected by the host before the page's own scripts. */
(() => {
  "use strict";
  const context = Object.freeze({ ...window.__SPACES_CONTEXT__ });
  const pending = new Map();
  const subscribers = new Set();
  const eventSubscribers = new Set();
  let snapshot = null;

  function request(type, fields = {}, requestId = crypto.randomUUID()) {
    if (typeof requestId !== "string" || !requestId || requestId.length > 200) return Promise.reject(new Error("requestId must be a nonempty string of at most 200 characters."));
    if (pending.has(requestId)) return Promise.reject(new Error("This requestId is already in flight."));
    return new Promise((resolve, reject) => {
      const timeout = setTimeout(() => { pending.delete(requestId); reject(new Error("The Space host did not respond. The selected Space may have changed.")); }, 15000);
      pending.set(requestId, { resolve, reject, timeout });
      try { parent.postMessage({ ...fields, type, requestId, space_id: context.space_id, epoch: context.epoch }, "*"); }
      catch (error) { clearTimeout(timeout); pending.delete(requestId); reject(error); }
    });
  }

  function notify(listeners, value) {
    listeners.forEach((listener) => { try { listener(structuredClone(value)); } catch (error) { console.error("Space subscriber failed", error); } });
  }

  addEventListener("message", (event) => {
    const message = event.data;
    if (event.source !== parent || !message || message.space_id !== context.space_id || message.epoch !== context.epoch) return;
    if (message.type === "spaces:state" && Number.isInteger(message.revision)) {
      if (!snapshot || message.revision >= snapshot.revision) {
        const changed = !snapshot || snapshot.revision !== message.revision || JSON.stringify(snapshot.state) !== JSON.stringify(message.state);
        snapshot = { state: structuredClone(message.state), revision: message.revision, space: structuredClone(message.space) };
        if (changed) notify(subscribers, snapshot);
      }
    }
    if (message.type === "spaces:event" && message.event) notify(eventSubscribers, message.event);
    const waiting = pending.get(message.requestId);
    if (!waiting) return;
    if (!["spaces:state", "spaces:result", "spaces:events:result", "spaces:event:result", "spaces:error"].includes(message.type)) return;
    clearTimeout(waiting.timeout);
    pending.delete(message.requestId);
    if (message.type === "spaces:error") {
      const error = new Error(message.error || "Space request failed.");
      error.status = message.status || 0;
      for (const key of ["revision", "current_revision", "expected_revision"]) {
        if (Number.isInteger(message[key]) && message[key] >= 0) error[key] = message[key];
      }
      if (typeof message.code === "string") error.code = message.code;
      waiting.reject(error);
    } else if (message.type === "spaces:state") waiting.resolve({ state: structuredClone(message.state), revision: message.revision, space: structuredClone(message.space) });
    else if (message.type === "spaces:event:result") waiting.resolve(structuredClone(message.event));
    else {
      const { type, requestId, space_id, epoch, ...result } = message;
      waiting.resolve(structuredClone(result));
    }
  });

  function revision(options) {
    const value = options.expected_revision;
    if (!Number.isInteger(value) || value < 0) {
      const error = new Error("Provide expected_revision from the snapshot used to prepare this edit.");
      error.status = 400;
      throw error;
    }
    return value;
  }

  window.spaces = Object.freeze({
    context,
    get: () => request("spaces:get"),
    patch: (patch, options = {}) => {
      try { return request("spaces:patch", { patch, expected_revision: revision(options) }, options.requestId); }
      catch (error) { return Promise.reject(error); }
    },
    action: (action, payload = {}, options = {}) => {
      try { return request("spaces:action", { action, payload, expected_revision: revision(options) }, options.requestId); }
      catch (error) { return Promise.reject(error); }
    },
    events: (options = {}) => request("spaces:events", { ...(options.after !== undefined ? { after: options.after } : {}), ...(options.status !== undefined ? { status: options.status } : {}) }, options.requestId),
    event: (eventId) => request("spaces:event:get", { event_id: eventId }),
    subscribe: (listener) => {
      if (typeof listener !== "function") throw new TypeError("A subscriber must be a function.");
      subscribers.add(listener);
      if (snapshot) listener(structuredClone(snapshot));
      return () => subscribers.delete(listener);
    },
    subscribeEvents: (listener) => {
      if (typeof listener !== "function") throw new TypeError("A subscriber must be a function.");
      eventSubscribers.add(listener);
      return () => eventSubscribers.delete(listener);
    },
  });
  addEventListener("pagehide", () => {
    pending.forEach(({ reject, timeout }) => { clearTimeout(timeout); reject(new Error("This Space page was unloaded.")); });
    pending.clear();
  });
})();
