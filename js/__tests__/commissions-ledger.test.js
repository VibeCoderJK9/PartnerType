import { readFileSync } from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { Window } from "happy-dom";
import { describe, expect, it, vi } from "vitest";
import { appStub } from "./helpers/copy.js";

const PROJECT_ROOT = path.resolve(import.meta.dirname, "..", "..");

function runClassicScript(window, relativePath) {
  const source = readFileSync(path.join(PROJECT_ROOT, relativePath), "utf8");
  const context = window.__vmContext || (window.__vmContext = vm.createContext(window));
  vm.runInContext(source, context, { filename: relativePath });
}

function invoke(window, source) {
  return vm.runInContext(source, window.__vmContext);
}

// Mirrors the seeded pipeline exactly (migrations/default_stages.json), matching
// js/__tests__/admin-leads.test.js's fixture. trigger = "accepted"; the two
// terminals are "paid" (win) and "rejected" (lose).
const STAGES = [
  { id: 1, key: "contact", name: "Nawiązanie kontaktu", sort_order: 0, kind: "progress", is_terminal: 0, color: "#94a3b8", active: 1 },
  { id: 2, key: "presentation", name: "Prezentacja", sort_order: 1, kind: "progress", is_terminal: 0, color: "#3b82f6", active: 1 },
  { id: 3, key: "offer", name: "Oferta", sort_order: 2, kind: "progress", is_terminal: 0, color: "#8b5cf6", active: 1 },
  { id: 4, key: "negotiation", name: "Negocjacje", sort_order: 3, kind: "progress", is_terminal: 0, color: "#f59e0b", active: 1 },
  { id: 5, key: "won", name: "Wygrany", sort_order: 4, kind: "win", is_terminal: 1, color: "#10b981", active: 1 },
  { id: 6, key: "lost", name: "Przegrany", sort_order: 5, kind: "lose", is_terminal: 1, color: "#ef4444", active: 1 },
];

// A stage a producer adds after this code was written -- outside the
// original six-word vocabulary entirely.
const CUSTOM_STAGE = { id: 7, key: "qualifying", name: "Kwalifikacja", sort_order: 6, kind: "progress", is_terminal: 0, is_commission_trigger: 0, color: "#8b5cf6", active: 1 };

function ledgerWindow({ leads = [], stages = STAGES } = {}) {
  const window = new Window({ url: "http://localhost/pages/admin/commissions.html" });
  window.ApiClient = {
    get: vi.fn((url) => {
      if (url === "/api/lead-stages") return Promise.resolve({ stages });
      if (url === "/api/leads") return Promise.resolve({ leads });
      return Promise.resolve({});
    }),
    post: vi.fn().mockResolvedValue({ partner: {} }),
    patch: vi.fn().mockResolvedValue({}),
    delete: vi.fn().mockResolvedValue({ partner: {} }),
  };
  window.App = appStub("pl");
  window.getInitials = vi.fn((value) => String(value));
  window.showToast = vi.fn();
  window.handleApiError = vi.fn();
  window.confirm = vi.fn(() => true);
  window.document.body.innerHTML = `
    <table><tbody id="partner-comm-tbody"></tbody></table>
    <div id="partner-comm-error-state" hidden></div>
    <input id="partner-select-all" type="checkbox">
    <div id="bonus-percent-grid"></div><button id="add-percent-btn"></button><span id="count-percent-btn"></span>
    <div id="panel-partner-name"></div><div id="panel-partner-sub"></div>
    <div id="commission-summary-box"></div><select id="panel-tier-select"></select>
    <div id="bonus-toggles-container"></div><div id="slide-overlay"></div><div id="slide-panel"></div>
    <table id="ledger-table"><tbody id="ledger-tbody"></tbody><tfoot><tr><td id="ledger-total"></td></tr></tfoot></table>
    <div id="ledger-empty-state" hidden></div>
    <div id="ledger-error-state" hidden><button id="ledger-retry-btn"></button></div>
  `;
  // js/utils.js is the real shared module (Ui.buildStagePipeline/stageFor/
  // stageBadge) so this test proves the actual renderer, not a stand-in.
  runClassicScript(window, "js/utils.js");
  runClassicScript(window, "js/commissions.js");
  return window;
}

describe("per-lead commission ledger", () => {
  it("renders one row per lead carrying a commission, with id, client, partner, value, commission and status", async () => {
    const window = ledgerWindow({
      leads: [
        { id: 5, client_name: "Nova Sklep", partner_name: "Marek W.", value: 5000, commission: 750, status: "negotiation" },
      ],
    });
    await invoke(window, "loadLedger()");

    const row = window.document.querySelector('#ledger-tbody tr[data-lead-id="5"]');
    expect(row).not.toBeNull();
    expect(row.textContent).toContain("5");
    expect(row.textContent).toContain("Nova Sklep");
    expect(row.textContent).toContain("Marek W.");
    expect(row.textContent).toContain("5000");
    expect(row.textContent).toContain("750");
  });

  it("carries the lead's actual stage name and colour on the badge", async () => {
    const window = ledgerWindow({
      leads: [{ id: 1, client_name: "A", partner_name: "P", value: 100, commission: 10, status: "negotiation" }],
    });
    await invoke(window, "loadLedger()");

    const row = window.document.querySelector('#ledger-tbody tr[data-lead-id="1"]');
    const badge = row.querySelector(".badge");
    expect(row.textContent).toContain("Negocjacje");
    expect(badge.style.color).toBeTruthy();
  });

  it("renders different badges for a lead on the terminal win stage and one still in the pipeline, never the same fixed value", async () => {
    const window = ledgerWindow({
      leads: [
        { id: 1, client_name: "A", partner_name: "P", value: 100, commission: 10, status: "negotiation" },
        { id: 2, client_name: "B", partner_name: "P", value: 100, commission: 10, status: "won" },
      ],
    });
    await invoke(window, "loadLedger()");

    const acceptedRow = window.document.querySelector('#ledger-tbody tr[data-lead-id="1"]');
    const paidRow = window.document.querySelector('#ledger-tbody tr[data-lead-id="2"]');
    expect(acceptedRow.textContent).toContain("Negocjacje");
    expect(paidRow.textContent).toContain("Wygrany");
    expect(acceptedRow.querySelector(".badge").textContent).not.toBe(paidRow.querySelector(".badge").textContent);
  });

  it("renders a lead in a producer-defined stage added after this code was written", async () => {
    const window = ledgerWindow({
      leads: [{ id: 3, client_name: "C", partner_name: "P", value: 100, commission: 10, status: "qualifying" }],
      stages: [...STAGES, CUSTOM_STAGE],
    });
    await invoke(window, "loadLedger()");

    const row = window.document.querySelector('#ledger-tbody tr[data-lead-id="3"]');
    expect(row.textContent).toContain("Kwalifikacja");
    expect(row.textContent).not.toContain("Etap nieznany");
  });

  it("never multiplies a rate in the browser — the displayed commission is exactly the API figure", async () => {
    const window = ledgerWindow({
      leads: [{ id: 9, client_name: "X", partner_name: "Y", value: 12345, commission: 987.65, status: "won" }],
    });
    await invoke(window, "loadLedger()");

    const row = window.document.querySelector('#ledger-tbody tr[data-lead-id="9"]');
    expect(row.textContent).toContain("987,65");
  });

  it("sums the displayed row commissions into the ledger total", async () => {
    const window = ledgerWindow({
      leads: [
        { id: 1, client_name: "A", partner_name: "P", value: 100, commission: 100, status: "negotiation" },
        { id: 2, client_name: "B", partner_name: "P", value: 200, commission: 250, status: "won" },
      ],
    });
    await invoke(window, "loadLedger()");

    const total = window.document.getElementById("ledger-total").textContent;
    expect(total).toContain("350");
  });

  it("renders an explicit empty state instead of a blank table when there is no ledger data", async () => {
    const window = ledgerWindow({ leads: [] });
    await invoke(window, "loadLedger()");

    expect(window.document.getElementById("ledger-empty-state").hidden).toBe(false);
    expect(window.document.getElementById("ledger-table").hidden).toBe(true);
  });

  it("renders the error copy with a working retry on a rejected fetch", async () => {
    const window = ledgerWindow();
    window.ApiClient.get = vi.fn((url) => {
      if (url === "/api/lead-stages") return Promise.resolve({ stages: STAGES });
      return Promise.reject(new Error("network down"));
    });
    await invoke(window, "loadLedger()");

    expect(window.document.getElementById("ledger-error-state").hidden).toBe(false);
    expect(window.document.getElementById("ledger-table").hidden).toBe(true);

    invoke(window, "setupLedgerRetry()");
    window.ApiClient.get = vi.fn((url) => {
      if (url === "/api/lead-stages") return Promise.resolve({ stages: STAGES });
      if (url === "/api/leads") return Promise.resolve({ leads: [] });
      return Promise.resolve({});
    });
    window.document.getElementById("ledger-retry-btn").click();
    await vi.waitFor(() => expect(window.document.getElementById("ledger-error-state").hidden).toBe(true));
  });

  it("renders the error copy with a working retry when only the stage-list fetch fails", async () => {
    const window = ledgerWindow();
    window.ApiClient.get = vi.fn((url) => {
      if (url === "/api/leads") return Promise.resolve({ leads: [{ id: 1, client_name: "A", partner_name: "P", value: 100, commission: 10, status: "negotiation" }] });
      return Promise.reject(new Error("stages unavailable"));
    });
    await invoke(window, "loadLedger()");

    expect(window.document.getElementById("ledger-error-state").hidden).toBe(false);
    expect(window.document.getElementById("ledger-table").hidden).toBe(true);
  });

  it("renders a partner or client name containing markup characters as literal text", async () => {
    const window = ledgerWindow();
    const payload = '<img src=x onerror="window.__ledgerPwned=true">';
    window.ApiClient.get = vi.fn((url) => {
      if (url === "/api/lead-stages") return Promise.resolve({ stages: STAGES });
      if (url === "/api/leads") return Promise.resolve({ leads: [{ id: 3, client_name: payload, partner_name: payload, value: 10, commission: 1, status: "new" }] });
      return Promise.resolve({});
    });
    await invoke(window, "loadLedger()");

    expect(window.document.querySelector("#ledger-tbody img, #ledger-tbody script, #ledger-tbody [onerror]")).toBeNull();
    expect(window.document.querySelector('#ledger-tbody tr[data-lead-id="3"]').textContent).toContain(payload);
    expect(window.__ledgerPwned).toBeUndefined();
  });

  it("renders a stage name containing markup characters as literal text", async () => {
    const window = ledgerWindow();
    const payload = '<img src=x onerror="window.__ledgerStagePwned=true">';
    const dirtyStage = { ...CUSTOM_STAGE, name: payload };
    window.ApiClient.get = vi.fn((url) => {
      if (url === "/api/lead-stages") return Promise.resolve({ stages: [...STAGES, dirtyStage] });
      if (url === "/api/leads") return Promise.resolve({ leads: [{ id: 4, client_name: "D", partner_name: "P", value: 10, commission: 1, status: "qualifying" }] });
      return Promise.resolve({});
    });
    await invoke(window, "loadLedger()");

    expect(window.document.querySelector("#ledger-tbody img, #ledger-tbody script, #ledger-tbody [onerror]")).toBeNull();
    expect(window.document.querySelector('#ledger-tbody tr[data-lead-id="4"]').textContent).toContain(payload);
    expect(window.__ledgerStagePwned).toBeUndefined();
  });
});

// The paid ledger (the payout register) shows one line per
// commission or bonus payment. A bonus line names a bonus the PRODUCER
// wrote and translated (Ui.producerLabel, company/company_en); a commission
// line names the CUSTOMER's company, which is never translated -- even when
// the screen itself is switched to English.
describe("payout evidence table — producer's bonus name vs. the customer's company", () => {
  function evidenceWindow(lang = "en") {
    const window = new Window({ url: "http://localhost/pages/admin/commissions.html" });
    window.App = appStub(lang);
    // Delegates to app.js in the real app; a plain stub is enough here since
    // these tests assert on the deal-name cell, not on currency formatting.
    window.formatCurrency = (value) => `${Number(value) || 0} PLN`;
    window.formatDate = (iso) => String(iso || "—");
    window.document.body.innerHTML = `
      <select id="evidence-year"></select>
      <div id="evidence-summary"></div>
      <div id="evidence-months"></div>
      <div id="evidence-quarters"></div>
      <div id="evidence-partners"></div>
      <table id="evidence-table"><tbody id="evidence-tbody"></tbody></table>
      <div id="evidence-empty-state" hidden></div>
    `;
    runClassicScript(window, "js/utils.js");
    runClassicScript(window, "js/commissions.js");
    return window;
  }

  function loadEvidenceState(window, entries) {
    invoke(window, `state.evidence = ${JSON.stringify({
      years: [], total: 0, entries, months: [], quarters: [], partners: [],
    })}; renderEvidence();`);
  }

  it("shows the producer's English bonus name on a bonus line", () => {
    const window = evidenceWindow("en");
    loadEvidenceState(window, [{
      kind: "bonus", bonus_id: 1, lead_id: null,
      company: "Nagroda kwartalna", company_en: "Quarterly award",
      client_name: null, value: null, amount: 500,
      won_at: "2026-09-01T00:00:00", paid_at: "2026-09-10T09:00:00",
      partner_id: 1, partner_name: "P", partner_company: "F",
    }]);

    const row = window.document.querySelector("#evidence-tbody tr");
    expect(row.textContent).toContain("Quarterly award");
    expect(row.textContent).not.toContain("Nagroda kwartalna");
  });

  it("falls back to the Polish bonus name when it has no English snapshot yet", () => {
    const window = evidenceWindow("en");
    loadEvidenceState(window, [{
      kind: "bonus", bonus_id: 2, lead_id: null,
      company: "Nagroda", company_en: "",
      client_name: null, value: null, amount: 100,
      won_at: "2026-09-01T00:00:00", paid_at: "2026-09-10T09:00:00",
      partner_id: 1, partner_name: "P", partner_company: "F",
    }]);

    expect(window.document.querySelector("#evidence-tbody tr").textContent).toContain("Nagroda");
  });

  it("never translates a customer's company name on a commission line", () => {
    const window = evidenceWindow("en");
    loadEvidenceState(window, [{
      kind: "commission", lead_id: "LD-1",
      company: "Firma sp. z o.o.", client_name: "Klient",
      value: 1000, amount: 100,
      won_at: "2026-07-01T00:00:00", paid_at: "2026-07-10T00:00:00",
      partner_id: 1, partner_name: "P", partner_company: "F",
    }]);

    expect(window.document.querySelector("#evidence-tbody tr").textContent)
      .toContain("Firma sp. z o.o.");
  });
});

// The partner panel's subtitle and its "stawka poziomu" row both name the
// commission tier -- a name the producer wrote themselves, with its own
// English column, so both follow the reader's language.
describe("partner panel — commission tier in the reader's language", () => {
  function panelWindow(lang) {
    const window = ledgerWindow();
    window.App = appStub(lang);
    return window;
  }

  const partner = (extra) => ({
    id: 3, name: "Jan Kowalski", company: "Alfa sp. z o.o.",
    tier_name: "Zloty", base_rate: 12.5, total_percent: 12.5, total_cash: 0,
    bonus_percent: [], bonus_cash: [], ...extra,
  });

  it("names the tier in English in the panel subtitle", () => {
    const window = panelWindow("en");

    invoke(window, `renderSlidePanel(${JSON.stringify(partner({ tier_name_en: "Gold" }))})`);

    const sub = window.document.getElementById("panel-partner-sub").textContent;
    expect(sub).toContain("Gold");
    expect(sub).not.toContain("Zloty");
  });

  it("falls back to the producer's Polish tier name when it is not translated", () => {
    const window = panelWindow("en");

    invoke(window, `renderSlidePanel(${JSON.stringify(partner({ tier_name_en: "" }))})`);

    expect(window.document.getElementById("panel-partner-sub").textContent).toContain("Zloty");
  });

  it("keeps the company name untranslated — it is the partner's own registered name", () => {
    const window = panelWindow("en");

    invoke(window, `renderSlidePanel(${JSON.stringify(partner({ tier_name_en: "Gold" }))})`);

    expect(window.document.getElementById("panel-partner-sub").textContent).toContain("Alfa sp. z o.o.");
  });

  it("names the tier in English on the base-rate row of the summary", () => {
    const window = panelWindow("en");

    invoke(window, `renderCommissionSummary(${JSON.stringify(partner({ tier_name_en: "Gold" }))})`);

    const box = window.document.getElementById("commission-summary-box").textContent;
    expect(box).toContain("Gold");
    expect(box).not.toContain("Zloty");
  });

  it("leaves the Polish reader on the Polish tier name", () => {
    const window = panelWindow("pl");

    invoke(window, `renderSlidePanel(${JSON.stringify(partner({ tier_name_en: "Gold" }))})`);

    const sub = window.document.getElementById("panel-partner-sub").textContent;
    expect(sub).toContain("Zloty");
    expect(sub).not.toContain("Gold");
  });
});

describe("granting a cash bonus is the account owner's action", () => {
  function bulkWindow(role) {
    const window = new Window({ url: "http://localhost/pages/admin/commissions.html" });
    window.App = appStub("pl");
    window.sessionStorage.setItem("ph_session", JSON.stringify({ id: 1, role }));
    window.document.body.innerHTML = `
      <select id="pbulk-action-select">
        <option value="assign_bonus"></option>
        <option value="grant_bonus"></option>
      </select>
      <button id="pbulk-apply"></button><button id="pbulk-clear"></button>
    `;
    runClassicScript(window, "js/utils.js");
    runClassicScript(window, "js/commissions.js");
    invoke(window, "setupBulkBar()");
    return window;
  }

  it("does not offer a moderator the grant the server refuses", () => {
    const window = bulkWindow("manager");
    expect(window.document.querySelector('option[value="grant_bonus"]')).toBeNull();
    expect(window.document.querySelector('option[value="assign_bonus"]')).not.toBeNull();
  });

  it("still offers it to the administrator", () => {
    const window = bulkWindow("admin");
    expect(window.document.querySelector('option[value="grant_bonus"]')).not.toBeNull();
  });
});
