import { readFileSync } from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { Window } from "happy-dom";
import { describe, expect, it, vi } from "vitest";

const PROJECT_ROOT = path.resolve(import.meta.dirname, "..", "..");
const ADMIN_PAGES = Object.freeze({
  "affiliate.html": ["theme-boot.js", "icons.js", "dropdown.js", "datepicker.js", "api.js", "app.js", "utils.js", "admin-shell.js", "notifications.js", "admin-affiliate.js", "admin-affiliate-settings.js", "agent.js", "branding.js"],
  "analytics.html": ["theme-boot.js", "icons.js", "dropdown.js", "datepicker.js", "api.js", "app.js", "utils.js", "admin-shell.js", "charts.js", "notifications.js", "analytics.js", "agent.js", "analytics-empty-states.js", "branding.js"],
  "audit.html": ["theme-boot.js", "icons.js", "dropdown.js", "datepicker.js", "api.js", "app.js", "utils.js", "admin-shell.js", "notifications.js", "audit.js", "agent.js", "branding.js"],
  "commissions.html": ["theme-boot.js", "icons.js", "dropdown.js", "datepicker.js", "api.js", "app.js", "utils.js", "admin-shell.js", "notifications.js", "commissions.js", "agent.js", "branding.js"],
  "dashboard.html": ["theme-boot.js", "icons.js", "dropdown.js", "datepicker.js", "api.js", "app.js", "utils.js", "admin-shell.js", "admin-dashboard.js", "charts.js", "notifications.js", "agent.js", "branding.js"],
  "knowledge.html": ["theme-boot.js", "icons.js", "dropdown.js", "datepicker.js", "api.js", "app.js", "utils.js", "admin-shell.js", "notifications.js", "knowledge.js", "agent.js", "branding.js"],
  "leads.html": ["theme-boot.js", "icons.js", "dropdown.js", "datepicker.js", "api.js", "app.js", "utils.js", "admin-shell.js", "lead-fields.js", "admin-leads.js", "charts.js", "notifications.js", "comments.js", "leads6.js", "agent.js", "branding.js"],
  "partners.html": ["theme-boot.js", "icons.js", "dropdown.js", "datepicker.js", "api.js", "app.js", "utils.js", "admin-shell.js", "admin-partners.js", "charts.js", "notifications.js", "agent.js", "branding.js"],
  "profile.html": ["theme-boot.js", "icons.js", "dropdown.js", "datepicker.js", "api.js", "app.js", "utils.js", "admin-shell.js", "profile.js", "claude-connect.js", "agent.js", "branding.js"],
  "settings.html": ["theme-boot.js", "icons.js", "dropdown.js", "datepicker.js", "api.js", "app.js", "utils.js", "admin-shell.js", "admin-products.js", "admin-blocks.js", "admin-integrations.js", "charts.js", "notifications.js", "settings.js", "agent.js", "branding.js"],
  "users.html": ["theme-boot.js", "icons.js", "dropdown.js", "datepicker.js", "api.js", "app.js", "utils.js", "admin-shell.js", "users.js", "charts.js", "notifications.js", "agent.js", "branding.js"],
});

function scriptSources(pagePath) {
  const html = readFileSync(path.join(PROJECT_ROOT, pagePath), "utf8");
  return [...html.matchAll(/<script\s+src="([^"]+)"/g)].filter((match) => match[1] !== "/locales.js").map((match) => path.basename(match[1]));
}

function runClassicScript(window, relativePath) {
  const source = readFileSync(path.join(PROJECT_ROOT, relativePath), "utf8");
  const context = window.__vmContext || (window.__vmContext = vm.createContext(window));
  vm.runInContext(source, context, { filename: relativePath });
}

function response(body) {
  return { ok: true, status: 200, json: async () => body };
}

function responseBody(pathname) {
  if (pathname === "/api/auth/me") {
    return { user: { id: 1, role: "admin", name: "Ada Admin", company: "PartnerType", created_at: "2026-08-22" } };
  }
  if (pathname === "/api/users/1") {
    return { user: { id: 1, role: "admin", name: "Ada Admin", email: "ada@example.com", company: "PartnerType", created_at: "2026-08-22" } };
  }
  if (pathname === "/api/users") return { users: [] };
  if (pathname === "/api/settings") return { settings: {} };
  if (pathname === "/api/tiers") return { tiers: [] };
  if (pathname === "/api/bonus-defs") return { bonuses: [], bonus_defs: [] };
  if (pathname === "/api/partner-bonuses") return { partners: [] };
  if (pathname === "/api/notifications") return { notifications: [], unread_count: 0 };
  if (pathname === "/api/audit") return { logs: [], total: 0, labels: {} };
  return {};
}

async function executePage(pageName) {
  const pagePath = `pages/admin/${pageName}`;
  const window = new Window({ url: `http://localhost:8000/${pagePath}` });
  window.document.write(readFileSync(path.join(PROJECT_ROOT, pagePath), "utf8"));
  window.console = console;
  window.confirm = vi.fn(() => true);
  window.HTMLCanvasElement.prototype.getContext = () => ({
    scale: vi.fn(), beginPath: vi.fn(), moveTo: vi.fn(), lineTo: vi.fn(), stroke: vi.fn(),
    fill: vi.fn(), arc: vi.fn(), closePath: vi.fn(), fillText: vi.fn(), setLineDash: vi.fn(),
    quadraticCurveTo: vi.fn(), createLinearGradient: vi.fn(() => ({ addColorStop: vi.fn() })),
  });
  window.fetch = vi.fn(async (url) => {
    const parsed = new URL(url, window.location.href);
    return response(responseBody(parsed.pathname));
  });

  for (const fileName of ADMIN_PAGES[pageName]) runClassicScript(window, `js/${fileName}`);
  window.document.dispatchEvent(new window.Event("DOMContentLoaded"));
  await window.AdminShell.init();
  return window;
}

describe("admin page dependency graph", () => {
  it("enumerates the exact script order for all eleven admin pages", () => {
    expect(Object.keys(ADMIN_PAGES)).toHaveLength(11);
    for (const [pageName, expected] of Object.entries(ADMIN_PAGES)) {
      expect(scriptSources(`pages/admin/${pageName}`), pageName).toEqual(expected);
    }
  });

  it("contains no retired admin.js reference in any repository HTML", () => {
    const htmlFiles = readFileSync(path.join(PROJECT_ROOT, "README.md"), "utf8");
    void htmlFiles;
    const repositoryHtml = [
      "index.html",
      ...Object.keys(ADMIN_PAGES).map((page) => `pages/admin/${page}`),
      "pages/partner/affiliate.html",
      "pages/partner/dashboard.html",
      "pages/partner/earnings.html",
      "pages/partner/knowledge.html",
      "pages/partner/leads.html",
      "pages/partner/new-lead.html",
      "pages/partner/profile.html",
    ];
    for (const htmlPath of repositoryHtml) {
      expect(readFileSync(path.join(PROJECT_ROOT, htmlPath), "utf8"), htmlPath)
        .not.toContain("js/admin.js");
    }
  });
});

describe("remaining admin entry points", () => {
  it.each([
    ["audit.html", "/api/audit"],
    ["knowledge.html", "/api/knowledge"],
    ["profile.html", "/api/users/1"],
    ["settings.html", "/api/settings"],
    ["users.html", "/api/users"],
  ])("executes %s through one shell and one specialized initialization", async (pageName, endpoint) => {
    const window = await executePage(pageName);

    const authCalls = () => window.fetch.mock.calls.filter(
      ([url]) => new URL(url, window.location.href).pathname === "/api/auth/me",
    ).length;
    const initialAuthCalls = authCalls();
    expect(initialAuthCalls).toBeGreaterThanOrEqual(1);
    await Promise.all([window.AdminShell.init(), window.AdminShell.init()]);
    expect(authCalls()).toBe(initialAuthCalls);
    // Settings.html's specialized init now awaits AdminShell.init()
    // itself before issuing its own request (the page-level staff/admin
    // gate), one microtask hop later than before. That request is fired from
    // a DOMContentLoaded listener this test never awaits directly, so poll
    // for it instead of assuming a fixed number of ticks have elapsed.
    await vi.waitFor(() => {
      expect(window.fetch.mock.calls.filter(([url]) => new URL(url, window.location.href).pathname === endpoint)).toHaveLength(1);
    });
    expect(window.AdminShell).toBeDefined();
  });
});
