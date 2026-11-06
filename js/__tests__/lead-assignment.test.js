/*
 * Assigning a lead to a partner, on the producer's own screen.
 *
 * What is pinned here: the entry point exists only for an admin session; the
 * partner selector never comes preselected; the summary shows the real
 * company, partner and first stage rather than invented ones; missing
 * partners, catalogue or pipeline disable the save and say which piece is
 * missing without hiding what has already been typed; and a failed save keeps
 * the draft, with an identical retry reusing the same operation id so a
 * response that never arrived cannot become a second lead.
 *
 * Same happy-dom + classic-script-in-vm harness as
 * js/__tests__/lead-reopen.test.js -- this project has no bundler, so the raw
 * source runs in the real global realm exactly as a <script> tag would.
 */
import { readFileSync } from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { Window } from "happy-dom";
import { describe, expect, it, vi } from "vitest";
import { appStub } from "./helpers/copy.js";

const PROJECT_ROOT = path.resolve(import.meta.dirname, "..", "..");

function runClassicScript(window, relativePath) {
  const absolutePath = path.join(PROJECT_ROOT, relativePath);
  const source = readFileSync(absolutePath, "utf8");
  const context = window.__vmContext || (window.__vmContext = vm.createContext(window));
  vm.runInContext(source, context, { filename: absolutePath });
  return context;
}

const STAGES = [
  { id: 1, key: "contact", name: "Nawiązanie kontaktu", sort_order: 0, kind: "progress", is_terminal: 0, color: "#94a3b8", active: 1 },
  { id: 2, key: "presentation", name: "Prezentacja", sort_order: 1, kind: "progress", is_terminal: 0, color: "#3b82f6", active: 1 },
  { id: 5, key: "won", name: "Wygrany", sort_order: 4, kind: "win", is_terminal: 1, color: "#10b981", active: 1 },
];

const PARTNERS = [
  { id: 7, name: "Partner Testowy", company: "Acme Sp. z o.o." },
  { id: 9, name: "Druga Firma", company: "Beta Sp. z o.o." },
];

const PRODUCTS = [{ id: 3, name: "Wdrożenie ERP", active: 1, offered: 1 }];

const FIELDS = [
  { id: 11, label: "Termin wdrożenia", field_type: "text", required: true, active: true, partner_editable: false },
];

function createRealm({
  session = { id: 1, role: "admin" },
  stages = STAGES,
  partners = PARTNERS,
  products = PRODUCTS,
  fields = FIELDS,
  version = "1-1",
  assignImpl = null,
  failLoad = false,
  lang = "pl",
} = {}) {
  // Mutable so a test can change the world between the first load and the
  // refetch a conflict triggers -- a partner deactivated under the open form
  // is exactly the case the form has to survive.
  const world = { stages, partners, products, fields, version, failLoad };
  const window = new Window({ url: "http://localhost:8000/pages/admin/leads.html" });
  window.document.body.dataset.page = "admin-leads";
  window.document.body.innerHTML = `
    <button id="assign-lead-btn" hidden></button>
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
    <div id="assign-lead-modal">
      <div id="assign-loading-state" hidden></div>
      <div id="assign-error-state" hidden><p id="assign-error-message"></p><button id="assign-retry-btn"></button></div>
      <div id="assign-empty-state" hidden><p id="assign-empty-message"></p></div>
      <form id="assign-lead-form">
        <select id="assign-partner"><option value=""></option></select>
        <input id="assign-client-name"><input id="assign-company"><input id="assign-email">
        <input id="assign-phone"><input id="assign-nip"><input id="assign-website">
        <select id="assign-product"><option value=""></option></select>
        <input id="assign-quantity" type="number"><input id="assign-value" type="number">
        <input id="assign-sector"><textarea id="assign-notes"></textarea>
        <div id="assign-custom-fields"></div>
      </form>
      <div id="assign-preview">
        <strong id="assign-preview-company">—</strong>
        <strong id="assign-preview-partner">—</strong>
        <strong id="assign-preview-stage">—</strong>
        <strong id="assign-preview-value">—</strong>
      </div>
      <button id="assign-submit-btn" disabled></button>
    </div>
  `;
  const assignCalls = [];
  window.ApiClient = {
    get: vi.fn((url) => {
      if (url === "/api/leads") return Promise.resolve({ leads: [] });
      if (url === "/api/lead-stages") return Promise.resolve({ stages: world.stages });
      if (url === "/api/settings") return Promise.resolve({ settings: {} });
      if (url === "/api/partners") {
        return world.failLoad ? Promise.reject(new Error("network"))
                              : Promise.resolve({ partners: world.partners });
      }
      if (url === "/api/products") return Promise.resolve({ products: world.products });
      if (url === "/api/lead-fields") {
        return Promise.resolve({ fields: world.fields, version: world.version });
      }
      return Promise.resolve({});
    }),
    patch: vi.fn(() => Promise.resolve({})),
    post: vi.fn((url, body) => {
      if (url === "/api/leads/assign") {
        assignCalls.push(body);
        if (assignImpl) return assignImpl(body, assignCalls.length);
        return Promise.resolve({
          lead: { id: "LD-042", partner_id: body.partner_id, partner_name: "Partner Testowy" },
        });
      }
      return Promise.resolve({});
    }),
  };
  // Hooks are collected so a test can play a language switch (ledger #55).
  const rerenderHooks = [];
  window.App = { ...appStub(lang), applyTranslations: vi.fn(), onRerender: (hook) => rerenderHooks.push(hook) };
  window.__rerender = (nextLang) => {
    Object.assign(window.App, appStub(nextLang), { onRerender: window.App.onRerender });
    rerenderHooks.forEach((hook) => hook());
  };
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
  runClassicScript(window, "js/lead-fields.js");
  runClassicScript(window, "js/admin-leads.js");
  return { window, assignCalls, world };
}

async function openAssign(window) {
  window.document.getElementById("assign-lead-btn").click();
  await vi.waitFor(() => expect(window.openModal).toHaveBeenCalledWith("assign-lead-modal"));
  await vi.waitFor(() =>
    expect(window.document.getElementById("assign-loading-state").hidden).toBe(true));
}

function fillForm(window, overrides = {}) {
  const values = {
    "assign-partner": "7",
    "assign-client-name": "Jan Kowalski",
    "assign-company": "Acme Sp. z o.o.",
    "assign-email": "jan@example.com",
    "assign-nip": "1234567890",
    "assign-website": "example.pl",
    "assign-product": "3",
    "assign-quantity": "2",
    "assign-value": "10000",
    ...overrides,
  };
  Object.entries(values).forEach(([id, value]) => {
    window.document.getElementById(id).value = value;
  });
  const custom = window.document.querySelector('#assign-custom-fields [data-field-id]');
  if (custom) custom.value = "Q1 2027";
}

describe("lead assignment -- who sees the entry point", () => {
  it("unhides the button for a producer session", async () => {
    const { window } = createRealm();
    await window.AdminLeads.init();
    expect(window.document.getElementById("assign-lead-btn").hidden).toBe(false);
  });

  it.each(["manager", "observer", "partner"])("keeps it hidden for %s", async (role) => {
    const { window } = createRealm({ session: { id: 2, role } });
    await window.AdminLeads.init();
    expect(window.document.getElementById("assign-lead-btn").hidden).toBe(true);
  });

  it("sends nothing when a non-producer session clicks it anyway", async () => {
    const { window, assignCalls } = createRealm({ session: { id: 2, role: "manager" } });
    await window.AdminLeads.init();
    window.document.getElementById("assign-lead-btn").click();
    window.document.getElementById("assign-submit-btn").click();
    expect(assignCalls).toEqual([]);
    expect(window.openModal).not.toHaveBeenCalled();
  });
});

describe("lead assignment -- the form and its summary", () => {
  it("offers every active partner with no preselection", async () => {
    const { window } = createRealm();
    await window.AdminLeads.init();
    await openAssign(window);
    const select = window.document.getElementById("assign-partner");
    expect(select.value).toBe("");
    expect(Array.from(select.options).map((option) => option.value))
      .toEqual(["", "7", "9"]);
  });

  it("renders the producer's own questions through the shared component", async () => {
    const { window } = createRealm();
    await window.AdminLeads.init();
    await openAssign(window);
    const input = window.document.querySelector('#assign-custom-fields [data-field-id="11"]');
    expect(input).not.toBeNull();
    expect(input.dataset.required).toBe("1");
  });

  it("summarises the real company, partner and first stage", async () => {
    const { window } = createRealm();
    await window.AdminLeads.init();
    await openAssign(window);
    fillForm(window);
    window.document.getElementById("assign-partner").dispatchEvent(new window.Event("change"));
    window.document.getElementById("assign-company").dispatchEvent(new window.Event("input"));
    expect(window.document.getElementById("assign-preview-company").textContent)
      .toBe("Acme Sp. z o.o.");
    expect(window.document.getElementById("assign-preview-partner").textContent)
      .toBe("Partner Testowy");
    expect(window.document.getElementById("assign-preview-stage").textContent)
      .toBe("Nawiązanie kontaktu");
  });

  it("shows a dash rather than an invented partner before one is chosen", async () => {
    const { window } = createRealm();
    await window.AdminLeads.init();
    await openAssign(window);
    expect(window.document.getElementById("assign-preview-partner").textContent).toBe("—");
    expect(window.document.getElementById("assign-preview-company").textContent).toBe("—");
  });
});

describe("lead assignment -- missing data disables the save", () => {
  it.each([
    ["no partner", { partners: [] }, /partner/i],
    ["no product", { products: [] }, /produkt|katalog/i],
    ["no stage", { stages: STAGES.filter((stage) => stage.is_terminal) }, /etap/i],
  ])("%s: explains what is missing and refuses to save", async (_label, options, pattern) => {
    const { window, assignCalls } = createRealm(options);
    await window.AdminLeads.init();
    await openAssign(window);
    expect(window.document.getElementById("assign-submit-btn").disabled).toBe(true);
    expect(window.document.getElementById("assign-empty-state").hidden).toBe(false);
    expect(window.document.getElementById("assign-empty-message").textContent)
      .toMatch(pattern);
    // The form the producer may already have typed into is still there.
    expect(window.document.getElementById("assign-lead-form")).not.toBeNull();
    window.document.getElementById("assign-submit-btn").click();
    expect(assignCalls).toEqual([]);
  });

  it("offers a retry when the form data cannot be loaded", async () => {
    const { window } = createRealm({ failLoad: true });
    await window.AdminLeads.init();
    await openAssign(window);
    expect(window.document.getElementById("assign-error-state").hidden).toBe(false);
    expect(window.document.getElementById("assign-submit-btn").disabled).toBe(true);
  });
});

describe("lead assignment -- saving", () => {
  it("sends the whole envelope, confirmed, for the chosen partner", async () => {
    const { window, assignCalls } = createRealm();
    await window.AdminLeads.init();
    await openAssign(window);
    fillForm(window);
    window.document.getElementById("assign-submit-btn").click();
    await vi.waitFor(() => expect(assignCalls).toHaveLength(1));
    const body = assignCalls[0];
    expect(body.partner_id).toBe(7);
    expect(body.confirm_accept).toBe(true);
    expect(body.schema_revision).toBe("1-1");
    expect(body.product_id).toBe(3);
    expect(body.quantity).toBe(2);
    expect(body.lead_field_values).toEqual({ 11: "Q1 2027" });
    expect(typeof body.operation_id).toBe("string");
    expect(body.operation_id.length).toBeGreaterThan(0);
    await vi.waitFor(() =>
      expect(window.closeModal).toHaveBeenCalledWith("assign-lead-modal"));
  });

  it("refuses an incomplete form without sending anything", async () => {
    const { window, assignCalls } = createRealm();
    await window.AdminLeads.init();
    await openAssign(window);
    fillForm(window, { "assign-partner": "" });
    window.document.getElementById("assign-submit-btn").click();
    expect(assignCalls).toEqual([]);
    expect(window.document.getElementById("assign-partner").classList.contains("error"))
      .toBe(true);
  });

  it("refuses a missing required answer and marks it", async () => {
    const { window, assignCalls } = createRealm();
    await window.AdminLeads.init();
    await openAssign(window);
    fillForm(window);
    window.document.querySelector('#assign-custom-fields [data-field-id="11"]').value = "";
    window.document.getElementById("assign-submit-btn").click();
    expect(assignCalls).toEqual([]);
    expect(
      window.document.querySelector('#assign-custom-fields [data-field-id="11"]')
        .classList.contains("input-error"),
    ).toBe(true);
  });

  it("keeps the draft and reuses the operation id on an identical retry", async () => {
    const { window, assignCalls } = createRealm({
      assignImpl: (body, callNumber) => (
        callNumber === 1
          ? Promise.reject(Object.assign(new Error("network"), { status: 0 }))
          : Promise.resolve({ lead: { id: "LD-043", partner_name: "Partner Testowy" } })
      ),
    });
    await window.AdminLeads.init();
    await openAssign(window);
    fillForm(window);
    window.document.getElementById("assign-submit-btn").click();
    await vi.waitFor(() => expect(assignCalls).toHaveLength(1));

    // Nothing typed was cleared by the failure.
    expect(window.document.getElementById("assign-company").value).toBe("Acme Sp. z o.o.");
    await vi.waitFor(() =>
      expect(window.document.getElementById("assign-submit-btn").disabled).toBe(false));

    window.document.getElementById("assign-submit-btn").click();
    await vi.waitFor(() => expect(assignCalls).toHaveLength(2));
    expect(assignCalls[1].operation_id).toBe(assignCalls[0].operation_id);
  });

  it("takes a new operation id once the payload changes", async () => {
    const { window, assignCalls } = createRealm({
      assignImpl: () => Promise.reject(Object.assign(new Error("network"), { status: 0 })),
    });
    await window.AdminLeads.init();
    await openAssign(window);
    fillForm(window);
    window.document.getElementById("assign-submit-btn").click();
    await vi.waitFor(() => expect(assignCalls).toHaveLength(1));
    window.document.getElementById("assign-partner").value = "9";
    await vi.waitFor(() =>
      expect(window.document.getElementById("assign-submit-btn").disabled).toBe(false));
    window.document.getElementById("assign-submit-btn").click();
    await vi.waitFor(() => expect(assignCalls).toHaveLength(2));
    expect(assignCalls[1].operation_id).not.toBe(assignCalls[0].operation_id);
  });

  it("does not send twice while the first save is in flight", async () => {
    let release;
    const gate = new Promise((resolve) => { release = resolve; });
    const { window, assignCalls } = createRealm({
      assignImpl: () => gate.then(() => ({ lead: { id: "LD-044", partner_name: "P" } })),
    });
    await window.AdminLeads.init();
    await openAssign(window);
    fillForm(window);
    const submit = window.document.getElementById("assign-submit-btn");
    submit.click();
    await vi.waitFor(() => expect(assignCalls).toHaveLength(1));
    expect(submit.disabled).toBe(true);
    expect(submit.getAttribute("aria-busy")).toBe("true");
    submit.click();
    release();
    await vi.waitFor(() => expect(window.closeModal).toHaveBeenCalled());
    expect(assignCalls).toHaveLength(1);
  });

  it("refreshes the questions when the schema moved under the form", async () => {
    const { window, assignCalls } = createRealm({
      assignImpl: () => Promise.reject(Object.assign(
        new Error("Producent zmienił formularz zgłoszenia."),
        { status: 409, code: "stale_lead_fields" },
      )),
    });
    await window.AdminLeads.init();
    await openAssign(window);
    fillForm(window);
    window.document.getElementById("assign-submit-btn").click();
    await vi.waitFor(() => expect(assignCalls).toHaveLength(1));
    await vi.waitFor(() =>
      expect(window.ApiClient.get).toHaveBeenCalledWith("/api/lead-fields"));
    // The draft survives the refresh: the answer is still on screen.
    await vi.waitFor(() => expect(
      window.document.querySelector('#assign-custom-fields [data-field-id="11"]').value,
    ).toBe("Q1 2027"));
  });
});

/* ============================================================================
 * The edges: partial data, zero/one/many, errors, long text and the
 * accessibility half of the assignment must-haves. Everything below is about
 * what happens when the data is thin, hostile or absent, because that is where
 * an assignment form quietly invents a partner, drops a `false`, or turns one
 * failed save into two leads.
 * ========================================================================== */

const NUMBER_FIELD = {
  id: 12, label: "Liczba stanowisk", field_type: "number",
  required: true, active: true, partner_editable: false,
};
const CHECKBOX_FIELD = {
  id: 13, label: "Klient ma już system", field_type: "checkbox",
  required: false, active: true, partner_editable: false,
};

/** Fill in only the standard part of the form, leaving custom answers alone. */
function fillStandard(window, overrides = {}) {
  const values = {
    "assign-partner": "7",
    "assign-client-name": "Jan Kowalski",
    "assign-company": "Acme Sp. z o.o.",
    "assign-email": "jan@example.com",
    "assign-nip": "1234567890",
    "assign-website": "example.pl",
    "assign-product": "3",
    "assign-quantity": "2",
    "assign-value": "10000",
    ...overrides,
  };
  Object.entries(values).forEach(([id, value]) => {
    window.document.getElementById(id).value = value;
  });
  // Answer the producer's own questions too, so a test about an error path is
  // not really a test about an unanswered required field.
  window.document.querySelectorAll("#assign-custom-fields [data-field-id]")
    .forEach((input) => {
      if (input.dataset.fieldType === "checkbox" || input.value !== "") return;
      input.value = input.dataset.fieldType === "number" ? "5" : "Q1 2027";
    });
}

function answer(window, fieldId) {
  return window.document.querySelector(`#assign-custom-fields [data-field-id="${fieldId}"]`);
}

describe("lead assignment -- 0, 1 and many partners", () => {
  it("offers nothing but the placeholder when there is no partner", async () => {
    const { window } = createRealm({ partners: [] });
    await window.AdminLeads.init();
    await openAssign(window);
    const select = window.document.getElementById("assign-partner");
    // No fabricated row, and no "first partner" quietly standing in.
    expect(Array.from(select.options).map((o) => o.value)).toEqual([""]);
    expect(select.value).toBe("");
  });

  it("still refuses to preselect the only partner there is", async () => {
    const { window } = createRealm({ partners: [PARTNERS[0]] });
    await window.AdminLeads.init();
    await openAssign(window);
    const select = window.document.getElementById("assign-partner");
    expect(Array.from(select.options).map((o) => o.value)).toEqual(["", "7"]);
    expect(select.value).toBe("");
    // One partner is not a blocked state: the save is available.
    expect(window.document.getElementById("assign-submit-btn").disabled).toBe(false);
    expect(window.document.getElementById("assign-empty-state").hidden).toBe(true);
  });

  it("keeps many partners in the order the server sent them", async () => {
    const many = Array.from({ length: 24 }, (_, i) => ({
      id: 100 + i, name: `Partner ${String(i).padStart(2, "0")}`, company: `Firma ${i}`,
    }));
    const { window } = createRealm({ partners: many });
    await window.AdminLeads.init();
    await openAssign(window);
    const select = window.document.getElementById("assign-partner");
    expect(select.options.length).toBe(25);
    expect(Array.from(select.options).slice(1).map((o) => o.value))
      .toEqual(many.map((p) => String(p.id)));
  });
});

describe("lead assignment -- missing is not 0 and not false", () => {
  it("says the value is not provided rather than showing 0 PLN", async () => {
    const { window } = createRealm();
    await window.AdminLeads.init();
    await openAssign(window);
    expect(window.document.getElementById("assign-preview-value").textContent)
      .toMatch(/nie podano/i);
  });

  it("shows a real zero as a real zero", async () => {
    const { window } = createRealm();
    await window.AdminLeads.init();
    await openAssign(window);
    const value = window.document.getElementById("assign-value");
    value.value = "0";
    value.dispatchEvent(new window.Event("input"));
    expect(window.document.getElementById("assign-preview-value").textContent)
      .toBe("0 PLN");
    // A switch to English must not repaint the producer's own figure with the
    // "not provided" placeholder the static markup starts out holding.
    expect(window.document.getElementById("assign-preview-value")
      .hasAttribute("data-i18n")).toBe(false);
  });

  it("sends 0 and false as answers, not as silence", async () => {
    const { window, assignCalls } = createRealm({
      fields: [NUMBER_FIELD, CHECKBOX_FIELD],
    });
    await window.AdminLeads.init();
    await openAssign(window);
    fillStandard(window);
    answer(window, 12).value = "0";
    answer(window, 13).checked = false;
    window.document.getElementById("assign-submit-btn").click();
    await vi.waitFor(() => expect(assignCalls).toHaveLength(1));
    expect(assignCalls[0].lead_field_values).toEqual({ 12: 0, 13: false });
  });

  it("does not call a required zero an unanswered question", async () => {
    const { window, assignCalls } = createRealm({ fields: [NUMBER_FIELD] });
    await window.AdminLeads.init();
    await openAssign(window);
    fillStandard(window);
    answer(window, 12).value = "0";
    window.document.getElementById("assign-submit-btn").click();
    await vi.waitFor(() => expect(assignCalls).toHaveLength(1));
    expect(answer(window, 12).classList.contains("input-error")).toBe(false);
  });

  it("keeps 0 and false on screen when the schema is refetched under the form", async () => {
    const { window, assignCalls } = createRealm({
      fields: [NUMBER_FIELD, CHECKBOX_FIELD],
      assignImpl: () => Promise.reject(Object.assign(
        new Error("stale"), { status: 409, code: "stale_lead_fields" },
      )),
    });
    await window.AdminLeads.init();
    await openAssign(window);
    fillStandard(window);
    answer(window, 12).value = "0";
    answer(window, 13).checked = true;
    window.document.getElementById("assign-submit-btn").click();
    await vi.waitFor(() => expect(assignCalls).toHaveLength(1));
    await vi.waitFor(() => expect(window.ApiClient.get)
      .toHaveBeenCalledWith("/api/lead-fields"));
    expect(answer(window, 12).value).toBe("0");
    expect(answer(window, 13).checked).toBe(true);
  });
});

describe("lead assignment -- a schema without a version blocks editing", () => {
  it("disables the save and the answers without hiding what was typed", async () => {
    const { window, assignCalls } = createRealm({
      fields: [NUMBER_FIELD], version: null,
    });
    await window.AdminLeads.init();
    await openAssign(window);
    fillStandard(window);
    expect(window.document.getElementById("assign-submit-btn").disabled).toBe(true);
    expect(window.document.getElementById("assign-empty-state").hidden).toBe(false);
    expect(window.document.getElementById("assign-empty-message").textContent)
      .toMatch(/formularz|schemat|wersj/i);
    // Editing the producer's own questions is what is blocked...
    expect(answer(window, 12).disabled).toBe(true);
    // ...but the base data the producer already typed is still on screen.
    expect(window.document.getElementById("assign-company").value)
      .toBe("Acme Sp. z o.o.");
    window.document.getElementById("assign-submit-btn").click();
    expect(assignCalls).toEqual([]);
  });

  it("re-enables the answers once a versioned schema comes back", async () => {
    const { window, world } = createRealm({ fields: [NUMBER_FIELD], version: null });
    await window.AdminLeads.init();
    await openAssign(window);
    expect(answer(window, 12).disabled).toBe(true);
    world.version = "2-9";
    window.document.getElementById("assign-retry-btn").click();
    await vi.waitFor(() => expect(answer(window, 12).disabled).toBe(false));
    expect(window.document.getElementById("assign-submit-btn").disabled).toBe(false);
  });
});

describe("lead assignment -- what each kind of failure says and does", () => {
  const DRAFT_NOTES = "Klient prosi o kontakt po 15:00, budżet potwierdzony przez zarząd.";

  async function failWith(error, options = {}) {
    const realm = createRealm({
      assignImpl: () => Promise.reject(error),
      ...options,
    });
    const { window, assignCalls } = realm;
    await window.AdminLeads.init();
    await openAssign(window);
    fillStandard(window);
    window.document.getElementById("assign-notes").value = DRAFT_NOTES;
    window.document.getElementById("assign-submit-btn").click();
    await vi.waitFor(() => expect(assignCalls).toHaveLength(1));
    // The POST is recorded synchronously; wait for the rejection to have been
    // handled and the button to have come out of its busy state, so what the
    // test inspects is the settled screen rather than a half-finished one.
    await vi.waitFor(() => expect(window.showToast).toHaveBeenCalled());
    await vi.waitFor(() => expect(window.document.getElementById("assign-submit-btn")
      .getAttribute("aria-busy")).toBe("false"));
    return realm;
  }

  function lastToast(window) {
    const calls = window.showToast.mock.calls;
    return calls.length ? calls[calls.length - 1] : null;
  }

  it("a plain network failure says so, keeps the draft and re-arms the button", async () => {
    const { window, assignCalls } = await failWith(new Error(
      "Brak połączenia z serwerem. Sprawdź czy serwer działa.",
    ));
    await vi.waitFor(() =>
      expect(window.document.getElementById("assign-submit-btn").disabled).toBe(false));
    expect(lastToast(window)[0]).toBe("error");
    expect(`${lastToast(window)[1]} ${lastToast(window)[2]}`)
      .toMatch(/połącz|sieci/i);
    expect(window.document.getElementById("assign-notes").value).toBe(DRAFT_NOTES);
    // Nothing was re-sent on our behalf.
    expect(assignCalls).toHaveLength(1);
  });

  it("a 409 on the operation id takes a fresh id instead of looping on 409", async () => {
    const { window, assignCalls } = await failWith(Object.assign(
      new Error("Ten identyfikator operacji ma inne dane."),
      { status: 409, code: "operation_conflict" },
    ));
    expect(`${lastToast(window)[1]} ${lastToast(window)[2]}`)
      .toMatch(/konflikt|operacj/i);
    expect(window.document.getElementById("assign-notes").value).toBe(DRAFT_NOTES);
    expect(assignCalls).toHaveLength(1);
    // The next attempt cannot reuse a key the server has already bound to a
    // different payload, or it 409s forever.
    await vi.waitFor(() =>
      expect(window.document.getElementById("assign-submit-btn").disabled).toBe(false));
    window.document.getElementById("assign-submit-btn").click();
    await vi.waitFor(() => expect(assignCalls).toHaveLength(2));
    expect(assignCalls[1].operation_id).not.toBe(assignCalls[0].operation_id);
  });

  it("a 409 on the schema revision refetches and keeps the same operation id", async () => {
    let calls = 0;
    const { window, assignCalls } = createRealm({
      assignImpl: () => {
        calls += 1;
        return calls === 1
          ? Promise.reject(Object.assign(new Error("stale"),
            { status: 409, code: "stale_revision" }))
          : Promise.resolve({ lead: { id: "LD-050", partner_name: "Partner Testowy" } });
      },
    });
    await window.AdminLeads.init();
    await openAssign(window);
    fillStandard(window);
    window.document.getElementById("assign-submit-btn").click();
    await vi.waitFor(() => expect(assignCalls).toHaveLength(1));
    await vi.waitFor(() =>
      expect(window.document.getElementById("assign-submit-btn").disabled).toBe(false));
    expect(`${lastToast(window)[1]} ${lastToast(window)[2]}`).toMatch(/formularz|pytani/i);
    window.document.getElementById("assign-submit-btn").click();
    await vi.waitFor(() => expect(assignCalls).toHaveLength(2));
    expect(assignCalls[1].operation_id).toBe(assignCalls[0].operation_id);
  });

  it("a 403 drops the detail the producer may no longer touch", async () => {
    const realm = createRealm({
      assignImpl: () => Promise.reject(Object.assign(
        new Error("Brak uprawnień do tego partnera."), { status: 403 },
      )),
    });
    const { window, assignCalls, world } = realm;
    await window.AdminLeads.init();
    await openAssign(window);
    fillStandard(window);
    window.document.getElementById("assign-notes").value = DRAFT_NOTES;
    // The partner is deactivated while the form sits open.
    world.partners = [PARTNERS[1]];
    window.document.getElementById("assign-submit-btn").click();
    await vi.waitFor(() => expect(assignCalls).toHaveLength(1));
    await vi.waitFor(() =>
      expect(Array.from(window.document.getElementById("assign-partner").options)
        .map((o) => o.value)).toEqual(["", "9"]));
    // The inaccessible selection is gone, and nothing invented replaced it.
    expect(window.document.getElementById("assign-partner").value).toBe("");
    expect(window.document.getElementById("assign-preview-partner").textContent).toBe("—");
    // Everything else the producer typed survived.
    expect(window.document.getElementById("assign-notes").value).toBe(DRAFT_NOTES);
    expect(`${lastToast(window)[1]} ${lastToast(window)[2]}`).toMatch(/uprawnie|dostęp/i);
    expect(assignCalls).toHaveLength(1);
  });

  it("a 404 is treated the same way as a 403", async () => {
    const realm = createRealm({
      assignImpl: () => Promise.reject(Object.assign(
        new Error("Nie znaleziono."), { status: 404 },
      )),
    });
    const { window, assignCalls } = realm;
    await window.AdminLeads.init();
    await openAssign(window);
    fillStandard(window);
    window.document.getElementById("assign-notes").value = DRAFT_NOTES;
    const before = window.ApiClient.get.mock.calls
      .filter(([url]) => url === "/api/partners").length;
    window.document.getElementById("assign-submit-btn").click();
    await vi.waitFor(() => expect(assignCalls).toHaveLength(1));
    // The screen re-reads what it is allowed to see instead of keeping a row
    // the server has just said is not there.
    await vi.waitFor(() => expect(window.ApiClient.get.mock.calls
      .filter(([url]) => url === "/api/partners").length).toBe(before + 1));
    expect(window.document.getElementById("assign-notes").value).toBe(DRAFT_NOTES);
    expect(assignCalls).toHaveLength(1);
    expect(`${lastToast(window)[1]} ${lastToast(window)[2]}`)
      .toMatch(/nie znaleziono|nie istnieje|dostęp/i);
  });

  it("a server error of unknown outcome blocks the next save until a refetch", async () => {
    const { window, assignCalls } = await failWith(Object.assign(
      new Error("Błąd serwera (500)"), { status: 500 },
    ));
    // We do not know whether the lead was created. Sending again could double
    // it, so the button stays shut and the copy says to check.
    expect(window.document.getElementById("assign-submit-btn").disabled).toBe(true);
    expect(window.document.getElementById("assign-error-state").hidden).toBe(false);
    expect(window.document.getElementById("assign-error-message").textContent)
      .toMatch(/nie wiadomo|sprawdź/i);
    expect(window.document.getElementById("assign-notes").value).toBe(DRAFT_NOTES);
    // The retry control refetches; it never replays the mutation by itself.
    window.document.getElementById("assign-retry-btn").click();
    await vi.waitFor(() =>
      expect(window.document.getElementById("assign-submit-btn").disabled).toBe(false));
    expect(assignCalls).toHaveLength(1);
  });

  it("says all of it in English for an English producer", async () => {
    const { window } = await failWith(
      Object.assign(new Error("Błąd serwera (500)"), { status: 500 }),
      { lang: "en" },
    );
    const message = window.document.getElementById("assign-error-message").textContent;
    expect(message).toMatch(/[A-Za-z]/);
    expect(message).not.toMatch(/[ąćęłńóśźż]/i);
    expect(message).toMatch(/check|unknown|not know/i);
  });
});

describe("lead assignment -- long text", () => {
  const LONG_COMPANY = `Przedsiębiorstwo Wielobranżowe ${"Bardzo-Długa-Nazwa-".repeat(12)}Sp. z o.o.`;
  const LONG_NOTES = "Szczegóły rozmowy. ".repeat(80);

  it("shows the whole company name in the summary without truncating it", async () => {
    const { window } = createRealm();
    await window.AdminLeads.init();
    await openAssign(window);
    const company = window.document.getElementById("assign-company");
    company.value = LONG_COMPANY;
    company.dispatchEvent(new window.Event("input"));
    expect(window.document.getElementById("assign-preview-company").textContent)
      .toBe(LONG_COMPANY);
  });

  it("shows a long partner name in full rather than an ellipsis of it", async () => {
    const longName = `Partner ${"Nadzwyczaj-Rozbudowana-Nazwa-".repeat(8)}Końcówka`;
    const { window } = createRealm({
      partners: [{ id: 7, name: longName, company: "Acme Sp. z o.o." }],
    });
    await window.AdminLeads.init();
    await openAssign(window);
    const select = window.document.getElementById("assign-partner");
    select.value = "7";
    select.dispatchEvent(new window.Event("change"));
    expect(window.document.getElementById("assign-preview-partner").textContent)
      .toBe(longName);
  });

  it("keeps a long draft intact across a failed save", async () => {
    const { window, assignCalls } = createRealm({
      assignImpl: () => Promise.reject(new Error("Brak połączenia z serwerem.")),
    });
    await window.AdminLeads.init();
    await openAssign(window);
    fillStandard(window, { "assign-company": LONG_COMPANY });
    window.document.getElementById("assign-notes").value = LONG_NOTES;
    window.document.getElementById("assign-submit-btn").click();
    await vi.waitFor(() => expect(assignCalls).toHaveLength(1));
    await vi.waitFor(() =>
      expect(window.document.getElementById("assign-submit-btn").disabled).toBe(false));
    expect(window.document.getElementById("assign-company").value).toBe(LONG_COMPANY);
    expect(window.document.getElementById("assign-notes").value).toBe(LONG_NOTES);
  });
});

describe("lead assignment -- keyboard and screen reader", () => {
  it("moves focus to the first field the producer has to fix", async () => {
    const { window, assignCalls } = createRealm();
    await window.AdminLeads.init();
    await openAssign(window);
    fillStandard(window, { "assign-client-name": "", "assign-email": "" });
    window.document.getElementById("assign-submit-btn").click();
    expect(assignCalls).toEqual([]);
    const clientName = window.document.getElementById("assign-client-name");
    expect(window.document.activeElement.id).toBe("assign-client-name");
    expect(clientName.getAttribute("aria-invalid")).toBe("true");
    // A field that is fine is not accused of being invalid.
    expect(window.document.getElementById("assign-company")
      .getAttribute("aria-invalid")).not.toBe("true");
  });

  it("clears aria-invalid once the field is filled in", async () => {
    const { window } = createRealm();
    await window.AdminLeads.init();
    await openAssign(window);
    fillStandard(window, { "assign-client-name": "" });
    window.document.getElementById("assign-submit-btn").click();
    expect(window.document.getElementById("assign-client-name")
      .getAttribute("aria-invalid")).toBe("true");
    window.document.getElementById("assign-client-name").value = "Jan Kowalski";
    window.document.getElementById("assign-submit-btn").click();
    expect(window.document.getElementById("assign-client-name")
      .getAttribute("aria-invalid")).not.toBe("true");
  });

  it("marks the modal busy while the save is in flight", async () => {
    let release;
    const gate = new Promise((resolve) => { release = resolve; });
    const { window, assignCalls } = createRealm({
      assignImpl: () => gate.then(() => ({ lead: { id: "LD-060", partner_name: "P" } })),
    });
    await window.AdminLeads.init();
    await openAssign(window);
    fillStandard(window);
    window.document.getElementById("assign-submit-btn").click();
    await vi.waitFor(() => expect(assignCalls).toHaveLength(1));
    expect(window.document.getElementById("assign-lead-modal")
      .getAttribute("aria-busy")).toBe("true");
    release();
    await vi.waitFor(() => expect(window.closeModal).toHaveBeenCalled());
    expect(window.document.getElementById("assign-lead-modal")
      .getAttribute("aria-busy")).toBe("false");
  });

  it("closes on Escape without throwing the draft away", async () => {
    const { window } = createRealm();
    await window.AdminLeads.init();
    await openAssign(window);
    fillStandard(window);
    window.document.getElementById("assign-notes").value = "Wersja robocza";
    window.document.dispatchEvent(new window.KeyboardEvent("keydown", {
      key: "Escape", bubbles: true,
    }));
    expect(window.closeModal).toHaveBeenCalledWith("assign-lead-modal");
    expect(window.document.getElementById("assign-notes").value).toBe("Wersja robocza");
    expect(window.document.getElementById("assign-company").value)
      .toBe("Acme Sp. z o.o.");
  });

  it("does not abandon a save that is already in flight when Escape is pressed", async () => {
    let release;
    const gate = new Promise((resolve) => { release = resolve; });
    const { window, assignCalls } = createRealm({
      assignImpl: () => gate.then(() => ({ lead: { id: "LD-061", partner_name: "P" } })),
    });
    await window.AdminLeads.init();
    await openAssign(window);
    fillStandard(window);
    window.document.getElementById("assign-submit-btn").click();
    await vi.waitFor(() => expect(assignCalls).toHaveLength(1));
    window.document.dispatchEvent(new window.KeyboardEvent("keydown", {
      key: "Escape", bubbles: true,
    }));
    expect(window.closeModal).not.toHaveBeenCalled();
    release();
    await vi.waitFor(() => expect(window.closeModal).toHaveBeenCalled());
  });
});

// The product catalogue carries the producer's own English name, so a
// producer working in English picks from an English list here, like their
// partners' new-lead form.
describe("assign form — product names in the reader's language", () => {
  const TRANSLATED = [{ id: 3, name: "Wdrozenie ERP", name_en: "ERP rollout", active: 1, offered: 1 }];

  function optionText(window) {
    return [...window.document.getElementById("assign-product").options]
      .map((option) => option.textContent)
      .join(" | ");
  }

  it("shows the producer's English product name to an English reader", async () => {
    const { window } = createRealm({ products: TRANSLATED, lang: "en" });
    await window.AdminLeads.init();
    await openAssign(window);

    expect(optionText(window)).toContain("ERP rollout");
    expect(optionText(window)).not.toContain("Wdrozenie ERP");
  });

  it("falls back to the Polish original when the product is not translated", async () => {
    const { window } = createRealm({
      products: [{ id: 3, name: "Wdrozenie ERP", name_en: "", active: 1, offered: 1 }],
      lang: "en",
    });
    await window.AdminLeads.init();
    await openAssign(window);

    expect(optionText(window)).toContain("Wdrozenie ERP");
  });

  it("keeps the product id as the submitted value, whatever the language", async () => {
    const { window } = createRealm({ products: TRANSLATED, lang: "en" });
    await window.AdminLeads.init();
    await openAssign(window);

    const values = [...window.document.getElementById("assign-product").options].map((o) => o.value);
    expect(values).toContain("3");
  });

  it("relabels the open window when the language changes, keeping the choice", async () => {
    const { window } = createRealm({ products: TRANSLATED, lang: "pl" });
    await window.AdminLeads.init();
    await openAssign(window);
    window.document.getElementById("assign-product").value = "3";

    window.__rerender("en");

    expect(optionText(window)).toContain("ERP rollout");
    expect(window.document.getElementById("assign-product").value).toBe("3");
  });

  it("leaves the Polish reader on the Polish product name", async () => {
    const { window } = createRealm({ products: TRANSLATED, lang: "pl" });
    await window.AdminLeads.init();
    await openAssign(window);

    expect(optionText(window)).toContain("Wdrozenie ERP");
    expect(optionText(window)).not.toContain("ERP rollout");
  });
});
