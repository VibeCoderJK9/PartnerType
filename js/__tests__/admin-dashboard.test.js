import { readFileSync } from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { Window } from "happy-dom";
import { describe, expect, it, vi } from "vitest";
import { appStub } from "./helpers/copy.js";
import { isLeadListUrl, serveLeadList } from "./helpers/lead-list-server.js";

const PROJECT_ROOT = path.resolve(import.meta.dirname, "..", "..");
const DASHBOARD_PAGE = "pages/admin/dashboard.html";
const EXPECTED_SCRIPTS = [
  "js/theme-boot.js",
  "js/icons.js",
  "js/dropdown.js",
  "js/datepicker.js",
  "js/api.js",
  "js/app.js",
  "js/utils.js",
  "js/admin-shell.js",
  "js/admin-dashboard.js",
  "js/charts.js",
  "js/notifications.js",
  "js/agent.js",
  // The producer's name and logo (js/branding.js), last: it only swaps
  // images and one caption, nothing above depends on it.
  "js/branding.js",
];

function orderedScripts(pagePath = DASHBOARD_PAGE) {
  const html = readFileSync(path.join(PROJECT_ROOT, pagePath), "utf8");
  return [...html.matchAll(/<script\s+src="([^"]+)"/g)].filter((match) => match[1] !== "/locales.js").map((match) =>
    path.normalize(path.join(path.dirname(pagePath), match[1])),
  );
}

function runClassicScript(window, relativePath) {
  const absolutePath = path.join(PROJECT_ROOT, relativePath);
  const source = readFileSync(absolutePath, "utf8");
  const context = window.__vmContext || (window.__vmContext = vm.createContext(window));
  vm.runInContext(source, context, { filename: absolutePath });
}

// Mirrors the seeded pipeline exactly (migrations/default_stages.json).
const STAGES = [
  { id: 1, key: "contact", name: "Nawiązanie kontaktu", sort_order: 0, kind: "progress", is_terminal: 0, color: "#94a3b8", active: 1 },
  { id: 2, key: "presentation", name: "Prezentacja", sort_order: 1, kind: "progress", is_terminal: 0, color: "#3b82f6", active: 1 },
  { id: 3, key: "offer", name: "Oferta", sort_order: 2, kind: "progress", is_terminal: 0, color: "#8b5cf6", active: 1 },
  { id: 4, key: "negotiation", name: "Negocjacje", sort_order: 3, kind: "progress", is_terminal: 0, color: "#f59e0b", active: 1 },
  { id: 5, key: "won", name: "Wygrany", sort_order: 4, kind: "win", is_terminal: 1, color: "#10b981", active: 1 },
  { id: 6, key: "lost", name: "Przegrany", sort_order: 5, kind: "lose", is_terminal: 1, color: "#ef4444", active: 1 },
];

function dashboardData(payload = '<img src=x onerror="window.__dashboardPwned=true">') {
  return {
    user: { id: 1, role: "admin", name: "Ada Admin", company: "PartnerType" },
    stats: {
      new_leads: 1,
      pending_leads: 1,
      accepted_leads: 0,
      total_commissions: 0,
      total_partners: 1,
      total_leads: 1,
      trends: {},
      // Two axes, as the API publishes them: registration_dist counts the
      // producer's DECISION (what the KPI cards link to and count), status_dist
      // counts the SALES STAGE (what the donut and funnel draw). Decision keys
      // never appear inside status_dist.
      registration_dist: { pending: 1, needs_info: 0, accepted: 0, rejected: 0 },
      status_dist: { null: 1 },
    },
    leads: [{
      id: "L-7' onclick='window.__dashboardPwned=true",
      client_name: payload,
      company: payload,
      partner_name: payload,
      value: 2500,
      registration_status: "pending",
      status: null,
      created_at: "2026-08-22T10:00:00Z",
      notes: payload,
    }],
    partners: [{
      id: 4,
      name: payload,
      email: payload,
      accepted_leads: 2,
      total_earnings: 500,
    }],
  };
}

function createControllerRealm(data = dashboardData(), stages = STAGES) {
  const window = new Window({ url: "http://localhost:8000/pages/admin/dashboard.html" });
  window.document.body.dataset.page = "admin-dashboard";
  // Mirrors pages/admin/dashboard.html: each counting KPI value lives inside
  // the card that links to its own status, which is how renderKpis pairs the
  // number with the filter the card navigates to.
  //
  // These four cards mirror pages/admin/dashboard.html exactly -- the same
  // statuses, the same ?registration_status= links, the same element ids. They
  // must match the page, or no test would notice the cards reading the wrong
  // column.
  window.document.body.innerHTML = `
    <span id="welcome-name"></span><span id="new-leads-badge"></span>
    <span id="kpi-partners"></span><span id="kpi-total-leads"></span>
    <a class="kpi-card" href="leads.html?registration_status=pending" data-kpi-status="pending">
      <div class="kpi-value" id="kpi-new-leads">0</div>
    </a>
    <a class="kpi-card" href="leads.html?overdue=1" data-kpi-metric="overdue">
      <div class="kpi-value" id="kpi-pending">0</div>
    </a>
    <a class="kpi-card" href="leads.html?registration_status=needs_info" data-kpi-status="needs_info">
      <div class="kpi-value" id="kpi-accepted">0</div>
    </a>
    <a class="kpi-card" href="leads.html?registration_status=accepted" data-kpi-status="accepted" data-kpi-metric="commissions">
      <div class="kpi-value" id="kpi-commissions">0</div>
    </a>
    <section id="dashboard-error" hidden>
      <button id="dashboard-retry" type="button" data-dashboard-action="retry">Ponów</button>
    </section>
    <table><tbody id="recent-leads-tbody"></tbody></table>
    <div id="recent-leads-empty" hidden></div>
    <canvas id="leads-chart"></canvas><div id="leads-chart-empty" hidden></div>
    <div id="activity-list"></div><div id="top-partners"></div>
  `;
  window.ApiClient = {
    get: vi.fn((url) => {
      if (url === "/api/stats") return Promise.resolve(data.stats);
      if (isLeadListUrl(url)) return Promise.resolve(serveLeadList(url, data.leads));
      if (url === "/api/partners") return Promise.resolve({ partners: data.partners });
      if (url === "/api/lead-stages") return Promise.resolve({ stages });
      throw new Error(`Unexpected GET ${url}`);
    }),
    patch: vi.fn().mockImplementation((url, body) => Promise.resolve({
      lead: { ...data.leads[0], ...body },
    })),
    // Quick accept goes through the registration endpoint.
    post: vi.fn().mockImplementation(() => Promise.resolve({
      lead: { ...data.leads[0], registration_status: "accepted" },
    })),
  };
  // Real copy, not an identity stub: the widgets read their text from the
  // dictionary now, so `t: key => key` would make every assertion about
  // user-visible text compare against a raw key.
  window.App = appStub('pl');
  window.Charts = {
    drawLine: vi.fn(),
    drawBars: vi.fn(),
    drawDonut: vi.fn(),
    renderTrend: vi.fn(),
  };
  window.formatCurrency = vi.fn((value) => `${value} PLN`);
  window.formatDate = vi.fn(() => "22 sie 2026");
  window.getInitials = vi.fn(() => "AA");
  window.getAvatarColorClass = vi.fn(() => "bg-nebula-indigo");
  window.handleApiError = vi.fn();
  window.showToast = vi.fn();
  runClassicScript(window, "js/icons.js");
  runClassicScript(window, "js/utils.js");
  window.Ui.safeNavigate = vi.fn().mockReturnValue(true);
  runClassicScript(window, "js/admin-dashboard.js");
  return { window, data };
}

describe("AdminDashboard", () => {
  it("loads and renders the dashboard exactly once with inert persisted values", async () => {
    const { window, data } = createControllerRealm();

    await Promise.all([
      window.AdminDashboard.init(data.user),
      window.AdminDashboard.init(data.user),
    ]);

    expect(window.ApiClient.get).toHaveBeenCalledTimes(4);
    expect(window.document.getElementById("welcome-name").textContent).toBe("Ada");
    expect(window.document.body.textContent).toContain(data.leads[0].client_name);
    expect(window.document.body.textContent).toContain(data.partners[0].email);
    expect(window.document.querySelector("img, script")).toBeNull();
    expect(window.__dashboardPwned).toBeUndefined();
    expect(window.document.querySelector("[onclick]")).toBeNull();
  });

  it("delegates lead actions through stored record IDs", async () => {
    const { window, data } = createControllerRealm();
    await window.AdminDashboard.init(data.user);

    const accept = window.document.querySelector('[data-action="accept"]');
    accept.click();
    await vi.waitFor(() => expect(window.ApiClient.post).toHaveBeenCalledTimes(1));

    expect(window.ApiClient.post).toHaveBeenCalledWith(
      `/api/leads/${encodeURIComponent(data.leads[0].id)}/registration`,
      expect.objectContaining({ action: "accept" }),
    );
  });

  it("takes a rejection here, with its reason, instead of handing the producer to another screen", async () => {
    const { window, data } = createControllerRealm();
    await window.AdminDashboard.init(data.user);

    window.document.querySelector('[data-action="reject"]').click();

    // A refusal owes the partner a reason. It is typed under the row, so the
    // producer keeps their place on the dashboard.
    const form = window.document.querySelector(".row-inline-form");
    expect(form).not.toBeNull();
    expect(window.Ui.safeNavigate).not.toHaveBeenCalled();
    expect(window.ApiClient.post).not.toHaveBeenCalled();

    const confirm = [...form.querySelectorAll("button")]
      .find((button) => button.textContent === "Odrzuć leada");
    confirm.click();
    expect(window.ApiClient.post).not.toHaveBeenCalled();

    form.querySelector("textarea").value = "Klient obsługiwany bezpośrednio.";
    confirm.click();

    await vi.waitFor(() => expect(window.ApiClient.post).toHaveBeenCalledTimes(1));
    expect(window.ApiClient.post).toHaveBeenCalledWith(
      `/api/leads/${encodeURIComponent(data.leads[0].id)}/registration`,
      expect.objectContaining({
        action: "reject",
        message: "Klient obsługiwany bezpośrednio.",
      }),
    );
    expect(window.Ui.safeNavigate).not.toHaveBeenCalled();
  });

  it("offers the third decision, asking the partner for information, without leaving the widget", async () => {
    const { window, data } = createControllerRealm();
    await window.AdminDashboard.init(data.user);

    const ask = window.document.querySelector('[data-action="request_info"]');
    expect(ask).not.toBeNull();
    ask.click();

    const form = window.document.querySelector(".row-inline-form");
    form.querySelector("textarea").value = "Proszę o NIP klienta.";
    [...form.querySelectorAll("button")]
      .find((button) => button.textContent === "Wyślij prośbę").click();

    await vi.waitFor(() => expect(window.ApiClient.post).toHaveBeenCalledTimes(1));
    expect(window.ApiClient.post).toHaveBeenCalledWith(
      `/api/leads/${encodeURIComponent(data.leads[0].id)}/registration`,
      expect.objectContaining({ action: "request_info", message: "Proszę o NIP klienta." }),
    );
    expect(window.Ui.safeNavigate).not.toHaveBeenCalled();
  });

  it("renders plain zeroes for a stats payload whose KPI counts are all zero", async () => {
    const data = dashboardData("Safe value");
    data.stats = {
      ...data.stats,
      new_leads: 0,
      pending_leads: 0,
      accepted_leads: 0,
      total_commissions: 0,
      total_partners: 0,
      total_leads: 0,
      overdue_leads: 0,
      registration_dist: { pending: 0, needs_info: 0, accepted: 0, rejected: 0 },
    };
    const { window } = createControllerRealm(data);

    await window.AdminDashboard.init(data.user);

    for (const id of ["kpi-new-leads", "kpi-pending", "kpi-accepted", "kpi-commissions"]) {
      expect(window.document.getElementById(id).textContent).toBe("0");
    }
  });

  it("shows each KPI card the count for the status its own link filters by", async () => {
    // Regression: the cards were fed stats.new_leads / pending_leads /
    // accepted_leads, which are aggregates by stage KIND and were also offset
    // by one card. "Do zatwierdzenia" claimed 5 while its filtered list held 2.
    const data = dashboardData("Safe value");
    data.stats = {
      ...data.stats,
      // Deliberately unlike the per-status truth below, so reading the old
      // aggregates instead of registration_dist fails this test.
      new_leads: 30,
      pending_leads: 40,
      accepted_leads: 50,
      registration_dist: { pending: 7, needs_info: 6, accepted: 3, rejected: 2 },
      // The sales-stage axis carries different numbers on purpose: a card that
      // reads this one instead fails.
      status_dist: { contact: 11, won: 4, lost: 2 },
    };
    const { window } = createControllerRealm(data);

    await window.AdminDashboard.init(data.user);

    for (const card of window.document.querySelectorAll(".kpi-card[data-kpi-status]")) {
      if (card.dataset.kpiMetric === "commissions") continue;
      const status = card.dataset.kpiStatus;
      expect(card.querySelector(".kpi-value").textContent, status).toBe(
        String(data.stats.registration_dist[status]),
      );
    }
    // The money card keeps showing money, not a lead count.
    expect(window.document.getElementById("kpi-commissions").textContent).not.toBe("3");
    // "Po terminie" is not a status at all: it reads stats.overdue_leads.
    expect(window.document.getElementById("kpi-pending").textContent)
      .toBe(String(data.stats.overdue_leads ?? 0));
  });

  it("falls back to zero for a status missing from the distribution", async () => {
    const data = dashboardData("Safe value");
    data.stats = { ...data.stats, registration_dist: { pending: 4 } };
    const { window } = createControllerRealm(data);

    await window.AdminDashboard.init(data.user);

    // kpi-new-leads is the 'pending' card; kpi-pending is the overdue card and
    // kpi-accepted the needs_info one -- the element ids do not match their
    // labels.
    expect(window.document.getElementById("kpi-new-leads").textContent).toBe("4");
    expect(window.document.getElementById("kpi-pending").textContent).toBe("0");
    expect(window.document.getElementById("kpi-accepted").textContent).toBe("0");
  });

  it("counts the decision axis, not the sales stage, so the cards are not permanently zero", async () => {
    const data = dashboardData("Safe value");
    data.stats = {
      ...data.stats,
      // Exactly the shape the API returns: stage keys on one axis, decision
      // keys on the other. Reading status_dist for a decision key misses every
      // time, which is the bug this pins.
      status_dist: { null: 3, contact: 2, won: 1, lost: 1 },
      registration_dist: { pending: 3, needs_info: 2, accepted: 4, rejected: 1 },
    };
    const { window } = createControllerRealm(data);

    await window.AdminDashboard.init(data.user);

    expect(window.document.querySelector('[data-kpi-status="pending"] .kpi-value').textContent).toBe("3");
    expect(window.document.querySelector('[data-kpi-status="needs_info"] .kpi-value').textContent).toBe("2");
  });

  it("routes KPI cards through safe navigation with their status filter", async () => {
    const { window, data } = createControllerRealm();
    await window.AdminDashboard.init(data.user);

    window.document.querySelector('[data-kpi-status="pending"]').click();

    // The dashboard's KPI cards filter on the producer's DECISION now, which
    // is what their labels have always described.
    expect(window.Ui.safeNavigate).toHaveBeenCalledWith(
      "leads.html?registration_status=pending",
    );
  });

  it("routes recent lead rows through an opaque closure-owned record key", async () => {
    const { window, data } = createControllerRealm();
    await window.AdminDashboard.init(data.user);

    const row = window.document.querySelector("tr[data-record-key]");
    expect(row.dataset.recordKey).not.toContain(data.leads[0].id);
    row.querySelector(".cell-primary").click();

    expect(window.Ui.safeNavigate).toHaveBeenCalledWith(
      `leads.html?lead=${encodeURIComponent(data.leads[0].id)}`,
    );
  });

  it("shows the stated retryable error when a dashboard fetch rejects", async () => {
    const { window, data } = createControllerRealm();
    window.ApiClient.get.mockRejectedValueOnce(new Error("offline"));

    await window.AdminDashboard.init(data.user);

    expect(window.document.getElementById("dashboard-error").hidden).toBe(false);
    expect(window.document.getElementById("dashboard-error").textContent).toContain("Ponów");
    expect(window.handleApiError).toHaveBeenCalledWith(expect.any(Error), "AdminDashboard.load");
  });

  it("reissues all dashboard fetches when retry is invoked", async () => {
    const { window, data } = createControllerRealm();
    window.ApiClient.get.mockRejectedValueOnce(new Error("offline"));
    await window.AdminDashboard.init(data.user);
    const callsAfterFailure = window.ApiClient.get.mock.calls.length;

    window.ApiClient.get.mockImplementation((url) => {
      if (url === "/api/stats") return Promise.resolve(data.stats);
      if (isLeadListUrl(url)) return Promise.resolve(serveLeadList(url, data.leads));
      if (url === "/api/partners") return Promise.resolve({ partners: data.partners });
      if (url === "/api/lead-stages") return Promise.resolve({ stages: STAGES });
      throw new Error(`Unexpected GET ${url}`);
    });
    window.document.getElementById("dashboard-retry").click();

    await vi.waitFor(() => expect(window.ApiClient.get.mock.calls.length).toBe(callsAfterFailure + 4));
    expect(window.document.getElementById("dashboard-error").hidden).toBe(true);
  });

  it("renders a markup-bearing lead name as literal table text", async () => {
    const payload = '<svg onload="window.__dashboardPwned=true">';
    const data = dashboardData(payload);
    const { window } = createControllerRealm(data);

    await window.AdminDashboard.init(data.user);

    expect(window.document.querySelector(".cell-primary").textContent).toBe(payload);
    // The activity feed renders Lucide icons, so SVG on the page is expected
    // now. Every one must be ours -- an <svg> parsed out of the payload would
    // not carry the generated icon class.
    const svgs = [...window.document.querySelectorAll("svg")];
    expect(svgs.length).toBeGreaterThan(0);
    expect(svgs.every((node) => node.classList.contains("icon"))).toBe(true);
    expect(window.document.querySelector("[onload]")).toBeNull();
    expect(window.__dashboardPwned).toBeUndefined();
  });

  it("draws the declared lead activity canvas when monthly data exists", async () => {
    const data = dashboardData("Safe value");
    data.stats.monthly = [{ label: "Sie", leads: 3 }];
    const { window } = createControllerRealm(data);

    await window.AdminDashboard.init(data.user);

    await vi.waitFor(() => expect(window.Charts.drawLine).toHaveBeenCalledWith(
      "leads-chart",
      data.stats.monthly,
      { showCommission: false },
    ));
    expect(window.document.getElementById("leads-chart").hidden).toBe(false);
    expect(window.document.getElementById("leads-chart-empty").hidden).toBe(true);
  });

  it("replaces an empty lead activity canvas with an explicit empty state", async () => {
    const data = dashboardData("Safe value");
    data.stats.monthly = [];
    const { window } = createControllerRealm(data);

    await window.AdminDashboard.init(data.user);

    expect(window.Charts.drawLine).not.toHaveBeenCalled();
    expect(window.document.getElementById("leads-chart").hidden).toBe(true);
    expect(window.document.getElementById("leads-chart-empty").hidden).toBe(false);
  });
});

describe("dashboard entry point", () => {
  it("uses the required classic-script dependency order", () => {
    expect(orderedScripts()).toEqual(EXPECTED_SCRIPTS);
  });

  it("executes the actual ordered scripts without missing or duplicate globals", async () => {
    const data = dashboardData("Safe value");
    const window = new Window({ url: "http://localhost:8000/pages/admin/dashboard.html" });
    window.document.write(readFileSync(path.join(PROJECT_ROOT, DASHBOARD_PAGE), "utf8"));
    window.document.body.dataset.page = "admin-dashboard";
    window.HTMLCanvasElement.prototype.getContext = () => ({
      scale: vi.fn(), beginPath: vi.fn(), moveTo: vi.fn(), lineTo: vi.fn(), stroke: vi.fn(),
      fill: vi.fn(), arc: vi.fn(), closePath: vi.fn(), fillText: vi.fn(), setLineDash: vi.fn(),
      quadraticCurveTo: vi.fn(), createLinearGradient: vi.fn(() => ({ addColorStop: vi.fn() })),
    });
    window.fetch = vi.fn(async (url) => {
      const pathname = new URL(url, window.location.href).pathname;
      const body = pathname === "/api/auth/me" ? { user: data.user }
        : pathname === "/api/stats" ? data.stats
          : pathname === "/api/leads" ? { leads: [], total: 0 }
            : pathname === "/api/partners" ? { partners: [] }
              : pathname === "/api/notifications" ? { notifications: [], unread_count: 0 }
                : {};
      return { ok: true, status: 200, json: async () => body };
    });
    window.console = console;

    for (const scriptPath of orderedScripts()) runClassicScript(window, scriptPath);
    window.document.dispatchEvent(new window.Event("DOMContentLoaded"));
    await window.AdminShell.init();
    await window.AdminDashboard.init(data.user);

    await vi.waitFor(() => expect(window.fetch).toHaveBeenCalledWith(
      "/api/stats",
      expect.objectContaining({ credentials: "same-origin" }),
    ));
    expect(window.AdminShell).toBeDefined();
    expect(window.AdminDashboard).toBeDefined();
  });
});

describe("configurable stage pipeline on A1", () => {
  it("ignores a kpi-status that does not resolve against the fetched pipeline", async () => {
    const { window, data } = createControllerRealm();
    await window.AdminDashboard.init(data.user);
    window.Ui.safeNavigate.mockClear();

    window.document.body.insertAdjacentHTML(
      "beforeend",
      '<a class="kpi-card" href="leads.html?status=ghost-stage" data-kpi-status="ghost-stage"></a>',
    );
    window.document.querySelector('[data-kpi-status="ghost-stage"]').click();

    expect(window.Ui.safeNavigate).not.toHaveBeenCalled();
  });

  it("renders a recent-lead row's badge from the producer's decision, not from the sales stage", async () => {
    // The dashboard answers "what has been decided?" at a glance. The sales
    // stage --- which the producer can rename freely --- belongs on the
    // leads screen, and is asserted there.
    const data = dashboardData("Safe value");
    data.leads[0].registration_status = "accepted";
    const { window } = createControllerRealm(data);

    await window.AdminDashboard.init(data.user);

    expect(window.document.getElementById("recent-leads-tbody").textContent)
      .toContain("Zaakceptowany");
  });

  it("describes an activity-feed transition using the producer's own stage name", async () => {
    const customStage = {
      id: 7, key: "negocjacje", name: "Negocjacje", sort_order: 6,
      kind: "progress", is_terminal: 0, is_commission_trigger: 0, color: "#8266ff", active: 1,
    };
    const data = dashboardData("Safe value");
    data.leads[0].status = "negocjacje";
    const { window } = createControllerRealm(data, [...STAGES, customStage]);

    await window.AdminDashboard.init(data.user);

    expect(window.document.getElementById("activity-list").textContent).toContain("Negocjacje");
  });

  it("renders the shared retryable error state, not a screen of unknown values, when the stage-list fetch fails", async () => {
    const { window, data } = createControllerRealm();
    window.ApiClient.get.mockImplementation((url) => {
      if (url === "/api/lead-stages") return Promise.reject(new Error("offline"));
      if (url === "/api/stats") return Promise.resolve(data.stats);
      if (isLeadListUrl(url)) return Promise.resolve(serveLeadList(url, data.leads));
      if (url === "/api/partners") return Promise.resolve({ partners: data.partners });
      throw new Error(`Unexpected GET ${url}`);
    });

    await window.AdminDashboard.init(data.user);

    expect(window.document.getElementById("dashboard-error").hidden).toBe(false);
    expect(window.handleApiError).toHaveBeenCalledWith(expect.any(Error), "AdminDashboard.load");
  });
});
