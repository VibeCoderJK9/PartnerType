import { readFileSync } from "node:fs";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { loadScript } from "./helpers/load-script.js";

globalThis.ApiClient = {
  get: vi.fn(),
  post: vi.fn(),
  clearToken: vi.fn(),
};

loadScript("js/icons.js");
loadScript("js/utils.js");
loadScript("js/app.js");
loadScript("js/partner.js");

const PROJECT_ROOT = path.resolve(import.meta.dirname, "..", "..");
const PAYLOAD = '<img src=x onerror="window.__partnerPwned=true">';

function scriptSources(relativePath) {
  const html = readFileSync(path.join(PROJECT_ROOT, relativePath), "utf8");
  return [...html.matchAll(/<script\s+src="([^"]+)"/g)].filter((match) => match[1] !== "/locales.js").map((match) => match[1]);
}

beforeEach(() => {
  partnerInitializationPromise = null;
  document.body.innerHTML = "";
  document.body.dataset.page = "";
  sessionStorage.clear();
  window.__partnerPwned = false;
  ApiClient.get.mockReset();
  ApiClient.post.mockReset();
  ApiClient.clearToken.mockReset();
  vi.restoreAllMocks();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("partner renderer and session boundary", () => {
  it("rehydrates an authoritative partner and renders stored lead fields inertly", async () => {
    document.body.dataset.page = "partner-dashboard";
    document.body.innerHTML = `
      <aside class="sidebar"></aside>
      <div id="sidebar-user-name"></div><div id="sidebar-user-role"></div><div id="sidebar-avatar"></div>
      <div id="welcome-name"></div><div id="partner-level"></div>
      <div id="my-recent-leads"></div><div id="acceptance-rate"></div><div id="acceptance-rate-bar"></div>
    `;
    ApiClient.get.mockImplementation(async (url) => {
      if (url === "/api/auth/me") return { user: { id: 7, role: "partner", name: PAYLOAD, company: PAYLOAD, level: "gold" } };
      if (url === "/api/stats") return { total_leads: 1, accepted_leads: 1 };
      if (url === "/api/leads") return { leads: [{ id: 1, client_name: PAYLOAD, company: PAYLOAD, status: "new", value: 100, created_at: "2026-08-22" }] };
      if (url === "/api/lead-stages") return { stages: [{ id: 1, key: "new", name: "Nowy", sort_order: 0, kind: "intake", is_terminal: 0, is_commission_trigger: 0, color: "#94a3b8" }] };
      throw new Error(`unexpected ${url}`);
    });

    document.dispatchEvent(new Event("DOMContentLoaded"));
    await vi.waitFor(() => expect(ApiClient.get).toHaveBeenCalledWith("/api/leads"));

    const recent = document.getElementById("my-recent-leads");
    expect(ApiClient.get).toHaveBeenCalledWith("/api/auth/me");
    expect(recent.textContent).toContain(PAYLOAD);
    expect(recent.querySelector("img, script")).toBeNull();
    expect(document.getElementById("sidebar-user-name").textContent).toBe(PAYLOAD);
    expect(window.__partnerPwned).toBe(false);
  });

  it("rejects a cached partner when the server session is not a partner", async () => {
    document.body.dataset.page = "partner-leads";
    document.body.innerHTML = '<tbody id="leads-tbody"></tbody>';
    sessionStorage.setItem("ph_session", JSON.stringify({ id: 7, role: "partner", name: "Cached Partner" }));
    ApiClient.get.mockResolvedValue({ user: { id: 1, role: "admin", name: "Server Admin" } });
    const navigate = vi.spyOn(Ui, "safeNavigate").mockReturnValue(true);

    document.dispatchEvent(new Event("DOMContentLoaded"));
    await vi.waitFor(() => expect(navigate).toHaveBeenCalledWith("../../index.html"));

    expect(ApiClient.get).toHaveBeenCalledTimes(1);
    expect(ApiClient.get).toHaveBeenCalledWith("/api/auth/me");
  });

  it("routes successful lead creation through Ui.safeNavigate", async () => {
    vi.useFakeTimers();
    document.body.innerHTML = `
      <form id="new-lead-form">
        <input id="lead-client-name" class="form-input" value="Ada"><input id="lead-company" class="form-input" value="ACME">
        <input id="lead-email" class="form-input" value="ada@example.com"><input id="lead-value" class="form-input" value="100">
        <input id="lead-phone" class="form-input" value=""><textarea id="lead-notes" class="form-textarea"></textarea>
        <select id="lead-sector" class="form-select"><option value="it" selected>IT</option></select>
        <input id="lead-nip" class="form-input" value="1111111111"><input id="lead-website" class="form-input" value="acme.pl">
        <select id="lead-product" class="form-select"><option value="">Wybierz produkt...</option></select>
        <div id="product-catalog-message" hidden></div><input id="lead-quantity" class="form-input" value="1">
        <button id="submit-lead-btn"></button>
      </form>`;
    ApiClient.get.mockImplementation(async (url) => {
      if (url === "/api/products") return { products: [{ id: 7, name: "Produkt" }] };
      if (url === "/api/partner-bonuses/7") return { partner: { total_percent: 7 } };
      throw new Error(`unexpected ${url}`);
    });
    ApiClient.post.mockResolvedValue({ lead: { id: 9 } });
    const navigate = vi.spyOn(Ui, "safeNavigate").mockReturnValue(true);
    initNewLeadForm({ id: 7, role: "partner" });
    await vi.runAllTimersAsync();
    document.getElementById("lead-product").value = "7";

    document.getElementById("new-lead-form").dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    await vi.runAllTimersAsync();

    expect(navigate).toHaveBeenCalledWith("leads.html");
  });
});

describe("partner classic-script entry points", () => {
  it.each(["dashboard.html", "leads.html", "new-lead.html"])("loads utils before partner on %s", (page) => {
    const scripts = scriptSources(`pages/partner/${page}`);
    expect(scripts.indexOf("../../js/api.js")).toBeLessThan(scripts.indexOf("../../js/app.js"));
    expect(scripts.indexOf("../../js/app.js")).toBeLessThan(scripts.indexOf("../../js/utils.js"));
    expect(scripts.indexOf("../../js/utils.js")).toBeLessThan(scripts.indexOf("../../js/partner.js"));
  });
});
