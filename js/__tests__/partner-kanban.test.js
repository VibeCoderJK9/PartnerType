/*
 * Partner kanban board -- own accepted leads only, in the producer's
 * configured stage order; forward/back stage moves via drag or an
 * equally capable select+button path; terminal entry requires an
 * explicit confirmation the user can cancel without sending anything.
 *
 * Loads js/utils.js and js/partner.js through the classic-script loader,
 * exactly like js/__tests__/partner-leads.test.js does (see that file's own
 * comment for why loadScript is one-per-script-per-test-file).
 */
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { loadScript } from "./helpers/load-script.js";
import { appStub } from "./helpers/copy.js";

// partner.js reads its copy from the dictionary at load time
globalThis.App = appStub("pl");

loadScript("js/icons.js");
loadScript("js/utils.js");
loadScript("js/partner.js");

const PAYLOAD = '<img src=x onerror="window.__partnerKanbanPwned=true">';

// Mirrors the seeded pipeline exactly (migrations/default_stages.json), matching
// js/__tests__/partner-leads.test.js and js/__tests__/admin-leads.test.js.
const STAGES = [
  { id: 1, key: "contact", name: "Nawiązanie kontaktu", sort_order: 0, kind: "progress", is_terminal: 0, color: "#94a3b8", active: 1 },
  { id: 2, key: "presentation", name: "Prezentacja", sort_order: 1, kind: "progress", is_terminal: 0, color: "#3b82f6", active: 1 },
  { id: 3, key: "offer", name: "Oferta", sort_order: 2, kind: "progress", is_terminal: 0, color: "#8b5cf6", active: 1 },
  { id: 4, key: "negotiation", name: "Negocjacje", sort_order: 3, kind: "progress", is_terminal: 0, color: "#f59e0b", active: 1 },
  { id: 5, key: "won", name: "Wygrany", sort_order: 4, kind: "win", is_terminal: 1, color: "#10b981", active: 1 },
  { id: 6, key: "lost", name: "Przegrany", sort_order: 5, kind: "lose", is_terminal: 1, color: "#ef4444", active: 1 },
];

function lead(overrides = {}) {
  return {
    id: "LD-001",
    client_name: "Jan Kowalski",
    company: "Acme Sp. z o.o.",
    value: 10000,
    registration_status: "accepted",
    status: "contact",
    commission: null,
    commission_projected: 700,
    revision: 3,
    created_at: "2026-08-20T10:00:00",
    updated_at: "2026-08-20T10:00:00",
    ...overrides,
  };
}

function mockPartnerApi({
  leads = [],
  stages = STAGES,
  queueShares = "100",
  settings,
} = {}) {
  return vi.fn((path) => {
    if (path === "/api/leads") return Promise.resolve({ leads });
    if (path === "/api/settings") return Promise.resolve({ settings: settings || { lead_queue_shares: queueShares } });
    if (path === "/api/lead-stages") return Promise.resolve({ stages });
    throw new Error(`unexpected GET ${path}`);
  });
}

function buildFixture() {
  document.body.innerHTML = `
    <div class="view-toggle">
      <button type="button" id="view-registration-btn" aria-pressed="false">Rejestracja</button>
      <button type="button" id="view-sales-btn" aria-pressed="true">Sprzedaż</button>
    </div>

    <div id="leads-registration-view" hidden>
      <input type="text" id="leads-search">
      <select id="filter-status"><option value="">Wszystkie statusy</option></select>
      <div id="leads-loading-state"></div>
      <div id="leads-empty-state" hidden></div>
      <div id="leads-error-state" hidden><button id="leads-retry-btn"></button></div>
      <div id="leads-table-wrapper" hidden><table><tbody id="leads-tbody"></tbody></table></div>
    </div>

    <div id="leads-sales-view">
      <div id="kanban-loading-state"></div>
      <div id="kanban-empty-state" hidden></div>
      <div id="kanban-no-stages-state" hidden></div>
      <div id="kanban-error-state" hidden><button id="kanban-retry-btn"></button></div>
      <p id="kanban-settled-note" hidden><span id="kanban-settled-text"></span></p>
      <div id="kanban-board-wrapper" hidden>
        <div class="kanban-board" id="kanban-board"></div>
        <div class="kanban-unknown" id="kanban-unknown-section" hidden>
          <div id="kanban-unknown-cards"></div>
        </div>
      </div>
    </div>
  `;
}

beforeEach(() => {
  globalThis.App = { ...appStub("pl"), applyTranslations: vi.fn() };
  globalThis.showToast = vi.fn();
  globalThis.handleApiError = vi.fn();
  globalThis.formatCurrency = vi.fn(
    (value) => new Intl.NumberFormat("pl-PL", { style: "currency", currency: "PLN", maximumFractionDigits: 0 }).format(value ?? 0),
  );
  globalThis.formatDate = vi.fn((value) => (value ? String(value).slice(0, 10) : "—"));
  globalThis.getInitials = vi.fn(() => "JK");
  buildFixture();
});

afterEach(() => {
  vi.restoreAllMocks();
  delete window.PartnerLeadDetails;
});

// ─── DRAG/DROP TEST HELPERS ────────────────────────────────────────────────
// happy-dom's Event does not implement a full DataTransfer, so tests attach
// a minimal object literal with the get/setData surface the production code
// actually calls (js/partner.js reads/writes it defensively with try/catch
// specifically so a partial implementation like this one degrades safely).
function fakeDataTransfer() {
  const store = new Map();
  return {
    effectAllowed: null,
    dropEffect: null,
    setData: (type, value) => store.set(type, value),
    getData: (type) => store.get(type) || "",
  };
}

// Mirrors real HTML5 DnD, not the shape of the DOM API: a browser's
// 'dragstart' always targets the draggable node itself (the card), never
// whatever the pointer actually went down on, so the production code reads
// "did this drag start on the handle" off a prior 'mousedown' instead. A
// real mouse gesture always fires mousedown before dragstart, so the helper
// does too -- skipping it (see the rejection test below) is how a drag
// started elsewhere on the card is told apart from one started on the
// handle.
function dragAndDrop(handle, columnBody) {
  const dt = fakeDataTransfer();
  const card = handle.closest(".kanban-card");

  const mousedown = new Event("mousedown", { bubbles: true, cancelable: true });
  Object.defineProperty(mousedown, "target", { value: handle, configurable: true });
  card.dispatchEvent(mousedown);

  const dragstart = new Event("dragstart", { bubbles: true, cancelable: true });
  dragstart.dataTransfer = dt;
  Object.defineProperty(dragstart, "target", { value: card, configurable: true });
  card.dispatchEvent(dragstart);

  const drop = new Event("drop", { bubbles: true, cancelable: true });
  drop.dataTransfer = dt;
  columnBody.dispatchEvent(drop);

  card.dispatchEvent(new Event("dragend", { bubbles: true }));
}

// ─── RENDERING: OWN ACCEPTED LEADS ONLY, CONFIGURED ORDER ──────────────────

it("renders only the caller's own accepted leads, one column per active configured stage in order", async () => {
  globalThis.ApiClient = {
    get: mockPartnerApi({
      leads: [
        lead({ id: "LD-001", status: "contact" }),
        lead({ id: "LD-002", status: "negotiation" }),
      ],
    }),
  };

  await initPartnerLeads({ id: 1, role: "partner" });

  const columns = [...document.querySelectorAll(".kanban-column")];
  expect(columns.map((c) => c.dataset.stageKey)).toEqual(STAGES.map((s) => s.key));
  const contactColumn = document.querySelector('.kanban-column[data-stage-key="contact"]');
  const negotiationColumn = document.querySelector('.kanban-column[data-stage-key="negotiation"]');
  expect(contactColumn.textContent).toContain("LD-001");
  expect(negotiationColumn.textContent).toContain("LD-002");
  expect(document.getElementById("kanban-board-wrapper").hidden).toBe(false);
});

it("shows a per-column count derived from the actual leads on it", async () => {
  globalThis.ApiClient = {
    get: mockPartnerApi({
      leads: [lead({ id: "LD-001", status: "contact" }), lead({ id: "LD-002", status: "contact" })],
    }),
  };

  await initPartnerLeads({ id: 1, role: "partner" });

  const contactColumn = document.querySelector('.kanban-column[data-stage-key="contact"]');
  expect(contactColumn.querySelector(".kanban-column-count").textContent).toBe("2");
  const offerColumn = document.querySelector('.kanban-column[data-stage-key="offer"]');
  expect(offerColumn.querySelector(".kanban-column-count").textContent).toBe("0");
  expect(offerColumn.textContent).toContain("Brak leadów w tym etapie");
});

it("shows the empty-board copy when there are no accepted leads at all", async () => {
  globalThis.ApiClient = { get: mockPartnerApi({ leads: [] }) };

  await initPartnerLeads({ id: 1, role: "partner" });

  expect(document.getElementById("kanban-empty-state").hidden).toBe(false);
  expect(document.getElementById("kanban-board-wrapper").hidden).toBe(true);
});

it("shows a blocking message instead of a board when no active sales stage is configured", async () => {
  globalThis.ApiClient = { get: mockPartnerApi({ leads: [lead()], stages: [] }) };

  await initPartnerLeads({ id: 1, role: "partner" });

  expect(document.getElementById("kanban-no-stages-state").hidden).toBe(false);
  expect(document.getElementById("kanban-board-wrapper").hidden).toBe(true);
});

it("renders an accepted lead on a stage the producer removed or renamed away from in a separate 'Stage not available' section, not a fabricated column", async () => {
  globalThis.ApiClient = {
    get: mockPartnerApi({ leads: [lead({ id: "LD-001", status: "removed_stage" })] }),
  };

  await initPartnerLeads({ id: 1, role: "partner" });

  expect(document.querySelectorAll(".kanban-column").length).toBe(STAGES.length);
  expect(document.getElementById("kanban-unknown-section").hidden).toBe(false);
  expect(document.getElementById("kanban-unknown-cards").textContent).toContain("LD-001");
});

it("renders malicious company and client names inertly on the board", async () => {
  window.__partnerKanbanPwned = false;
  globalThis.ApiClient = {
    get: mockPartnerApi({ leads: [lead({ company: PAYLOAD, client_name: PAYLOAD })] }),
  };

  await initPartnerLeads({ id: 1, role: "partner" });

  const board = document.getElementById("kanban-board");
  expect(board.textContent).toContain(PAYLOAD);
  expect(board.querySelector("img, script")).toBeNull();
  expect(window.__partnerKanbanPwned).toBe(false);
});

// ─── CARD CLICK OPENS DETAILS ──────────────────────────────────────────────

it("opens the real detail controller on a plain card click, not on the handle/select/button", async () => {
  globalThis.ApiClient = { get: mockPartnerApi({ leads: [lead({ status: "contact" })] }) };
  window.PartnerLeadDetails = { open: vi.fn() };
  await initPartnerLeads({ id: 1, role: "partner" });

  const card = document.querySelector('.kanban-card[data-lead-id="LD-001"]');
  card.querySelector(".kanban-card-name").click();
  expect(window.PartnerLeadDetails.open).toHaveBeenCalledWith("LD-001", expect.anything());

  window.PartnerLeadDetails.open.mockClear();
  card.querySelector(".kanban-card-handle").click();
  card.querySelector(".kanban-card-stage-select").click();
  card.querySelector(".kanban-card-footer button").click();
  expect(window.PartnerLeadDetails.open).not.toHaveBeenCalled();
});

it("suppresses the click that follows a drag-drop, but not the next real click", async () => {
  const patch = vi.fn(() => Promise.resolve({ lead: lead({ status: "presentation", revision: 4 }) }));
  globalThis.ApiClient = { get: mockPartnerApi({ leads: [lead({ status: "contact" })] }), patch };
  window.PartnerLeadDetails = { open: vi.fn() };
  await initPartnerLeads({ id: 1, role: "partner" });

  const card = document.querySelector('.kanban-card[data-lead-id="LD-001"]');
  const handle = card.querySelector(".kanban-card-handle");
  const targetBody = document.querySelector('.kanban-column-body[data-stage-key="presentation"]');
  dragAndDrop(handle, targetBody);
  // A real browser may fire one spurious click on the SAME element right
  // after drop, synchronously, before the async save resolves -- that one
  // must be swallowed.
  card.click();
  expect(window.PartnerLeadDetails.open).not.toHaveBeenCalled();

  await vi.waitFor(() => expect(patch).toHaveBeenCalled());
  // The board re-rendered under the same lead id with a fresh element (and
  // therefore a fresh, unset suppress flag): its own next click must work.
  const movedCard = document.querySelector('.kanban-card[data-lead-id="LD-001"]');
  movedCard.click();
  expect(window.PartnerLeadDetails.open).toHaveBeenCalledWith("LD-001", expect.anything());
});

// A real browser's 'dragstart' targets the draggable card itself, never the
// handle the pointer actually pressed down on -- so "handle-only drag" has to
// be decided from a prior 'mousedown', not from dragstart's own event.target
// (see bindKanbanCardDrag in js/partner.js).
it("a drag started elsewhere on the card body (not the handle) never moves the card", async () => {
  const patch = vi.fn(() => Promise.resolve({ lead: lead({ status: "presentation", revision: 4 }) }));
  globalThis.ApiClient = { get: mockPartnerApi({ leads: [lead({ status: "contact", revision: 3 })] }), patch };
  await initPartnerLeads({ id: 1, role: "partner" });

  const card = document.querySelector('.kanban-card[data-lead-id="LD-001"]');
  const targetBody = document.querySelector('.kanban-column-body[data-stage-key="presentation"]');
  const meta = card.querySelector(".kanban-card-meta");

  const dt = fakeDataTransfer();
  const mousedown = new Event("mousedown", { bubbles: true, cancelable: true });
  Object.defineProperty(mousedown, "target", { value: meta, configurable: true });
  card.dispatchEvent(mousedown);

  const dragstart = new Event("dragstart", { bubbles: true, cancelable: true });
  dragstart.dataTransfer = dt;
  Object.defineProperty(dragstart, "target", { value: card, configurable: true });
  const notCancelled = card.dispatchEvent(dragstart);
  expect(notCancelled).toBe(false); // dispatchEvent returns false once preventDefault() was called

  const drop = new Event("drop", { bubbles: true, cancelable: true });
  drop.dataTransfer = dt;
  targetBody.dispatchEvent(drop);
  card.dispatchEvent(new Event("dragend", { bubbles: true }));

  expect(patch).not.toHaveBeenCalled();
  expect(document.querySelector('.kanban-column-body[data-stage-key="contact"]').textContent).toContain("LD-001");
});

// ─── FORWARD/BACK MOVES: DRAG AND SELECT SAVE THE SAME STAGE ───────────────

it("drags a card to a nonterminal column and PATCHes the same destination the column represents", async () => {
  const patch = vi.fn(() => Promise.resolve({ lead: lead({ status: "presentation", revision: 4 }) }));
  globalThis.ApiClient = { get: mockPartnerApi({ leads: [lead({ status: "contact", revision: 3 })] }), patch };
  await initPartnerLeads({ id: 1, role: "partner" });

  const handle = document.querySelector('.kanban-card[data-lead-id="LD-001"] .kanban-card-handle');
  const targetBody = document.querySelector('.kanban-column-body[data-stage-key="presentation"]');
  dragAndDrop(handle, targetBody);
  await vi.waitFor(() => expect(patch).toHaveBeenCalled());

  const [url, body] = patch.mock.calls[0];
  expect(url).toBe("/api/leads/LD-001/stage");
  expect(body).toMatchObject({ target_stage: "presentation", expected_revision: 3, confirmed_terminal: false });
  expect(typeof body.operation_id).toBe("string");
  expect(body.operation_id.length).toBeGreaterThan(0);
});

it("moves via the select + Change stage button, saving the exact same endpoint and payload shape as drag", async () => {
  const patch = vi.fn(() => Promise.resolve({ lead: lead({ status: "offer", revision: 4 }) }));
  globalThis.ApiClient = { get: mockPartnerApi({ leads: [lead({ status: "contact", revision: 3 })] }), patch };
  await initPartnerLeads({ id: 1, role: "partner" });

  const card = document.querySelector('.kanban-card[data-lead-id="LD-001"]');
  const select = card.querySelector(".kanban-card-stage-select");
  select.value = "offer";
  card.querySelector(".kanban-card-footer button").click();
  await vi.waitFor(() => expect(patch).toHaveBeenCalled());

  const [url, body] = patch.mock.calls[0];
  expect(url).toBe("/api/leads/LD-001/stage");
  expect(body).toMatchObject({ target_stage: "offer", expected_revision: 3 });
});

// One save per lead at a time: mutation controls disabled, aria-busy=true,
// "Zapisywanie" text -- proven with a controllable promise
// so the in-flight moment itself is inspectable, not just the outcome.
it("disables the card's controls and marks aria-busy while a move is in flight", async () => {
  let resolvePatch;
  const patch = vi.fn(() => new Promise((resolve) => { resolvePatch = resolve; }));
  globalThis.ApiClient = { get: mockPartnerApi({ leads: [lead({ status: "contact", revision: 3 })] }), patch };
  await initPartnerLeads({ id: 1, role: "partner" });

  const card = document.querySelector('.kanban-card[data-lead-id="LD-001"]');
  const select = card.querySelector(".kanban-card-stage-select");
  const button = card.querySelector(".kanban-card-footer button");
  select.value = "offer";
  button.click();

  await vi.waitFor(() => expect(patch).toHaveBeenCalled());
  expect(card.getAttribute("aria-busy")).toBe("true");
  expect(select.disabled).toBe(true);
  expect(button.disabled).toBe(true);
  expect(button.textContent).toMatch(/Zapisywanie/);

  resolvePatch({ lead: lead({ status: "offer", revision: 4 }) });
  await vi.waitFor(() => {
    const freshCard = document.querySelector('.kanban-column-body[data-stage-key="offer"] .kanban-card');
    expect(freshCard).not.toBeNull();
  });
});

it("moves a card backward to an earlier nonterminal stage with no extra confirmation", async () => {
  const patch = vi.fn(() => Promise.resolve({ lead: lead({ status: "contact", revision: 6 }) }));
  globalThis.ApiClient = { get: mockPartnerApi({ leads: [lead({ status: "negotiation", revision: 5 })] }), patch };
  await initPartnerLeads({ id: 1, role: "partner" });

  const card = document.querySelector('.kanban-card[data-lead-id="LD-001"]');
  card.querySelector(".kanban-card-stage-select").value = "contact";
  card.querySelector(".kanban-card-footer button").click();
  await vi.waitFor(() => expect(patch).toHaveBeenCalled());

  expect(patch.mock.calls[0][1]).toMatchObject({ target_stage: "contact", confirmed_terminal: false });
});

it("the current stage's own option is present but disabled, so choosing it is a no-op that never calls the API", async () => {
  globalThis.ApiClient = { get: mockPartnerApi({ leads: [lead({ status: "contact" })] }) };
  const patch = vi.fn();
  globalThis.ApiClient.patch = patch;
  await initPartnerLeads({ id: 1, role: "partner" });

  const select = document.querySelector('.kanban-card[data-lead-id="LD-001"] .kanban-card-stage-select');
  const currentOption = [...select.options].find((o) => o.value === "contact");
  expect(currentOption.disabled).toBe(true);
  expect(select.value).toBe("contact");
  document.querySelector(".kanban-card-footer button").click();
  expect(patch).not.toHaveBeenCalled();
});

it("survives a reload: the moved lead reappears in its new column after a fresh fetch", async () => {
  const patch = vi.fn(() => Promise.resolve({ lead: lead({ status: "presentation", revision: 4 }) }));
  const get = mockPartnerApi({ leads: [lead({ status: "contact", revision: 3 })] });
  globalThis.ApiClient = { get, patch };
  await initPartnerLeads({ id: 1, role: "partner" });

  const handle = document.querySelector('.kanban-card[data-lead-id="LD-001"] .kanban-card-handle');
  dragAndDrop(handle, document.querySelector('.kanban-column-body[data-stage-key="presentation"]'));
  await vi.waitFor(() => expect(patch).toHaveBeenCalled());

  // "Reload": re-run the page's own init with the server's now-current state.
  globalThis.ApiClient.get = mockPartnerApi({ leads: [lead({ status: "presentation", revision: 4 })] });
  await initPartnerLeads({ id: 1, role: "partner" });

  expect(document.querySelector('.kanban-column-body[data-stage-key="presentation"]').textContent).toContain("LD-001");
  expect(document.querySelector('.kanban-column-body[data-stage-key="contact"]').textContent).not.toContain("LD-001");
});

// ─── TERMINAL CONFIRMATION: A REAL MODAL, CANCEL SENDS NOTHING ─────────────

it("dropping onto a terminal column opens a confirmation modal and sends nothing until confirmed", async () => {
  const patch = vi.fn(() => Promise.resolve({ lead: lead({ status: "won", revision: 4, commission: 700 }) }));
  globalThis.ApiClient = { get: mockPartnerApi({ leads: [lead({ status: "negotiation", revision: 3 })] }), patch };
  await initPartnerLeads({ id: 1, role: "partner" });

  const handle = document.querySelector('.kanban-card[data-lead-id="LD-001"] .kanban-card-handle');
  dragAndDrop(handle, document.querySelector('.kanban-column-body[data-stage-key="won"]'));

  const dialog = await vi.waitFor(() => {
    const el = document.querySelector('[role="dialog"]');
    expect(el).not.toBeNull();
    return el;
  });
  expect(dialog.textContent).toContain("Acme Sp. z o.o.");
  expect(patch).not.toHaveBeenCalled();

  dialog.querySelector(".btn-primary").click();
  await vi.waitFor(() => expect(patch).toHaveBeenCalled());
  expect(patch.mock.calls[0][1]).toMatchObject({ target_stage: "won", confirmed_terminal: true });
});

it("cancelling the terminal confirmation (Pozostaw obecny etap) sends no mutation and leaves the card in place", async () => {
  const patch = vi.fn();
  globalThis.ApiClient = { get: mockPartnerApi({ leads: [lead({ status: "negotiation" })] }), patch };
  await initPartnerLeads({ id: 1, role: "partner" });

  const handle = document.querySelector('.kanban-card[data-lead-id="LD-001"] .kanban-card-handle');
  dragAndDrop(handle, document.querySelector('.kanban-column-body[data-stage-key="lost"]'));

  const dialog = await vi.waitFor(() => {
    const el = document.querySelector('[role="dialog"]');
    expect(el).not.toBeNull();
    return el;
  });
  dialog.querySelector(".btn-ghost").click();

  expect(patch).not.toHaveBeenCalled();
  expect(document.querySelector('[role="dialog"]')).toBeNull();
  expect(document.querySelector('.kanban-column-body[data-stage-key="negotiation"]').textContent).toContain("LD-001");
});

it("Escape on the terminal confirmation also sends no mutation", async () => {
  const patch = vi.fn();
  globalThis.ApiClient = { get: mockPartnerApi({ leads: [lead({ status: "negotiation" })] }), patch };
  await initPartnerLeads({ id: 1, role: "partner" });

  const select = document.querySelector('.kanban-card[data-lead-id="LD-001"] .kanban-card-stage-select');
  select.value = "won";
  document.querySelector(".kanban-card-footer button").click();

  const dialog = await vi.waitFor(() => {
    const el = document.querySelector('[role="dialog"]');
    expect(el).not.toBeNull();
    return el;
  });
  dialog.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));

  expect(patch).not.toHaveBeenCalled();
  expect(document.querySelector('[role="dialog"]')).toBeNull();
  // Regression: the select must not go on lying about
  // the card's real stage after the mutation it was about to send is
  // cancelled -- it goes back to what the card actually is now. The reset
  // itself runs after the cancel Promise resolves (a microtask queued by
  // the dispatched event, not synchronous with it), so this waits for it.
  await vi.waitFor(() => expect(select.value).toBe("negotiation"));
});

// Regression coverage for the other two dismissal
// paths: the select must reset to the lead's real stage no matter HOW the
// terminal confirmation was dismissed, not only via Escape.

it("cancelling the terminal confirmation via 'Pozostaw obecny etap' resets the stage select to the lead's real stage", async () => {
  const patch = vi.fn();
  globalThis.ApiClient = { get: mockPartnerApi({ leads: [lead({ status: "negotiation" })] }), patch };
  await initPartnerLeads({ id: 1, role: "partner" });

  const select = document.querySelector('.kanban-card[data-lead-id="LD-001"] .kanban-card-stage-select');
  select.value = "won";
  document.querySelector(".kanban-card-footer button").click();

  const dialog = await vi.waitFor(() => {
    const el = document.querySelector('[role="dialog"]');
    expect(el).not.toBeNull();
    return el;
  });
  dialog.querySelector(".btn-ghost").click();

  expect(patch).not.toHaveBeenCalled();
  expect(document.querySelector('[role="dialog"]')).toBeNull();
  await vi.waitFor(() => expect(select.value).toBe("negotiation"));
  expect(document.querySelector('.kanban-column-body[data-stage-key="negotiation"]').textContent).toContain("LD-001");
});

it("dismissing the terminal confirmation via a backdrop click resets the stage select to the lead's real stage", async () => {
  const patch = vi.fn();
  globalThis.ApiClient = { get: mockPartnerApi({ leads: [lead({ status: "negotiation" })] }), patch };
  await initPartnerLeads({ id: 1, role: "partner" });

  const select = document.querySelector('.kanban-card[data-lead-id="LD-001"] .kanban-card-stage-select');
  select.value = "lost";
  document.querySelector(".kanban-card-footer button").click();

  const overlay = await vi.waitFor(() => {
    const el = document.querySelector(".modal-overlay");
    expect(el).not.toBeNull();
    return el;
  });
  // A click that lands on the backdrop itself (not the dialog inside it) --
  // js/partner.js's own listener checks event.target === overlay.
  overlay.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));

  expect(patch).not.toHaveBeenCalled();
  expect(document.querySelector('[role="dialog"]')).toBeNull();
  await vi.waitFor(() => expect(select.value).toBe("negotiation"));
});

it("once terminal, the card offers no handle, select or Change stage button -- read-only note only", async () => {
  globalThis.ApiClient = { get: mockPartnerApi({ leads: [lead({ status: "won" })] }) };
  await initPartnerLeads({ id: 1, role: "partner" });

  const card = document.querySelector('.kanban-card[data-lead-id="LD-001"]');
  expect(card.querySelector(".kanban-card-handle")).toBeNull();
  expect(card.querySelector(".kanban-card-stage-select")).toBeNull();
  expect(card.querySelector(".kanban-card-footer button")).toBeNull();
  expect(card.querySelector(".kanban-card-readonly-note").textContent).toContain("tylko podgląd");
  expect(card.draggable).toBe(false);
});

// ─── ERRORS: A CONFLICT DOES NOT CRASH OR SILENTLY SUCCEED ─────────────────

it("shows an error toast and leaves the lead where it was on a 409 conflict, without throwing", async () => {
  const conflict = Object.assign(new Error("stale"), { status: 409, code: "stale_revision" });
  const patch = vi.fn(() => Promise.reject(conflict));
  globalThis.ApiClient = { get: mockPartnerApi({ leads: [lead({ status: "contact", revision: 3 })] }), patch };
  await initPartnerLeads({ id: 1, role: "partner" });

  const card = document.querySelector('.kanban-card[data-lead-id="LD-001"]');
  card.querySelector(".kanban-card-stage-select").value = "presentation";
  card.querySelector(".kanban-card-footer button").click();

  await vi.waitFor(() => expect(patch).toHaveBeenCalled());
  await vi.waitFor(() => expect(globalThis.showToast).toHaveBeenCalledWith("error", expect.any(String), expect.any(String)));
  expect(document.querySelector('.kanban-column-body[data-stage-key="contact"]').textContent).toContain("LD-001");
});

// A timeout or dropped connection after sending means an uncertain outcome:
// refetch before retrying. A version conflict means: fetch the current lead.
// A failed move must not just redraw whatever the client already had in
// memory (which may be stale, or may not reflect a write that actually DID
// land on the server despite the response being lost) -- it must go back
// to the server for the truth before the board is trusted again.
it("refetches from the server after a failed move rather than trusting stale local data", async () => {
  const conflict = Object.assign(new Error("stale"), { status: 409, code: "stale_revision" });
  const patch = vi.fn(() => Promise.reject(conflict));
  const get = mockPartnerApi({ leads: [lead({ status: "contact", revision: 3 })] });
  globalThis.ApiClient = { get, patch };
  await initPartnerLeads({ id: 1, role: "partner" });

  const callsBefore = get.mock.calls.filter(([path]) => path === "/api/leads").length;

  const card = document.querySelector('.kanban-card[data-lead-id="LD-001"]');
  card.querySelector(".kanban-card-stage-select").value = "presentation";
  card.querySelector(".kanban-card-footer button").click();

  await vi.waitFor(() => expect(patch).toHaveBeenCalled());
  await vi.waitFor(() => {
    const callsAfter = get.mock.calls.filter(([path]) => path === "/api/leads").length;
    expect(callsAfter).toBeGreaterThan(callsBefore);
  });
});

it("refetches after a network/timeout failure too, not only after a 409", async () => {
  const timeout = Object.assign(new Error("network down"), { status: undefined });
  const patch = vi.fn(() => Promise.reject(timeout));
  const get = mockPartnerApi({ leads: [lead({ status: "contact", revision: 3 })] });
  globalThis.ApiClient = { get, patch };
  await initPartnerLeads({ id: 1, role: "partner" });

  const callsBefore = get.mock.calls.filter(([path]) => path === "/api/leads").length;

  const card = document.querySelector('.kanban-card[data-lead-id="LD-001"]');
  card.querySelector(".kanban-card-stage-select").value = "presentation";
  card.querySelector(".kanban-card-footer button").click();

  await vi.waitFor(() => expect(patch).toHaveBeenCalled());
  await vi.waitFor(() => {
    const callsAfter = get.mock.calls.filter(([path]) => path === "/api/leads").length;
    expect(callsAfter).toBeGreaterThan(callsBefore);
  });
});

// ─── VIEW TOGGLE ────────────────────────────────────────────────────────────

it("defaults to the Sales board and switches to Registration on toggle, with aria-pressed reflecting the active view", async () => {
  globalThis.ApiClient = { get: mockPartnerApi({ leads: [lead()] }) };
  await initPartnerLeads({ id: 1, role: "partner" });

  expect(document.getElementById("leads-sales-view").hidden).toBe(false);
  expect(document.getElementById("leads-registration-view").hidden).toBe(true);
  expect(document.getElementById("view-sales-btn").getAttribute("aria-pressed")).toBe("true");

  document.getElementById("view-registration-btn").click();

  expect(document.getElementById("leads-registration-view").hidden).toBe(false);
  expect(document.getElementById("leads-sales-view").hidden).toBe(true);
  expect(document.getElementById("view-registration-btn").getAttribute("aria-pressed")).toBe("true");
  expect(document.getElementById("view-sales-btn").getAttribute("aria-pressed")).toBe("false");
});

/* ── Settled leads leave the funnel ──────────────────────────
   Once the producer marks the commission as paid, the lead leaves the funnel
   and moves to Earnings.

   `commission_paid_at` is the stamp that means it -- api/payouts.py writes it
   when the producer records a payout. NOT `settlement_status`, which is the
   producer's review of whether the END CLIENT paid HIM (api/leads.py), a fact
   about a different debt entirely. Getting those two confused would empty a
   partner's board for the wrong reason. */

it("takes a lead whose commission has been paid out off the board", async () => {
  globalThis.ApiClient = { get: mockPartnerApi({ leads: [
    lead({ id: "LD-001", status: "won" }),
    lead({ id: "LD-002", status: "won", commission: 900, commission_paid_at: "2026-09-13T08:00:00" }),
  ] }) };

  await initPartnerLeads({ id: 1, role: "partner", name: "Ada" });

  const ids = [...document.querySelectorAll(".kanban-card")].map((c) => c.dataset.leadId);
  expect(ids).toEqual(["LD-001"]);
});

it("keeps a won-but-unpaid lead on the board, because that money is still owed", async () => {
  globalThis.ApiClient = { get: mockPartnerApi({ leads: [
    lead({ id: "LD-001", status: "won", commission: 900, win_confirmed_at: "2026-09-13T08:00:00" }),
  ] }) };

  await initPartnerLeads({ id: 1, role: "partner", name: "Ada" });

  expect(document.querySelectorAll(".kanban-card")).toHaveLength(1);
});

it("never confuses the producer's settlement review with a payout to the partner", async () => {
  // settlement_status 'paid' means the end client settled the producer's own
  // invoice. The partner has not been paid anything, so the lead stays.
  globalThis.ApiClient = { get: mockPartnerApi({ leads: [
    lead({ id: "LD-001", status: "won", commission: 900, settlement_status: "paid" }),
  ] }) };

  await initPartnerLeads({ id: 1, role: "partner", name: "Ada" });

  expect(document.querySelectorAll(".kanban-card")).toHaveLength(1);
});

it("says how many leads were settled and where they went, instead of letting them vanish", async () => {
  globalThis.ApiClient = { get: mockPartnerApi({ leads: [
    lead({ id: "LD-001", status: "won", commission: 900, commission_paid_at: "2026-09-13T08:00:00" }),
    lead({ id: "LD-002", status: "won", commission: 100, commission_paid_at: "2026-09-13T08:00:00" }),
  ] }) };

  await initPartnerLeads({ id: 1, role: "partner", name: "Ada" });

  const note = document.getElementById("kanban-settled-note");
  expect(note.hidden).toBe(false);
  expect(document.getElementById("kanban-settled-text").textContent).toContain("2");
});

it("hides that line entirely when nothing has been settled", async () => {
  globalThis.ApiClient = { get: mockPartnerApi({ leads: [lead({ status: "won" })] }) };

  await initPartnerLeads({ id: 1, role: "partner", name: "Ada" });

  expect(document.getElementById("kanban-settled-note").hidden).toBe(true);
});

it("offers the producer's English stage name in the move select, not the Polish original", async () => {
  // The select read `stage.name` directly, so an EN reader was moving a lead
  // between "First contact" and "Negotiation" (the Polish stage names in the fixture).
  globalThis.App = { ...appStub("en"), applyTranslations: vi.fn() };
  globalThis.ApiClient = { get: mockPartnerApi({
    leads: [lead({ status: "contact" })],
    stages: STAGES.map((s) => ({ ...s, name_en: s.key === "contact" ? "First contact" : null })),
  }) };

  await initPartnerLeads({ id: 1, role: "partner", name: "Ada" });

  const options = [...document.querySelectorAll(".kanban-card-stage-select option")]
    .map((o) => o.textContent);
  expect(options).toContain("First contact");
  expect(options).not.toContain("Nawiązanie kontaktu");
});
