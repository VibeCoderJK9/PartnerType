import { readFileSync } from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { describe, expect, it, vi } from "vitest";

const PROJECT_ROOT = path.resolve(import.meta.dirname, "..", "..");
const SW_PATH = path.join(PROJECT_ROOT, "sw.js");
const ORIGIN = "https://app.example.test";

// Runs sw.js in its own realm with just enough of the worker global scope to
// drive its fetch handler: a fake Cache Storage and a scripted network.
function loadWorker({ network, stored = {} } = {}) {
  const listeners = {};
  const cache = { put: vi.fn(async () => {}), addAll: vi.fn(async () => {}) };
  const caches = {
    open: vi.fn(async () => cache),
    keys: vi.fn(async () => Object.keys(stored)),
    delete: vi.fn(async () => true),
    match: vi.fn(async () => undefined),
  };
  const context = vm.createContext({
    self: {
      location: new URL(`${ORIGIN}/sw.js`),
      addEventListener: (type, fn) => { listeners[type] = fn; },
      skipWaiting: vi.fn(async () => {}),
      clients: { claim: vi.fn(async () => {}) },
    },
    caches,
    fetch: vi.fn(network || (async () => okResponse())),
    URL,
    Promise,
  });
  vm.runInContext(readFileSync(SW_PATH, "utf8"), context, { filename: SW_PATH });
  return { listeners, cache, caches, context };
}

function okResponse({ headers = {}, type = "basic", ok = true } = {}) {
  const response = {
    ok,
    type,
    headers: new Headers(headers),
    clone() { return { ...response, cloned: true }; },
  };
  return response;
}

async function dispatchFetch(worker, url, { method = "GET", mode = "no-cors" } = {}) {
  let responded = null;
  const event = {
    request: { url: new URL(url, ORIGIN).href, method, mode },
    respondWith: (value) => { responded = value; },
  };
  worker.listeners.fetch(event);
  const result = responded ? await responded : undefined;
  // Let the fire-and-forget cache write settle.
  await new Promise((resolve) => setTimeout(resolve, 0));
  return { result, responded: responded !== null };
}

describe("service worker caching", () => {
  it("still caches plain static files", async () => {
    const worker = loadWorker();
    await dispatchFetch(worker, "/css/main.css");
    await dispatchFetch(worker, "/pages/admin/dashboard.html", { mode: "navigate" });
    expect(worker.cache.put).toHaveBeenCalledTimes(2);
  });

  it.each([
    "/set-password.html?token=secret-reset-token&reset=1",
    "/index.html?oauth=abcdef0123456789",
    "/pages/oauth/consent.html?req=abcdef0123456789",
    "/js/app.js?v=2",
  ])("never stores %s, whose query string may carry a secret", async (url) => {
    const worker = loadWorker();
    await dispatchFetch(worker, url, { mode: "navigate" });
    expect(worker.cache.put).not.toHaveBeenCalled();
  });

  it.each(["/api/leads", "/oauth/token", "/mcp", "/.well-known/oauth-authorization-server"])(
    "never stores the token-bearing endpoint %s",
    async (url) => {
      const worker = loadWorker();
      await dispatchFetch(worker, url);
      expect(worker.cache.put).not.toHaveBeenCalled();
    },
  );

  it("does not store responses marked no-store, errors or cross-origin responses", async () => {
    const responses = [
      okResponse({ headers: { "Cache-Control": "no-store" } }),
      okResponse({ ok: false }),
      okResponse({ type: "opaque" }),
    ];
    const worker = loadWorker({ network: async () => responses.shift() });
    await dispatchFetch(worker, "/a.html");
    await dispatchFetch(worker, "/b.html");
    await dispatchFetch(worker, "/c.html");
    expect(worker.cache.put).not.toHaveBeenCalled();
  });

  it("leaves non-GET requests to the browser", async () => {
    const worker = loadWorker();
    const { responded } = await dispatchFetch(worker, "/api/auth/login", { method: "POST" });
    expect(responded).toBe(false);
    expect(worker.context.fetch).not.toHaveBeenCalled();
  });

  it("serves an offline navigation from the cached page regardless of its query string", async () => {
    const worker = loadWorker({ network: async () => { throw new TypeError("offline"); } });
    await dispatchFetch(worker, "/set-password.html?token=x", { mode: "navigate" });
    expect(worker.caches.match).toHaveBeenCalledWith(expect.anything(), { ignoreSearch: true });
  });

  it("uses a new cache name so entries stored under the old rules are deleted on activation", async () => {
    const worker = loadWorker({ stored: { "partnertype-v1": true } });
    let pending = null;
    worker.listeners.activate({ waitUntil: (p) => { pending = p; } });
    await pending;
    expect(worker.caches.delete).toHaveBeenCalledWith("partnertype-v1");
  });
});
