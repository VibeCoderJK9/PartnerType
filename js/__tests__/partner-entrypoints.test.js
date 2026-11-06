import { readFileSync } from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { Window } from "happy-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

const PROJECT_ROOT = path.resolve(import.meta.dirname, "..", "..");

function externalScripts(page) {
  const html = readFileSync(path.join(PROJECT_ROOT, page), "utf8");
  return [...html.matchAll(/<script\s+src="([^"]+)"/g)].filter((match) => match[1] !== "/locales.js").map((match) => match[1].replace("../../", ""));
}

function runScript(window, script) {
  const source = readFileSync(path.join(PROJECT_ROOT, script), "utf8");
  const context = window.__vmContext || (window.__vmContext = vm.createContext(window));
  vm.runInContext(source, context, { filename: script });
}

function response(data, status = 200) {
  return { ok: status >= 200 && status < 300, status, json: vi.fn().mockResolvedValue(data) };
}

function createRealm(page, html) {
  const window = new Window({ url: `http://localhost:8000/${page}` });
  window.document.write(html);
  const pageName = path.basename(page, ".html");
  window.document.body.dataset.page = ["profile", "affiliate"].includes(pageName) ? `partner-${pageName}` : "partner-earnings";
  window.console = console;
  window.fetch = vi.fn(async (url) => {
    const pathname = new URL(url, window.location.href).pathname;
    const target = `${pathname}${new URL(url, window.location.href).search}`;
    if (pathname === "/api/auth/me") return response({ user: { id: 7, role: "partner", name: "Ada Partner", company: "ACME", level: "gold" } });
    if (pathname === "/api/users/7") return response({ user: { id: 7, role: "partner", name: "Ada Partner", company: "ACME", level: "gold", created_at: "2026-08-22" } });
    if (pathname === "/api/stats") return response({ total_leads: 1, accepted_leads: 1 });
    if (pathname === "/api/partner-bonuses/7") return response({ partner: { tier_name: "Gold", base_rate: 7 } });
    if (pathname === "/api/leads") return response({ leads: [] });
    if (pathname === "/api/settings") return response({ settings: { lead_queue_shares: "100,50,25,10,0" } });
    if (pathname === "/api/notifications") return response({ unread: 0, notifications: [] });
    if (pathname === "/api/affiliate/me") {
      return response({
        enabled: true, link: { id: 1, code: "abcde12345", url: "https://partners.example.com/r/abcde12345" },
        clicks: 4, purchases: 1, pending_count: 1, pending_commission: "35.00", approved_commission: "0.00", currency: "PLN",
      });
    }
    throw new Error(`unexpected request: ${target}`);
  });
  return window;
}

afterEach(() => vi.restoreAllMocks());

describe("real partner entry points", () => {
  it("executes the profile script list with one shared partner bootstrap", async () => {
    const page = "pages/partner/profile.html";
    const html = readFileSync(path.join(PROJECT_ROOT, page), "utf8");
    const scripts = externalScripts(page);
    expect(scripts).toEqual(["js/theme-boot.js", "js/icons.js", "js/dropdown.js", "js/datepicker.js", "js/api.js", "js/app.js", "js/utils.js", "js/partner.js", "js/profile.js", "js/claude-connect.js", "js/agent.js", "js/branding.js"]);
    const window = createRealm(page, html);
    scripts.forEach((script) => runScript(window, script));
    await vm.runInContext("Promise.all([initPartnerPage(), initProfilePage()])", window.__vmContext);
    await vi.waitFor(() => expect(window.document.getElementById("profile-name").textContent).toBe("Ada Partner"));
    expect(window.fetch.mock.calls.filter(([url]) => url === "/api/auth/me")).toHaveLength(1);
    expect(window.fetch).toHaveBeenCalledWith("/api/users/7", expect.any(Object));
  });

  it("executes earnings without admin.js and starts partner, notification and earnings controllers", async () => {
    const page = "pages/partner/earnings.html";
    const html = readFileSync(path.join(PROJECT_ROOT, page), "utf8");
    const scripts = externalScripts(page);
    expect(scripts).toEqual(["js/theme-boot.js", "js/icons.js", "js/dropdown.js", "js/datepicker.js", "js/api.js", "js/app.js", "js/utils.js", "js/partner.js", "js/notifications.js", "js/partner-earnings.js", "js/agent.js", "js/branding.js"]);
    expect(scripts).not.toContain("js/admin.js");
    const window = createRealm(page, html);
    scripts.forEach((script) => runScript(window, script));
    await vm.runInContext("Promise.all([initPartnerPage(), Notifications.init(), initEarnings()])", window.__vmContext);
    await vi.waitFor(() => expect(window.document.getElementById("earnings-content").textContent).toContain("Gold"));
    expect(window.document.getElementById("sidebar-user-name").textContent).toBe("Ada Partner");
    expect(window.document.getElementById("notif-bell")).not.toBeNull();
    expect(window.fetch).toHaveBeenCalledWith("/api/partner-bonuses/7", expect.any(Object));
    window.Notifications.stop();
  });

  it("executes the affiliate page and draws the partner's referral link", async () => {
    const page = "pages/partner/affiliate.html";
    const html = readFileSync(path.join(PROJECT_ROOT, page), "utf8");
    const scripts = externalScripts(page);
    expect(scripts).toEqual(["js/theme-boot.js", "js/icons.js", "js/dropdown.js", "js/datepicker.js", "js/api.js", "js/app.js", "js/utils.js", "js/partner.js", "js/notifications.js", "js/partner-affiliate.js", "js/agent.js", "js/branding.js"]);
    const window = createRealm(page, html);
    scripts.forEach((script) => runScript(window, script));
    await vm.runInContext("Promise.all([initPartnerPage(), PartnerAffiliate.load()])", window.__vmContext);
    await vi.waitFor(() => expect(window.document.getElementById("affiliate-link").value)
      .toBe("https://partners.example.com/r/abcde12345"));
    expect(window.document.getElementById("affiliate-card").hidden).toBe(false);
    expect(window.document.getElementById("affiliate-off").hidden).toBe(true);
    expect(window.document.getElementById("affiliate-clicks").textContent).toBe("4");
    expect(window.document.querySelector(".nav-item.active").getAttribute("href")).toBe("affiliate.html");
    window.Notifications?.stop?.();
  });

  it("drops the cached session when /api/auth/me fails without a 401 so the login page cannot bounce back", async () => {
    const page = "pages/partner/earnings.html";
    const html = readFileSync(path.join(PROJECT_ROOT, page), "utf8");
    const window = createRealm(page, html);
    const fallback = window.fetch;
    window.fetch = vi.fn(async (url, init) => {
      if (new URL(url, window.location.href).pathname === "/api/auth/me") return response({ error: "down" }, 503);
      return fallback(url, init);
    });
    window.sessionStorage.setItem("ph_session", JSON.stringify({ id: 7, role: "partner" }));
    ["js/api.js", "js/app.js", "js/utils.js", "js/partner.js"].forEach((script) => runScript(window, script));
    const navigate = vi.fn();
    window.Ui.safeNavigate = navigate;
    window.showToast = vi.fn();
    vi.spyOn(console, "error").mockImplementation(() => {});

    await vm.runInContext("initPartnerPage()", window.__vmContext);

    expect(navigate).toHaveBeenCalledWith("../../index.html");
    expect(window.sessionStorage.getItem("ph_session")).toBeNull();
  });
});
