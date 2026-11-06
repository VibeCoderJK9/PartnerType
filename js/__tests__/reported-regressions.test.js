/* ============================================================
   reported-regressions.test.js

   Automated reproduction of four reported browser-side problems: the
   "Aktywuj" button, leads pagination, the
   "Akceptuj" button and the eye/detail icon. (The other two reported
   items -- partner login and submitting a lead as a partner -- are
   server-side and live in api/tests/integration/test_reported_regressions.py.)

   Browser automation cannot always inject a trusted click, so a person
   reporting "it does not work" and a test showing "the handler runs" are
   two separate facts. This file establishes the second one: every case below dispatches a bubbling
   `new MouseEvent('click', { bubbles: true })` on the actual rendered
   button, never calls the handler function directly, so both the
   delegated listener and the DOM selector it depends on are exercised
   exactly as a real click would exercise them.

   Fixture note on the one-load-per-script rule (js/__tests__/helpers/
   load-script.js): that helper's shared ambient realm is not used
   anywhere in this file. js/__tests__/admin-leads.test.js already
   established the alternative -- a fresh happy-dom `Window` plus its own
   `vm.createContext`, given to `runClassicScript` below -- and this file
   reuses that pattern for both js/users.js and js/admin-leads.js, so each
   `it()` gets an entirely separate global realm and there is no
   cross-file (or cross-case) script-reload conflict to respect.

   Duplication note: admin-leads.test.js already proves detail/accept
   delegation and pagination with a plain `.click()` call (see its
   "delegates detail and accept through records held in memory" test and
   the page/search/sort test above it) -- and happy-dom's `.click()` is
   itself a bubbling MouseEvent dispatch, so that coverage already answers
   the same machine-side question. The three admin-leads cases below are
   not new coverage of new behaviour; they make the explicit
   `new MouseEvent(...)` construction visible in *this* file, tying the three
   reports to named test cases rather than leaving them implied by another
   file's incidental use of `.click()`.
   ============================================================ */
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
  const source = readFileSync(absolutePath, "utf8");
  const context = window.__vmContext || (window.__vmContext = vm.createContext(window));
  vm.runInContext(source, context, { filename: absolutePath });
  return context;
}

function invoke(window, source) {
  return vm.runInContext(source, window.__vmContext);
}

/* ────────────────────────────────────────────────────────────────────────
   Item: "Activate" did not work

   Every partner surface lives on pages/admin/partners.html, so the button
   this item is about is the Akceptuj control in js/admin-partners.js.
   ──────────────────────────────────────────────────────────────────────── */

function createUsersRealm(partners) {
  const window = new Window({ url: "http://localhost:8000/pages/admin/partners.html" });
  window.document.body.dataset.page = "admin-partners";
  window.document.body.innerHTML = `
    <div class="kpi-value" id="kpi-partners-active"></div>
    <div class="kpi-value" id="kpi-partners-leads"></div>
    <div class="kpi-value" id="kpi-partners-accepted"></div>
    <span id="partners-count-label"></span>
    <button id="add-partner-btn" type="button"></button>
    <table><tbody id="partners-tbody"></tbody></table>
    <div id="partners-loading-state" hidden></div>
    <div id="partners-empty-state" hidden></div>
    <div id="partners-error-state" hidden><button id="partners-retry-btn"></button></div>
  `;
  window.ApiClient = {
    get: vi.fn((requestPath) => {
      if (requestPath === "/api/partner-bonuses?scope=roster") return Promise.resolve({ partners });
      if (requestPath === "/api/tiers") return Promise.resolve({ tiers: [] });
      if (isLeadListUrl(requestPath)) return Promise.resolve(serveLeadList(requestPath, []));
      return Promise.resolve({});
    }),
    patch: vi.fn((url, body) => {
      const id = Number(url.replace("/api/users/", ""));
      const found = partners.find((u) => u.id === id);
      return Promise.resolve({ user: { ...found, ...body } });
    }),
  };
  window.App = appStub('pl');
  window.Ui = {
    safeNavigate: vi.fn().mockReturnValue(true),
    plPlural: (count, one, few, many) => (count === 1 ? one : many),
  };
  window.showToast = vi.fn();
  window.handleApiError = vi.fn();
  window.getInitials = vi.fn(() => "TP");
  window.getAvatarColorClass = vi.fn(() => "bg-nebula-indigo");
  window.formatCurrency = vi.fn((value) => `${value} PLN`);
  window.formatDate = vi.fn(() => "03 wrz 2026");
  window.openModal = vi.fn();
  window.closeModal = vi.fn();
  runClassicScript(window, "js/icons.js");
  runClassicScript(window, "js/admin-partners.js");
  return { window };
}

function pendingPartner() {
  return {
    id: 42,
    name: "Testowy Partner",
    email: "p@test.pl",
    company: "Testowa Sp. z o.o.",
    account_status: "pending",
    state: "self_registered",
    active: 0,
    tier_id: null,
    tier_name: null,
    total_percent: 0,
    created_at: "2026-09-03T10:00:00Z",
  };
}

describe('reported item: "Activate" did not work (now js/admin-partners.js)', () => {
  it("dispatching a bubbling click on the rendered Akceptuj button PATCHes the account to active exactly once", async () => {
    const { window } = createUsersRealm([pendingPartner()]);
    await window.AdminPartners.init({ id: 1, role: "admin" });

    const button = window.document.querySelector('[data-partner-action="accept"]');
    expect(button).not.toBeNull();

    button.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
    await vi.waitFor(() => expect(window.ApiClient.patch).toHaveBeenCalledTimes(1));

    expect(window.ApiClient.patch).toHaveBeenCalledWith("/api/users/42", {
      account_status: "active",
    });
  });
});

/* ────────────────────────────────────────────────────────────────────────
   Items: leads pagination, "Akceptuj" and the eye icon (js/admin-leads.js)
   ──────────────────────────────────────────────────────────────────────── */

// Mirrors the seeded pipeline (migrations/default_stages.json), same shape
// admin-leads.test.js's own STAGES fixture uses.
const STAGES = [
  { id: 1, key: "contact", name: "Nawiązanie kontaktu", sort_order: 0, kind: "progress", is_terminal: 0, color: "#94a3b8", active: 1 },
  { id: 2, key: "presentation", name: "Prezentacja", sort_order: 1, kind: "progress", is_terminal: 0, color: "#3b82f6", active: 1 },
  { id: 3, key: "offer", name: "Oferta", sort_order: 2, kind: "progress", is_terminal: 0, color: "#8b5cf6", active: 1 },
  { id: 4, key: "negotiation", name: "Negocjacje", sort_order: 3, kind: "progress", is_terminal: 0, color: "#f59e0b", active: 1 },
  { id: 5, key: "won", name: "Wygrany", sort_order: 4, kind: "win", is_terminal: 1, color: "#10b981", active: 1 },
  { id: 6, key: "lost", name: "Przegrany", sort_order: 5, kind: "lose", is_terminal: 1, color: "#ef4444", active: 1 },
];

function leadData(count = 6) {
  return Array.from({ length: count }, (_, index) => ({
    id: `lead-${index + 1}`,
    client_name: `Client ${index + 1}`,
    client_email: `client${index + 1}@example.com`,
    client_phone: "+48123456789",
    company: `Company ${index + 1}`,
    partner_id: 7,
    partner_name: "Partner Safe",
    sector: "IT",
    notes: "Notes",
    nip: "1234567890",
    website: "https://example.com",
    product: "Partner CRM",
    quantity: index + 1,
    queue_position: 1,
    queue_share: 100,
    commission_breakdown: { tier: 80, recurring: 20, cash: 0 },
    value: 2500 + index,
    commission: 100,
    commission_projected: 100,
    // Two axes (see js/utils.js): a freshly submitted
    // deal is awaiting a decision and is on no sales stage at all.
    registration_status: "pending",
    status: null,
    // Distinct per row on purpose: a decision taken from row 3 must carry
    // row 3's version, not row 1's and not zero.
    revision: 4 + index,
    created_at: `2026-08-${String(10 + index).padStart(2, "0")}T10:00:00Z`,
  }));
}

function createLeadRealm(leads = leadData(), stages = STAGES) {
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
  const firstLead = leads[0];
  const partners = [{ id: 7, name: firstLead.partner_name, company: firstLead.company }];
  window.ApiClient = {
    get: vi.fn((url) => {
      if (isLeadListUrl(url)) return Promise.resolve(serveLeadList(url, leads));
      if (url === "/api/partners") return Promise.resolve({ partners });
      if (url === "/api/lead-stages") return Promise.resolve({ stages });
      if (url === "/api/audit") return Promise.resolve({ logs: [] });
      const id = decodeURIComponent(url.replace("/api/leads/", ""));
      return Promise.resolve({ lead: leads.find((lead) => String(lead.id) === id) });
    }),
    patch: vi.fn((url, body) => {
      const id = decodeURIComponent(url.replace("/api/leads/", ""));
      return Promise.resolve({ lead: { ...leads.find((lead) => String(lead.id) === id), ...body } });
    }),
    // The producer's decision is its own endpoint.
    post: vi.fn((url, body) => {
      const match = /^\/api\/leads\/(.+)\/registration$/.exec(url);
      if (!match) return Promise.resolve({ comment: { id: 1 } });
      const id = decodeURIComponent(match[1]);
      const lead = leads.find((entry) => String(entry.id) === id);
      const decided = {
        accept: "accepted", reject: "rejected", request_info: "needs_info",
      }[body?.action];
      return Promise.resolve({
        lead: { ...lead, registration_status: decided, revision: Number(lead?.revision ?? 0) + 1 },
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

describe('reported item: leads pagination "nothing happens" (js/admin-leads.js)', () => {
  it("dispatching a bubbling click on page 2's button re-renders the row set and moves the active class", async () => {
    // Twenty-six leads against a page of twenty-five, so a second page exists
    // at all; the server sends one page at a time.
    const { window } = createLeadRealm(leadData(26));
    await window.AdminLeads.init({ id: 1, role: "admin" });
    expect(window.document.querySelectorAll("#leads-tbody tr")).toHaveLength(25);

    const page2 = window.document.querySelector('[data-page="2"]');
    expect(page2).not.toBeNull();
    page2.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));

    await vi.waitFor(() => {
      expect(window.document.querySelectorAll("#leads-tbody tr")).toHaveLength(1);
    });
    // Re-query: renderPagination() replaces every button on each call, so
    // `page2` above is now a detached node -- the live one must be fetched
    // again to see the class the re-render actually applied.
    expect(window.document.querySelector('[data-page="2"]').className).toContain("active");
  });
});

describe('reported item: "Accept" does nothing (js/admin-leads.js)', () => {
  it("dispatching a bubbling click on the Akceptuj button issues exactly one accept request for that lead's id", async () => {
    const { window, leads } = createLeadRealm();
    await window.AdminLeads.init({ id: 1, role: "admin" });

    const acceptBtn = window.document.querySelector('[data-action="accept"]');
    expect(acceptBtn).not.toBeNull();
    acceptBtn.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));

    await vi.waitFor(() => expect(window.ApiClient.post).toHaveBeenCalledTimes(1));
    expect(window.ApiClient.post).toHaveBeenCalledWith(
      `/api/leads/${encodeURIComponent(leads[0].id)}/registration`,
      expect.objectContaining({ action: "accept" }),
    );
  });
});

describe('reported item: the eye icon does nothing (js/admin-leads.js)', () => {
  it("dispatching a bubbling click on the eye/detail button opens the lead detail view for that lead's id", async () => {
    const { window, leads } = createLeadRealm();
    await window.AdminLeads.init({ id: 1, role: "admin" });

    const detailBtn = window.document.querySelector('[data-action="detail"]');
    expect(detailBtn).not.toBeNull();
    detailBtn.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));

    await vi.waitFor(() => expect(window.openModal).toHaveBeenCalledWith("lead-modal"));
    expect(window.ApiClient.get).toHaveBeenCalledWith(
      `/api/leads/${encodeURIComponent(leads[0].id)}`,
    );
    expect(window.document.getElementById("modal-lead-id").textContent).toBe(String(leads[0].id));
  });
});

/*
 * The tests above dispatch a lone `click`, and they passed even while the
 * buttons were genuinely dead for a real mouse — which is exactly how this
 * shipped. A physical press sends pointerdown → mousedown → mouseup → click,
 * and a document-level `mousedown` listener belonging to the filter dropdowns
 * re-ran the table filter on EVERY press anywhere on the page. That re-rendered
 * the rows, so the button being pressed was destroyed between mousedown and
 * mouseup and the browser never completed the click.
 *
 * These tests press the way a person does. Their load-bearing assertion is that
 * the button is still connected to the document after mousedown — everything
 * else follows from that.
 */
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

describe("a real mouse press, not just a click event", () => {
  // Honest limitation: this one passes with or without the fix. The partners
  // fixture renders no filter dropdowns, so nothing attaches the
  // document-level mousedown listener and the bug has nothing to reproduce
  // here. The two leads tests below DO fail without the fix, and they cover
  // the identical listener shape. Left in place because it still pins the
  // button's own behaviour under a full press.
  it("leaves the Akceptuj button alive through mousedown and still activates the account", async () => {
    const { window } = createUsersRealm([pendingPartner()]);
    await window.AdminPartners.init({ id: 1, role: "admin" });

    const button = window.document.querySelector('[data-partner-action="accept"]');
    const survivedMousedown = pressLikeAMouse(window, button);

    expect(survivedMousedown).toBe(true);
    await vi.waitFor(() => expect(window.ApiClient.patch).toHaveBeenCalledTimes(1));
  });

  it("leaves the Akceptuj button alive through mousedown and still accepts the lead", async () => {
    const { window } = createLeadRealm();
    await window.AdminLeads.init({ id: 1, role: "admin" });

    const acceptBtn = window.document.querySelector(
      '#leads-tbody [data-action="accept"]',
    );
    expect(acceptBtn).not.toBeNull();
    const survivedMousedown = pressLikeAMouse(window, acceptBtn);

    expect(survivedMousedown).toBe(true);
    await vi.waitFor(() => expect(window.ApiClient.post).toHaveBeenCalledTimes(1));
  });

  it("leaves the detail icon alive through mousedown", async () => {
    const { window } = createLeadRealm();
    await window.AdminLeads.init({ id: 1, role: "admin" });

    const detailBtn = window.document.querySelector(
      '#leads-tbody [data-action="detail"]',
    );
    expect(detailBtn).not.toBeNull();

    expect(pressLikeAMouse(window, detailBtn)).toBe(true);
  });
});

/*
   A lead row offers three decisions in place -- reject, accept, ask for
   information -- without leaving the screen.

   Two things must hold. All three actions work from the row itself, not only
   inside the detail modal; and the accept path builds its optimistic-
   concurrency envelope from the row's own revision, not from the module-level
   `selectedLead`, which is only populated once that modal has been opened.
   Otherwise deciding from a row would send `expected_revision: 0` and an
   operation_id of `accept-undefined-0`.
*/
describe("deciding on a lead from the table row", () => {
  function bodyOfLastPost(window) {
    const calls = window.ApiClient.post.mock.calls;
    return calls[calls.length - 1][1];
  }

  it("accepts with the clicked row's revision, not a stale zero", async () => {
    const { window } = createLeadRealm();
    await window.AdminLeads.init({ id: 1, role: "admin" });

    const rows = [...window.document.querySelectorAll("#leads-tbody tr[data-record-key]")];
    const thirdRowAccept = rows[2].querySelector('[data-action="accept"]');
    pressLikeAMouse(window, thirdRowAccept);

    await vi.waitFor(() => expect(window.ApiClient.post).toHaveBeenCalledTimes(1));
    const body = bodyOfLastPost(window);
    expect(body.action).toBe("accept");
    expect(body.expected_revision).toBe(6);
    expect(body.operation_id).toBe("accept-lead-3-6");
  });

  it("rejects in place: the reason is typed under the row, never on another screen", async () => {
    const { window } = createLeadRealm();
    await window.AdminLeads.init({ id: 1, role: "admin" });

    const firstRow = window.document.querySelector("#leads-tbody tr[data-record-key]");
    pressLikeAMouse(window, firstRow.querySelector('[data-action="reject"]'));

    const form = window.document.querySelector(".row-inline-form");
    expect(form).not.toBeNull();
    expect(form.previousElementSibling).toBe(firstRow);
    expect(window.ApiClient.post).not.toHaveBeenCalled();

    // An empty reason is refused rather than silently sent: the partner has to
    // be told what to fix.
    const confirm = [...form.querySelectorAll("button")]
      .find((button) => button.textContent === "Odrzuć leada");
    confirm.click();
    expect(window.ApiClient.post).not.toHaveBeenCalled();
    expect(form.querySelector(".row-inline-form-error").hidden).toBe(false);

    form.querySelector("textarea").value = "Klient jest już obsługiwany bezpośrednio.";
    confirm.click();

    await vi.waitFor(() => expect(window.ApiClient.post).toHaveBeenCalledTimes(1));
    const body = bodyOfLastPost(window);
    expect(body.action).toBe("reject");
    expect(body.message).toBe("Klient jest już obsługiwany bezpośrednio.");
    expect(body.expected_revision).toBe(4);
  });

  it("closes an open form when another row's form is opened, so a reason cannot land on the wrong lead", async () => {
    const { window } = createLeadRealm();
    await window.AdminLeads.init({ id: 1, role: "admin" });

    const rows = [...window.document.querySelectorAll("#leads-tbody tr[data-record-key]")];
    rows[0].querySelector('[data-action="reject"]').click();
    rows[1].querySelector('[data-action="request_info"]').click();

    const forms = window.document.querySelectorAll(".row-inline-form");
    expect(forms.length).toBe(1);
    expect(forms[0].previousElementSibling).toBe(rows[1]);
  });
});

/*
   The lead table shows where each lead sits in the funnel.

   A stage NAME alone does not answer the question: "Offer" says nothing about whether
   that is halfway or nearly closed. The stage now has its own column carrying
   the position and the total.
*/
describe("a lead's place in the funnel", () => {
  // Two leads, not the default six, and addressed by id rather than by row
  // position: the table sorts "needs attention" first and pages at five, so a
  // handled lead among six sorts last and lands on page two.
  async function realmWithStage(status) {
    const leads = leadData(2);
    leads[0].registration_status = "accepted";
    leads[0].status = status;
    const { window } = createLeadRealm(leads);
    await window.AdminLeads.init({ id: 1, role: "admin" });
    return window;
  }

  function funnelCellOf(window, leadId) {
    return window.document
      .querySelector(`#leads-tbody tr[data-id="${leadId}"]`)
      .querySelector(".funnel-cell");
  }

  it("names the position and the total, not just the stage", async () => {
    const window = await realmWithStage("offer");

    const cell = funnelCellOf(window, "lead-1");

    expect(cell.textContent).toContain("Oferta");
    // STAGES holds four progress stages plus the two terminals.
    expect(cell.textContent).toContain("Etap 3 z 4");
  });

  it("fills one bar segment per stage reached, and no more", async () => {
    const window = await realmWithStage("offer");

    const segments = [...funnelCellOf(window, "lead-1").querySelectorAll(".funnel-step")];
    const filled = segments.filter((seg) => seg.style.background);

    expect(segments).toHaveLength(4);
    expect(filled).toHaveLength(3);
  });

  it("says a lead awaiting the decision is outside the funnel, not at step zero", async () => {
    const { window } = createLeadRealm(leadData(2));
    await window.AdminLeads.init({ id: 1, role: "admin" });

    // leadData() ships every lead as pending with status null.
    expect(funnelCellOf(window, "lead-1").textContent).toContain("Przed lejkiem");
  });

  it("gives a closed deal no position, because a win is not step five of four", async () => {
    const window = await realmWithStage("won");

    const cell = funnelCellOf(window, "lead-1");

    expect(cell.textContent).toContain("Zamknięty");
    expect(cell.textContent).not.toContain("Etap");
    expect(cell.querySelectorAll(".funnel-step")).toHaveLength(0);
  });
});
