/* ============================================================
   Settings › Integrations
   ============================================================

   The tile is where the owner decides whether anything outside PartnerType
   may read its data. So these tests pin what makes that decision safe and
   honest: the tile exists only for whoever the admin-only API answers, the
   switch cannot be turned on without the EU-transfer acknowledgement, a raw
   key is shown exactly once, and every name a stranger chose (a DCR client
   name, a key name) is only ever text.

   The real settings markup is mounted (scripts stripped) so the ids the
   controller needs are the ids the page actually has.
   ============================================================ */
import { readFileSync } from "node:fs";
import path from "node:path";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { loadScript } from "./helpers/load-script.js";
import { dictionary } from "./helpers/copy.js";

const PROJECT_ROOT = path.resolve(import.meta.dirname, "..", "..");
const PAGE = path.join(PROJECT_ROOT, "pages", "admin", "settings.html");

// A stand-in for App that reads the real dictionaries and can switch language.
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
globalThis.ApiClient = { get: vi.fn(), patch: vi.fn(), post: vi.fn(), delete: vi.fn() };

loadScript("js/admin-integrations.js");

const SETTINGS = {
  enabled: false,
  partners_enabled: false,
  field_groups: [],
  field_group_catalogue: [
    { key: "base", fields: ["id", "company"] },
    { key: "contact", fields: ["client_name", "client_email", "client_phone"] },
    { key: "notes", fields: ["notes", "comments"] },
    { key: "client_ids", fields: ["sector", "nip", "website"] },
    { key: "custom", fields: ["custom_fields"] },
    { key: "commissions", fields: ["commission"] },
  ],
  limits: { per_minute: 60, per_day: 5000 },
  operator_caps: { rate_per_minute: 60, rate_per_day: 5000, register_per_hour: 20 },
  mcp_url: "https://crm.example.pl/mcp",
  api_base: "https://crm.example.pl/api/v1",
  openapi_url: "https://crm.example.pl/api/v1/openapi.json",
};

function forbidden() {
  const error = new Error("Brak uprawnień");
  error.status = 403;
  return Promise.reject(error);
}

/** Answer each GET by path; anything not listed is a 404. */
function serve(routes) {
  ApiClient.get.mockImplementation((url) => {
    const key = Object.keys(routes).find((prefix) => url === prefix || url.startsWith(`${prefix}?`));
    if (!key) {
      const error = new Error("Not found");
      error.status = 404;
      return Promise.reject(error);
    }
    const value = routes[key];
    return typeof value === "function" ? value(url) : Promise.resolve(structuredClone(value));
  });
}

function mountPage() {
  const html = readFileSync(PAGE, "utf8");
  const body = /<body[^>]*>([\s\S]*)<\/body>/i.exec(html)[1]
    .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, "");
  document.body.innerHTML = body;
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));
const $ = (id) => document.getElementById(id);

function change(input, checked) {
  input.checked = checked;
  input.dispatchEvent(new window.Event("change", { bubbles: true }));
}

beforeEach(() => {
  AppStub.lang = "pl";
  ApiClient.get.mockReset();
  ApiClient.patch.mockReset();
  ApiClient.post.mockReset();
  ApiClient.delete.mockReset();
  showToast.mockReset();
  mountPage();
});

describe("Settings › Integrations — the tile and the main switch (tracer)", () => {
  it("keeps the tile and the pane hidden in the markup until the API answers", () => {
    expect($("integrations-tile").hidden).toBe(true);
    expect($("tab-integrations").hidden).toBe(true);
  });

  it("shows the tile and the pane to the admin once GET /api/integrations/settings answers 200", async () => {
    serve({ "/api/integrations/settings": SETTINGS });
    await AdminIntegrations.load();
    expect(ApiClient.get).toHaveBeenCalledWith("/api/integrations/settings");
    expect($("integrations-tile").hidden).toBe(false);
    expect($("tab-integrations").hidden).toBe(false);
    expect($("int-switches").textContent).toContain(dictionary("pl").int_status_off);
  });

  it("leaves the tile hidden and draws nothing for a manager (403)", async () => {
    ApiClient.get.mockImplementation(() => forbidden());
    await AdminIntegrations.load();
    expect($("integrations-tile").hidden).toBe(true);
    expect($("tab-integrations").hidden).toBe(true);
    expect($("int-switches").childElementCount).toBe(0);
  });

  it("will not switch on before the EU-transfer acknowledgement is ticked", async () => {
    serve({ "/api/integrations/settings": SETTINGS });
    await AdminIntegrations.load();
    const toggle = $("int-enabled");
    expect(toggle.checked).toBe(false);
    expect(toggle.disabled).toBe(true);
    expect($("int-switches").textContent).toContain(dictionary("pl").int_eu_ack);

    change(toggle, true);
    await flush();
    expect(ApiClient.patch).not.toHaveBeenCalled();
  });

  it("switches on with acknowledge_eu_transfer after the box is ticked", async () => {
    serve({ "/api/integrations/settings": SETTINGS });
    ApiClient.patch.mockResolvedValue({ ...SETTINGS, enabled: true });
    await AdminIntegrations.load();

    change($("int-eu-ack"), true);
    expect($("int-enabled").disabled).toBe(false);
    change($("int-enabled"), true);
    await flush();

    expect(ApiClient.patch).toHaveBeenCalledWith("/api/integrations/settings",
      { enabled: true, acknowledge_eu_transfer: true });
    expect($("int-switches").textContent).toContain(dictionary("pl").int_status_on);
    expect(showToast).toHaveBeenCalledWith("success", dictionary("pl").int_saved, expect.any(String));
  });

  it("asks inline before switching off, and sends enabled false only after the confirmation", async () => {
    serve({ "/api/integrations/settings": { ...SETTINGS, enabled: true } });
    ApiClient.patch.mockResolvedValue({ ...SETTINGS, enabled: false });
    await AdminIntegrations.load();

    change($("int-enabled"), false);
    await flush();
    expect(ApiClient.patch).not.toHaveBeenCalled();
    expect($("int-switches").textContent).toContain(dictionary("pl").int_confirm_disable);
    expect($("int-enabled").checked).toBe(true);

    $("int-disable-confirm").click();
    await flush();
    expect(ApiClient.patch).toHaveBeenCalledWith("/api/integrations/settings", { enabled: false });
    expect($("int-switches").textContent).toContain(dictionary("pl").int_status_off);
  });

  it("cancelling the switch-off sends nothing", async () => {
    serve({ "/api/integrations/settings": { ...SETTINGS, enabled: true } });
    await AdminIntegrations.load();
    change($("int-enabled"), false);
    $("int-disable-cancel").click();
    await flush();
    expect(ApiClient.patch).not.toHaveBeenCalled();
    expect($("int-disable-confirm")).toBeNull();
  });

  it("saves the partners switch on its own", async () => {
    serve({ "/api/integrations/settings": { ...SETTINGS, enabled: true } });
    ApiClient.patch.mockResolvedValue({ ...SETTINGS, enabled: true, partners_enabled: true });
    await AdminIntegrations.load();
    change($("int-partners"), true);
    await flush();
    expect(ApiClient.patch).toHaveBeenCalledWith("/api/integrations/settings", { partners_enabled: true });
    expect($("int-partners").checked).toBe(true);
  });

  it("reports a failed save and puts the switch back", async () => {
    serve({ "/api/integrations/settings": { ...SETTINGS, enabled: true } });
    ApiClient.patch.mockRejectedValue(new Error("Serwer odmówił"));
    await AdminIntegrations.load();
    change($("int-partners"), true);
    await flush();
    expect(showToast).toHaveBeenCalledWith("error", dictionary("pl").int_save_failed, "Serwer odmówił");
    expect($("int-partners").checked).toBe(false);
  });
});

/* ------------------------------------------------------------------
   Połącz z Claude, connections, keys shown once, data scope
   and limits, log and usage.
   ------------------------------------------------------------------ */
const OVERVIEW = {
  keys: [{
    id: 7, name: "Laptop Anny", display_prefix: "pt_ab12", scopes: "read",
    created_at: "2026-10-01T10:00:00+00:00", expires_at: "2026-12-30T10:00:00+00:00",
    last_used_at: null, revoked_at: null, user_id: 2, user_name: "Anna Producentka", user_role: "admin",
  }],
  connections: [{
    id: 3, user_id: 4, user_name: "Piotr Opiekun", user_role: "manager", client_name: "Claude",
    redirect_hosts: ["claude.ai"], created_at: "2026-10-02T09:00:00+00:00",
    last_used_at: "2026-10-17T08:00:00+00:00", revoked_at: null, revoked_reason: null,
  }],
};
const LOG = {
  calls: [{
    created_at: "2026-10-17T08:00:00+00:00", user_name: "Anna Producentka", connection_kind: "key",
    connection_id: 7, surface: "mcp", operation: "list_leads", status_code: 200, outcome: "ok",
    records: 12, duration_ms: 40,
  }],
};
const USAGE = {
  usage: [{ day: "2026-10-17", connection_kind: "oauth", connection_id: 3, label: "Claude", calls: 5, denied: 1, records: 30 }],
};

function serveAll(overrides = {}) {
  serve({
    "/api/integrations/settings": SETTINGS,
    "/api/integrations/admin/connections": OVERVIEW,
    "/api/integrations/admin/log": LOG,
    "/api/integrations/admin/usage": USAGE,
    ...overrides,
  });
}

const writeText = vi.fn(() => Promise.resolve());
Object.defineProperty(globalThis.navigator, "clipboard", { value: { writeText }, configurable: true });

function rerenderAll() {
  for (const fn of hooks) fn();
}

function attributesContain(secret) {
  return [...document.querySelectorAll("*")].some((node) =>
    [...node.attributes].some((attr) => attr.value.includes(secret)));
}

describe("Settings › Integrations — Connect to Claude", () => {
  it("shows the exact MCP address read-only, copies exactly it and prints the Claude Code line", async () => {
    serveAll();
    writeText.mockClear();
    await AdminIntegrations.load();
    const field = $("int-mcp-url");
    expect(field.value).toBe(SETTINGS.mcp_url);
    expect(field.readOnly).toBe(true);
    $("int-copy-mcp").click();
    await flush();
    expect(writeText).toHaveBeenCalledWith(SETTINGS.mcp_url);
    const text = $("int-connect").textContent;
    expect(text).toContain(`claude mcp add --transport http partnertype ${SETTINGS.mcp_url}`);
    expect(text).toContain(dictionary("pl").int_connect_claude_ai);
    expect(text).toContain(dictionary("pl").int_connect_public_https);
  });
});

describe("Settings › Integrations — connections and keys", () => {
  it("lists every connection and key with person, created and last-used times", async () => {
    serveAll();
    await AdminIntegrations.load();
    const connections = $("int-connections").textContent;
    expect(ApiClient.get).toHaveBeenCalledWith("/api/integrations/admin/connections");
    expect(connections).toContain("Claude");
    expect(connections).toContain("Piotr Opiekun");
    expect(connections).toContain("claude.ai");
    const keys = $("int-keys").textContent;
    expect(keys).toContain("Laptop Anny");
    expect(keys).toContain("Anna Producentka");
    expect(keys).toContain("pt_ab12");
    expect(keys).toContain(dictionary("pl").int_never);
  });

  it("revokes a connection only on the second click, then reloads the list", async () => {
    serveAll();
    ApiClient.delete.mockResolvedValue({ ok: true });
    await AdminIntegrations.load();
    const before = ApiClient.get.mock.calls.filter(([u]) => u === "/api/integrations/admin/connections").length;
    const revoke = document.querySelector('[data-revoke="connection-3"]');
    expect(revoke.textContent).toBe(dictionary("pl").int_revoke);
    revoke.click();
    await flush();
    expect(ApiClient.delete).not.toHaveBeenCalled();
    const confirm = document.querySelector('[data-revoke="connection-3"]');
    expect(confirm.textContent).toBe(dictionary("pl").int_revoke_confirm);
    confirm.click();
    await flush();
    await flush();
    expect(ApiClient.delete).toHaveBeenCalledWith("/api/integrations/connections/3");
    const after = ApiClient.get.mock.calls.filter(([u]) => u === "/api/integrations/admin/connections").length;
    expect(after).toBe(before + 1);
  });

  it("revokes a key the same two-step way", async () => {
    serveAll();
    ApiClient.delete.mockResolvedValue({ ok: true });
    await AdminIntegrations.load();
    document.querySelector('[data-revoke="key-7"]').click();
    await flush();
    document.querySelector('[data-revoke="key-7"]').click();
    await flush();
    expect(ApiClient.delete).toHaveBeenCalledWith("/api/integrations/keys/7");
  });

  it("says so explicitly when nothing has been created yet", async () => {
    serveAll({ "/api/integrations/admin/connections": { keys: [], connections: [] } });
    await AdminIntegrations.load();
    expect($("int-connections").textContent).toContain(dictionary("pl").int_connections_empty);
    expect($("int-keys").textContent).toContain(dictionary("pl").int_keys_empty);
  });

  it("renders a hostile client name as literal text", async () => {
    const hostile = { ...OVERVIEW, connections: [{ ...OVERVIEW.connections[0], client_name: "<b>x</b><img src=x onerror=\"window.__pwned=1\">" }] };
    serveAll({ "/api/integrations/admin/connections": hostile });
    await AdminIntegrations.load();
    expect($("int-connections").querySelector("b")).toBeNull();
    expect($("int-connections").querySelector("img")).toBeNull();
    expect($("int-connections").textContent).toContain("<b>x</b>");
    expect(window.__pwned).toBeUndefined();
  });

  it("shows its own error when one list fails and still draws the others", async () => {
    serveAll({ "/api/integrations/admin/connections": () => Promise.reject(new Error("boom")) });
    await AdminIntegrations.load();
    expect($("int-connections").textContent).toContain(dictionary("pl").int_load_failed);
    expect($("int-log").textContent).toContain("list_leads");
  });
});

describe("Settings › Integrations — a new key is shown once", () => {
  it("offers 30, 90 (selected) and 365 days and posts name and expiry", async () => {
    serveAll();
    ApiClient.post.mockResolvedValue({ id: 9, name: "Nowy", display_prefix: "pt_zz", expires_at: "2027-01-17T00:00:00+00:00", key: "pt_zz_SECRET123" });
    await AdminIntegrations.load();
    const select = $("int-key-expiry");
    expect([...select.options].map((o) => o.value)).toEqual(["30", "90", "365"]);
    expect(select.value).toBe("90");
    $("int-key-name").value = "Nowy";
    $("int-key-create").click();
    await flush();
    await flush();
    expect(ApiClient.post).toHaveBeenCalledWith("/api/integrations/keys", { name: "Nowy", expires_in_days: 90 });
    expect($("int-key-once").textContent).toContain("pt_zz_SECRET123");
    expect(attributesContain("pt_zz_SECRET123")).toBe(false);
  });

  it("copies the key, and after Close it is gone for good — also after a re-render", async () => {
    serveAll();
    ApiClient.post.mockResolvedValue({ id: 9, name: "Nowy", display_prefix: "pt_zz", expires_at: null, key: "pt_zz_SECRET123" });
    writeText.mockClear();
    await AdminIntegrations.load();
    $("int-key-name").value = "Nowy";
    $("int-key-create").click();
    await flush();
    await flush();
    $("int-key-copy").click();
    await flush();
    expect(writeText).toHaveBeenCalledWith("pt_zz_SECRET123");
    $("int-key-close").click();
    expect($("int-key-once")).toBeNull();
    expect(document.body.textContent).not.toContain("pt_zz_SECRET123");
    rerenderAll();
    expect(document.body.textContent).not.toContain("pt_zz_SECRET123");
  });

  it("does not post a key without a name", async () => {
    serveAll();
    await AdminIntegrations.load();
    $("int-key-name").value = "   ";
    $("int-key-create").click();
    await flush();
    expect(ApiClient.post).not.toHaveBeenCalled();
  });
});

describe("Settings › Integrations — data scope and limits", () => {
  it("draws five group checkboxes, off unless enabled, and saves them with one button", async () => {
    serveAll({ "/api/integrations/settings": { ...SETTINGS, field_groups: ["notes"] } });
    ApiClient.patch.mockResolvedValue({ ...SETTINGS, field_groups: ["contact", "notes"] });
    await AdminIntegrations.load();
    const ids = ["contact", "notes", "client_ids", "custom", "commissions"];
    expect(ids.map((g) => $(`int-group-${g}`).checked)).toEqual([false, true, false, false, false]);
    expect($("int-group-base")).toBeNull();
    $("int-group-contact").checked = true;
    $("int-fields-save").click();
    await flush();
    expect(ApiClient.patch).toHaveBeenCalledWith("/api/integrations/settings", { field_groups: ["contact", "notes"] });
  });

  it("shows the limits in force with the operator caps", async () => {
    serveAll({ "/api/integrations/settings": { ...SETTINGS, limits: { per_minute: 30, per_day: 1000 } } });
    await AdminIntegrations.load();
    expect($("int-limit-minute").value).toBe("30");
    expect($("int-limit-day").value).toBe("1000");
    const text = $("int-fields").textContent;
    expect(text).toContain(AppStub.t("int_limit_operator_cap", { cap: 60 }));
    expect(text).toContain(AppStub.t("int_limit_operator_cap", { cap: 5000 }));
  });

  it("refuses a limit above the operator cap before asking the server", async () => {
    serveAll();
    await AdminIntegrations.load();
    $("int-limit-minute").value = "61";
    $("int-limits-save").click();
    await flush();
    expect(ApiClient.patch).not.toHaveBeenCalled();
    expect($("int-fields").textContent).toContain(AppStub.t("int_limit_above_cap", { cap: 60 }));
  });

  it("saves limits within the caps", async () => {
    serveAll();
    ApiClient.patch.mockResolvedValue({ ...SETTINGS, limits: { per_minute: 30, per_day: 5000 } });
    await AdminIntegrations.load();
    $("int-limit-minute").value = "30";
    $("int-limits-save").click();
    await flush();
    expect(ApiClient.patch).toHaveBeenCalledWith("/api/integrations/settings", { rate_per_minute: 30, rate_per_day: 5000 });
  });
});

describe("Settings › Integrations — log and usage", () => {
  it("shows the latest calls and the daily usage per connection", async () => {
    serveAll();
    await AdminIntegrations.load();
    expect(ApiClient.get).toHaveBeenCalledWith("/api/integrations/admin/log?limit=50");
    expect(ApiClient.get).toHaveBeenCalledWith("/api/integrations/admin/usage?days=30");
    const text = $("int-log").textContent;
    expect(text).toContain("list_leads");
    expect(text).toContain("Anna Producentka");
    expect(text).toContain("Laptop Anny");
    expect(text).toContain(dictionary("pl").int_outcome_ok);
    expect(text).toContain("12");
    expect(text).toContain("Claude");
    expect(text).toContain("5");
  });

  it("has empty states for both", async () => {
    serveAll({ "/api/integrations/admin/log": { calls: [] }, "/api/integrations/admin/usage": { usage: [] } });
    await AdminIntegrations.load();
    expect($("int-log").textContent).toContain(dictionary("pl").int_log_empty);
    expect($("int-log").textContent).toContain(dictionary("pl").int_usage_empty);
  });
});

describe("Settings › Integrations — language", () => {
  it("re-renders every section in English without fetching again", async () => {
    serveAll();
    await AdminIntegrations.load();
    const calls = ApiClient.get.mock.calls.length;
    AppStub.lang = "en";
    rerenderAll();
    const en = dictionary("en");
    expect($("int-switches").textContent).toContain(en.int_switches_title);
    expect($("int-connect").textContent).toContain(en.int_connect_title);
    expect($("int-connections").textContent).toContain(en.int_connections_title);
    expect($("int-keys").textContent).toContain(en.int_keys_title);
    expect($("int-fields").textContent).toContain(en.int_fields_title);
    expect($("int-log").textContent).toContain(en.int_log_title);
    expect(ApiClient.get.mock.calls.length).toBe(calls);
  });

  it("announces no future integrations", () => {
    const source = readFileSync(path.join(PROJECT_ROOT, "js/admin-integrations.js"), "utf8");
    expect(source).not.toMatch(/zapier|hubspot|wkrótce|coming soon/i);
  });
});
