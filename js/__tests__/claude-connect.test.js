/* ============================================================
   Profile › „Połącz z Claude”
   ============================================================

   Every user manages their own keys and connections from their profile.
   These tests pin who sees the section (staff always, a partner only when
   partners may connect or still has something to revoke), that a raw key is
   shown exactly once, and that names are only ever text.

   The real profile markup is mounted (scripts stripped).
   ============================================================ */
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { loadScript } from "./helpers/load-script.js";
import { dictionary } from "./helpers/copy.js";

const PROJECT_ROOT = path.resolve(import.meta.dirname, "..", "..");
const CONTROLLER = "js/claude-connect.js";

const hooks = [];
const AppStub = {
  lang: "pl",
  t(key, params) {
    const value = dictionary(this.lang)[key];
    if (value === undefined) return key;
    if (!params) return value;
    return String(value).replace(/\{(\w+)\}/g, (whole, name) =>
      (params[name] === undefined ? whole : String(params[name])));
  },
  applyTranslations() {},
  onRerender(fn) {
    hooks.push(fn);
  },
};
globalThis.App = AppStub;
globalThis.showToast = vi.fn();
globalThis.ApiClient = { get: vi.fn(), post: vi.fn(), delete: vi.fn() };
const writeText = vi.fn(() => Promise.resolve());
Object.defineProperty(globalThis.navigator, "clipboard", { value: { writeText }, configurable: true });

if (existsSync(path.join(PROJECT_ROOT, CONTROLLER))) loadScript(CONTROLLER);

const load = () => (globalThis.ClaudeConnect ? globalThis.ClaudeConnect.load() : Promise.resolve());
const flush = () => new Promise((resolve) => setTimeout(resolve, 0));
const $ = (id) => document.getElementById(id);

const MCP = "https://crm.example.pl/mcp";
const KEY = {
  id: 5, name: "Mój laptop", display_prefix: "pt_k5", scopes: "read",
  created_at: "2026-10-01T10:00:00+00:00", expires_at: "2026-12-30T10:00:00+00:00",
  last_used_at: null, revoked_at: null,
};
const CONNECTION = {
  id: 2, client_name: "Claude", redirect_host: "claude.ai",
  created_at: "2026-10-02T09:00:00+00:00", last_used_at: "2026-10-17T08:00:00+00:00", revoked_at: null,
};
const ME = { can_connect: true, refusal_code: null, mcp_url: MCP, keys: [KEY], connections: [CONNECTION] };

function mountPage(area, page) {
  const html = readFileSync(path.join(PROJECT_ROOT, "pages", area, "profile.html"), "utf8");
  const body = /<body[^>]*>([\s\S]*)<\/body>/i.exec(html)[1]
    .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, "");
  document.body.innerHTML = body;
  document.body.dataset.page = page;
}

function serveMe(me) {
  ApiClient.get.mockImplementation((url) => (url === "/api/integrations/me"
    ? Promise.resolve(structuredClone(me))
    : Promise.reject(Object.assign(new Error("Not found"), { status: 404 }))));
}

function attributesContain(secret) {
  return [...document.querySelectorAll("*")].some((node) =>
    [...node.attributes].some((attr) => attr.value.includes(secret)));
}

beforeEach(() => {
  AppStub.lang = "pl";
  ApiClient.get.mockReset();
  ApiClient.post.mockReset();
  ApiClient.delete.mockReset();
  showToast.mockReset();
  writeText.mockClear();
  mountPage("admin", "admin-profile");
});

describe("Profile › Connect to Claude — markup", () => {
  it("both profile pages carry a hidden section and the script tag", () => {
    for (const area of ["admin", "partner"]) {
      const html = readFileSync(path.join(PROJECT_ROOT, "pages", area, "profile.html"), "utf8");
      expect(html).toMatch(/id="claude-connect"[^>]*hidden/);
      expect(html).toContain('id="claude-connect-body"');
      expect(html.match(/claude-connect\.js/g) || []).toHaveLength(1);
    }
  });
});

describe("Profile › Connect to Claude — staff", () => {
  it("shows the address, the instructions, own keys and own connections when the user may connect", async () => {
    serveMe(ME);
    await load();
    expect(ApiClient.get).toHaveBeenCalledWith("/api/integrations/me");
    expect($("claude-connect").hidden).toBe(false);
    expect($("connect-mcp-url").value).toBe(MCP);
    $("connect-copy-mcp").click();
    await flush();
    expect(writeText).toHaveBeenCalledWith(MCP);
    const text = $("claude-connect-body").textContent;
    expect(text).toContain(`claude mcp add --transport http partnertype ${MCP}`);
    expect(text).toContain("Mój laptop");
    expect(text).toContain("pt_k5");
    expect(text).toContain("Claude");
    expect(text).toContain("claude.ai");
    expect(text).toContain(dictionary("pl").int_never);
  });

  it("creates a key, shows it once, and after Close it is gone — also after a re-render", async () => {
    serveMe(ME);
    ApiClient.post.mockResolvedValue({ id: 6, name: "Nowy", display_prefix: "pt_n6", expires_at: null, key: "pt_n6_SECRET" });
    await load();
    expect([...$("connect-key-expiry").options].map((o) => o.value)).toEqual(["30", "90", "365"]);
    expect($("connect-key-expiry").value).toBe("90");
    $("connect-key-name").value = "Nowy";
    $("connect-key-create").click();
    await flush();
    await flush();
    expect(ApiClient.post).toHaveBeenCalledWith("/api/integrations/keys", { name: "Nowy", expires_in_days: 90 });
    expect($("connect-key-once").textContent).toContain("pt_n6_SECRET");
    expect(attributesContain("pt_n6_SECRET")).toBe(false);
    $("connect-key-copy").click();
    await flush();
    expect(writeText).toHaveBeenCalledWith("pt_n6_SECRET");
    $("connect-key-close").click();
    expect($("connect-key-once")).toBeNull();
    expect(document.body.textContent).not.toContain("pt_n6_SECRET");
    for (const fn of hooks) fn();
    expect(document.body.textContent).not.toContain("pt_n6_SECRET");
  });

  it("still shows a just-created key when the refresh after creating it fails", async () => {
    serveMe(ME);
    ApiClient.post.mockResolvedValue({ id: 6, name: "Nowy", display_prefix: "pt_n6", expires_at: null, key: "pt_n6_SECRET" });
    await load();
    ApiClient.get.mockRejectedValue(Object.assign(new Error("timeout"), { status: 504 }));
    $("connect-key-name").value = "Nowy";
    $("connect-key-create").click();
    await flush();
    await flush();
    expect($("claude-connect").hidden).toBe(false);
    expect($("connect-key-once").textContent).toContain("pt_n6_SECRET");
    expect($("claude-connect-body").textContent).toContain(dictionary("pl").connect_list_stale);
    expect($("claude-connect-body").textContent).toContain("Mój laptop");
  });

  it("shows a just-created key even when the refreshed state would hide the section", async () => {
    mountPage("partner", "partner-profile");
    serveMe(ME);
    ApiClient.post.mockResolvedValue({ id: 6, name: "Nowy", display_prefix: "pt_n6", expires_at: null, key: "pt_n6_SECRET" });
    await load();
    serveMe({ ...ME, can_connect: false, refusal_code: "partners_disabled", keys: [], connections: [] });
    $("connect-key-name").value = "Nowy";
    $("connect-key-create").click();
    await flush();
    await flush();
    expect($("claude-connect").hidden).toBe(false);
    expect($("connect-key-once").textContent).toContain("pt_n6_SECRET");
    $("connect-key-close").click();
    expect($("claude-connect").hidden).toBe(true);
  });

  it("revokes an own key and an own connection only on the second click", async () => {
    serveMe(ME);
    ApiClient.delete.mockResolvedValue({ ok: true });
    await load();
    document.querySelector('[data-revoke="key-5"]').click();
    await flush();
    expect(ApiClient.delete).not.toHaveBeenCalled();
    expect(document.querySelector('[data-revoke="key-5"]').textContent).toBe(dictionary("pl").int_revoke_confirm);
    document.querySelector('[data-revoke="key-5"]').click();
    await flush();
    expect(ApiClient.delete).toHaveBeenCalledWith("/api/integrations/keys/5");
    document.querySelector('[data-revoke="connection-2"]').click();
    await flush();
    document.querySelector('[data-revoke="connection-2"]').click();
    await flush();
    expect(ApiClient.delete).toHaveBeenCalledWith("/api/integrations/connections/2");
  });

  it("explains that integrations are off and still lists own keys for revocation", async () => {
    serveMe({ ...ME, can_connect: false, refusal_code: "integrations_disabled", connections: [] });
    await load();
    expect($("claude-connect").hidden).toBe(false);
    const text = $("claude-connect-body").textContent;
    expect(text).toContain(dictionary("pl").connect_off_admin);
    expect(text).toContain("Mój laptop");
    expect(document.querySelector('[data-revoke="key-5"]')).not.toBeNull();
    expect($("connect-key-create")).toBeNull();
  });

  it("renders a hostile key name as text", async () => {
    serveMe({ ...ME, keys: [{ ...KEY, name: '<img src=x onerror="window.__pwned=1">' }] });
    await load();
    expect($("claude-connect-body").querySelector("img")).toBeNull();
    expect($("claude-connect-body").textContent).toContain("<img src=x");
    expect(window.__pwned).toBeUndefined();
  });

  it("stays hidden when /api/integrations/me fails", async () => {
    ApiClient.get.mockRejectedValue(Object.assign(new Error("boom"), { status: 500 }));
    await load();
    expect($("claude-connect").hidden).toBe(true);
  });

  it("re-renders in English without fetching again", async () => {
    serveMe(ME);
    await load();
    const calls = ApiClient.get.mock.calls.length;
    AppStub.lang = "en";
    for (const fn of hooks) fn();
    expect($("claude-connect-body").textContent).toContain(dictionary("en").connect_intro);
    expect(ApiClient.get.mock.calls.length).toBe(calls);
  });
});

describe("Profile › Connect to Claude — partner", () => {
  beforeEach(() => mountPage("partner", "partner-profile"));

  it("stays hidden for a partner who may not connect and has nothing to revoke", async () => {
    serveMe({ ...ME, can_connect: false, refusal_code: "partners_disabled", keys: [], connections: [] });
    await load();
    expect($("claude-connect").hidden).toBe(true);
    expect($("claude-connect-body").childElementCount).toBe(0);
  });

  it("renders like staff when partners may connect", async () => {
    serveMe(ME);
    await load();
    expect($("claude-connect").hidden).toBe(false);
    expect($("connect-mcp-url").value).toBe(MCP);
    expect($("connect-key-create")).not.toBeNull();
  });

  it("still lets a partner revoke old keys after partners were switched off", async () => {
    serveMe({ ...ME, can_connect: false, refusal_code: "partners_disabled", connections: [] });
    await load();
    expect($("claude-connect").hidden).toBe(false);
    expect($("claude-connect-body").textContent).toContain(dictionary("pl").connect_partner_off);
    expect(document.querySelector('[data-revoke="key-5"]')).not.toBeNull();
  });
});
