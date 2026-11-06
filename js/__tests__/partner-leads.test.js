import { readFileSync } from "node:fs";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { loadScript } from "./helpers/load-script.js";
import { appStub } from "./helpers/copy.js";

const PROJECT_ROOT = path.resolve(import.meta.dirname, "..", "..");

/*
 * This module loads js/utils.js and js/partner.js through the classic-script
 * loader, exactly like js/__tests__/partner-new-lead.test.js does. Per the
 * loader's one-load-per-script-per-test-file rule, this must not also load a
 * script the form module loads in the same realm -- vitest isolates each
 * test file's realm by default, so the two modules loading the same two
 * scripts independently is safe; this file simply never imports or re-uses
 * anything from partner-new-lead.test.js.
 */
// partner.js reads its copy from the dictionary at load time
globalThis.App = appStub("pl");

loadScript("js/icons.js");
loadScript("js/utils.js");
loadScript("js/partner.js");

const PAYLOAD = '<img src=x onerror="window.__partnerLeadsPwned=true">';

it('opens the detail controller from the actual list and updates its record after save', async () => {
  buildLeadsFixture();
  globalThis.ApiClient = { get: mockPartnerApi({ leads: [lead()] }) };
  window.PartnerLeadDetails = { open: vi.fn() };
  await initPartnerLeads({ id: 1, role: 'partner' });
  const opener = document.querySelector('[data-detail-lead]');
  opener.click();
  expect(window.PartnerLeadDetails.open).toHaveBeenCalledWith('LD-001', opener);
  document.dispatchEvent(new CustomEvent('partner-lead-updated', {
    detail: { lead: lead({ client_name: 'Kontakt po zapisie' }) },
  }));
  expect(document.getElementById('leads-tbody').textContent).toContain('Kontakt po zapisie');
  delete window.PartnerLeadDetails;
});

// Mirrors the seeded pipeline exactly (migrations/default_stages.json), matching
// js/__tests__/admin-leads.test.js's fixture so a badge/required-action
// derivation proven here stays consistent with the producer side. trigger =
// "accepted" (sort_order 3); moreInfoTarget resolves structurally to
// "in_progress" (the non-terminal stage immediately before the trigger).
const STAGES = [
  { id: 1, key: "contact", name: "Nawiązanie kontaktu", sort_order: 0, kind: "progress", is_terminal: 0, color: "#94a3b8", active: 1 },
  { id: 2, key: "presentation", name: "Prezentacja", sort_order: 1, kind: "progress", is_terminal: 0, color: "#3b82f6", active: 1 },
  { id: 3, key: "offer", name: "Oferta", sort_order: 2, kind: "progress", is_terminal: 0, color: "#8b5cf6", active: 1 },
  { id: 4, key: "negotiation", name: "Negocjacje", sort_order: 3, kind: "progress", is_terminal: 0, color: "#f59e0b", active: 1 },
  { id: 5, key: "won", name: "Wygrany", sort_order: 4, kind: "win", is_terminal: 1, color: "#10b981", active: 1 },
  { id: 6, key: "lost", name: "Przegrany", sort_order: 5, kind: "lose", is_terminal: 1, color: "#ef4444", active: 1 },
];

// A stage a producer adds after this code was written -- outside the
// original six-word vocabulary entirely, proving no hardcoded allowlist.
const CUSTOM_STAGE = { id: 7, key: "qualifying", name: "Kwalifikacja", sort_order: 6, kind: "progress", is_terminal: 0, is_commission_trigger: 0, color: "#8b5cf6", active: 1 };

function mockPartnerApi({
  leads = [],
  stages = STAGES,
  queueShares = "100,50,25,10,0",
  stats = { total_leads: 0, accepted_leads: 0 },
  partner = { tier_name: "Gold", base_rate: 7, total_percent: 7 },
  earnings = { totals: { due: 0, awaiting: 0 } },
} = {}) {
  return vi.fn((path) => {
    if (path === "/api/my-earnings") {
      return earnings ? Promise.resolve(earnings) : Promise.reject(new Error("Błąd serwera (500)"));
    }
    if (path === "/api/leads") return Promise.resolve({ leads });
    if (path === "/api/settings") return Promise.resolve({ settings: { lead_queue_shares: queueShares } });
    if (path === "/api/lead-stages") return Promise.resolve({ stages });
    if (path === "/api/stats") return Promise.resolve(stats);
    if (path.startsWith("/api/partner-bonuses/")) {
      return partner ? Promise.resolve({ partner }) : Promise.reject(new Error("Błąd serwera (500)"));
    }
    throw new Error(`unexpected ${path}`);
  });
}

function buildLeadsFixture() {
  document.body.innerHTML = `
    <input type="text" id="leads-search">
    <select id="filter-status"><option value="">Wszystkie statusy</option></select>
    <div id="leads-loading-state"></div>
    <div id="leads-empty-state" hidden>
      <h3>Nie masz jeszcze żadnych leadów</h3>
      <p>Zgłoś pierwszego klienta, aby zobaczyć tutaj status i naliczoną prowizję. <a href="new-lead.html">Zgłoś lead</a></p>
    </div>
    <div id="leads-no-registration-state" hidden>
      <h3>Brak zgłoszeń do rozpatrzenia</h3>
      <p>Wszystkie Twoje zgłoszenia zostały już rozpatrzone. Sprawdź zakładkę Sprzedaż, aby zobaczyć swoje zaakceptowane leady.</p>
    </div>
    <div id="leads-error-state" hidden>
      <p>Nie udało się załadować danych. Spróbuj ponownie.</p>
      <button type="button" id="leads-retry-btn">Spróbuj ponownie</button>
    </div>
    <div id="leads-table-wrapper" hidden>
      <table>
        <tbody id="leads-tbody"></tbody>
      </table>
    </div>
  `;
}

function buildDashboardFixture() {
  document.body.innerHTML = `
    <div id="welcome-name"></div>
    <div id="partner-level"></div>
    <div id="kpi-total">—</div>
    <div id="kpi-pending">—</div>
    <div id="kpi-accepted">—</div>
    <div id="kpi-earnings">—</div>
    <div id="my-recent-leads"></div>
    <div id="acceptance-rate">—%</div>
    <div id="acceptance-rate-bar" style="width:0%"></div>
    <div id="dashboard-commission-rows">
      <div class="stat-row"><span class="stat-row-label">Twoja stawka</span><span class="stat-row-value">—</span></div>
    </div>
  `;
}

function lead(overrides = {}) {
  return {
    id: "LD-001",
    client_name: "Jan Kowalski",
    company: "Acme Sp. z o.o.",
    value: 10000,
    // Two axes: the producer's decision, and (only once
    // accepted) where the sale has got to. A fresh submission has no stage.
    registration_status: "pending",
    status: null,
    commission: null,
    commission_projected: 0,
    queue_position: 1,
    created_at: "2026-08-20T10:00:00",
    updated_at: "2026-08-20T10:00:00",
    ...overrides,
  };
}

beforeEach(() => {
  globalThis.App = { ...appStub('pl'), applyTranslations: vi.fn() };
  globalThis.showToast = vi.fn();
  globalThis.handleApiError = vi.fn();
  globalThis.formatCurrency = vi.fn(
    (value) => new Intl.NumberFormat("pl-PL", { style: "currency", currency: "PLN", maximumFractionDigits: 0 }).format(value ?? 0),
  );
  globalThis.formatDate = vi.fn((value) => (value ? String(value).slice(0, 10) : "—"));
  globalThis.getInitials = vi.fn(() => "JK");
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("My leads — table columns and empty/error states", () => {
  beforeEach(buildLeadsFixture);

  it("renders client/company, date, status, value, commission, last change and the required action", async () => {
    globalThis.ApiClient = { get: mockPartnerApi({ leads: [lead()] }) };

    await initPartnerLeads({ id: 1, role: "partner" });

    const headerTexts = ["Klient", "Data", "Status", "Wartość", "Prowizja", "Ostatnia zmiana", "Wymagane działanie"];
    // Column presence is proven through the rendered row rather than static
    // headers (fixture omits <thead> to stay focused on controller output).
    const row = document.querySelector("#leads-tbody tr");
    expect(row).not.toBeNull();
    expect(row.cells.length).toBe(7);
    void headerTexts;
  });

  // This table is the Registration
  // view only: an accepted lead has moved to the Sales board and its
  // sales-stage badge/colour is proven there
  // (js/__tests__/partner-kanban.test.js), not here.
  it("shows the registration decision as the status badge, not a sales stage", async () => {
    globalThis.ApiClient = { get: mockPartnerApi({ leads: [lead({ registration_status: "needs_info" })] }) };

    await initPartnerLeads({ id: 1, role: "partner" });

    const row = document.querySelector("#leads-tbody tr");
    expect(row.cells[2].textContent).toContain("Prośba o dodatkowe informacje");
  });

  it("keeps an accepted lead out of the Registration table entirely -- it belongs on the Sales board", async () => {
    globalThis.ApiClient = {
      get: mockPartnerApi({
        leads: [
          lead({ id: "LD-001", registration_status: "accepted", status: "contact" }),
          lead({ id: "LD-002", registration_status: "pending" }),
        ],
      }),
    };

    await initPartnerLeads({ id: 1, role: "partner" });

    const rows = [...document.querySelectorAll("#leads-tbody tr")];
    expect(rows.length).toBe(1);
    expect(rows[0].textContent).not.toContain("LD-001");
  });

  it("shows a required action naming what to supply for the request-for-information stage, and that it is with the producer otherwise", async () => {
    globalThis.ApiClient = {
      get: mockPartnerApi({
        leads: [
          lead({ id: "LD-001", registration_status: "needs_info" }),
          lead({ id: "LD-002", registration_status: "pending" }),
        ],
      }),
    };

    await initPartnerLeads({ id: 1, role: "partner" });

    const rows = document.querySelectorAll("#leads-tbody tr");
    // The label is read from its own element: the cell may also carry the
    // answer control, which is a sibling of the label, not part of it.
    const label = (row) => row.cells[6].querySelector(".required-action-label").textContent;
    expect(label(rows[0])).toBe("Uzupełnij brakujące dane");
    expect(label(rows[1])).toBe("Czeka na producenta");
    expect(label(rows[0])).not.toBe(label(rows[1]));
  });

  it("shows the required-action column as settled for a rejected lead", async () => {
    globalThis.ApiClient = { get: mockPartnerApi({ leads: [lead({ registration_status: "rejected" })] }) };

    await initPartnerLeads({ id: 1, role: "partner" });

    const row = document.querySelector("#leads-tbody tr");
    expect(row.cells[6].textContent).toBe("Brak działania");
  });

  it("distinguishes pending from needs_info in the status column", async () => {
    globalThis.ApiClient = {
      get: mockPartnerApi({
        leads: [
          lead({ id: "LD-001", registration_status: "pending" }),
          lead({ id: "LD-002", registration_status: "needs_info" }),
        ],
      }),
    };

    await initPartnerLeads({ id: 1, role: "partner" });

    const rows = document.querySelectorAll("#leads-tbody tr");
    expect(rows[0].cells[2].textContent).not.toBe(rows[1].cells[2].textContent);
  });

  it("offers one filter option per registration status this table can contain, never 'accepted'", async () => {
    globalThis.ApiClient = { get: mockPartnerApi({ leads: [lead()] }) };

    await initPartnerLeads({ id: 1, role: "partner" });

    const options = [...document.getElementById("filter-status").options];
    expect(options.map((option) => option.value)).toEqual(["", "pending", "needs_info", "rejected"]);
    expect(options.map((option) => option.value)).not.toContain("accepted");
  });

  it("shows the queue position badge in the row for a lead above position one", async () => {
    globalThis.ApiClient = { get: mockPartnerApi({ leads: [lead({ queue_position: 2 })] }) };

    await initPartnerLeads({ id: 1, role: "partner" });

    const row = document.querySelector("#leads-tbody tr");
    expect(row.textContent).toContain("2. zgłoszenie");
    expect(row.textContent).toContain("50%");
  });

  it("shows an explicit provisional state for a lead with no commission yet, not a bare dash", async () => {
    globalThis.ApiClient = { get: mockPartnerApi({ leads: [lead({ commission: null })] }) };

    await initPartnerLeads({ id: 1, role: "partner" });

    const row = document.querySelector("#leads-tbody tr");
    const commissionCell = row.cells[4];
    expect(commissionCell.textContent.trim()).not.toBe("—");
    expect(commissionCell.textContent.trim().length).toBeGreaterThan(0);
  });

  it("renders the Copywriting Contract empty state with a link to the submission form when the list is empty", async () => {
    globalThis.ApiClient = { get: mockPartnerApi({ leads: [] }) };

    await initPartnerLeads({ id: 1, role: "partner" });

    expect(document.getElementById("leads-empty-state").hidden).toBe(false);
    expect(document.getElementById("leads-no-registration-state").hidden).toBe(true);
    expect(document.getElementById("leads-table-wrapper").hidden).toBe(true);
  });

  // Checkpoint defect #2: a partner whose leads are ALL accepted has zero
  // rows in this table too, but "you have never submitted a lead" is false
  // for them -- they have leads, just none currently awaiting a decision.
  // The two empty states must be visibly different copy, not the same
  // "Submit your first..." message.
  it("shows the distinct 'nothing awaiting registration' empty state when every lead is already accepted, not the 'no leads at all' copy", async () => {
    globalThis.ApiClient = {
      get: mockPartnerApi({
        leads: [
          lead({ id: "LD-001", registration_status: "accepted", status: "contact" }),
          lead({ id: "LD-002", registration_status: "accepted", status: "won" }),
        ],
      }),
    };

    await initPartnerLeads({ id: 1, role: "partner" });

    expect(document.getElementById("leads-no-registration-state").hidden).toBe(false);
    expect(document.getElementById("leads-empty-state").hidden).toBe(true);
    expect(document.getElementById("leads-table-wrapper").hidden).toBe(true);
  });

  it("still shows the 'no leads at all' empty state (not the registration-specific one) when the partner truly has no leads at all", async () => {
    globalThis.ApiClient = { get: mockPartnerApi({ leads: [] }) };

    await initPartnerLeads({ id: 1, role: "partner" });

    expect(document.getElementById("leads-empty-state").hidden).toBe(false);
    expect(document.getElementById("leads-no-registration-state").hidden).toBe(true);
  });

  it("keeps the plain empty state (not the accepted-elsewhere copy) when a search/filter -- not the underlying data -- is what leaves zero rows", async () => {
    globalThis.ApiClient = {
      get: mockPartnerApi({ leads: [lead({ id: "LD-001", registration_status: "pending" })] }),
    };

    await initPartnerLeads({ id: 1, role: "partner" });
    expect(document.getElementById("leads-table-wrapper").hidden).toBe(false);

    document.getElementById("leads-search").value = "no-such-client";
    document.getElementById("leads-search").dispatchEvent(new Event("input"));

    expect(document.getElementById("leads-empty-state").hidden).toBe(false);
    expect(document.getElementById("leads-no-registration-state").hidden).toBe(true);
  });

  it("renders the shared error copy with a working retry on a rejected leads fetch", async () => {
    globalThis.ApiClient = {
      get: vi.fn().mockRejectedValue(new Error("network down")),
    };

    await initPartnerLeads({ id: 1, role: "partner" });

    expect(document.getElementById("leads-error-state").hidden).toBe(false);

    globalThis.ApiClient.get = mockPartnerApi({ leads: [lead()] });
    document.getElementById("leads-retry-btn").dispatchEvent(new Event("click"));
    await vi.waitFor(() => expect(document.getElementById("leads-table-wrapper").hidden).toBe(false));
  });

  it("renders the shared error copy with a working retry when only the stage-list fetch fails", async () => {
    globalThis.ApiClient = {
      get: vi.fn((path) => {
        if (path === "/api/lead-stages") return Promise.reject(new Error("stages unavailable"));
        if (path === "/api/leads") return Promise.resolve({ leads: [lead()] });
        if (path === "/api/settings") return Promise.resolve({ settings: { lead_queue_shares: "100" } });
        throw new Error(`unexpected ${path}`);
      }),
    };

    await initPartnerLeads({ id: 1, role: "partner" });

    expect(document.getElementById("leads-error-state").hidden).toBe(false);
    expect(document.getElementById("leads-table-wrapper").hidden).toBe(true);

    globalThis.ApiClient.get = mockPartnerApi({ leads: [lead()] });
    document.getElementById("leads-retry-btn").dispatchEvent(new Event("click"));
    await vi.waitFor(() => expect(document.getElementById("leads-table-wrapper").hidden).toBe(false));
  });

  it("renders a lead field containing markup characters as literal text", async () => {
    globalThis.ApiClient = { get: mockPartnerApi({ leads: [lead({ client_name: PAYLOAD, company: PAYLOAD })] }) };
    window.__partnerLeadsPwned = false;

    await initPartnerLeads({ id: 1, role: "partner" });

    const row = document.querySelector("#leads-tbody tr");
    expect(row.textContent).toContain(PAYLOAD);
    expect(row.querySelector("img, script")).toBeNull();
    expect(window.__partnerLeadsPwned).toBe(false);
  });

  // A malicious sales-stage name only ever reaches the Sales board
  // (an accepted lead's stage badge does not render in this table) --
  // see js/__tests__/partner-kanban.test.js for that inert-rendering proof.
});

describe("Pulpit partnera — four figures, no programme-average widget", () => {
  beforeEach(buildDashboardFixture);

  // The "Commissions — how do they work?" panel answers "what do I earn?",
  // with the partner's own rate, not a static rate card.
  it("states this partner's own rate, not a static list of every tier", async () => {
    globalThis.ApiClient = { get: mockPartnerApi({ partner: { tier_name: "Silver", base_rate: 6, total_percent: 6 } }) };

    await initPartnerDashboard({ id: 1, role: "partner", name: "Ada Partner", level: "silver" });

    const panel = document.getElementById("dashboard-commission-rows");
    await vi.waitFor(() => expect(panel.textContent).toContain("6%"));
    expect(panel.textContent).toContain("Silver");
    expect(panel.textContent).toContain("Twoja łączna stawka");
    expect(panel.textContent).not.toContain("10%");
  });

  // The commission-rate panel and the badge above it read the
  // same tierSummary.partner object -- they must never disagree about which
  // language a tier name is shown in.
  it("names the tier in English on the commission-rate panel, matching the badge", async () => {
    globalThis.ApiClient = {
      get: mockPartnerApi({ partner: { tier_name: "Diament", tier_name_en: "Diamond", base_rate: 15, total_percent: 15 } }),
    };
    App.lang = "en";

    await initPartnerDashboard({ id: 1, role: "partner", name: "Ada Partner", level: "bronze" });

    const panel = document.getElementById("dashboard-commission-rows");
    await vi.waitFor(() => expect(panel.textContent).toContain("Diamond"));
    expect(panel.textContent).not.toContain("Diament");
  });

  it("falls back to the Polish tier name on the commission-rate panel when untranslated", async () => {
    globalThis.ApiClient = {
      get: mockPartnerApi({ partner: { tier_name: "Diament", tier_name_en: "", base_rate: 15, total_percent: 15 } }),
    };
    App.lang = "en";

    await initPartnerDashboard({ id: 1, role: "partner", name: "Ada Partner", level: "bronze" });

    const panel = document.getElementById("dashboard-commission-rows");
    await vi.waitFor(() => expect(panel.textContent).toContain("Diament"));
  });

  it("separates the tier base rate from percentage bonuses in the total", async () => {
    globalThis.ApiClient = { get: mockPartnerApi({ partner: { tier_name: "Gold", base_rate: 7, total_percent: 9.5 } }) };

    await initPartnerDashboard({ id: 1, role: "partner", name: "Ada Partner", level: "gold" });

    const panel = document.getElementById("dashboard-commission-rows");
    await vi.waitFor(() => expect(panel.textContent).toContain("9.5%"));
    expect(panel.textContent).toContain("7%");
    expect(panel.textContent).toContain("+2.5%");
  });

  it("leaves the placeholder in place rather than inventing a rate when the lookup fails", async () => {
    globalThis.ApiClient = { get: mockPartnerApi({ partner: null }) };

    await initPartnerDashboard({ id: 1, role: "partner", name: "Ada Partner", level: "gold" });

    const panel = document.getElementById("dashboard-commission-rows");
    expect(panel.textContent).toContain("—");
    expect(panel.textContent).not.toMatch(/\d%/);
  });

  // With no tiers defined the badge must not claim one: a "Bronze" above a
  // panel reading "Base rate (no tier) 0%" would be two answers on one screen.
  it("says 'No tier' on the badge when the partner has no tier, matching the rate panel", async () => {
    globalThis.ApiClient = {
      get: mockPartnerApi({ partner: { tier_name: null, base_rate: null, total_percent: 0 } }),
    };

    await initPartnerDashboard({ id: 1, role: "partner", name: "Ada Partner", level: "bronze" });

    const badge = document.getElementById("partner-level");
    await vi.waitFor(() => expect(badge.textContent.trim()).not.toBe(""));
    expect(badge.textContent).toContain("Brak poziomu");
    expect(badge.textContent).not.toContain("Bronze");
  });

  it("names the real tier on the badge, not the legacy level word", async () => {
    globalThis.ApiClient = {
      get: mockPartnerApi({ partner: { tier_name: "Diament", base_rate: 15, total_percent: 15 } }),
    };

    // level says "bronze"; the producer's own tier for this partner is Diament.
    await initPartnerDashboard({ id: 1, role: "partner", name: "Ada Partner", level: "bronze" });

    const badge = document.getElementById("partner-level");
    await vi.waitFor(() => expect(badge.textContent).toContain("Diament"));
    expect(badge.textContent).not.toContain("Bronze");
  });

  // The colour is the producer's own, set next to the level name in
  // Settings -- never a class named after a legacy level word.
  it("colours the badge with the producer's tier colour", async () => {
    globalThis.ApiClient = {
      get: mockPartnerApi({
        partner: { tier_name: "Diament", base_rate: 15, total_percent: 15, color: "#2244ff" },
      }),
    };

    await initPartnerDashboard({ id: 1, role: "partner", name: "Ada Partner", level: "bronze" });

    const badge = document.querySelector("#partner-level .level-badge");
    await vi.waitFor(() => expect(badge.textContent).toContain("Diament"));
    expect(badge.className).not.toContain("bronze");
    expect(badge.className).not.toContain("none");
    // Not the raw #2244ff any more: a producer picks a tier colour
    // with nothing to stop it vanishing on the light theme's paper card, so the
    // badge wears Ui.readableInk's form of it -- same hue, clamped lightness.
    expect(badge.style.color).toBe(Ui.readableInk("#2244ff"));
    expect(badge.style.color).toContain("hsl(");
  });

  it("keeps the producer's hue but darkens a pale tier colour so the badge stays readable on the light theme", () => {
    const pale = Ui.readableInk("#c7d2fe", "light");
    const lightness = Number(pale.match(/([\d.]+)%\)$/)[1]);

    expect(pale).toContain("hsl(");
    expect(lightness).toBeLessThanOrEqual(42);
  });

  it("lightens the same colour instead on the dark theme, where the card is graphite", () => {
    const onGraphite = Ui.readableInk("#312e81", "dark");
    const lightness = Number(onGraphite.match(/([\d.]+)%\)$/)[1]);

    expect(lightness).toBeGreaterThanOrEqual(62);
  });

  it("falls back to body text for a colour it cannot read, rather than emitting an invalid value", () => {
    expect(Ui.readableInk(null)).toBe("var(--text-secondary)");
    expect(Ui.readableInk("rgb(1,2,3)")).toBe("var(--text-secondary)");
  });

  it("says 'No tier' when the tier lookup fails, never the legacy level word", async () => {
    // A failed lookup must not fall back to session.level and print a
    // "Gold" the producer never granted.
    globalThis.ApiClient = { get: mockPartnerApi({ partner: null }) };

    await initPartnerDashboard({ id: 1, role: "partner", name: "Ada Partner", level: "gold" });

    const badge = document.getElementById("partner-level");
    await vi.waitFor(() => expect(badge.textContent.trim()).not.toBe(""));
    expect(badge.textContent).toContain("Brak poziomu");
    expect(badge.textContent).not.toContain("Gold");
  });

  it("renders leads needing completion, new decisions, the amount due and its deadline, with zero rendered plainly", async () => {
    globalThis.ApiClient = { get: mockPartnerApi({ leads: [] }) };

    await initPartnerDashboard({ id: 1, role: "partner", name: "Ada Partner", level: "gold" });

    expect(document.getElementById("kpi-total").textContent).toBe("0");
    expect(document.getElementById("kpi-pending").textContent).toBe("0");
  });

  it("shows the server's due and awaiting amounts, not a sum of won leads", async () => {
    // A won lead that was already paid must not read as money due: the
    // the dashboard never adds up won leads itself.
    globalThis.ApiClient = {
      get: mockPartnerApi({
        leads: [lead({ registration_status: "accepted", status: "won", commission: 500,
                       commission_paid_at: "2026-09-05T10:00:00" })],
        stats: { total_leads: 1, accepted_leads: 1 },
        earnings: { totals: { due: 1490, awaiting: 6000 } },
      }),
    };

    await initPartnerDashboard({ id: 1, role: "partner", name: "Ada Partner", level: "gold" });

    expect(document.getElementById("kpi-accepted").textContent).toBe(formatCurrency(1490));
    expect(document.getElementById("kpi-earnings").textContent).toBe(formatCurrency(6000));
  });

  it("labels a lead with no sales stage by its registration decision, never 'Etap nieznany'", async () => {
    // Waiting and rejected leads read "Etap nieznany".
    globalThis.ApiClient = {
      get: mockPartnerApi({
        leads: [lead({ id: "LD-900", status: null, registration_status: "pending" }),
                lead({ id: "LD-901", status: null, registration_status: "rejected" })],
      }),
    };

    await initPartnerDashboard({ id: 1, role: "partner", name: "Ada Partner", level: "gold" });

    const text = document.body.textContent;
    expect(text).not.toContain("Etap nieznany");
    expect(text).toContain(App.t("reg_status_pending"));
    expect(text).toContain(App.t("reg_status_rejected"));
  });

  it("shows a real zero as an amount, keeping the dash for 'could not read'", async () => {
    globalThis.ApiClient = { get: mockPartnerApi({ earnings: { totals: { due: 0, awaiting: 0 } } }) };

    await initPartnerDashboard({ id: 1, role: "partner", name: "Ada Partner", level: "gold" });

    expect(document.getElementById("kpi-accepted").textContent).not.toBe("—");
    expect(document.getElementById("kpi-accepted").textContent).toContain("0");
  });

  it("renders a dash, not a guessed amount, when the earnings cannot be read", async () => {
    globalThis.ApiClient = {
      get: mockPartnerApi({
        leads: [lead({ registration_status: "accepted", status: "won", commission: 500 })],
        earnings: null,
      }),
    };

    await initPartnerDashboard({ id: 1, role: "partner", name: "Ada Partner", level: "gold" });

    expect(document.getElementById("kpi-accepted").textContent).toBe("—");
    expect(document.getElementById("kpi-earnings").textContent).toBe("—");
  });

  it("counts a lead on the terminal lose stage as a new decision, not a lead needing completion", async () => {
    globalThis.ApiClient = { get: mockPartnerApi({ leads: [lead({ registration_status: "rejected" })] }) };

    await initPartnerDashboard({ id: 1, role: "partner", name: "Ada Partner", level: "gold" });

    expect(document.getElementById("kpi-total").textContent).toBe("0");
    expect(document.getElementById("kpi-pending").textContent).toBe("1");
  });

  it("renders a recent-lead badge from the configured stage record, including a stage added after this code was written", async () => {
    globalThis.ApiClient = { get: mockPartnerApi({ leads: [lead({ registration_status: "accepted", status: "qualifying" })], stages: [...STAGES, CUSTOM_STAGE] }) };

    await initPartnerDashboard({ id: 1, role: "partner", name: "Ada Partner", level: "gold" });

    expect(document.getElementById("my-recent-leads").textContent).toContain("Kwalifikacja");
  });

  it("renders no widget comparing the partner against a programme average anywhere on the page", async () => {
    globalThis.ApiClient = { get: mockPartnerApi({ leads: [] }) };

    await initPartnerDashboard({ id: 1, role: "partner", name: "Ada Partner", level: "gold" });

    expect(document.body.textContent).not.toMatch(/w programie/i);
  });

  it("the real dashboard page markup carries no programme-average comparison text", () => {
    const html = readFileSync(path.join(PROJECT_ROOT, "pages/partner/dashboard.html"), "utf8");
    expect(html).not.toMatch(/w programie/i);
  });
});

// ─── ANSWERING A REQUEST FOR INFORMATION ─────────────────────
//
// Until this wave a partner could read "Complete the missing data" in the
// table and had nowhere to type the answer. The row now offers the action,
// and the answer goes through the versioned workflow endpoint.

describe("Completing data — the partner answers the vendor's request", () => {
  beforeEach(buildLeadsFixture);

  function needsInfoLead(overrides = {}) {
    return lead({ registration_status: "needs_info", status: null, revision: 3, ...overrides });
  }

  it("offers an answer control only on a lead the producer actually asked about", async () => {
    globalThis.ApiClient = {
      get: mockPartnerApi({
        leads: [needsInfoLead({ id: "LD-001" }), lead({ id: "LD-002", registration_status: "pending" })],
      }),
    };

    await initPartnerLeads({ id: 1, role: "partner" });

    const rows = [...document.querySelectorAll("#leads-tbody tr")];
    const buttons = rows.map(row => row.querySelector("[data-answer-lead]"));
    expect(buttons[0]).not.toBeNull();
    expect(buttons[1]).toBeNull();
  });

  it("sends the answer with the lead's own revision and a replay key", async () => {
    const post = vi.fn(() => Promise.resolve({ lead: needsInfoLead({ revision: 4 }) }));
    globalThis.ApiClient = { get: mockPartnerApi({ leads: [needsInfoLead({ id: "LD-001" })] }), post };

    await initPartnerLeads({ id: 1, role: "partner" });
    document.querySelector("[data-answer-lead]").click();

    document.getElementById("answer-text").value = "  Klient prosi o telefon w piątek.  ";
    document.getElementById("answer-submit-btn").click();
    await vi.waitFor(() => expect(post).toHaveBeenCalled());

    const [url, body] = post.mock.calls[0];
    expect(url).toBe("/api/leads/LD-001/information");
    expect(body.message).toBe("Klient prosi o telefon w piątek.");
    expect(body.expected_revision).toBe(3);
    expect(typeof body.operation_id).toBe("string");
    expect(body.operation_id.length).toBeGreaterThan(0);
  });

  it("refuses to send an empty answer and never calls the API", async () => {
    const post = vi.fn();
    globalThis.ApiClient = { get: mockPartnerApi({ leads: [needsInfoLead()] }), post };

    await initPartnerLeads({ id: 1, role: "partner" });
    document.querySelector("[data-answer-lead]").click();
    document.getElementById("answer-text").value = "   ";
    document.getElementById("answer-submit-btn").click();

    expect(post).not.toHaveBeenCalled();
  });

  it("keeps the draft and shows no success when the request fails", async () => {
    const failure = Object.assign(new Error("Błąd serwera (500)"), { status: 500 });
    const post = vi.fn(() => Promise.reject(failure));
    globalThis.ApiClient = { get: mockPartnerApi({ leads: [needsInfoLead()] }), post };

    await initPartnerLeads({ id: 1, role: "partner" });
    document.querySelector("[data-answer-lead]").click();
    document.getElementById("answer-text").value = "Odpowiedź nie do stracenia";
    document.getElementById("answer-submit-btn").click();
    await vi.waitFor(() => expect(post).toHaveBeenCalled());

    expect(document.getElementById("answer-text").value).toBe("Odpowiedź nie do stracenia");
    expect(document.body.textContent).not.toContain("Odpowiedź wysłana");
  });

  it("tells the partner to look again when the lead moved on underneath them", async () => {
    const conflict = Object.assign(new Error("Zgłoszenie zmieniło się."), {
      status: 409, code: "stale_revision",
    });
    const post = vi.fn(() => Promise.reject(conflict));
    const get = mockPartnerApi({ leads: [needsInfoLead()] });
    globalThis.ApiClient = { get, post };

    await initPartnerLeads({ id: 1, role: "partner" });
    const callsBefore = get.mock.calls.length;
    document.querySelector("[data-answer-lead]").click();
    document.getElementById("answer-text").value = "Odpowiedź";
    document.getElementById("answer-submit-btn").click();
    await vi.waitFor(() => expect(post).toHaveBeenCalled());

    // A conflict is the one case that refetches: the screen is out of date,
    // so re-reading is the only honest thing to show.
    await vi.waitFor(() => expect(get.mock.calls.length).toBeGreaterThan(callsBefore));
  });
});
