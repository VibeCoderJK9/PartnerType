import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { Window } from "happy-dom";
import { describe, expect, it, vi } from "vitest";
import { appStub } from "./helpers/copy.js";
import { isLeadListUrl, serveLeadList } from "./helpers/lead-list-server.js";

const PROJECT_ROOT = path.resolve(import.meta.dirname, "..", "..");
const LEADS_PAGE = "pages/admin/leads.html";
const EXPECTED_SCRIPTS = [
  "js/theme-boot.js",
  "js/icons.js",
  "js/dropdown.js",
  "js/datepicker.js",
  "js/api.js",
  "js/app.js",
  "js/utils.js",
  "js/admin-shell.js",
  // The shared producer-question renderer, loaded before the controller that
  // calls it (the assignment form uses the same component the partner's
  // submission form does).
  "js/lead-fields.js",
  "js/admin-leads.js",
  "js/charts.js",
  "js/notifications.js",
  "js/comments.js",
  "js/leads6.js",
  // The docked AI chat, last on every screen: it builds itself from App,
  // ApiClient and Icons, and it has to exist on every page or it cannot stay
  // open across a navigation.
  "js/agent.js",
  // The producer's name and logo (js/branding.js), last: it only swaps
  // images and one caption, nothing above depends on it.
  "js/branding.js",
];

function orderedScripts(pagePath = LEADS_PAGE) {
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
  return context;
}

function invoke(window, source) {
  return vm.runInContext(source, window.__vmContext);
}

// Mirrors the seeded pipeline exactly (migrations/default_stages.json) so
// byte-identical old-vocabulary behavior is provable against real keys.
const STAGES = [
  { id: 1, key: "contact", name: "Nawiązanie kontaktu", sort_order: 0, kind: "progress", is_terminal: 0, color: "#94a3b8", active: 1 },
  { id: 2, key: "presentation", name: "Prezentacja", sort_order: 1, kind: "progress", is_terminal: 0, color: "#3b82f6", active: 1 },
  { id: 3, key: "offer", name: "Oferta", sort_order: 2, kind: "progress", is_terminal: 0, color: "#8b5cf6", active: 1 },
  { id: 4, key: "negotiation", name: "Negocjacje", sort_order: 3, kind: "progress", is_terminal: 0, color: "#f59e0b", active: 1 },
  { id: 5, key: "won", name: "Wygrany", sort_order: 4, kind: "win", is_terminal: 1, color: "#10b981", active: 1 },
  { id: 6, key: "lost", name: "Przegrany", sort_order: 5, kind: "lose", is_terminal: 1, color: "#ef4444", active: 1 },
];

function leadData(payload = '<img src=x onerror="window.__leadPwned=true">') {
  return Array.from({ length: 6 }, (_, index) => ({
    id: index === 0 ? "lead-' onclick='window.__leadPwned=true" : `lead-${index + 1}`,
    client_name: index === 0 ? payload : `Client ${index + 1}`,
    client_email: index === 0 ? payload : `client${index + 1}@example.com`,
    client_phone: index === 0 ? payload : "+48123456789",
    company: index === 0 ? payload : `Company ${index + 1}`,
    partner_id: 7,
    partner_name: index === 0 ? payload : "Partner Safe",
    sector: index === 0 ? payload : "IT",
    notes: index === 0 ? payload : "Notes",
    nip: index === 0 ? payload : "1234567890",
    website: index === 0 ? payload : "https://example.com",
    product: index === 0 ? payload : "Partner CRM",
    quantity: index === 0 ? 0 : index + 1,
    queue_position: index === 0 ? 5 : 1,
    queue_share: index === 0 ? 0 : 100,
    commission_breakdown: { tier: 80, recurring: 20, cash: 0 },
    value: 2500 + index,
    commission: 100,
    commission_projected: 100,
    // Two axes: the producer's decision, and the sales
    // stage that only exists once the deal is accepted.
    registration_status: "pending",
    status: null,
    revision: 2 + index,
    created_at: `2026-08-${String(10 + index).padStart(2, "0")}T10:00:00Z`,
  }));
}

/** Every lead-list request the controller actually sent, as parsed queries. */
function leadListQueries(window) {
  return window.ApiClient.get.mock.calls
    .map(([url]) => String(url))
    .filter(isLeadListUrl)
    .map((url) => new URLSearchParams(url.split("?")[1] || ""));
}

function createLeadRealm(leads = leadData(), query = "", stages = STAGES) {
  const window = new Window({ url: `http://localhost:8000/pages/admin/leads.html${query}` });
  window.document.body.dataset.page = "admin-leads";
  window.document.body.innerHTML = `
    <input id="leads-search"><span id="leads-count"></span>
    <input id="filter-date-from"><input id="filter-date-to">
    <input id="filter-value" type="number"><input id="filter-overdue" type="checkbox">
    <div id="status-select-wrapper"><input id="status-search-input"><input id="filter-status"><div id="status-dropdown-list"></div></div>
    <div id="partner-select-wrapper"><input id="partner-search-input"><input id="filter-partner"><div id="partner-dropdown-list"></div></div>
    <table><thead id="leads-table-head"><tr><th data-sort="client_name">Klient</th></tr></thead><tbody id="leads-tbody"></tbody></table>
    <div id="pagination"><span class="pagination-info"></span><div class="pagination-btns"></div></div>
    <div id="leads-loading-state" hidden></div>
    <div id="leads-empty-state" hidden><h2 id="leads-empty-title"></h2><p id="leads-empty-body"></p></div>
    <div id="leads-error-state" hidden><button id="leads-retry-btn"></button></div>
    <div id="lead-modal"></div>
    <div id="modal-lead-id"></div><div id="modal-lead-name"></div><div id="modal-lead-company"></div>
    <div id="modal-lead-email"></div><div id="modal-lead-phone"></div><div id="modal-lead-value"></div>
    <div id="modal-lead-date"></div><div id="modal-lead-partner"></div><div id="modal-lead-notes"></div>
    <div id="modal-lead-sector"></div><div id="modal-lead-contact"></div><div id="modal-lead-nip"></div>
    <div id="modal-lead-website"></div><div id="modal-lead-product"></div><div id="modal-lead-quantity"></div>
    <div id="modal-queue-position" hidden></div><div id="modal-lead-status"></div><input id="modal-commission">
    <input id="modal-commission-pct"><button id="save-commission-pct-btn"></button>
    <p id="modal-commission-pct-hint"></p>
    <div id="modal-commission-tier"></div><div id="modal-commission-recurring"></div>
    <div id="modal-commission-cash"></div><div id="modal-commission-state"></div><div id="modal-commission-total"></div>
    <div id="modal-comments-container"></div><button id="lead-accept-btn"></button>
    <div id="lead-advance-controls"></div>
    <button id="lead-reject-btn"></button><button id="save-commission-btn"></button>
    <button id="lead-more-info-btn"></button><div id="lead-reject-form" hidden></div>
    <textarea id="lead-reject-reason"></textarea><button id="lead-reject-confirm-btn" disabled></button>
    <div id="lead-more-info-form" hidden></div><input type="checkbox" name="missing-field" value="NIP">
    <textarea id="lead-more-info-note"></textarea><button id="lead-more-info-confirm-btn"></button>
  `;
  const firstLead = leads[0] || leadData("Safe partner")[0];
  const partners = [{ id: 7, name: firstLead.partner_name, company: firstLead.company }];
  window.ApiClient = {
    get: vi.fn((url) => {
      if (isLeadListUrl(url)) return Promise.resolve(serveLeadList(url, leads, { now: () => window.Date.now() }));
      if (url === "/api/partners") return Promise.resolve({ partners });
      if (url === "/api/lead-stages") return Promise.resolve({ stages });
      if (url === "/api/audit") return Promise.resolve({ logs: [
        { entity: "lead", entity_id: String(leads[0]?.id), user_role: "admin", user_name: "Ada Admin", created_at: "2026-08-20T10:00:00Z" },
      ] });
      const id = decodeURIComponent(url.replace("/api/leads/", ""));
      return Promise.resolve({ lead: leads.find((lead) => String(lead.id) === id) });
    }),
    patch: vi.fn((url, body) => {
      const id = decodeURIComponent(url.replace("/api/leads/", ""));
      return Promise.resolve({ lead: { ...leads.find((lead) => String(lead.id) === id), ...body } });
    }),
    // Comments still POST; the registration endpoint answers with the lead
    // as the producer's decision left it, exactly like the real handler.
    post: vi.fn((url, body) => {
      const match = /^\/api\/leads\/(.+)\/registration$/.exec(url);
      if (!match) return Promise.resolve({ comment: { id: 1 } });
      const id = decodeURIComponent(match[1]);
      const lead = leads.find((entry) => String(entry.id) === id);
      const decided = {
        accept: "accepted", reject: "rejected", request_info: "needs_info",
      }[body?.action];
      return Promise.resolve({
        lead: {
          ...lead,
          registration_status: decided,
          status: decided === "accepted" ? "contact" : null,
          revision: Number(lead?.revision ?? 0) + 1,
        },
      });
    }),
  };
  window.App = { ...appStub('pl'), applyTranslations: vi.fn() };
  window.formatCurrency = vi.fn((value) => `${value} PLN`);
  window.formatDate = vi.fn(() => "22 sie 2026");
  window.getInitials = vi.fn(() => "PS");
  window.getAvatarColorClass = vi.fn(() => "bg-nebula-indigo");
  window.getStatusBadge = vi.fn((status) => `<span class="badge">${status}</span>`);
  window.openModal = vi.fn();
  window.closeModal = vi.fn();
  window.showToast = vi.fn();
  window.handleApiError = vi.fn();
  window.Comments = { load: vi.fn().mockResolvedValue(undefined) };
  window.sessionStorage.setItem("ph_session", JSON.stringify({ id: 1, role: "admin" }));
  runClassicScript(window, "js/icons.js");
  runClassicScript(window, "js/utils.js");
  runClassicScript(window, "js/admin-leads.js");
  return { window, leads };
}

describe("admin lead controller boundary", () => {
  it.each([true, false])("exports only an available revision to bulk row metadata (%s)", async (hasRevision) => {
    const leads = leadData();
    if (!hasRevision) delete leads[0].revision;
    const { window } = createLeadRealm(leads);
    await window.AdminLeads.init({ id: 1, role: "admin" });
    const row = [...window.document.querySelectorAll('#leads-tbody tr')]
      .find(row => row.dataset.id === String(leads[0].id));
    expect(row).toBeDefined();
    expect(row.dataset.revision).toBe(hasRevision ? String(leads[0].revision) : undefined);
  });

  it("loads, filters and sorts through the server without executable row markup", async () => {
    const { window } = createLeadRealm();
    await window.AdminLeads.init({ id: 1, role: "admin" });

    expect(window.ApiClient.get).toHaveBeenCalledTimes(4);
    expect(window.document.querySelectorAll("#leads-tbody tr")).toHaveLength(6);
    expect(window.document.querySelector("[onclick], [onerror]")).toBeNull();

    // Searching is a request now, not a pass over an array the browser was
    // holding, so the assertion has to wait for the answer -- and the query has
    // to carry the term, or the server would be sifting nothing.
    window.document.getElementById("leads-search").value = "Client 2";
    window.document.getElementById("leads-search").dispatchEvent(new window.Event("input", { bubbles: true }));
    await vi.waitFor(() => {
      expect(window.document.getElementById("leads-count").textContent).toBe("1");
    });
    expect(leadListQueries(window).at(-1).get("q")).toBe("Client 2");

    window.document.querySelector('[data-sort="client_name"]').click();
    await vi.waitFor(() => {
      expect(leadListQueries(window).at(-1).get("sort")).toBe("client_name");
    });
    expect(window.document.querySelectorAll("#leads-tbody tr")).toHaveLength(1);
  });

  it("asks the server for the next page instead of slicing one it already has", async () => {
    // Thirty leads against a page of twenty-five: the second page exists only
    // on the server; the browser never holds them all and cuts them locally.
    const many = Array.from({ length: 30 }, (_, index) => ({
      ...leadData("safe")[1],
      id: `bulk-${String(index + 1).padStart(2, "0")}`,
      client_name: `Klient ${index + 1}`,
      created_at: `2026-08-${String(1 + index).padStart(2, "0")}T10:00:00Z`,
    }));
    const { window } = createLeadRealm(many);
    await window.AdminLeads.init({ id: 1, role: "admin" });

    expect(window.document.querySelectorAll("#leads-tbody tr")).toHaveLength(25);
    expect(window.document.querySelector(".pagination-info").textContent).toBe("1–25 z 30");
    expect(leadListQueries(window).at(-1).get("page")).toBe("1");

    window.document.querySelector('[data-page="2"]').click();
    await vi.waitFor(() => {
      expect(window.document.querySelectorAll("#leads-tbody tr")).toHaveLength(5);
    });
    expect(leadListQueries(window).at(-1).get("page")).toBe("2");
    expect(window.document.querySelector(".pagination-info").textContent).toBe("26–30 z 30");
  });

  it("renders every persisted list and partner field inertly", async () => {
    const { window, leads } = createLeadRealm();
    await window.AdminLeads.init({ id: 1, role: "admin" });

    expect(window.document.getElementById("leads-tbody").textContent).toContain(leads[0].client_name);
    expect(window.document.body.textContent).toContain(leads[0].partner_name);
    expect(window.document.querySelector("img, script, [onclick], [onerror]")).toBeNull();
    expect(window.__leadPwned).toBeUndefined();
  });

  it("delegates detail and accept through records held in memory", async () => {
    const { window, leads } = createLeadRealm();
    await window.AdminLeads.init({ id: 1, role: "admin" });

    window.document.querySelector('[data-action="detail"]').click();
    await vi.waitFor(() => expect(window.openModal).toHaveBeenCalledWith("lead-modal"));
    expect(window.ApiClient.get).toHaveBeenCalledWith(`/api/leads/${encodeURIComponent(leads[0].id)}`);
    expect(window.document.getElementById("modal-lead-notes").textContent).toBe(leads[0].notes);
    expect(window.Comments.load).toHaveBeenCalledWith(
      leads[0].id,
      "#modal-comments-container",
      { id: 1, role: "admin" },
    );

    window.document.querySelector('[data-action="accept"]').click();
    await vi.waitFor(() => expect(window.ApiClient.post).toHaveBeenCalledTimes(1));
    expect(window.ApiClient.post).toHaveBeenCalledWith(
      `/api/leads/${encodeURIComponent(leads[0].id)}/registration`,
      expect.objectContaining({ action: "accept" }),
    );
  });

  it("keeps API errors on the shared inert error boundary", async () => {
    const { window } = createLeadRealm([]);
    const payload = '<img src=x onerror="window.__leadPwned=true">';
    window.ApiClient.get.mockRejectedValueOnce(new Error(payload));

    await window.AdminLeads.reload();

    expect(window.handleApiError).toHaveBeenCalledWith(expect.objectContaining({ message: payload }), "loadLeads");
    expect(window.document.querySelector("img, [onerror]")).toBeNull();
    expect(window.__leadPwned).toBeUndefined();
  });

  it("exercises status, partner, and date filter branches through delegated controls", async () => {
    const { window } = createLeadRealm();
    await window.AdminLeads.init({ id: 1, role: "admin" });

    const statusInput = window.document.getElementById("status-search-input");
    const statusDropdown = window.document.getElementById("status-dropdown-list");
    statusInput.dispatchEvent(new window.Event("focus"));
    expect(statusDropdown.querySelectorAll(".custom-select-option").length).toBeGreaterThan(1);
    statusInput.value = "missing-status";
    statusInput.dispatchEvent(new window.Event("input", { bubbles: true }));
    expect(statusDropdown.querySelector(".empty-msg")).not.toBeNull();
    statusInput.value = "Oczekujący";
    statusInput.dispatchEvent(new window.Event("input", { bubbles: true }));
    statusDropdown.querySelector('[data-id="pending"]').dispatchEvent(
      new window.MouseEvent("mousedown", { bubbles: true }),
    );
    expect(window.document.getElementById("filter-status").value).toBe("pending");

    const partnerInput = window.document.getElementById("partner-search-input");
    const partnerDropdown = window.document.getElementById("partner-dropdown-list");
    partnerInput.dispatchEvent(new window.Event("focus"));
    partnerInput.value = "missing-partner";
    partnerInput.dispatchEvent(new window.Event("input", { bubbles: true }));
    expect(partnerDropdown.querySelector(".empty-msg")).not.toBeNull();
    partnerInput.value = "";
    partnerInput.dispatchEvent(new window.Event("input", { bubbles: true }));
    partnerDropdown.querySelector('[data-id="7"]').dispatchEvent(
      new window.MouseEvent("mousedown", { bubbles: true }),
    );
    expect(window.document.getElementById("filter-partner").value).toBe("7");

    window.document.getElementById("filter-date-from").value = "2026-08-10";
    window.document.getElementById("filter-date-to").value = "2026-08-15";
    window.applyLocalFilters();
    // Six, not five: there is no sector filter -- the partner filter above
    // covers the narrowing.
    expect(window.document.getElementById("leads-count").textContent).toBe("6");
  });

  it("covers empty rendering, modal actions, and commission validation", async () => {
    const { window, leads } = createLeadRealm();
    await window.AdminLeads.init({ id: 1, role: "admin" });

    window.document.querySelector('[data-action="detail"]').click();
    await vi.waitFor(() => expect(window.openModal).toHaveBeenCalled());

    window.document.getElementById("lead-accept-btn").click();
    window.document.getElementById("lead-reject-btn").click();
    expect(window.document.getElementById("lead-reject-form").hidden).toBe(false);
    await vi.waitFor(() => expect(window.closeModal).toHaveBeenCalledTimes(1));

    const commission = window.document.getElementById("modal-commission");
    commission.value = "-1";
    window.document.getElementById("save-commission-btn").click();
    commission.value = "125.5";
    window.document.getElementById("save-commission-btn").click();
    await vi.waitFor(() => expect(window.ApiClient.patch).toHaveBeenCalledWith(
      `/api/leads/${encodeURIComponent(leads[0].id)}`,
      expect.objectContaining({ commission: 125.5 }),
    ));

    window.document.getElementById("leads-search").value = "no matches";
    window.document.getElementById("leads-search").dispatchEvent(
      new window.Event("input", { bubbles: true }),
    );
    await vi.waitFor(() => {
      expect(window.document.getElementById("leads-empty-state").hidden).toBe(false);
    });
  });
});

describe("lead queue contract", () => {
  it("sorts unhandled leads oldest first before handled leads", async () => {
    const leads = leadData("safe");
    leads[0].registration_status = "accepted";
    leads[1].created_at = "2026-08-14T10:00:00Z";
    leads[2].created_at = "2026-08-11T10:00:00Z";
    const { window } = createLeadRealm(leads);
    await window.AdminLeads.init();
    const ids = [...window.document.querySelectorAll("#leads-tbody tr")].map((row) => row.dataset.id);
    expect(ids.slice(0, 2)).toEqual([leads[2].id, leads[3].id]);
    // The decided lead goes last, behind every one still waiting.
    expect(ids.at(-1)).toBe(leads[0].id);
  });

  it("pre-applies the dashboard registration-status query parameter", async () => {
    const leads = leadData("safe");
    leads.forEach((lead) => { lead.registration_status = "accepted"; });
    leads[0].registration_status = "needs_info";
    const { window } = createLeadRealm(leads, "?registration_status=needs_info");
    await window.AdminLeads.init();
    expect(window.document.getElementById("filter-status").value).toBe("needs_info");
    expect(window.document.getElementById("status-search-input").value)
      .toBe("Prośba o dodatkowe informacje");
    expect(window.document.querySelectorAll("#leads-tbody tr")).toHaveLength(1);
  });

  it("opens a dashboard lead deep link only after resolving the loaded record", async () => {
    const leads = leadData("safe");
    const { window } = createLeadRealm(leads, `?lead=${encodeURIComponent(leads[2].id)}`);
    await window.AdminLeads.init();
    await vi.waitFor(() => expect(window.openModal).toHaveBeenCalledWith("lead-modal"));
    expect(window.ApiClient.get).toHaveBeenCalledWith(`/api/leads/${encodeURIComponent(leads[2].id)}`);
  });

  it("applies search, status, partner, sector, date and value controls and clears them", async () => {
    const leads = leadData("safe");
    leads[0].sector = "Finance";
    leads[0].value = 9000;
    const { window } = createLeadRealm(leads);
    await window.AdminLeads.init();
    const controls = {
      "leads-search": "Client 2", "filter-status": "pending", "filter-partner": "7",
      "filter-date-from": "2026-08-11", "filter-date-to": "2026-08-11",
      "filter-value": "2501",
    };
    for (const [id, value] of Object.entries(controls)) window.document.getElementById(id).value = value;
    window.applyLocalFilters();
    await vi.waitFor(() => {
      expect(window.document.getElementById("leads-count").textContent).toBe("1");
    });
    // Each control has to reach the server as its own condition: a filter that
    // is applied in the browser only narrows the page, not the count above it.
    const applied = leadListQueries(window).at(-1);
    expect(Object.fromEntries(applied)).toMatchObject({
      q: "Client 2", registration_status: "pending", partner_id: "7",
      date_from: "2026-08-11", date_to: "2026-08-11", min_value: "2501",
    });

    for (const id of Object.keys(controls)) window.document.getElementById(id).value = "";
    window.applyLocalFilters();
    await vi.waitFor(() => {
      expect(window.document.getElementById("leads-count").textContent).toBe(String(leads.length));
    });
    // A cleared control is not a filter with an empty value -- it is absent.
    expect([...leadListQueries(window).at(-1).keys()].sort())
      .toEqual(["dir", "page", "per_page", "sort"]);
  });

  it("keeps only overdue unhandled leads when po terminie is selected", async () => {
    const leads = leadData("safe");
    leads[0].created_at = "2026-08-10T10:00:00Z";
    leads[1].created_at = "2026-08-20T10:00:00Z";
    leads[2].registration_status = "accepted";
    const { window } = createLeadRealm(leads);
    window.Date.now = vi.fn(() => new Date("2026-08-20T12:00:00Z").getTime());
    await window.AdminLeads.init();
    window.document.getElementById("filter-overdue").checked = true;
    window.applyLocalFilters();
    await vi.waitFor(() => {
      expect(window.document.getElementById("leads-count").textContent).toBe("4");
    });
    expect([...window.document.querySelectorAll("#leads-tbody tr")].map((row) => row.dataset.id)).toContain(leads[0].id);
    // "Po terminie" is one condition in the query now, measured against the
    // producer's own lead_action_days -- not a second deadline the browser
    // worked out for itself from a separately fetched setting.
    expect(leadListQueries(window).at(-1).get("overdue")).toBe("1");
  });

  // Two different nothings. Telling a producer who has just searched for a
  // client that "all submissions have been handled" is a false statement
  // about their data, offered in place of an answer about their search.
  it("says the programme is empty when there are no leads and no filters", async () => {
    const { window } = createLeadRealm([]);
    await window.AdminLeads.init();

    expect(window.document.getElementById("leads-empty-state").hidden).toBe(false);
    expect(window.document.getElementById("leads-empty-title").textContent)
      .toBe("Brak nowych leadów");
    expect(window.document.getElementById("leads-empty-title").dataset.i18n)
      .toBe("brak_nowych_leadow");
  });

  it("says the filters matched nothing when a filter is set", async () => {
    const { window } = createLeadRealm(leadData("safe"));
    await window.AdminLeads.init();

    window.document.getElementById("leads-search").value = "nic takiego";
    window.applyLocalFilters();
    await vi.waitFor(() => {
      expect(window.document.getElementById("leads-empty-state").hidden).toBe(false);
    });

    expect(window.document.getElementById("leads-empty-title").textContent)
      .toBe("Nic nie pasuje do filtrów");
    // The key travels with the text, so a language switch re-translates
    // whichever of the two messages is currently showing.
    expect(window.document.getElementById("leads-empty-title").dataset.i18n)
      .toBe("leads_empty_filtered_title");
  });

  it("shows an error state and retries a rejected fetch", async () => {
    const { window } = createLeadRealm([]);
    window.ApiClient.get.mockRejectedValueOnce(new Error("offline"));
    await window.AdminLeads.reload();
    expect(window.document.getElementById("leads-error-state").hidden).toBe(false);
    window.document.getElementById("leads-retry-btn").click();
    await vi.waitFor(() => expect(window.ApiClient.get).toHaveBeenCalledTimes(2));
  });

  it("shows the skeleton, not the empty state, while the lead fetch is pending", async () => {
    let resolveLeads;
    const pending = new Promise((resolve) => { resolveLeads = resolve; });
    const { window } = createLeadRealm([]);
    window.ApiClient.get.mockImplementationOnce(() => pending);
    const loading = window.AdminLeads.reload();
    expect(window.document.getElementById("leads-loading-state").hidden).toBe(false);
    expect(window.document.getElementById("leads-empty-state").hidden).toBe(true);
    resolveLeads({ leads: [] });
    await loading;
  });
});

describe("lead detail decision contract", () => {
  async function openFirstLead(query = "") {
    const realm = createLeadRealm(leadData(), query);
    await realm.window.AdminLeads.init();
    realm.window.document.querySelector('[data-action="detail"]').click();
    await vi.waitFor(() => expect(realm.window.openModal).toHaveBeenCalled());
    return realm;
  }

  it("fills the complete submitted record and uses an explicit dash for absent values", async () => {
    const { window, leads } = await openFirstLead();
    expect(window.document.getElementById("modal-lead-nip").textContent).toBe(leads[0].nip);
    expect(window.document.getElementById("modal-lead-website").textContent).toBe(leads[0].website);
    expect(window.document.getElementById("modal-lead-product").textContent).toBe(leads[0].product);
    expect(window.document.getElementById("modal-lead-quantity").textContent).toBe("—");
  });

  // The lead's persisted Polish product text (leads.product) is
  // never rewritten -- the modal reads the catalogue's CURRENT English name
  // only when the admin's own reading language is English.
  it("shows the catalogue's current English product name to an English-reading admin", async () => {
    const leads = leadData();
    leads[0] = { ...leads[0], product: "Wdrożenie ERP", product_en: "ERP rollout" };
    const realm = createLeadRealm(leads);
    realm.window.App.lang = "en";
    await realm.window.AdminLeads.init();
    realm.window.document.querySelector('[data-action="detail"]').click();
    await vi.waitFor(() => expect(realm.window.openModal).toHaveBeenCalled());

    expect(realm.window.document.getElementById("modal-lead-product").textContent)
      .toBe("ERP rollout");
  });

  it("falls back to the persisted Polish product text when it has no English catalogue name", async () => {
    const leads = leadData();
    leads[0] = { ...leads[0], product: "Wdrożenie ERP", product_en: "" };
    const realm = createLeadRealm(leads);
    realm.window.App.lang = "en";
    await realm.window.AdminLeads.init();
    realm.window.document.querySelector('[data-action="detail"]').click();
    await vi.waitFor(() => expect(realm.window.openModal).toHaveBeenCalled());

    expect(realm.window.document.getElementById("modal-lead-product").textContent)
      .toBe("Wdrożenie ERP");
  });

  it("shows queue position and share without another partner identity", async () => {
    const { window } = await openFirstLead();
    const badge = window.document.getElementById("modal-queue-position");
    expect(badge.hidden).toBe(false);
    expect(badge.textContent).toBe("5. zgłoszenie — 0% udziału");
  });

  it("renders API-supplied commission components and exactly one unchanged total", async () => {
    const { window, leads } = await openFirstLead();
    expect(window.document.getElementById("modal-commission-tier").textContent).toBe("80 PLN");
    expect(window.document.getElementById("modal-commission-recurring").textContent).toBe("20 PLN");
    expect(window.document.getElementById("modal-commission-cash").textContent).toBe("0 PLN");
    expect(window.document.getElementById("modal-commission-total").textContent).toBe(`${leads[0].commission} PLN`);
    expect(readFileSync(path.join(PROJECT_ROOT, "js/admin-leads.js"), "utf8"))
      .not.toMatch(/commission[^\n;]*\*/i);
  });

  it("offers no commission correction once the commission is paid", async () => {
    const leads = leadData();
    leads[0] = { ...leads[0], commission_paid_at: "2026-09-01T10:00:00" };
    const realm = createLeadRealm(leads);
    await realm.window.AdminLeads.init();
    realm.window.document.querySelector('[data-action="detail"]').click();
    await vi.waitFor(() => expect(realm.window.openModal).toHaveBeenCalled());

    const doc = realm.window.document;
    for (const id of ["modal-commission", "save-commission-btn", "modal-commission-pct", "save-commission-pct-btn"]) {
      expect(doc.getElementById(id).disabled).toBe(true);
    }
    expect(doc.getElementById("modal-commission-pct-hint").textContent)
      .toBe(realm.window.App.t("al_commission_locked_paid"));
  });

  it("leaves a confirmed win's commission to the administrator", async () => {
    const leads = leadData();
    leads[0] = { ...leads[0], win_confirmed_at: "2026-09-01T10:00:00" };
    const realm = createLeadRealm(leads);
    realm.window.sessionStorage.setItem("ph_session", JSON.stringify({ id: 2, role: "manager" }));
    await realm.window.AdminLeads.init();
    realm.window.document.querySelector('[data-action="detail"]').click();
    await vi.waitFor(() => expect(realm.window.openModal).toHaveBeenCalled());

    expect(realm.window.document.getElementById("save-commission-btn").disabled).toBe(true);
  });

  it("keeps a confirmed win's commission editable for the administrator", async () => {
    const leads = leadData();
    leads[0] = { ...leads[0], win_confirmed_at: "2026-09-01T10:00:00" };
    const realm = createLeadRealm(leads);
    await realm.window.AdminLeads.init();
    realm.window.document.querySelector('[data-action="detail"]').click();
    await vi.waitFor(() => expect(realm.window.openModal).toHaveBeenCalled());

    expect(realm.window.document.getElementById("save-commission-btn").disabled).toBe(false);
  });

  it("accepts a lead and refreshes the rendered row in place", async () => {
    const { window, leads } = await openFirstLead();
    window.document.getElementById("lead-accept-btn").click();
    await vi.waitFor(() => expect(window.ApiClient.post).toHaveBeenCalledWith(
      `/api/leads/${encodeURIComponent(leads[0].id)}/registration`,
      expect.objectContaining({ action: "accept" }),
    ));
    await vi.waitFor(() => {
      const refreshed = [...window.document.querySelectorAll("#leads-tbody tr")]
        .find((row) => row.dataset.id === leads[0].id);
      expect(refreshed.textContent).toContain("Zaakceptowany");
    });
  });

  it("saves rejection and its reason in a single request", async () => {
    const { window, leads } = await openFirstLead();
    const reason = window.document.getElementById("lead-reject-reason");
    const confirm = window.document.getElementById("lead-reject-confirm-btn");
    window.document.getElementById("lead-reject-btn").click();
    expect(confirm.disabled).toBe(true);
    reason.value = "Brak zgodności z profilem klienta";
    reason.dispatchEvent(new window.Event("input", { bubbles: true }));
    expect(confirm.disabled).toBe(false);
    confirm.click();
    await vi.waitFor(() => expect(window.ApiClient.post).toHaveBeenCalledWith(
      `/api/leads/${encodeURIComponent(leads[0].id)}/registration`,
      expect.objectContaining({ action: "reject", message: reason.value }),
    ));
    expect(window.ApiClient.patch).not.toHaveBeenCalled();
  });

  it("requests more information and records selected missing fields", async () => {
    const { window, leads } = await openFirstLead();
    window.document.getElementById("lead-more-info-btn").click();
    window.document.querySelector('[name="missing-field"]').checked = true;
    window.document.getElementById("lead-more-info-note").value = "Proszę sprawdzić numer";
    window.document.getElementById("lead-more-info-confirm-btn").click();
    await vi.waitFor(() => expect(window.ApiClient.post).toHaveBeenCalledWith(
      `/api/leads/${encodeURIComponent(leads[0].id)}/registration`,
      expect.objectContaining({
        action: "request_info", message: expect.stringContaining("NIP"),
      }),
    ));
    expect(window.ApiClient.patch).not.toHaveBeenCalled();
  });

  it("sends the lead's own revision and a stable replay key with every decision", async () => {
    const { window, leads } = await openFirstLead();
    window.document.getElementById("lead-accept-btn").click();
    await vi.waitFor(() => expect(window.ApiClient.post).toHaveBeenCalled());
    const [, body] = window.ApiClient.post.mock.calls[0];
    // Not filled in by the server on the client's behalf: a missing revision
    // would turn compare-and-swap back into last-write-wins.
    expect(body.expected_revision).toBe(leads[0].revision);
    expect(typeof body.operation_id).toBe("string");
    expect(body.operation_id.length).toBeGreaterThan(0);
  });

  it("preserves the information draft and reports no success when the decision fails", async () => {
    const { window, leads } = await openFirstLead();
    const error = new Error("Nie zapisano decyzji");
    window.ApiClient.post.mockRejectedValueOnce(error);
    window.document.getElementById("lead-more-info-btn").click();
    const note = window.document.getElementById("lead-more-info-note");
    note.value = "Proszę o numer telefonu";
    window.document.getElementById("lead-more-info-confirm-btn").click();
    // The modal and the row now share one writer (requestInformation), so
    // that is the context a failure is reported against from either path.
    await vi.waitFor(() => expect(window.handleApiError).toHaveBeenCalledWith(
      error, "requestInformation",
    ));
    expect(note.value).toBe("Proszę o numer telefonu");
    expect(leads[0].registration_status).toBe("pending");
    expect(window.showToast).not.toHaveBeenCalled();
  });

  it("renders markup-bearing submission fields as literal text", async () => {
    const { window, leads } = await openFirstLead();
    expect(window.document.getElementById("modal-lead-nip").textContent).toBe(leads[0].nip);
    expect(window.document.querySelector("#lead-modal img, #lead-modal script, #lead-modal [onerror]")).toBeNull();
    expect(window.__leadPwned).toBeUndefined();
  });
});

describe("AdminLeads extraction", () => {
  it("exposes one idempotent namespaced controller with private mutable state", async () => {
    const { window } = createLeadRealm();
    await Promise.all([
      window.AdminLeads.init({ id: 1, role: "admin" }),
      window.AdminLeads.init({ id: 1, role: "admin" }),
    ]);

    expect(window.AdminLeads).toBeDefined();
    expect(Object.keys(window.AdminLeads).sort()).toEqual(["init", "reload"]);
    expect(window.ApiClient.get).toHaveBeenCalledTimes(4);
    expect(window.currentPage).toBeUndefined();
    expect(window.allLeads).toBeUndefined();
  });

  it("uses the exact live dependency order and leaves no lead seam in admin.js", () => {
    expect(orderedScripts()).toEqual(EXPECTED_SCRIPTS);
    expect(existsSync(path.join(PROJECT_ROOT, "js/admin.js"))).toBe(false);
  });

  it("executes the actual page order through AdminShell without duplicate bindings", async () => {
    const window = new Window({ url: "http://localhost:8000/pages/admin/leads.html" });
    window.document.write(readFileSync(path.join(PROJECT_ROOT, LEADS_PAGE), "utf8"));
    window.console = console;
    window.fetch = vi.fn(async (url) => {
      const pathname = new URL(url, window.location.href).pathname;
      const body = pathname === "/api/auth/me"
        ? { user: { id: 1, role: "admin", name: "Ada Admin", company: "PartnerType" } }
        : pathname === "/api/leads" ? { leads: [] }
          : pathname === "/api/partners" ? { partners: [] }
            : pathname === "/api/notifications" ? { notifications: [], unread_count: 0 }
              : {};
      return { ok: true, status: 200, json: async () => body };
    });

    for (const scriptPath of orderedScripts()) runClassicScript(window, scriptPath);
    window.document.dispatchEvent(new window.Event("DOMContentLoaded"));
    await window.AdminShell.init();
    await window.AdminLeads.init({ id: 1, role: "admin" });

    await vi.waitFor(() => expect(window.fetch).toHaveBeenCalledWith(
      // The list carries its page, order and filters in the query now.
      expect.stringMatching(/^\/api\/leads\?/),
      expect.objectContaining({ credentials: "same-origin" }),
    ));
    expect(window.AdminShell).toBeDefined();
    expect(window.AdminLeads).toBeDefined();
    expect(window.document.querySelectorAll("#filter-panel")).toHaveLength(1);
  });
});

describe("configurable stage pipeline", () => {
  it("offers exactly the four fixed registration decisions as filter options", async () => {
    // The screen's status filter answers "what have I decided?" --- the
    // question the dashboard KPI cards link into. The sales stage is
    // producer-configurable and stays visible per row instead.
    const { window } = createLeadRealm();
    await window.AdminLeads.init();

    window.document.getElementById("status-search-input").dispatchEvent(new window.Event("focus"));
    const options = [...window.document.querySelectorAll("#status-dropdown-list .custom-select-option")]
      .filter((option) => option.dataset.id !== "");
    expect(options.map((option) => option.dataset.id))
      .toEqual(["pending", "needs_info", "accepted", "rejected"]);
    expect(options.map((option) => option.textContent)).toEqual([
      "Oczekujący", "Prośba o dodatkowe informacje", "Zaakceptowany", "Odrzucony",
    ]);
  });

  it("renders a lead sitting on a stage added after this code was written with that stage's own name and colour", async () => {
    const customStage = {
      id: 7, key: "negocjacje", name: "Negocjacje", sort_order: 6,
      kind: "progress", is_terminal: 0, is_commission_trigger: 0, color: "#8266ff", active: 1,
    };
    const leads = leadData("safe").slice(0, 3);
    leads[0].status = "negocjacje";
    const { window } = createLeadRealm(leads, "", [...STAGES, customStage]);
    await window.AdminLeads.init();

    const row = [...window.document.querySelectorAll("#leads-tbody tr")]
      .find((candidate) => candidate.dataset.id === leads[0].id);
    expect(row.textContent).toContain("Negocjacje");
    expect(row.textContent).not.toContain("Etap nieznany");
  });

  it("labels a lead on a stage the pipeline no longer recognises honestly, rather than a silent unknown-value blank", async () => {
    const leads = leadData("safe").slice(0, 3);
    leads[0].status = "usuniety_etap";
    const { window } = createLeadRealm(leads);
    await window.AdminLeads.init();

    const row = [...window.document.querySelectorAll("#leads-tbody tr")]
      .find((candidate) => candidate.dataset.id === leads[0].id);
    expect(row.textContent).toContain("Etap nieznany");
  });

  it("offers the three decision controls and no sales stages on an undecided lead", async () => {
    const { window } = await openFirstLeadWith(leadData("safe"));

    expect(window.document.querySelectorAll("#lead-advance-controls [data-advance-key]"))
      .toHaveLength(0);
    expect(window.document.getElementById("lead-accept-btn").style.display).toBe("flex");
    expect(window.document.getElementById("lead-reject-btn").style.display).toBe("flex");
    expect(window.document.getElementById("lead-more-info-btn").style.display).toBe("flex");
  });

  it("offers the onward sales stages plus the win terminal once the deal is accepted", async () => {
    const accepted = leadData("safe").map((lead) => ({
      ...lead, registration_status: "accepted", status: "contact",
    }));
    const { window } = await openFirstLeadWith(accepted);

    const extraKeys = [...window.document.querySelectorAll("#lead-advance-controls [data-advance-key]")]
      .map((button) => button.dataset.advanceKey);
    expect(extraKeys).toContain("won");
    expect(extraKeys).toContain("presentation");
    // A decided lead offers no second decision.
    expect(window.document.getElementById("lead-accept-btn").style.display).toBe("none");
  });

  it("rejects on the registration axis, unaffected by a producer renaming their sales stages", async () => {
    // Reject is a fixed registration decision, not a pipeline stage, so
    // renaming a stage cannot change what the button does.
    const renamed = STAGES.map((stage) => (
      stage.key === "lost" ? { ...stage, key: "stracony", name: "Stracony" } : stage
    ));
    const { window, leads } = await openFirstLeadWith(leadData("safe"), "", renamed);

    const reason = window.document.getElementById("lead-reject-reason");
    window.document.getElementById("lead-reject-btn").click();
    reason.value = "Brak zgodności z profilem klienta";
    reason.dispatchEvent(new window.Event("input", { bubbles: true }));
    window.document.getElementById("lead-reject-confirm-btn").click();

    await vi.waitFor(() => expect(window.ApiClient.post).toHaveBeenCalledWith(
      `/api/leads/${encodeURIComponent(leads[0].id)}/registration`,
      expect.objectContaining({ action: "reject", message: reason.value }),
    ));
  });

  it("asks for more information on the registration axis, with no stage to go missing", async () => {
    // The request never targets a stage, so a producer deleting stages
    // cannot stop their partners being asked.
    const renamed = STAGES.map((stage) => (
      stage.key === "offer" ? { ...stage, key: "przegladamy", name: "Przeglądamy" } : stage
    ));
    const { window, leads } = await openFirstLeadWith(leadData("safe"), "", renamed);

    window.document.getElementById("lead-more-info-btn").click();
    window.document.querySelector('[name="missing-field"]').checked = true;
    window.document.getElementById("lead-more-info-confirm-btn").click();

    await vi.waitFor(() => expect(window.ApiClient.post).toHaveBeenCalledWith(
      `/api/leads/${encodeURIComponent(leads[0].id)}/registration`,
      expect.objectContaining({
        action: "request_info", message: expect.stringContaining("NIP"),
      }),
    ));
  });

  it("advances an accepted lead through a generic control, carrying the target stage key", async () => {
    const accepted = leadData("safe").map((lead) => ({
      ...lead, registration_status: "accepted", status: "contact",
    }));
    const { window, leads } = await openFirstLeadWith(accepted);

    window.document.querySelector('#lead-advance-controls [data-advance-key="presentation"]').click();

    await vi.waitFor(() => expect(window.ApiClient.patch).toHaveBeenCalledWith(
      `/api/leads/${encodeURIComponent(leads[0].id)}`,
      expect.objectContaining({ status: "presentation" }),
    ));
  });

  it("offers no sales-stage controls at all for a lead nobody has accepted yet", async () => {
    const { window } = await openFirstLeadWith(leadData("safe"));

    expect(window.document.querySelectorAll("#lead-advance-controls [data-advance-key]"))
      .toHaveLength(0);
  });

  it("filters the queue to only leads carrying the selected decision", async () => {
    const leads = leadData("safe");
    leads[0].registration_status = "accepted";
    const selected = "accepted";
    const { window } = createLeadRealm(leads);
    await window.AdminLeads.init();

    window.document.getElementById("filter-status").value = selected;
    window.document.getElementById("filter-status").dispatchEvent(new window.Event("change", { bubbles: true }));

    await vi.waitFor(() => {
      expect(window.document.getElementById("leads-count").textContent).toBe("1");
    });
    expect(window.document.querySelector("#leads-tbody tr").dataset.id).toBe(leads[0].id);
    expect(leadListQueries(window).at(-1).get("registration_status")).toBe(selected);
  });

  it("renders a markup-bearing stage name as literal text", async () => {
    const payload = '<img src=x onerror="window.__leadPwned=true">';
    const customStage = {
      id: 8, key: "custom_markup", name: payload, sort_order: 6,
      kind: "progress", is_terminal: 0, is_commission_trigger: 0, color: "#8266ff", active: 1,
    };
    const leads = leadData("safe").slice(0, 3);
    leads[0].status = "custom_markup";
    const { window } = createLeadRealm(leads, "", [...STAGES, customStage]);
    await window.AdminLeads.init();

    const row = [...window.document.querySelectorAll("#leads-tbody tr")]
      .find((candidate) => candidate.dataset.id === leads[0].id);
    expect(row.querySelector("img, [onerror]")).toBeNull();
    expect(row.textContent).toContain(payload);
    expect(window.__leadPwned).toBeUndefined();
  });
});

async function openFirstLeadWith(leads, query = "", stages = STAGES) {
  const realm = createLeadRealm(leads, query, stages);
  await realm.window.AdminLeads.init();
  realm.window.document.querySelector('[data-action="detail"]').click();
  await vi.waitFor(() => expect(realm.window.openModal).toHaveBeenCalled());
  return realm;
}

// ─── correcting a deal's rate, not only its amount ───────────────

describe("commission rate correction", () => {
  it("resolves both correction controls to exactly one element in the real page", () => {
    const html = readFileSync(path.join(PROJECT_ROOT, "pages/admin/leads.html"), "utf8");
    for (const id of ["modal-commission", "modal-commission-pct",
      "save-commission-btn", "save-commission-pct-btn", "modal-commission-pct-hint"]) {
      const matches = html.match(new RegExp(`id="${id}"`, "g")) || [];
      expect(matches, `expected exactly one id="${id}"`).toHaveLength(1);
    }
  });

  it("sends the entered rate and never multiplies it in the browser", async () => {
    const { window, leads } = await openFirstLeadWith(leadData("safe"));

    window.document.getElementById("modal-commission-pct").value = "8";
    window.document.getElementById("save-commission-pct-btn").click();

    await vi.waitFor(() => expect(window.ApiClient.patch).toHaveBeenCalledWith(
      `/api/leads/${encodeURIComponent(leads[0].id)}`,
      expect.objectContaining({ commission_override_pct: 8 }),
    ));
    // The browser states the rate; the backend owns the arithmetic. Versioning
    // metadata travels with every write, so the assertion
    // is "no computed money in the payload", not "no other keys at all".
    const [, body] = window.ApiClient.patch.mock.calls.at(-1);
    expect(Object.keys(body).sort())
      .toEqual(["commission_override_pct", "expected_revision", "operation_id"]);
    expect(body).not.toHaveProperty("commission");
  });

  it("clears the correction when the field is emptied, rather than sending zero", async () => {
    const { window, leads } = await openFirstLeadWith(leadData("safe"));

    window.document.getElementById("modal-commission-pct").value = "";
    window.document.getElementById("save-commission-pct-btn").click();

    await vi.waitFor(() => expect(window.ApiClient.patch).toHaveBeenCalledWith(
      `/api/leads/${encodeURIComponent(leads[0].id)}`,
      expect.objectContaining({ commission_override_pct: null }),
    ));
  });

  it("refuses an impossible rate in the browser and issues no request", async () => {
    const { window } = await openFirstLeadWith(leadData("safe"));
    window.ApiClient.patch.mockClear();

    window.document.getElementById("modal-commission-pct").value = "150";
    window.document.getElementById("save-commission-pct-btn").click();
    await Promise.resolve();

    expect(window.ApiClient.patch).not.toHaveBeenCalled();
    expect(window.showToast).toHaveBeenCalledWith(
      "error", expect.any(String), expect.any(String),
    );
  });

  // The hint names the tier the rate came from -- a name the producer wrote
  // themselves, with its own English column.
  it("names the tier in the reader's language, falling back to the Polish original", async () => {
    const withTier = (extra) => leadData("safe").map((lead) => ({
      ...lead, commission_override_pct: 8, tier_name: "Zloty", ...extra,
    }));

    const english = createLeadRealm(withTier({ tier_name_en: "Gold" }));
    english.window.App = { ...appStub("en"), applyTranslations: vi.fn() };
    await english.window.AdminLeads.init();
    english.window.document.querySelector('[data-action="detail"]').click();
    await vi.waitFor(() => expect(english.window.openModal).toHaveBeenCalled());
    const enHint = english.window.document.getElementById("modal-commission-pct-hint").textContent;
    expect(enHint).toContain("Gold");
    expect(enHint).not.toContain("Zloty");

    const untranslated = createLeadRealm(withTier({ tier_name_en: "" }));
    untranslated.window.App = { ...appStub("en"), applyTranslations: vi.fn() };
    await untranslated.window.AdminLeads.init();
    untranslated.window.document.querySelector('[data-action="detail"]').click();
    await vi.waitFor(() => expect(untranslated.window.openModal).toHaveBeenCalled());
    expect(untranslated.window.document.getElementById("modal-commission-pct-hint").textContent)
      .toContain("Zloty");
  });

  it("says which rate is in force, so a corrected deal never reads as a plain one", async () => {
    const corrected = leadData("safe").map((lead) => ({
      ...lead, commission_override_pct: 8, tier_name: "Gold",
    }));
    const { window } = await openFirstLeadWith(corrected);

    expect(window.document.getElementById("modal-commission-pct").value).toBe("8");
    expect(window.document.getElementById("modal-commission-pct-hint").textContent)
      .toContain("8%");
  });
});
