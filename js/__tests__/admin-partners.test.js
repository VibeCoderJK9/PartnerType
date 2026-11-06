import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { Window } from "happy-dom";
import { describe, expect, it, vi } from "vitest";
import { appStub } from "./helpers/copy.js";

const PROJECT_ROOT = path.resolve(import.meta.dirname, "..", "..");
const PARTNERS_PAGE = "pages/admin/partners.html";
const EXPECTED_SCRIPTS = [
  "js/theme-boot.js",
  "js/icons.js",
  "js/dropdown.js",
  "js/datepicker.js",
  "js/api.js",
  "js/app.js",
  "js/utils.js",
  "js/admin-shell.js",
  "js/admin-partners.js",
  "js/charts.js",
  "js/notifications.js", "js/agent.js", "js/branding.js"];

function orderedScripts(pagePath = PARTNERS_PAGE) {
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

// A real mousedown -> mouseup -> click sequence, asserting the element
// survives mousedown -- a scripted click-only dispatch is not evidence a
// control works under a human hand.
// Copied from js/__tests__/reported-regressions.test.js::pressLikeAMouse
// rather than reimplemented.
function pressLikeAMouse(window, button) {
  const rect = button.getBoundingClientRect();
  const init = {
    bubbles: true,
    cancelable: true,
    view: window,
    button: 0,
    clientX: Math.round(rect.left + rect.width / 2),
    clientY: Math.round(rect.top + rect.height / 2),
  };
  button.dispatchEvent(new window.MouseEvent("mousedown", init));
  const survived = button.isConnected;
  button.dispatchEvent(new window.MouseEvent("mouseup", init));
  button.dispatchEvent(new window.MouseEvent("click", init));
  return survived;
}

function partner(overrides = {}) {
  return {
    id: 1,
    name: "Anna Kowalska",
    email: "anna@nova-sklep.pl",
    company: "Nova Sklep sp. z o.o.",
    tier_id: 2,
    account_status: "active",
    state: "active",
    created_at: "2026-06-01T10:00:00Z",
    tier_name: "Silver",
    base_rate: 15,
    color: "#6246ea",
    bonus_percent: 2.5,
    bonus_cash: 0,
    total_percent: 17.5,
    active_bonus_count: 1,
    ...overrides,
  };
}

function partnerData(overrides = {}) {
  return [partner(overrides)];
}

function xssPartnerData(payload = '<img src=x onerror="window.__partnerAdminPwned=true">') {
  return [{
    id: "1' onclick='window.__partnerAdminPwned=true",
    name: payload,
    email: payload,
    company: payload,
    tier_id: 1,
    account_status: "active",
    state: "active",
    created_at: "2026-06-01T10:00:00Z",
    tier_name: payload,
    base_rate: 10,
    color: "#a3652b",
    bonus_percent: 0,
    bonus_cash: 0,
    total_percent: 10,
    active_bonus_count: 0,
  }];
}

function tierData() {
  return [
    { id: 1, name: "Bronze", base_rate: 10, color: "#a3652b", sort_order: 0, active: 1 },
    { id: 2, name: "Silver", base_rate: 15, color: "#6b7280", sort_order: 1, active: 1 },
    { id: 3, name: "Gold", base_rate: 20, color: "#d4a017", sort_order: 2, active: 1 },
  ];
}

function leadFixture(overrides = {}) {
  return {
    id: "LD-001",
    partner_id: 1,
    value: 3000,
    commission: 450,
    status: "accepted",
    created_at: "2026-08-01T09:00:00Z",
    ...overrides,
  };
}

// The producer's own funnel, plus the two fixed terminals, in the shape
// /api/lead-stages returns.
function stageData() {
  return [
    { id: 1, key: "contact", name: "Nawiązanie kontaktu", kind: "progress", is_terminal: 0, sort_order: 0, color: "#94a3b8", active: 1 },
    { id: 2, key: "offer", name: "Oferta", kind: "progress", is_terminal: 0, sort_order: 1, color: "#8b5cf6", active: 1 },
    { id: 3, key: "won", name: "Wygrany", kind: "win", is_terminal: 1, sort_order: 2, color: "#10b981", active: 1 },
    { id: 4, key: "lost", name: "Przegrany", kind: "lose", is_terminal: 1, sort_order: 3, color: "#ef4444", active: 1 },
  ];
}

function statsData(overrides = {}) {
  return {
    total_partners: 0,
    total_leads: 0,
    accepted_leads: 0,
    total_commissions: 0,
    ...overrides,
  };
}

const KPI_IDS = Object.freeze({
  active: "kpi-partners-active",
  leads: "kpi-partners-leads",
  accepted: "kpi-partners-accepted",
});

// The pending-account notice element is retired now that the roster
// carries every pending account's state on its own row -- deliberately
// removed, not present in this fixture. Do not restore it or the tests that
// once asserted on it below.
const FIXTURE_HTML = `
  <div class="kpi-grid">
    <div class="kpi-value" id="${KPI_IDS.active}">—</div>
    <div class="kpi-value" id="${KPI_IDS.leads}">—</div>
    <div class="kpi-value" id="${KPI_IDS.accepted}">—</div>
  </div>
  <div class="table-container">
    <div class="table-header">
      <div>
        <div class="table-title">Lista partnerów</div>
        <div class="text-xs text-tertiary" id="partners-count-label">— partnerów łącznie</div>
      </div>
    </div>
    <button id="add-partner-btn" type="button">Dodaj partnera</button>
    <table class="data-table"><tbody id="partners-tbody"></tbody></table>
    <div id="partners-loading-state" hidden></div>
    <div id="partners-empty-state" class="empty-state" hidden></div>
    <div id="partners-error-state" class="empty-state" hidden>
      <button id="partners-retry-btn" type="button">Spróbuj ponownie</button>
    </div>
  </div>
  <div class="modal-overlay" id="deactivate-partner-modal">
    <div class="modal">
      <p id="deactivate-partner-copy"></p>
      <button id="deactivate-partner-close" type="button">x</button>
      <button id="deactivate-partner-cancel" type="button">Anuluj</button>
      <button id="deactivate-partner-confirm" type="button">Usuń</button>
    </div>
  </div>
  <div class="modal-overlay" id="partner-details-modal">
    <div class="modal">
      <div id="partner-details-state"></div>
      <input id="pd-name"><input id="pd-company"><input id="pd-email">
      <input id="pd-phone"><textarea id="pd-bio"></textarea>
      <span id="pd-tier"></span><span id="pd-rate"></span><span id="pd-joined"></span>
      <div id="pd-figures"></div>
      <div id="pd-funnel"></div>
      <select id="pd-tier-select"></select>
      <div id="pd-bonuses"></div>
      <a id="pd-mailto" href="#">E-mail</a><a id="pd-tel" href="#">Telefon</a>
      <div id="partner-details-error" role="alert"></div>
      <button id="partner-details-close" type="button">x</button>
      <button id="partner-details-cancel" type="button">Zamknij</button>
      <button id="partner-details-save" type="button">Zapisz zmiany</button>
    </div>
  </div>
  <div class="modal-overlay" id="invite-modal">
    <button id="invite-modal-close" type="button"></button>
    <button id="invite-modal-cancel" type="button"></button>
    <input id="i-name"><input id="i-company"><input id="i-email"><input id="i-phone">
    <textarea id="i-bio"></textarea>
    <select id="i-tier"></select>
    <div id="invite-error" role="alert"></div>
    <div id="invite-result" hidden>
      <div id="invite-mail-status"></div>
      <input id="invite-link" readonly>
      <button id="copy-invite-btn" type="button"></button>
    </div>
    <button id="send-invite-btn" type="button"></button>
  </div>
`;

// Every id the fixture above hand-writes must also exist, exactly once, in
// the real page --- otherwise these tests pass against markup the browser
// never serves. This is the same drift guard settings-tabs.test.js applies
// to the Settings panes.
const PARTNER_MODAL_IDS = [
  "partner-details-modal", "partner-details-state", "partner-details-error",
  "partner-details-close", "partner-details-cancel", "partner-details-save",
  "pd-name", "pd-company", "pd-email", "pd-phone", "pd-bio",
  "pd-tier", "pd-rate", "pd-joined",
  "invite-modal", "invite-modal-close", "invite-modal-cancel",
  "i-name", "i-company", "i-email", "i-phone", "i-bio", "i-tier",
  "invite-error", "invite-result", "invite-mail-status", "invite-link",
  "copy-invite-btn", "send-invite-btn",
];

function createPartnerRealm({ partners = partnerData(), tiers = tierData(), leads = [], stats = statsData() } = {}) {
  const window = new Window({ url: "http://localhost:8000/pages/admin/partners.html" });
  window.document.body.dataset.page = "admin-partners";
  window.document.body.innerHTML = FIXTURE_HTML;
  // the controller reads its copy from the dictionary
  window.App = appStub('pl');
  window.ApiClient = {
    get: vi.fn((requestPath) => {
      if (requestPath === "/api/stats") return Promise.resolve(stats);
      if (requestPath === "/api/partner-bonuses?scope=roster") return Promise.resolve({ partners });
      if (requestPath === "/api/tiers") return Promise.resolve({ tiers });
      if (requestPath === "/api/leads") return Promise.resolve({ leads });
      if (requestPath === "/api/lead-stages") return Promise.resolve({ stages: stageData() });
      return Promise.resolve({});
    }),
    post: vi.fn().mockResolvedValue({ partner: {} }),
    patch: vi.fn().mockResolvedValue({ user: {} }),
  };
  // Mirrors the real js/utils.js contract this controller depends on. plPlural
  // is the genuine three-form Polish rule rather than a stub that always
  // returns one string, so a wrong form here fails the assertion that reads it.
  window.Ui = {
    safeNavigate: vi.fn().mockReturnValue(true),
    // The real js/utils.js contract (Ui.producerLabel): the reader's own
    // language, falling back to the producer's Polish original when
    // untranslated. admin-partners.js uses it for bonus and tier names.
    producerLabel: (record, field) => {
      if (!record) return "";
      const base = String(record[field] ?? "");
      if (window.App.lang !== "en") return base;
      const english = String(record[`${field}_en`] ?? "").trim();
      return english || base;
    },
    stageLabel: (stage) => stage.kind === "win"
      ? window.App.t("stage_terminal_win")
      : stage.kind === "lose"
        ? window.App.t("stage_terminal_lose")
        : stage.name,
    buildStagePipeline: (stages) => {
      const list = (Array.isArray(stages) ? stages.slice() : []);
      return {
        list,
        byKey: new Map(list.map((stage) => [stage.key, stage])),
        nonTerminal: list.filter((stage) => !stage.is_terminal),
        firstStage: list.find((stage) => !stage.is_terminal) || null,
        winTerminal: list.find((stage) => stage.is_terminal && stage.kind === "win") || null,
        loseTerminal: list.find((stage) => stage.is_terminal && stage.kind === "lose") || null,
      };
    },
    plPlural: (count, one, few, many) => {
      const n = Math.abs(Number(count));
      if (!Number.isFinite(n)) return many;
      if (n === 1) return one;
      const lastTwo = n % 100;
      if (lastTwo >= 12 && lastTwo <= 14) return many;
      const last = n % 10;
      return last >= 2 && last <= 4 ? few : many;
    },
  };
  window.formatCurrency = vi.fn((value) => `${value} PLN`);
  window.formatDate = vi.fn(() => "22 sie 2026");
  window.getInitials = vi.fn(() => "AP");
  window.getAvatarColorClass = vi.fn(() => "bg-nebula-indigo");
  window.handleApiError = vi.fn();
  window.openModal = vi.fn();
  window.closeModal = vi.fn();
  window.showToast = vi.fn();
  Object.defineProperty(window.navigator, "clipboard", {
    value: { writeText: vi.fn().mockResolvedValue(undefined) },
    configurable: true,
  });
  runClassicScript(window, "js/admin-partners.js");
  return { window, partners, tiers, leads };
}

describe("AdminPartners", () => {
  it("loads once, fetches tier/lead context and renders persisted fields inertly", async () => {
    const { window, partners } = createPartnerRealm();

    await Promise.all([
      window.AdminPartners.init({ id: 1, role: "admin" }),
      window.AdminPartners.init({ id: 1, role: "admin" }),
    ]);

    expect(window.ApiClient.get).toHaveBeenCalledTimes(5);
    expect(window.ApiClient.get).toHaveBeenCalledWith("/api/stats");
    expect(window.ApiClient.get).toHaveBeenCalledWith("/api/partner-bonuses?scope=roster");
    expect(window.ApiClient.get).toHaveBeenCalledWith("/api/tiers");
    expect(window.ApiClient.get).toHaveBeenCalledWith("/api/leads");
    // The producer's stage names, for the hub modal's funnel.
    expect(window.ApiClient.get).toHaveBeenCalledWith("/api/lead-stages");
    expect(window.document.getElementById("partners-tbody").textContent).toContain(partners[0].name);
    expect(window.document.getElementById("partners-tbody").textContent).toContain(partners[0].company);
    expect(window.document.querySelector("img, script, [onclick], [onerror]")).toBeNull();
  });

  it("delegates row actions through opaque in-memory record keys", async () => {
    const { window, partners } = createPartnerRealm({ partners: partnerData({ id: 42 }) });
    await window.AdminPartners.init({ id: 1, role: "admin" });

    const button = window.document.querySelector('[data-partner-action="details"]');
    expect(button).not.toBeNull();
    expect(button.dataset.partnerKey).not.toContain(String(partners[0].id ?? 42));
    button.click();

    // Details open in place. Navigating away at all is the failure.
    expect(window.Ui.safeNavigate).not.toHaveBeenCalled();
    const overlay = window.document.getElementById("partner-details-modal");
    expect(overlay.classList.contains("show")).toBe(true);
    expect(window.document.getElementById("pd-name").value).toBe(partners[0].name);
  });

  it("opens the invite modal in place instead of handing off to another screen", async () => {
    // This screen owns the partner invite door outright. It used
    // to bounce to users.html?new=partner, which holds the
    // producer's own team and no partner form at all --- so the handoff
    // would now land on a screen that cannot serve it.
    const { window } = createPartnerRealm();
    await window.AdminPartners.init({ id: 1, role: "admin" });

    window.document.getElementById("add-partner-btn").click();

    expect(window.Ui.safeNavigate).not.toHaveBeenCalled();
    expect(window.openModal).toHaveBeenCalledWith("invite-modal");
  });

  it("keeps errors on the shared inert error boundary", async () => {
    const { window } = createPartnerRealm();
    const payload = '<img src=x onerror="window.__partnerAdminPwned=true">';
    // The roster request, specifically: it is the one that owns this
    // screen's error state. /api/stats is fetched on its own and only ever
    // costs the KPI cards.
    const respond = window.ApiClient.get.getMockImplementation();
    window.ApiClient.get.mockImplementation((requestPath) =>
      requestPath === "/api/partner-bonuses?scope=roster"
        ? Promise.reject(new Error(payload))
        : respond(requestPath),
    );

    await window.AdminPartners.init({ id: 1, role: "admin" });

    expect(window.handleApiError).toHaveBeenCalledWith(
      expect.objectContaining({ message: payload }),
      "AdminPartners.loadPartners",
    );
    expect(window.document.querySelector("img, [onerror]")).toBeNull();
  });

  it("renders exactly one percentage figure per row, taken from the partner summary", async () => {
    const { window } = createPartnerRealm({
      partners: partnerData({ tier_name: "Silver", base_rate: 15, total_percent: 17.5 }),
      tiers: tierData(),
    });
    await window.AdminPartners.init({ id: 1, role: "admin" });

    const row = window.document.querySelector("#partners-tbody tr");
    const percentSigns = (row.textContent.match(/%/g) || []).length;
    expect(percentSigns).toBe(1);
    expect(row.cells[5].textContent).toContain("17.5");
    expect(row.textContent).toContain("Silver");
  });

  it("sums lead values into volume and reflects lead count and most recent submission as activity", async () => {
    const { window } = createPartnerRealm({
      partners: partnerData(),
      leads: [
        leadFixture({ id: "LD-001", partner_id: 1, value: 3000, created_at: "2026-08-01T09:00:00Z" }),
        leadFixture({ id: "LD-002", partner_id: 1, value: 2000, created_at: "2026-08-10T09:00:00Z" }),
        leadFixture({ id: "LD-003", partner_id: 2, value: 9999, created_at: "2026-08-15T09:00:00Z" }),
      ],
    });
    await window.AdminPartners.init({ id: 1, role: "admin" });

    const row = window.document.querySelector("#partners-tbody tr");
    expect(window.formatCurrency).toHaveBeenCalledWith(5000);
    expect(row.cells[4].textContent).toContain("5000 PLN");
    expect(row.cells[3].textContent).toContain("2");
    expect(row.cells[3].textContent).toContain("22 sie 2026");
  });

  it("renders zero volume and zero activity plainly for a partner with no leads, not a dash", async () => {
    const { window } = createPartnerRealm({ partners: partnerData(), leads: [] });
    await window.AdminPartners.init({ id: 1, role: "admin" });

    const row = window.document.querySelector("#partners-tbody tr");
    expect(row).not.toBeNull();
    expect(row.cells[3].textContent).toContain("0");
    expect(row.cells[3].textContent).not.toContain("—");
    expect(row.cells[4].textContent).toContain("0");
    expect(row.cells[4].textContent).not.toContain("—");
    expect(window.formatCurrency).not.toHaveBeenCalled();
  });

  it("issues the tier update and re-renders the row from the returned figures on tier change", async () => {
    const { window } = createPartnerRealm({
      partners: partnerData({ id: 1, tier_id: 2 }),
      tiers: tierData(),
    });
    await window.AdminPartners.init({ id: 1, role: "admin" });

    window.ApiClient.post.mockResolvedValueOnce({
      partner: { ...partner(), id: 1, tier_id: 3, tier_name: "Gold", base_rate: 20, total_percent: 22.5, color: "#d4a017" },
    });

    const select = window.document.querySelector('select[data-partner-action="tier"]');
    expect(select).not.toBeNull();
    select.value = "3";
    select.dispatchEvent(new window.Event("change", { bubbles: true }));
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();

    expect(window.ApiClient.post).toHaveBeenCalledWith("/api/partner-bonuses/1/tier", { tier_id: 3 });
    const row = window.document.querySelector("#partners-tbody tr");
    expect(row.cells[2].textContent).toContain("Gold");
    expect(row.cells[5].textContent).toContain("22.5");
  });

  it("confirming deactivation issues the account-status update and reflects the new state in the row", async () => {
    const { window } = createPartnerRealm({
      partners: partnerData({ id: 1, name: "Anna Kowalska", account_status: "active", state: "active" }),
    });
    await window.AdminPartners.init({ id: 1, role: "admin" });

    window.ApiClient.patch.mockResolvedValueOnce({ user: { id: 1, account_status: "suspended" } });

    const deactivateButton = window.document.querySelector('[data-partner-action="deactivate"]');
    expect(deactivateButton).not.toBeNull();
    deactivateButton.click();

    expect(window.document.getElementById("deactivate-partner-modal").classList.contains("show")).toBe(true);
    expect(window.document.getElementById("deactivate-partner-copy").textContent).toContain("Anna Kowalska");
    expect(window.document.getElementById("deactivate-partner-copy").textContent).toContain(
      "Tej operacji nie można cofnąć",
    );

    window.document.getElementById("deactivate-partner-confirm").click();
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();

    expect(window.ApiClient.patch).toHaveBeenCalledWith("/api/users/1", { account_status: "suspended" });
    const row = window.document.querySelector("#partners-tbody tr");
    expect(row.cells[1].textContent).toContain("Zawieszony");
    expect(window.document.getElementById("deactivate-partner-modal").classList.contains("show")).toBe(false);
  });

  it("cancelling the deactivation confirmation issues no request", async () => {
    const { window } = createPartnerRealm({ partners: partnerData() });
    await window.AdminPartners.init({ id: 1, role: "admin" });

    window.document.querySelector('[data-partner-action="deactivate"]').click();
    expect(window.document.getElementById("deactivate-partner-modal").classList.contains("show")).toBe(true);

    window.document.getElementById("deactivate-partner-cancel").click();

    expect(window.ApiClient.patch).not.toHaveBeenCalled();
    expect(window.document.getElementById("deactivate-partner-modal").classList.contains("show")).toBe(false);
  });

  it("renders the error block on a rejected fetch and retry re-issues the load", async () => {
    const { window } = createPartnerRealm({ partners: partnerData() });
    const respond = window.ApiClient.get.getMockImplementation();
    let rosterFailed = false;
    window.ApiClient.get.mockImplementation((requestPath) => {
      if (requestPath === "/api/partner-bonuses?scope=roster" && !rosterFailed) {
        rosterFailed = true;
        return Promise.reject(new Error("network down"));
      }
      return respond(requestPath);
    });

    await window.AdminPartners.init({ id: 1, role: "admin" });

    expect(window.document.getElementById("partners-error-state").hidden).toBe(false);
    expect(window.document.getElementById("partners-tbody").children.length).toBe(0);

    window.ApiClient.get.mockClear();
    window.document.getElementById("partners-retry-btn").click();
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(window.ApiClient.get).toHaveBeenCalledWith("/api/partner-bonuses?scope=roster");
    expect(window.document.getElementById("partners-error-state").hidden).toBe(true);
    expect(window.document.getElementById("partners-tbody").children.length).toBeGreaterThan(0);
  });

  it("renders a partner name containing markup characters as literal text", async () => {
    const { window } = createPartnerRealm({ partners: xssPartnerData(), tiers: tierData() });
    const payload = '<img src=x onerror="window.__partnerAdminPwned=true">';

    await window.AdminPartners.init({ id: 1, role: "admin" });

    expect(window.document.getElementById("partners-tbody").textContent).toContain(payload);
    expect(window.document.querySelector("img, [onerror], [onclick]")).toBeNull();
    expect(window.__partnerAdminPwned).toBeUndefined();
  });
});

describe("AdminPartners KPI cards", () => {
  it("reads zero counts and the zero-amount string from an all-zero /api/stats response", async () => {
    const { window } = createPartnerRealm({ partners: [], leads: [], stats: statsData() });

    await window.AdminPartners.init({ id: 1, role: "admin" });

    expect(window.document.getElementById(KPI_IDS.active).textContent).toBe("0");
    expect(window.document.getElementById(KPI_IDS.leads).textContent).toBe("0");
    expect(window.document.getElementById(KPI_IDS.accepted).textContent).toBe("0");
  });

  it("reads each remaining figure from /api/stats", async () => {
    const stats = statsData({
      total_partners: 4,
      total_leads: 94,
      accepted_leads: 69,
      total_commissions: 158200,
    });
    const { window } = createPartnerRealm({ stats });

    await window.AdminPartners.init({ id: 1, role: "admin" });

    expect(window.document.getElementById(KPI_IDS.active).textContent).toBe("4");
    expect(window.document.getElementById(KPI_IDS.leads).textContent).toBe("94");
    expect(window.document.getElementById(KPI_IDS.accepted).textContent).toBe("69");
  });

  it("falls back to the neutral placeholder in every card when the stats request rejects, and still renders the roster", async () => {
    // A failing /api/stats must not send the whole screen to its error
    // state through one shared Promise.all. The KPI
    // cards are decoration; the roster is the screen. A dead stats endpoint
    // must cost three numbers, not the partner list.
    const { window, partners } = createPartnerRealm();
    window.ApiClient.get.mockImplementation((requestPath) => {
      if (requestPath === "/api/stats") return Promise.reject(new Error("stats down"));
      if (requestPath === "/api/partner-bonuses?scope=roster") return Promise.resolve({ partners });
      return Promise.resolve({});
    });

    await window.AdminPartners.init({ id: 1, role: "admin" });

    for (const id of Object.values(KPI_IDS)) {
      expect(window.document.getElementById(id).textContent).toBe("—");
    }
    expect(window.document.getElementById("partners-error-state").hidden).toBe(true);
    expect(window.document.getElementById("partners-tbody").textContent).toContain(partners[0].name);
  });

  it("shows the error state when the roster request itself rejects", async () => {
    const { window } = createPartnerRealm();
    window.ApiClient.get.mockImplementation((requestPath) => {
      if (requestPath === "/api/partner-bonuses?scope=roster") {
        return Promise.reject(new Error("roster down"));
      }
      return Promise.resolve({});
    });

    await window.AdminPartners.init({ id: 1, role: "admin" });

    expect(window.document.getElementById("partners-error-state").hidden).toBe(false);
    expect(window.document.getElementById("partners-tbody").textContent).toBe("");
    for (const id of Object.values(KPI_IDS)) {
      expect(window.document.getElementById(id).textContent).toBe("—");
    }
  });

  it("issues exactly one request to /api/stats per load", async () => {
    const { window } = createPartnerRealm();

    await window.AdminPartners.init({ id: 1, role: "admin" });

    const statsCalls = window.ApiClient.get.mock.calls.filter(([requestPath]) => requestPath === "/api/stats");
    expect(statsCalls).toHaveLength(1);
  });
});

describe("AdminPartners Stan column", () => {
  it("renders each of the four states with its Polish label and severity class", async () => {
    const partners = [
      partner({ id: 1, name: "Zaproszony Partner", state: "invited_pending" }),
      partner({ id: 2, name: "Zgloszony Partner", state: "self_registered" }),
      partner({ id: 3, name: "Aktywny Partner", state: "active" }),
      partner({ id: 4, name: "Zawieszony Partner", state: "suspended" }),
    ];
    const { window } = createPartnerRealm({ partners });
    await window.AdminPartners.init({ id: 1, role: "admin" });

    const rows = window.document.querySelectorAll("#partners-tbody tr");
    expect(rows).toHaveLength(4);
    expect(rows[0].cells[1].textContent).toContain("Zaproszony (nie odebrał)");
    expect(rows[0].querySelector(".badge-warning")).not.toBeNull();
    expect(rows[1].cells[1].textContent).toContain("Czeka na akceptację");
    expect(rows[1].querySelector(".badge-warning")).not.toBeNull();
    expect(rows[2].cells[1].textContent).toContain("Aktywny");
    expect(rows[2].querySelector(".badge-success")).not.toBeNull();
    expect(rows[3].cells[1].textContent).toContain("Zawieszony");
    expect(rows[3].querySelector(".badge-error")).not.toBeNull();
  });

  it("states the account's status exactly once -- the old identity-cell badge is gone", async () => {
    const { window } = createPartnerRealm({
      partners: partnerData({ account_status: "suspended", state: "suspended" }),
    });
    await window.AdminPartners.init({ id: 1, role: "admin" });

    const row = window.document.querySelector("#partners-tbody tr");
    const badgeCount = row.querySelectorAll(".badge-warning, .badge-error, .badge-success").length;
    expect(badgeCount).toBe(1);
    expect(row.cells[0].querySelector(".badge-error, .badge-warning, .badge-success")).toBeNull();
  });

  it("renders one row per roster entry -- two distinct partners produce two rows, an already-deduped partner produces one", async () => {
    const distinctPartners = [
      partner({ id: 10, email: "invited@test.pl", state: "invited_pending" }),
      partner({ id: 11, email: "selfreg@test.pl", state: "self_registered" }),
    ];
    const { window: windowA } = createPartnerRealm({ partners: distinctPartners });
    await windowA.AdminPartners.init({ id: 1, role: "admin" });
    expect(windowA.document.querySelectorAll("#partners-tbody tr")).toHaveLength(2);

    const dedupedPartner = [partner({ id: 12, state: "invited_pending" })];
    const { window: windowB } = createPartnerRealm({ partners: dedupedPartner });
    await windowB.AdminPartners.init({ id: 1, role: "admin" });
    expect(windowB.document.querySelectorAll("#partners-tbody tr")).toHaveLength(1);
  });

  it("shows the empty state and leaves the table body with no rows for an empty roster", async () => {
    const { window } = createPartnerRealm({ partners: [] });
    await window.AdminPartners.init({ id: 1, role: "admin" });

    expect(window.document.getElementById("partners-empty-state").hidden).toBe(false);
    expect(window.document.getElementById("partners-tbody").children.length).toBe(0);
  });

  it("renders two identically named partners in the order the response delivered them", async () => {
    const partners = [
      partner({ id: 5, name: "Duplicate Name", state: "active" }),
      partner({ id: 2, name: "Duplicate Name", state: "active" }),
    ];
    const { window } = createPartnerRealm({ partners });
    await window.AdminPartners.init({ id: 1, role: "admin" });

    const rows = window.document.querySelectorAll("#partners-tbody tr");
    expect(rows[0].dataset.partnerId).toBe("5");
    expect(rows[1].dataset.partnerId).toBe("2");
  });
});

describe("AdminPartners state-appropriate row actions", () => {
  it("offers Akceptuj on a self_registered row, patches the active status and updates the row", async () => {
    const { window } = createPartnerRealm({
      partners: [partner({ id: 7, name: "Self Registered Partner", state: "self_registered" })],
    });
    await window.AdminPartners.init({ id: 1, role: "admin" });

    window.ApiClient.patch.mockResolvedValueOnce({ user: { id: 7, account_status: "active" } });

    const acceptButton = window.document.querySelector('[data-partner-action="accept"]');
    expect(acceptButton).not.toBeNull();
    acceptButton.click();
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();

    expect(window.ApiClient.patch).toHaveBeenCalledWith("/api/users/7", { account_status: "active" });
    const row = window.document.querySelector("#partners-tbody tr");
    expect(row.cells[1].textContent).toContain("Aktywny");
  });

  it("offers Restore on a suspended row and patches the active status", async () => {
    const { window } = createPartnerRealm({
      partners: [partner({ id: 8, name: "Suspended Partner", state: "suspended" })],
    });
    await window.AdminPartners.init({ id: 1, role: "admin" });

    window.ApiClient.patch.mockResolvedValueOnce({ user: { id: 8, account_status: "active" } });

    const restoreButton = window.document.querySelector('[data-partner-action="restore"]');
    expect(restoreButton).not.toBeNull();
    restoreButton.click();
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();

    expect(window.ApiClient.patch).toHaveBeenCalledWith("/api/users/8", { account_status: "active" });
    const row = window.document.querySelector("#partners-tbody tr");
    expect(row.cells[1].textContent).toContain("Aktywny");
  });

  it("offers no mutating action on an invited_pending row -- the invited person has no password yet", async () => {
    const { window } = createPartnerRealm({
      partners: [partner({ id: 9, name: "Invited Partner", state: "invited_pending" })],
    });
    await window.AdminPartners.init({ id: 1, role: "admin" });

    expect(window.document.querySelector('[data-partner-action="accept"]')).toBeNull();
    expect(window.document.querySelector('[data-partner-action="restore"]')).toBeNull();
    expect(window.document.querySelector('[data-partner-action="deactivate"]')).toBeNull();
    expect(window.document.querySelector('[data-partner-action="details"]')).not.toBeNull();
  });

  it("renders mutating action buttons for a moderator, whom the server now admits", async () => {
    // Partner account state is
    // the moderator's job, and api/users.py::update_user accepts a manager
    // acting on a role='partner' target. Hiding these would withhold a
    // button that works.
    const partners = [
      partner({ id: 1, state: "self_registered" }),
      partner({ id: 2, state: "suspended" }),
      partner({ id: 3, state: "active" }),
    ];
    const { window } = createPartnerRealm({ partners });
    await window.AdminPartners.init({ id: 1, role: "manager" });

    expect(window.document.querySelector('[data-partner-action="accept"]')).not.toBeNull();
    expect(window.document.querySelector('[data-partner-action="restore"]')).not.toBeNull();
    expect(window.document.querySelector('[data-partner-action="deactivate"]')).not.toBeNull();
    expect(window.document.querySelectorAll('[data-partner-action="details"]')).toHaveLength(3);
  });

  it("renders no mutating action buttons for an observer, whom the server refuses", async () => {
    const partners = [
      partner({ id: 1, state: "self_registered" }),
      partner({ id: 2, state: "suspended" }),
      partner({ id: 3, state: "active" }),
    ];
    const { window } = createPartnerRealm({ partners });
    await window.AdminPartners.init({ id: 1, role: "observer" });

    expect(window.document.querySelector('[data-partner-action="accept"]')).toBeNull();
    expect(window.document.querySelector('[data-partner-action="restore"]')).toBeNull();
    expect(window.document.querySelector('[data-partner-action="deactivate"]')).toBeNull();
  });

  it("the Akceptuj button survives the mousedown half of a real mouse press", async () => {
    const { window } = createPartnerRealm({
      partners: [partner({ id: 7, name: "Self Registered Partner", state: "self_registered" })],
    });
    await window.AdminPartners.init({ id: 1, role: "admin" });

    const acceptButton = window.document.querySelector('[data-partner-action="accept"]');
    const survivedMousedown = pressLikeAMouse(window, acceptButton);

    expect(survivedMousedown).toBe(true);
    await vi.waitFor(() => expect(window.ApiClient.patch).toHaveBeenCalledTimes(1));
  });
});

describe("partners entry point", () => {
  it("uses the required classic-script order and removes the legacy partner seam", () => {
    expect(orderedScripts()).toEqual(EXPECTED_SCRIPTS);
    expect(existsSync(path.join(PROJECT_ROOT, "js/admin.js"))).toBe(false);
  });
});

// ─── the partner screen owns its own doors ────────────────────

describe("AdminPartners — details and invite live on this screen", () => {
  it("resolves every id the details and invite modals read to exactly one element in the real page", () => {
    const html = readFileSync(path.join(PROJECT_ROOT, PARTNERS_PAGE), "utf8");

    PARTNER_MODAL_IDS.forEach((id) => {
      const matches = html.match(new RegExp(`id="${id}"`, "g")) || [];
      expect(matches, `expected exactly one id="${id}" in ${PARTNERS_PAGE}`).toHaveLength(1);
    });
  });

  it("fills the details form from the record rather than re-fetching, and never navigates", async () => {
    const { window, partners } = createPartnerRealm({
      partners: [partner({ id: 42, name: "Firma Testowa", company: "Testowa Sp. z o.o." })],
    });
    await window.AdminPartners.init({ id: 1, role: "admin" });
    window.ApiClient.get.mockClear();

    window.document.querySelector('[data-partner-action="details"]').click();

    expect(window.document.getElementById("pd-name").value).toBe("Firma Testowa");
    expect(window.document.getElementById("pd-company").value).toBe("Testowa Sp. z o.o.");
    // The identity fields and every hub figure still come from the record this
    // screen already holds -- nothing about the partner is re-read. The one
    // permitted call is the bonus list added with the hub: the roster
    // response carries bonus TOTALS but not the individual bonus names, which
    // the screen shows.
    expect(window.ApiClient.get.mock.calls.map(([path]) => path))
      .toEqual(["/api/partner-bonuses/42"]);
    expect(window.Ui.safeNavigate).not.toHaveBeenCalled();
    expect(partners[0].id).toBe(42);
  });

  it("saves edited details through the user endpoint and refuses an emptied required field first", async () => {
    const { window } = createPartnerRealm({ partners: [partner({ id: 42 })] });
    await window.AdminPartners.init({ id: 1, role: "admin" });
    window.document.querySelector('[data-partner-action="details"]').click();

    window.document.getElementById("pd-name").value = "   ";
    window.document.getElementById("partner-details-save").click();
    await Promise.resolve();
    expect(window.ApiClient.patch).not.toHaveBeenCalled();
    expect(window.document.getElementById("partner-details-error").style.display).toBe("block");

    window.document.getElementById("pd-name").value = "Nowa Nazwa";
    window.document.getElementById("pd-company").value = "Nowa Firma";
    window.document.getElementById("pd-phone").value = "+48 600 100 200";
    window.document.getElementById("partner-details-save").click();

    await vi.waitFor(() =>
      expect(window.ApiClient.patch).toHaveBeenCalledWith("/api/users/42", {
        name: "Nowa Nazwa",
        company: "Nowa Firma",
        phone: "+48 600 100 200",
        bio: "",
      }),
    );
  });

  it("leaves the details form read-only for an observer, with no save control", async () => {
    const { window } = createPartnerRealm({ partners: [partner({ id: 42 })] });
    await window.AdminPartners.init({ id: 1, role: "observer" });

    window.document.querySelector('[data-partner-action="details"]').click();

    expect(window.document.getElementById("pd-name").disabled).toBe(true);
    expect(window.document.getElementById("partner-details-save").hidden).toBe(true);
  });

  it("posts the partner invite from this screen and reveals the link even when mail did not go", async () => {
    const { window } = createPartnerRealm();
    await window.AdminPartners.init({ id: 1, role: "admin" });
    window.ApiClient.post.mockResolvedValue({
      user: { id: 202, name: "Ewa Zaproszona", email: "ewa@test.pl" },
      invite_url: "http://localhost:8000/set-password.html?token=abc123",
      sent: false,
      reason: "smtp_disabled",
    });

    window.document.getElementById("add-partner-btn").click();
    window.document.getElementById("i-name").value = "Ewa Zaproszona";
    window.document.getElementById("i-company").value = "Zaproszeni Sp. z o.o.";
    window.document.getElementById("i-email").value = "ewa@test.pl";
    window.document.getElementById("send-invite-btn").click();

    await vi.waitFor(() => expect(window.ApiClient.post).toHaveBeenCalled());
    const [url, body] = window.ApiClient.post.mock.calls[0];
    expect(url).toBe("/api/users/invite");
    // No tier picked -- the invite carries no tier_id at all, and the partner
    // is assigned a level later on this same screen.
    expect(Object.keys(body).sort()).toEqual(["bio", "company", "email", "name", "phone"]);
    // A failed send is never presented as a failed invitation — a fresh
    // install starts with SMTP disabled and the link has to stay usable.
    await vi.waitFor(() =>
      expect(window.document.getElementById("invite-link").value).toBe(
        "http://localhost:8000/set-password.html?token=abc123",
      ),
    );
    expect(window.document.getElementById("invite-result").hidden).toBe(false);
  });

  it("offers the producer's own levels in the invite form, not bronze/silver/gold", async () => {
    // The select lists the producer's own tiers, never a hardcoded list.
    const { window } = createPartnerRealm();
    await window.AdminPartners.init({ id: 1, role: "admin" });

    window.document.getElementById("add-partner-btn").click();
    const options = [...window.document.querySelectorAll("#i-tier option")];
    expect(options[0].value).toBe("");
    expect(options.map((option) => option.textContent)).toEqual([
      "Brak poziomu", "Bronze — 10%", "Silver — 15%", "Gold — 20%",
    ]);

    window.document.getElementById("i-name").value = "Ewa Zaproszona";
    window.document.getElementById("i-company").value = "Zaproszeni Sp. z o.o.";
    window.document.getElementById("i-email").value = "ewa@test.pl";
    window.document.getElementById("i-tier").value = "3";
    window.document.getElementById("send-invite-btn").click();

    await vi.waitFor(() => expect(window.ApiClient.post).toHaveBeenCalled());
    const [, body] = window.ApiClient.post.mock.calls[0];
    expect(body.tier_id).toBe(3);
    expect(body.level).toBeUndefined();
  });

  it("validates the invite before issuing a request", async () => {
    const { window } = createPartnerRealm();
    await window.AdminPartners.init({ id: 1, role: "admin" });
    window.ApiClient.post.mockClear();

    window.document.getElementById("add-partner-btn").click();
    window.document.getElementById("i-name").value = "Ewa Zaproszona";
    window.document.getElementById("i-email").value = "";
    window.document.getElementById("send-invite-btn").click();
    await Promise.resolve();

    expect(window.ApiClient.post).not.toHaveBeenCalled();
    expect(window.document.getElementById("invite-error").style.display).toBe("block");
  });
});

// Partners carries two modals: one modal's open/close must never move the
// other.
describe("AdminPartners — the two modals on this page do not shadow each other", () => {
  it("closing the details modal leaves the invite modal untouched, and the reverse", async () => {
    const { window } = createPartnerRealm({ partners: [partner({ id: 42 })] });
    await window.AdminPartners.init({ id: 1, role: "admin" });

    window.document.querySelector('[data-partner-action="details"]').click();
    expect(window.document.getElementById("partner-details-modal").classList.contains("show")).toBe(true);

    window.document.getElementById("add-partner-btn").click();
    expect(window.openModal).toHaveBeenCalledWith("invite-modal");
    // The details overlay is driven by its own dedicated open/close pair, so
    // the page-level opener firing for the other modal must not disturb it.
    expect(window.document.getElementById("partner-details-modal").classList.contains("show")).toBe(true);

    window.document.getElementById("partner-details-cancel").click();
    expect(window.document.getElementById("partner-details-modal").classList.contains("show")).toBe(false);
    expect(window.closeModal).not.toHaveBeenCalledWith("invite-modal");
  });
});

/*
   The partner hub: open a partner and see at once how their sales go --
   conversion and how many deals sit at each stage.
*/
describe("the partner hub inside the details modal", () => {
  function hubRealm() {
    return createPartnerRealm({
      leads: [
        // Two won, one lost, one still on a stage, one awaiting the decision,
        // one rejected outright.
        leadFixture({ id: "LD-001", registration_status: "accepted", status: "won",  value: 10000, commission: 900, won_at: "2026-08-02T10:00:00Z", commission_paid_at: null }),
        leadFixture({ id: "LD-002", registration_status: "accepted", status: "won",  value: 4000,  commission: 300, won_at: "2026-08-03T10:00:00Z", commission_paid_at: "2026-09-01T10:00:00Z" }),
        leadFixture({ id: "LD-003", registration_status: "accepted", status: "lost", value: 7000,  commission: null }),
        leadFixture({ id: "LD-004", registration_status: "accepted", status: "offer", value: 5000, commission: null }),
        leadFixture({ id: "LD-005", registration_status: "pending",  status: null,   value: 2000,  commission: null }),
        leadFixture({ id: "LD-006", registration_status: "rejected", status: null,   value: 1000,  commission: null }),
      ],
    });
  }

  async function openHub() {
    const realm = hubRealm();
    await realm.window.AdminPartners.init({ id: 9, role: "admin" });
    realm.window.document.querySelector('[data-partner-action="details"]').click();
    return realm.window;
  }

  it("measures conversion against ACCEPTED leads, not every submission", async () => {
    const window = await openHub();

    const figures = [...window.document.querySelectorAll("#pd-figures .hub-figure")]
      .map((cell) => [
        cell.querySelector(".hub-figure-label").textContent,
        cell.querySelector(".hub-figure-value").textContent,
      ]);
    const value = (label) => figures.find(([l]) => l === label)?.[1];

    expect(value("zgłoszonych")).toBe("6");
    expect(value("przyjętych")).toBe("4");
    expect(value("wygranych")).toBe("2");
    // 2 of 4 accepted, not 2 of 6 submitted: a lead the producer refused never
    // got the chance to be sold, so counting it against the partner would be
    // measuring the producer's own decisions.
    expect(value("konwersja")).toBe("50%");
  });

  it("separates what the partner has earned from what is still owed", async () => {
    const window = await openHub();

    const figures = [...window.document.querySelectorAll("#pd-figures .hub-figure")]
      .map((cell) => [
        cell.querySelector(".hub-figure-label").textContent,
        cell.querySelector(".hub-figure-value").textContent,
      ]);
    const value = (label) => figures.find(([l]) => l === label)?.[1];

    // Both wins were commissioned; only one has been paid.
    expect(value("prowizja naliczona")).toBe("1200 PLN");
    expect(value("do wypłaty")).toBe("900 PLN");
    // Only won deals count as sales volume, not every lead's value.
    expect(value("sprzedaż wygrana")).toBe("14000 PLN");
  });

  it("counts leads awaiting the producer's own decision outside the funnel", async () => {
    const window = await openHub();

    const rows = [...window.document.querySelectorAll("#pd-funnel .hub-funnel-row")]
      .map((row) => [
        row.querySelector(".hub-funnel-name").textContent,
        row.querySelector(".hub-funnel-count").textContent,
      ]);

    // The two undecided leads are their own line, not an invented stage.
    expect(rows[0]).toEqual(["Czeka na Twoją decyzję", "2"]);
    expect(rows).toContainEqual(["Oferta", "1"]);
    expect(rows).toContainEqual(["Wygrany", "2"]);
    expect(rows).toContainEqual(["Przegrany", "1"]);
    expect(rows).toContainEqual(["Nawiązanie kontaktu", "0"]);
  });

  it("states no salesperson count, because the model has none yet", async () => {
    const window = await openHub();

    // Partner sub-accounts are not built yet. A fabricated
    // figure here would be worse than its absence.
    const labels = [...window.document.querySelectorAll("#pd-figures .hub-figure-label")]
      .map((el) => el.textContent);
    expect(labels.some((label) => /handlow/i.test(label))).toBe(false);
  });

  it("offers contact only where there is something to contact", async () => {
    const window = await openHub();

    const mail = window.document.getElementById("pd-mailto");
    expect(mail.hidden).toBe(false);
    expect(mail.getAttribute("href")).toContain("mailto:");
  });
});
