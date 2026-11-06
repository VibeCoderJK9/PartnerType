/*
 * Reopening a mistakenly won/lost lead.
 *
 * The shape being pinned on the producer's own screen: the reopen controls
 * exist only for an admin session looking at a terminal (won/lost) lead;
 * they stay entirely absent for a non-admin staff session and for a lead
 * that is not closed. A recorded commission with no settlement review
 * blocks the "Reopen" action and offers only "Check the settlement
 * rozliczenia"; an explicit unpaid review unblocks it; an already-paid
 * review leaves no action at all. The reopen action itself is a real modal
 * dialog (role=dialog) the user can cancel without sending anything.
 *
 * Same happy-dom + classic-script-in-vm harness as
 * js/__tests__/admin-leads.test.js (that file's own comment explains why:
 * this project has no bundler, so a bare ES `import` cannot see admin-leads.js's
 * top-level bindings -- the harness runs the raw source in the real global
 * realm instead, exactly like a <script> tag would).
 */
import { readFileSync } from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { Window } from "happy-dom";
import { describe, expect, it, vi } from "vitest";
import { appStub } from "./helpers/copy.js";
import { isLeadListUrl, serveLeadList } from "./helpers/lead-list-server.js";

const PROJECT_ROOT = path.resolve(import.meta.dirname, "..", "..");

function runClassicScript(window, relativePath) {
  const absolutePath = path.join(PROJECT_ROOT, relativePath);
  const source = readFileSync(absolutePath, "utf8").replace(
    'global.AdminLeads = Object.freeze({ init, reload });',
    'global.AdminLeads = Object.freeze({ init, reload, openForTest: openLeadDetail, selectForTest: (lead) => { selectedLead = lead; }, selectedForTest: () => selectedLead });',
  );
  const context = window.__vmContext || (window.__vmContext = vm.createContext(window));
  vm.runInContext(source, context, { filename: absolutePath });
  return context;
}

// Mirrors the seeded pipeline exactly (migrations/default_stages.json), same as
// js/__tests__/admin-leads.test.js and js/__tests__/partner-kanban.test.js.
const STAGES = [
  { id: 1, key: "contact", name: "Nawiązanie kontaktu", sort_order: 0, kind: "progress", is_terminal: 0, color: "#94a3b8", active: 1 },
  { id: 2, key: "presentation", name: "Prezentacja", sort_order: 1, kind: "progress", is_terminal: 0, color: "#3b82f6", active: 1 },
  { id: 3, key: "offer", name: "Oferta", sort_order: 2, kind: "progress", is_terminal: 0, color: "#8b5cf6", active: 1 },
  { id: 4, key: "negotiation", name: "Negocjacje", sort_order: 3, kind: "progress", is_terminal: 0, color: "#f59e0b", active: 1 },
  { id: 5, key: "won", name: "Wygrany", sort_order: 4, kind: "win", is_terminal: 1, color: "#10b981", active: 1 },
  { id: 6, key: "lost", name: "Przegrany", sort_order: 5, kind: "lose", is_terminal: 1, color: "#ef4444", active: 1 },
];

function baseLead(overrides = {}) {
  return {
    id: "LD-100",
    client_name: "Jan Kowalski",
    client_email: "jan@example.com",
    client_phone: "+48123456789",
    company: "Acme Sp. z o.o.",
    notes: "",
    sector: "IT",
    nip: "1234567890",
    website: "https://example.com",
    product: "Partner CRM",
    quantity: 1,
    queue_position: 1,
    value: 10000,
    partner_id: 7,
    partner_name: "Partner Testowy",
    registration_status: "accepted",
    status: "won",
    commission: null,
    commission_projected: 700,
    commission_breakdown: { tier: 700, recurring: 0, cash: 0 },
    settlement_status: null,
    revision: 5,
    created_at: "2026-08-20T10:00:00Z",
    ...overrides,
  };
}

function createRealm({ lead = baseLead(), session = { id: 1, role: "admin" }, stages = STAGES } = {}) {
  const window = new Window({ url: "http://localhost:8000/pages/admin/leads.html" });
  window.document.body.dataset.page = "admin-leads";
  window.document.body.innerHTML = `
    <input id="leads-search"><span id="leads-count"></span>
    <input id="filter-date-from"><input id="filter-date-to">
    <input id="filter-value" type="number"><input id="filter-overdue" type="checkbox">
    <div id="status-select-wrapper"><input id="status-search-input"><input id="filter-status"><div id="status-dropdown-list"></div></div>
    <div id="partner-select-wrapper"><input id="partner-search-input"><input id="filter-partner"><div id="partner-dropdown-list"></div></div>
    <table><thead id="leads-table-head"><tr><th data-sort="client_name">Klient</th></tr></thead><tbody id="leads-tbody"></tbody></table>
    <div id="pagination"><span class="pagination-info"></span><div class="pagination-btns"></div></div>
    <div id="leads-loading-state" hidden></div><div id="leads-empty-state" hidden></div>
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
    <div id="lead-reopen-section" hidden>
      <p id="lead-reopen-settlement-state"></p>
      <div id="lead-reopen-review-form" hidden>
        <input type="radio" name="reopen-review-result" value="unpaid" id="lead-reopen-review-unpaid">
        <input type="radio" name="reopen-review-result" value="paid" id="lead-reopen-review-paid">
        <textarea id="lead-reopen-review-reason"></textarea>
        <label id="lead-reopen-review-confirm-label" hidden><input type="checkbox" id="lead-reopen-review-confirm"></label>
        <button id="lead-reopen-review-save-btn" disabled></button>
      </div>
      <button id="lead-reopen-check-btn" hidden></button>
      <button id="lead-reopen-open-btn" hidden></button>
    </div>
  `;
  const partners = [{ id: 7, name: lead.partner_name, company: lead.company }];
  window.ApiClient = {
    get: vi.fn((url) => {
      if (isLeadListUrl(url)) return Promise.resolve(serveLeadList(url, [lead]));
      if (url === "/api/partners") return Promise.resolve({ partners });
      if (url === "/api/lead-stages") return Promise.resolve({ stages });
      if (url === "/api/audit") return Promise.resolve({ logs: [] });
      const id = decodeURIComponent(url.replace("/api/leads/", ""));
      return Promise.resolve({ lead: id === lead.id ? lead : undefined });
    }),
    patch: vi.fn((url, body) => Promise.resolve({ lead: { ...lead, ...body } })),
    post: vi.fn((url, body) => {
      if (url === `/api/leads/${encodeURIComponent(lead.id)}/reopen`) {
        return Promise.resolve({
          lead: {
            ...lead, status: body.target_stage,
            won_at: null, commission: null, settlement_status: null,
            revision: Number(lead.revision) + 1,
          },
        });
      }
      if (url === `/api/leads/${encodeURIComponent(lead.id)}/reopen-eligibility`) {
        return Promise.resolve({
          lead: { ...lead, settlement_status: body.result, revision: Number(lead.revision) + 1 },
        });
      }
      return Promise.resolve({ comment: { id: 1 } });
    }),
  };
  window.App = { ...appStub("pl"), applyTranslations: vi.fn() };
  window.formatCurrency = vi.fn((value) => `${value} PLN`);
  window.formatDate = vi.fn(() => "22 sie 2026");
  window.getInitials = vi.fn(() => "JK");
  window.getAvatarColorClass = vi.fn(() => "bg-nebula-indigo");
  window.getStatusBadge = vi.fn((status) => `<span class="badge">${status}</span>`);
  window.openModal = vi.fn();
  window.closeModal = vi.fn();
  window.showToast = vi.fn();
  window.handleApiError = vi.fn();
  window.Comments = { load: vi.fn().mockResolvedValue(undefined) };
  window.sessionStorage.setItem("ph_session", JSON.stringify(session));
  runClassicScript(window, "js/icons.js");
  runClassicScript(window, "js/utils.js");
  runClassicScript(window, "js/admin-leads.js");
  return { window, lead };
}

async function openDetail(window) {
  window.document.querySelector('[data-action="detail"]').click();
  await vi.waitFor(() => expect(window.openModal).toHaveBeenCalled());
}

describe("lead reopen -- visibility gated by role and terminal state", () => {
  it("shows the section for an admin viewing a terminal lead", async () => {
    const { window } = createRealm({ lead: baseLead({ status: "lost" }) });
    await window.AdminLeads.init();
    await openDetail(window);
    expect(window.document.getElementById("lead-reopen-section").hidden).toBe(false);
  });

  it("stays hidden for an admin viewing a lead that is not terminal", async () => {
    const { window } = createRealm({ lead: baseLead({ status: "contact" }) });
    await window.AdminLeads.init();
    await openDetail(window);
    expect(window.document.getElementById("lead-reopen-section").hidden).toBe(true);
  });

  it("stays hidden for a manager session even on a terminal lead", async () => {
    const { window } = createRealm({
      lead: baseLead({ status: "lost" }),
      session: { id: 2, role: "manager" },
    });
    await window.AdminLeads.init();
    await openDetail(window);
    expect(window.document.getElementById("lead-reopen-section").hidden).toBe(true);
  });

  it("stays hidden for an observer session even on a terminal lead", async () => {
    const { window } = createRealm({
      lead: baseLead({ status: "won", commission: null }),
      session: { id: 3, role: "observer" },
    });
    await window.AdminLeads.init();
    await openDetail(window);
    expect(window.document.getElementById("lead-reopen-section").hidden).toBe(true);
  });
});

describe("lead reopen -- settlement eligibility gate", () => {
  it.each(['paid', 'unknown'])("does not bypass %s evidence when amount is missing", async settlement_status => {
    const { window } = createRealm({ lead: baseLead({ commission: null, settlement_status }) });
    await window.AdminLeads.init();
    await openDetail(window);
    expect(window.document.getElementById('lead-reopen-open-btn').hidden).toBe(true);
    expect(window.document.getElementById('lead-reopen-check-btn').hidden).toBe(settlement_status === 'paid');
  });

  it('hides reopen for an unaccepted legacy terminal lead', async () => {
    const { window } = createRealm({ lead: baseLead({ status: 'lost', registration_status: 'rejected' }) });
    await window.AdminLeads.init();
    await openDetail(window);
    expect(window.document.getElementById('lead-reopen-section').hidden).toBe(true);
  });

  it("offers Reopen directly when there is no recorded commission", async () => {
    const { window } = createRealm({ lead: baseLead({ status: "lost", commission: null }) });
    await window.AdminLeads.init();
    await openDetail(window);
    expect(window.document.getElementById("lead-reopen-open-btn").hidden).toBe(false);
    expect(window.document.getElementById("lead-reopen-check-btn").hidden).toBe(true);
  });

  it("blocks with Check the settlement state when the commission's settlement is unreviewed", async () => {
    const { window } = createRealm({
      lead: baseLead({ status: "won", commission: 700, settlement_status: "unknown" }),
    });
    await window.AdminLeads.init();
    await openDetail(window);
    expect(window.document.getElementById("lead-reopen-open-btn").hidden).toBe(true);
    expect(window.document.getElementById("lead-reopen-check-btn").hidden).toBe(false);
    expect(window.document.getElementById("lead-reopen-settlement-state").textContent).not.toBe("");
  });

  it("offers Reopen once the settlement was reviewed unpaid", async () => {
    const { window } = createRealm({
      lead: baseLead({ status: "won", commission: 700, settlement_status: "unpaid" }),
    });
    await window.AdminLeads.init();
    await openDetail(window);
    expect(window.document.getElementById("lead-reopen-open-btn").hidden).toBe(false);
  });

  it("offers no action at all once the settlement was reviewed paid", async () => {
    const { window } = createRealm({
      lead: baseLead({ status: "won", commission: 700, settlement_status: "paid" }),
    });
    await window.AdminLeads.init();
    await openDetail(window);
    expect(window.document.getElementById("lead-reopen-open-btn").hidden).toBe(true);
    expect(window.document.getElementById("lead-reopen-check-btn").hidden).toBe(true);
    expect(window.document.getElementById("lead-reopen-settlement-state").textContent).not.toBe("");
  });

  it("clicking Check the settlement state reveals the review form", async () => {
    const { window } = createRealm({
      lead: baseLead({ status: "won", commission: 700, settlement_status: "unknown" }),
    });
    await window.AdminLeads.init();
    await openDetail(window);
    window.document.getElementById("lead-reopen-check-btn").click();
    expect(window.document.getElementById("lead-reopen-review-form").hidden).toBe(false);
  });

  it("requires a result, a reason and (for unpaid) the confirmation checkbox before Save is enabled", async () => {
    const { window } = createRealm({
      lead: baseLead({ status: "won", commission: 700, settlement_status: "unknown" }),
    });
    await window.AdminLeads.init();
    await openDetail(window);
    window.document.getElementById("lead-reopen-check-btn").click();

    const saveBtn = window.document.getElementById("lead-reopen-review-save-btn");
    const unpaid = window.document.getElementById("lead-reopen-review-unpaid");
    const reason = window.document.getElementById("lead-reopen-review-reason");
    const confirmLabel = window.document.getElementById("lead-reopen-review-confirm-label");
    const confirmBox = window.document.getElementById("lead-reopen-review-confirm");

    unpaid.checked = true;
    unpaid.dispatchEvent(new window.Event("change", { bubbles: true }));
    expect(confirmLabel.hidden).toBe(false);
    expect(saveBtn.disabled).toBe(true);

    reason.value = "Sprawdzono księgowość.";
    reason.dispatchEvent(new window.Event("input", { bubbles: true }));
    expect(saveBtn.disabled).toBe(true); // confirmation checkbox still unticked

    confirmBox.checked = true;
    confirmBox.dispatchEvent(new window.Event("change", { bubbles: true }));
    expect(saveBtn.disabled).toBe(false);
  });

  it("saving a review posts result/reason/confirmed and does not itself move the lead", async () => {
    const { window, lead } = createRealm({
      lead: baseLead({ status: "won", commission: 700, settlement_status: "unknown" }),
    });
    await window.AdminLeads.init();
    await openDetail(window);
    window.document.getElementById("lead-reopen-check-btn").click();

    window.document.getElementById("lead-reopen-review-unpaid").checked = true;
    window.document.getElementById("lead-reopen-review-unpaid")
      .dispatchEvent(new window.Event("change", { bubbles: true }));
    window.document.getElementById("lead-reopen-review-reason").value = "Sprawdzono księgowość.";
    window.document.getElementById("lead-reopen-review-reason")
      .dispatchEvent(new window.Event("input", { bubbles: true }));
    window.document.getElementById("lead-reopen-review-confirm").checked = true;
    window.document.getElementById("lead-reopen-review-confirm")
      .dispatchEvent(new window.Event("change", { bubbles: true }));

    window.document.getElementById("lead-reopen-review-save-btn").click();

    await vi.waitFor(() => expect(window.ApiClient.post).toHaveBeenCalledWith(
      `/api/leads/${encodeURIComponent(lead.id)}/reopen-eligibility`,
      expect.objectContaining({ result: "unpaid", reason: "Sprawdzono księgowość.", confirmed: true }),
    ));
    // After a successful review, the ordinary reopen path opens up.
    await vi.waitFor(() => expect(
      window.document.getElementById("lead-reopen-open-btn").hidden,
    ).toBe(false));
  });
});

describe("lead reopen -- the reopen confirmation dialog", () => {
  it("refreshes stages after conflict and requires a new choice when the draft destination disappears", async () => {
    const { window, lead } = createRealm({ lead: baseLead({ status: "lost" }) });
    await window.AdminLeads.init();
    await openDetail(window);
    window.ApiClient.post.mockRejectedValueOnce(Object.assign(new Error("stage changed"), { status: 409 }));
    window.ApiClient.get.mockResolvedValueOnce({ lead: { ...lead, revision: 6 } });
    window.ApiClient.get.mockResolvedValueOnce({ stages: STAGES.map((stage) => ({ ...stage, active: stage.key === "contact" ? 0 : 1 })) });
    const trigger = window.document.getElementById("lead-reopen-open-btn");
    trigger.click();
    let dialog = window.document.querySelector('[role="dialog"]');
    dialog.querySelector("textarea").value = "Zachowany powód";
    dialog.querySelector("textarea").dispatchEvent(new window.Event("input"));
    [...dialog.querySelectorAll("button")].at(-1).click();
    await vi.waitFor(() => expect(window.handleApiError).toHaveBeenCalled());
    trigger.click();
    dialog = window.document.querySelector('[role="dialog"]');
    const select = dialog.querySelector("select");
    const confirm = [...dialog.querySelectorAll("button")].at(-1);
    expect([...select.options].some((option) => option.value === "contact")).toBe(false);
    expect(select.value).toBe("");
    expect(confirm.disabled).toBe(true);
    expect(dialog.querySelector("textarea").value).toBe("Zachowany powód");
    select.value = "presentation";
    select.dispatchEvent(new window.Event("change"));
    expect(confirm.disabled).toBe(false);
  });

  it("asks before discarding an edited reason and focuses the safe action", async () => {
    const { window } = createRealm({ lead: baseLead({ status: "lost" }) });
    await window.AdminLeads.init();
    await openDetail(window);
    window.document.getElementById("lead-reopen-open-btn").click();
    const dialog = window.document.querySelector('[role="dialog"]');
    const reason = dialog.querySelector("textarea");
    reason.value = "Nie porzucaj przypadkiem";
    reason.dispatchEvent(new window.Event("input"));
    dialog.querySelector("button").click();
    const back = dialog.querySelector('[data-reopen-discard-back]');
    expect(back).not.toBeNull();
    expect(window.document.activeElement).toBe(back);
    back.click();
    expect(reason.value).toBe("Nie porzucaj przypadkiem");
    dialog.querySelector("button").click();
    dialog.querySelector('[data-reopen-discard]').click();
    expect(window.document.querySelector('[role="dialog"]')).toBeNull();
    expect(window.ApiClient.post).not.toHaveBeenCalled();
  });

  it("blocks another write when the recovery read fails and offers a read-only retry", async () => {
    const { window, lead } = createRealm({ lead: baseLead({ status: "lost" }) });
    await window.AdminLeads.init();
    await openDetail(window);
    window.ApiClient.post.mockRejectedValueOnce(new Error("offline"));
    window.ApiClient.get.mockRejectedValueOnce(new Error("still offline"));
    const trigger = window.document.getElementById("lead-reopen-open-btn");
    trigger.click();
    const dialog = window.document.querySelector('[role="dialog"]');
    dialog.querySelector("textarea").value = "Korekta";
    dialog.querySelector("textarea").dispatchEvent(new window.Event("input"));
    [...dialog.querySelectorAll("button")].at(-1).click();
    await vi.waitFor(() => expect(window.handleApiError).toHaveBeenCalled());
    expect(trigger.disabled).toBe(true);
    const retry = window.document.getElementById("lead-reopen-refresh-btn");
    expect(retry.hidden).toBe(false);
    window.ApiClient.get.mockResolvedValueOnce({ lead: { ...lead, revision: 8 } });
    retry.click();
    await vi.waitFor(() => expect(trigger.disabled).toBe(false));
    expect(window.AdminLeads.selectedForTest().revision).toBe(8);
    expect(window.ApiClient.post).toHaveBeenCalledTimes(1);
  });

  it("uses a new operation id when a rejected draft is edited", async () => {
    const { window } = createRealm({ lead: baseLead({ status: "lost" }) });
    await window.AdminLeads.init();
    await openDetail(window);
    window.ApiClient.post.mockRejectedValueOnce(Object.assign(new Error("validation"), { status: 400 }));
    const trigger = window.document.getElementById("lead-reopen-open-btn");
    trigger.click();
    let dialog = window.document.querySelector('[role="dialog"]');
    dialog.querySelector("textarea").value = "Pierwszy powód";
    dialog.querySelector("textarea").dispatchEvent(new window.Event("input"));
    [...dialog.querySelectorAll("button")].at(-1).click();
    await vi.waitFor(() => expect(window.handleApiError).toHaveBeenCalled());
    trigger.click();
    dialog = window.document.querySelector('[role="dialog"]');
    dialog.querySelector("textarea").value = "Poprawiony powód";
    dialog.querySelector("textarea").dispatchEvent(new window.Event("input"));
    [...dialog.querySelectorAll("button")].at(-1).click();
    await vi.waitFor(() => expect(window.ApiClient.post).toHaveBeenCalledTimes(2));
    expect(window.ApiClient.post.mock.calls[1][1].operation_id).not.toBe(window.ApiClient.post.mock.calls[0][1].operation_id);
  });

  it.each([409, 500, undefined])("refetches after an uncertain/conflicting write (%s) without automatic retry", async (status) => {
    const { window, lead } = createRealm({ lead: baseLead({ status: "lost" }) });
    await window.AdminLeads.init();
    await openDetail(window);
    window.ApiClient.post.mockRejectedValueOnce(Object.assign(new Error("uncertain"), { status }));
    window.ApiClient.get.mockResolvedValueOnce({ lead: { ...lead, revision: 7 } });
    window.document.getElementById("lead-reopen-open-btn").click();
    const dialog = window.document.querySelector('[role="dialog"]');
    dialog.querySelector("textarea").value = "Zachowaj powód";
    dialog.querySelector("textarea").dispatchEvent(new window.Event("input"));
    [...dialog.querySelectorAll("button")].at(-1).click();
    await vi.waitFor(() => expect(window.AdminLeads.selectedForTest().revision).toBe(7));
    expect(window.ApiClient.post).toHaveBeenCalledTimes(1);
    window.document.getElementById("lead-reopen-open-btn").click();
    expect(window.document.querySelector('[role="dialog"] textarea').value).toBe("Zachowaj powód");
  });

  it("keeps settlement review drafts separate and requires fresh confirmation after refetch", async () => {
    const lead = baseLead({ settlement_status: "unknown", commission: 700 });
    const other = baseLead({ id: "LD-200", settlement_status: "unknown" });
    const { window } = createRealm({ lead });
    await window.AdminLeads.init();
    await openDetail(window);
    window.document.getElementById("lead-reopen-check-btn").click();
    const reason = window.document.getElementById("lead-reopen-review-reason");
    reason.value = "Weryfikacja pierwszego leada";
    reason.dispatchEvent(new window.Event("input"));
    const unpaid = window.document.getElementById("lead-reopen-review-unpaid");
    unpaid.checked = true;
    unpaid.dispatchEvent(new window.Event("change"));
    const confirm = window.document.getElementById("lead-reopen-review-confirm");
    confirm.checked = true;
    confirm.dispatchEvent(new window.Event("change"));
    window.ApiClient.get.mockResolvedValueOnce({ lead: other });
    await window.AdminLeads.openForTest(other.id);
    expect(reason.value).toBe("");
    expect(unpaid.checked).toBe(false);
    expect(confirm.checked).toBe(false);
    window.ApiClient.get.mockResolvedValueOnce({ lead: { ...lead, revision: 6 } });
    await window.AdminLeads.openForTest(lead.id);
    expect(reason.value).toBe("Weryfikacja pierwszego leada");
    expect(unpaid.checked).toBe(true);
    expect(confirm.checked).toBe(false);
  });

  it.each([403, 404])("removes inaccessible detail and drafts on %s", async (status) => {
    const { window } = createRealm({ lead: baseLead({ status: "lost" }) });
    await window.AdminLeads.init();
    await openDetail(window);
    window.ApiClient.post.mockRejectedValueOnce(Object.assign(new Error("denied"), { status }));
    window.document.getElementById("lead-reopen-open-btn").click();
    const dialog = window.document.querySelector('[role="dialog"]');
    dialog.querySelector("textarea").value = "Korekta";
    dialog.querySelector("textarea").dispatchEvent(new window.Event("input"));
    [...dialog.querySelectorAll("button")].at(-1).click();
    await vi.waitFor(() => expect(window.closeModal).toHaveBeenCalledWith("lead-modal"));
    expect(window.AdminLeads.selectedForTest()).toBeNull();
    expect(window.document.getElementById("modal-lead-company").textContent).toBe("");
    expect(window.document.getElementById("modal-commission-total").textContent).toBe("");
    expect(window.document.querySelector('[data-action="detail"]')).toBeNull();
  });

  it("offers only active destinations and disables reopening when none exist", async () => {
    const stages = STAGES.map((stage) => ({ ...stage, active: stage.is_terminal ? 1 : 0 }));
    const { window } = createRealm({ lead: baseLead({ status: "lost" }), stages });
    await window.AdminLeads.init();
    await openDetail(window);
    const trigger = window.document.getElementById("lead-reopen-open-btn");
    expect(trigger.disabled).toBe(true);
    trigger.click();
    expect(window.document.querySelector('[role="dialog"]')).toBeNull();
  });

  it("preserves the reason and destination after a network failure for explicit retry", async () => {
    const { window } = createRealm({ lead: baseLead({ status: "lost" }) });
    await window.AdminLeads.init();
    await openDetail(window);
    window.ApiClient.post.mockRejectedValueOnce(new Error("offline"));
    const trigger = window.document.getElementById("lead-reopen-open-btn");
    trigger.click();
    let dialog = window.document.querySelector('[role="dialog"]');
    dialog.querySelector("select").value = "negotiation";
    dialog.querySelector("textarea").value = "Nie gub tego uzasadnienia";
    dialog.querySelector("textarea").dispatchEvent(new window.Event("input"));
    [...dialog.querySelectorAll("button")].at(-1).click();
    await vi.waitFor(() => expect(window.handleApiError).toHaveBeenCalled());
    expect(trigger.disabled).toBe(false);
    expect(window.ApiClient.post).toHaveBeenCalledTimes(1);
    trigger.click();
    dialog = window.document.querySelector('[role="dialog"]');
    expect(dialog.querySelector("textarea").value).toBe("Nie gub tego uzasadnienia");
    expect(dialog.querySelector("select").value).toBe("negotiation");
    [...dialog.querySelectorAll("button")].at(-1).click();
    await vi.waitFor(() => expect(window.ApiClient.post).toHaveBeenCalledTimes(2));
    expect(window.ApiClient.post.mock.calls[1]).toEqual(window.ApiClient.post.mock.calls[0]);
  });

  it("allows only one confirmation dialog at a time", async () => {
    const { window } = createRealm({ lead: baseLead({ status: "lost" }) });
    await window.AdminLeads.init();
    await openDetail(window);
    const trigger = window.document.getElementById("lead-reopen-open-btn");
    trigger.click();
    trigger.click();
    expect(window.document.querySelectorAll('[role="dialog"]')).toHaveLength(1);
  });

  it("keeps the confirmed lead and revision when selection changes during the dialog", async () => {
    const { window, lead } = createRealm({ lead: baseLead({ status: "lost" }) });
    await window.AdminLeads.init();
    await openDetail(window);
    window.document.getElementById("lead-reopen-open-btn").click();
    const dialog = window.document.querySelector('[role="dialog"]');
    const reason = dialog.querySelector("textarea");
    reason.value = "Korekta omyłkowego zamknięcia";
    reason.dispatchEvent(new window.Event("input"));
    window.AdminLeads.selectForTest({ id: "OTHER", revision: 99 });
    [...dialog.querySelectorAll("button")].at(-1).click();
    await vi.waitFor(() => expect(window.ApiClient.post).toHaveBeenCalledWith(
      `/api/leads/${lead.id}/reopen`,
      expect.objectContaining({ expected_revision: 5 }),
    ));
    await vi.waitFor(() => expect(window.showToast).toHaveBeenCalled());
    expect(window.AdminLeads.selectedForTest().id).toBe("OTHER");
  });

  it("coalesces review clicks and does not overwrite another selected lead on response", async () => {
    const { window, lead } = createRealm({ lead: baseLead({ settlement_status: "unknown", commission: 700 }) });
    await window.AdminLeads.init();
    await openDetail(window);
    let finish;
    window.ApiClient.post.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    window.document.getElementById("lead-reopen-check-btn").click();
    window.document.getElementById("lead-reopen-review-paid").checked = true;
    const reason = window.document.getElementById("lead-reopen-review-reason");
    reason.value = "Potwierdzono wypłatę";
    reason.dispatchEvent(new window.Event("input"));
    const save = window.document.getElementById("lead-reopen-review-save-btn");
    save.click();
    save.click();
    expect(window.ApiClient.post).toHaveBeenCalledTimes(1);
    window.AdminLeads.selectForTest({ id: "OTHER", revision: 99 });
    finish({ lead: { ...lead, settlement_status: "paid", revision: 6 } });
    await vi.waitFor(() => expect(window.showToast).toHaveBeenCalled());
    expect(window.AdminLeads.selectedForTest().id).toBe("OTHER");
  });

  it("labels inputs, traps keyboard focus and restores it after cancellation", async () => {
    const { window } = createRealm({ lead: baseLead({ status: "lost" }) });
    await window.AdminLeads.init();
    await openDetail(window);
    const trigger = window.document.getElementById("lead-reopen-open-btn");
    trigger.focus();
    trigger.click();
    const dialog = window.document.querySelector('[role="dialog"]');
    const select = dialog.querySelector("select");
    const reason = dialog.querySelector("textarea");
    expect(select.id).not.toBe("");
    expect(dialog.querySelector(`label[for="${select.id}"]`)).not.toBeNull();
    expect(dialog.querySelector(`label[for="${reason.id}"]`)).not.toBeNull();
    expect(reason.maxLength).toBe(4000);
    const keep = dialog.querySelector("button");
    keep.focus();
    keep.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Tab", bubbles: true, cancelable: true }));
    expect(window.document.activeElement).toBe(select);
    select.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Tab", shiftKey: true, bubbles: true, cancelable: true }));
    expect(window.document.activeElement).toBe(keep);
    keep.click();
    expect(window.document.activeElement).toBe(trigger);
  });

  it("is a real, cancellable dialog that sends nothing on Escape", async () => {
    const { window } = createRealm({ lead: baseLead({ status: "lost", commission: null }) });
    await window.AdminLeads.init();
    await openDetail(window);

    window.document.getElementById("lead-reopen-open-btn").click();
    const dialog = await vi.waitFor(() => {
      const el = window.document.querySelector('[role="dialog"]');
      expect(el).not.toBeNull();
      return el;
    });
    expect(dialog.getAttribute("aria-modal")).toBe("true");

    dialog.closest(".modal-overlay").dispatchEvent(new window.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    await vi.waitFor(() => expect(window.document.querySelector('[role="dialog"]')).toBeNull());
    expect(window.ApiClient.post).not.toHaveBeenCalledWith(
      expect.stringContaining("/reopen"),
      expect.anything(),
    );
  });

  it("Keep lead closed cancels without sending anything", async () => {
    const { window } = createRealm({ lead: baseLead({ status: "lost", commission: null }) });
    await window.AdminLeads.init();
    await openDetail(window);

    window.document.getElementById("lead-reopen-open-btn").click();
    const dialog = await vi.waitFor(() => {
      const el = window.document.querySelector('[role="dialog"]');
      expect(el).not.toBeNull();
      return el;
    });
    const keepBtn = [...dialog.querySelectorAll("button")][0];
    keepBtn.click();
    await vi.waitFor(() => expect(window.document.querySelector('[role="dialog"]')).toBeNull());
    expect(window.ApiClient.post).not.toHaveBeenCalledWith(
      expect.stringContaining("/reopen"),
      expect.anything(),
    );
  });

  it("confirming sends target_stage, reason and confirmed=true, then refreshes the lead in place", async () => {
    const { window, lead } = createRealm({ lead: baseLead({ status: "lost", commission: null }) });
    await window.AdminLeads.init();
    await openDetail(window);

    window.document.getElementById("lead-reopen-open-btn").click();
    const dialog = await vi.waitFor(() => {
      const el = window.document.querySelector('[role="dialog"]');
      expect(el).not.toBeNull();
      return el;
    });
    const select = dialog.querySelector("select");
    const textarea = dialog.querySelector("textarea");
    select.value = "negotiation";
    textarea.value = "Klient odezwał się ponownie.";
    textarea.dispatchEvent(new window.Event("input", { bubbles: true }));
    const buttons = [...dialog.querySelectorAll("button")];
    const confirmBtn = buttons[buttons.length - 1];
    confirmBtn.click();

    await vi.waitFor(() => expect(window.ApiClient.post).toHaveBeenCalledWith(
      `/api/leads/${encodeURIComponent(lead.id)}/reopen`,
      expect.objectContaining({
        target_stage: "negotiation",
        reason: "Klient odezwał się ponownie.",
        confirmed: true,
      }),
    ));
    await vi.waitFor(() => expect(
      window.document.getElementById("modal-lead-status").textContent,
    ).toContain("Negocjacje"));
  });

  it("the confirm button stays disabled until a reason is entered", async () => {
    const { window } = createRealm({ lead: baseLead({ status: "lost", commission: null }) });
    await window.AdminLeads.init();
    await openDetail(window);

    window.document.getElementById("lead-reopen-open-btn").click();
    const dialog = await vi.waitFor(() => {
      const el = window.document.querySelector('[role="dialog"]');
      expect(el).not.toBeNull();
      return el;
    });
    const buttons = [...dialog.querySelectorAll("button")];
    const confirmBtn = buttons[buttons.length - 1];
    expect(confirmBtn.disabled).toBe(true);
  });
});
