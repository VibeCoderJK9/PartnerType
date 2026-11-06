import { readFileSync } from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { Window } from "happy-dom";
import { describe, expect, it, vi } from "vitest";
import { appStub } from "./helpers/copy.js";

const PROJECT_ROOT = path.resolve(import.meta.dirname, "..", "..");
// The catalogue moved out of the standalone products.html page and
// into a Products tab inside Settings -- the page that now
// hosts the controller under test.
const SETTINGS_PAGE = "pages/admin/settings.html";

function runClassicScript(window, relativePath) {
  const absolutePath = path.join(PROJECT_ROOT, relativePath);
  const source = readFileSync(absolutePath, "utf8");
  const context = window.__vmContext || (window.__vmContext = vm.createContext(window));
  vm.runInContext(source, context, { filename: absolutePath });
}

function productData() {
  return [
    { id: 1, name: "Wdrożenie ERP", code: "ERP-01", category: "Wdrożenie", base_price: 40000, offered: 1, active: 1, sort_order: 0 },
    { id: 2, name: "Licencja CRM", code: "CRM-12", category: "Licencja", base_price: 1200, offered: 1, active: 1, sort_order: 0 },
    { id: 4, name: "Pakiet wsparcia 24/7", code: "SUP-24", category: "Wsparcie", base_price: 9600, offered: 0, active: 1, sort_order: 0 },
  ];
}

const FIXTURE_HTML = `
  <div id="kpi-products-total">—</div>
  <div id="kpi-products-offered">—</div>
  <div id="kpi-products-hidden">—</div>
  <button id="add-product-btn" type="button">Dodaj produkt</button>
  <div class="table-container">
    <div class="text-xs text-tertiary" id="products-count-label">— produktów</div>
    <input type="text" id="products-search">
    <select id="products-filter-offered">
      <option value="">Wszystkie</option>
      <option value="1">Oferowane partnerom</option>
      <option value="0">Ukryte przed partnerami</option>
    </select>
    <div id="products-loading-state"></div>
    <div id="products-empty-state" class="empty-state" hidden></div>
    <div id="products-error-state" class="empty-state" hidden>
      <button id="products-retry-btn" type="button">Spróbuj ponownie</button>
    </div>
    <div id="products-table-wrapper" hidden>
      <table class="data-table"><tbody id="products-tbody"></tbody></table>
    </div>
  </div>
  <div class="modal-overlay" id="product-modal">
    <div class="modal">
      <div class="modal-title" id="product-modal-title">Nowy produkt</div>
      <button id="product-modal-close" type="button">x</button>
      <input type="hidden" id="modal-product-id">
      <input type="text" id="p-name">
      <input type="text" id="p-name-en">
      <input type="text" id="p-code">
      <select id="p-category">
        <option value="Wdrożenie">Wdrożenie</option>
        <option value="Licencja">Licencja</option>
        <option value="Usługa">Usługa</option>
        <option value="Wsparcie">Wsparcie</option>
      </select>
      <input type="number" id="p-price">
      <input type="checkbox" id="p-offered" checked>
      <div class="form-error" id="product-modal-error" hidden></div>
      <button id="product-cancel-btn" type="button">Anuluj</button>
      <button id="product-save-btn" type="button">Zapisz produkt</button>
    </div>
  </div>
`;

function createProductRealm({ products = productData(), getImpl = null } = {}) {
  const window = new Window({ url: `http://localhost:8000/${SETTINGS_PAGE}` });
  window.document.body.dataset.page = "admin-settings";
  window.document.body.innerHTML = FIXTURE_HTML;
  window.ApiClient = {
    get: vi.fn(getImpl || (() => Promise.resolve({ products, total: products.length }))),
    post: vi.fn().mockResolvedValue({ product: {} }),
    patch: vi.fn().mockResolvedValue({ product: {} }),
    delete: vi.fn().mockResolvedValue({ product: {} }),
  };
  window.Ui = {
    safeNavigate: vi.fn().mockReturnValue(true),
    // Mirrors the real js/utils.js contract: the reader's language when the
    // producer filled the EN column in, the Polish original otherwise. The
    // controller under test started calling this once the table began
    // reading a translated product name.
    producerLabel: (record, field) => {
      if (!record) return '';
      const base = String(record[field] ?? '');
      if (window.App.lang !== 'en') return base;
      const english = String(record[`${field}_en`] ?? '').trim();
      return english || base;
    },
  };
  window.formatCurrency = vi.fn((value) => `${value} PLN`);
  window.getInitials = vi.fn(() => "MK");
  window.handleApiError = vi.fn();
  window.showToast = vi.fn();
  // the controller reads its copy from the dictionary. onRerender captures
  // the callback (App.onRerender(() => renderProducts())) so a test can
  // fire a language switch without a real App.rerender() implementation --
  // a fresh Window/vm context per test, unlike partner.js's shared-realm
  // test file, so this is safe to do per test.
  const rerenderHooks = [];
  window.App = { ...appStub('pl'), onRerender: (fn) => rerenderHooks.push(fn) };
  window.triggerRerender = () => rerenderHooks.forEach((fn) => fn());
  window.openModal = vi.fn();
  window.closeModal = vi.fn();
  runClassicScript(window, "js/admin-products.js");
  return { window, products };
}

describe("Products — the vendor's catalogue", () => {
  it("renders one row per product with its code, category and price", async () => {
    const { window } = createProductRealm();
    await window.AdminProducts.init({ id: 1, role: "admin" });

    const rows = window.document.querySelectorAll("#products-tbody tr");
    expect(rows).toHaveLength(3);
    expect(rows[0].textContent).toContain("Wdrożenie ERP");
    expect(rows[0].textContent).toContain("ERP-01");
    expect(rows[0].textContent).toContain("40000 PLN");
  });

  it("counts how many products the partner can actually see", async () => {
    const { window } = createProductRealm();
    await window.AdminProducts.init({ id: 1, role: "admin" });

    expect(window.document.getElementById("kpi-products-total").textContent).toBe("3");
    expect(window.document.getElementById("kpi-products-offered").textContent).toBe("2");
    expect(window.document.getElementById("kpi-products-hidden").textContent).toBe("1");
  });

  it("says plainly that an empty catalogue blocks partners from submitting leads", async () => {
    const { window } = createProductRealm({ products: [] });
    await window.AdminProducts.init({ id: 1, role: "admin" });

    const empty = window.document.getElementById("products-empty-state");
    expect(empty.hidden).toBe(false);
    expect(window.document.getElementById("products-table-wrapper").hidden).toBe(true);
  });

  it("shows the error state and a retry when the catalogue cannot be loaded", async () => {
    const { window } = createProductRealm({
      getImpl: () => Promise.reject(new Error("Błąd serwera (500)")),
    });
    await window.AdminProducts.init({ id: 1, role: "admin" });

    expect(window.document.getElementById("products-error-state").hidden).toBe(false);
    expect(window.handleApiError).toHaveBeenCalled();
  });

  it("routes row actions through opaque keys, never a database id in the DOM", async () => {
    const { window } = createProductRealm();
    await window.AdminProducts.init({ id: 1, role: "admin" });

    const toggle = window.document.querySelector('[data-product-action="offered"]');
    expect(toggle.dataset.productKey).not.toContain("1");
    expect(window.document.querySelector("#products-tbody").innerHTML).not.toContain('data-id="1"');
  });

  it("flips the offered switch through PATCH and reloads from the server", async () => {
    const { window } = createProductRealm();
    await window.AdminProducts.init({ id: 1, role: "admin" });

    const toggle = window.document.querySelector('[data-product-action="offered"]');
    expect(toggle.textContent).toBe("Tak");
    toggle.click();

    await vi.waitFor(() =>
      expect(window.ApiClient.patch).toHaveBeenCalledWith("/api/products/1", { offered: 0 }),
    );
  });

  it("turns a hidden product back on rather than toggling blindly", async () => {
    const { window } = createProductRealm();
    await window.AdminProducts.init({ id: 1, role: "admin" });

    const toggles = window.document.querySelectorAll('[data-product-action="offered"]');
    const hidden = toggles[2];
    expect(hidden.textContent).toBe("Nie");
    hidden.click();

    await vi.waitFor(() =>
      expect(window.ApiClient.patch).toHaveBeenCalledWith("/api/products/4", { offered: 1 }),
    );
  });

  it("filters the table by search term without re-fetching", async () => {
    const { window } = createProductRealm();
    await window.AdminProducts.init({ id: 1, role: "admin" });
    const callsBefore = window.ApiClient.get.mock.calls.length;

    const search = window.document.getElementById("products-search");
    search.value = "CRM";
    search.dispatchEvent(new window.Event("input", { bubbles: true }));

    expect(window.document.querySelectorAll("#products-tbody tr")).toHaveLength(1);
    expect(window.ApiClient.get.mock.calls.length).toBe(callsBefore);
  });

  it("filters down to what the partner cannot see", async () => {
    const { window } = createProductRealm();
    await window.AdminProducts.init({ id: 1, role: "admin" });

    const filter = window.document.getElementById("products-filter-offered");
    filter.value = "0";
    filter.dispatchEvent(new window.Event("change", { bubbles: true }));

    const rows = window.document.querySelectorAll("#products-tbody tr");
    expect(rows).toHaveLength(1);
    expect(rows[0].textContent).toContain("Pakiet wsparcia 24/7");
  });

  it("posts a new product with the entered values", async () => {
    const { window } = createProductRealm();
    await window.AdminProducts.init({ id: 1, role: "admin" });

    window.document.getElementById("add-product-btn").click();
    window.document.getElementById("p-name").value = "Audyt bezpieczeństwa";
    window.document.getElementById("p-code").value = "SEC-03";
    window.document.getElementById("p-category").value = "Usługa";
    window.document.getElementById("p-price").value = "18000";
    window.document.getElementById("product-save-btn").click();

    await vi.waitFor(() => expect(window.ApiClient.post).toHaveBeenCalled());
    const [requestPath, body] = window.ApiClient.post.mock.calls[0];
    expect(requestPath).toBe("/api/products");
    expect(body).toMatchObject({
      name: "Audyt bezpieczeństwa",
      code: "SEC-03",
      category: "Usługa",
      base_price: 18000,
      offered: 1,
    });
  });

  it("sends an absent price as null, never as a misleading zero", async () => {
    const { window } = createProductRealm();
    await window.AdminProducts.init({ id: 1, role: "admin" });

    window.document.getElementById("add-product-btn").click();
    window.document.getElementById("p-name").value = "Konsultacja";
    window.document.getElementById("p-price").value = "";
    window.document.getElementById("product-save-btn").click();

    await vi.waitFor(() => expect(window.ApiClient.post).toHaveBeenCalled());
    expect(window.ApiClient.post.mock.calls[0][1].base_price).toBeNull();
  });

  it("refuses to save a product with no name and says why", async () => {
    const { window } = createProductRealm();
    await window.AdminProducts.init({ id: 1, role: "admin" });

    window.document.getElementById("add-product-btn").click();
    window.document.getElementById("p-name").value = "   ";
    window.document.getElementById("product-save-btn").click();

    expect(window.ApiClient.post).not.toHaveBeenCalled();
    const error = window.document.getElementById("product-modal-error");
    expect(error.hidden).toBe(false);
    expect(error.textContent.length).toBeGreaterThan(0);
  });

  it("keeps the modal open and shows the server reason when a save is rejected", async () => {
    const { window } = createProductRealm();
    await window.AdminProducts.init({ id: 1, role: "admin" });
    window.ApiClient.post.mockRejectedValue(new Error("Produkt o tej nazwie już istnieje."));

    window.document.getElementById("add-product-btn").click();
    window.document.getElementById("p-name").value = "Wdrożenie ERP";
    window.document.getElementById("product-save-btn").click();

    await vi.waitFor(() =>
      expect(window.document.getElementById("product-modal-error").textContent)
        .toBe("Produkt o tej nazwie już istnieje."),
    );
    expect(window.closeModal).not.toHaveBeenCalled();
    // The typed name must survive the failure so it can be corrected.
    expect(window.document.getElementById("p-name").value).toBe("Wdrożenie ERP");
  });

  it("patches the edited product rather than creating a second one", async () => {
    const { window } = createProductRealm();
    await window.AdminProducts.init({ id: 1, role: "admin" });

    window.document.querySelector('[data-product-action="edit"]').click();
    expect(window.document.getElementById("p-name").value).toBe("Wdrożenie ERP");
    window.document.getElementById("p-name").value = "Wdrożenie ERP Plus";
    window.document.getElementById("product-save-btn").click();

    await vi.waitFor(() => expect(window.ApiClient.patch).toHaveBeenCalled());
    expect(window.ApiClient.patch.mock.calls[0][0]).toBe("/api/products/1");
    expect(window.ApiClient.post).not.toHaveBeenCalled();
  });

  it("keeps the open edit modal's typed draft and category choice across a rerender", async () => {
    const { window } = createProductRealm();
    await window.AdminProducts.init({ id: 1, role: "admin" });

    window.document.querySelector('[data-product-action="edit"]').click();
    window.document.getElementById("p-name").value = "Wdrożenie ERP (w trakcie edycji)";
    window.document.getElementById("p-category").value = "Usługa";

    // App.rerender() only redraws the TABLE (App.onRerender(() =>
    // renderProducts())) -- it must never reach into an open modal and
    // overwrite what the admin is mid-typing.
    window.triggerRerender();

    expect(window.document.getElementById("p-name").value).toBe("Wdrożenie ERP (w trakcie edycji)");
    expect(window.document.getElementById("p-category").value).toBe("Usługa");
  });

  it("archives a product through DELETE", async () => {
    const { window } = createProductRealm();
    await window.AdminProducts.init({ id: 1, role: "admin" });

    window.document.querySelector('[data-product-action="archive"]').click();

    await vi.waitFor(() => expect(window.ApiClient.delete).toHaveBeenCalledWith("/api/products/1"));
  });

  it("shows the producer's English product name to an English-reading admin", async () => {
    const { window } = createProductRealm({
      products: [
        { id: 1, name: "Wdrożenie ERP", name_en: "ERP rollout", code: "ERP-01", category: "Wdrożenie", base_price: 40000, offered: 1, active: 1, sort_order: 0 },
        { id: 2, name: "Licencja CRM", name_en: "", code: "CRM-12", category: "Licencja", base_price: 1200, offered: 1, active: 1, sort_order: 0 },
      ],
    });
    window.App = appStub("en");

    await window.AdminProducts.init({ id: 1, role: "admin" });

    const rows = window.document.querySelectorAll("#products-tbody tr");
    expect(rows[0].textContent).toContain("ERP rollout");
    expect(rows[0].textContent).not.toContain("Wdrożenie ERP");
    // No English name yet -- the Polish original is the honest fallback,
    // never a blank cell.
    expect(rows[1].textContent).toContain("Licencja CRM");
  });

  it("edits and saves the product's English name alongside its Polish one", async () => {
    const { window } = createProductRealm({
      products: [{ id: 1, name: "Wdrożenie ERP", name_en: "ERP rollout", code: "ERP-01", category: "Wdrożenie", base_price: 40000, offered: 1, active: 1, sort_order: 0 }],
    });
    await window.AdminProducts.init({ id: 1, role: "admin" });

    window.document.querySelector('[data-product-action="edit"]').click();
    expect(window.document.getElementById("p-name-en").value).toBe("ERP rollout");
    window.document.getElementById("p-name-en").value = "ERP deployment";
    window.document.getElementById("product-save-btn").click();

    await vi.waitFor(() => expect(window.ApiClient.patch).toHaveBeenCalled());
    expect(window.ApiClient.patch.mock.calls[0][1]).toMatchObject({ name_en: "ERP deployment" });
  });

  it("renders a product name containing markup as literal text", async () => {
    const payload = '<img src=x onerror="window.__productsPwned=true">';
    const { window } = createProductRealm({
      products: [{ id: 9, name: payload, code: payload, category: "Usługa", base_price: 100, offered: 1, active: 1 }],
    });
    window.__productsPwned = false;

    await window.AdminProducts.init({ id: 1, role: "admin" });

    const row = window.document.querySelector("#products-tbody tr");
    expect(row.textContent).toContain(payload);
    expect(row.querySelector("img, script")).toBeNull();
    expect(window.__productsPwned).toBe(false);
  });
});

// The catalogue lives in Settings, not on a standalone page: the Settings page loads the catalogue controller
// script, the shell's page-controller map holds no catalogue entry, and the
// settings controller invokes the catalogue initialiser explicitly.
describe("Products — the page entry point (inside Settings)", () => {
  it("is no longer dispatched by the admin shell's page-controller map", () => {
    const source = readFileSync(path.join(PROJECT_ROOT, "js/admin-shell.js"), "utf8");
    expect(source).not.toContain("'admin-products'");
  });

  it("loads the catalogue controller script from the Settings page", () => {
    const html = readFileSync(path.join(PROJECT_ROOT, SETTINGS_PAGE), "utf8");
    expect(html).toContain('data-page="admin-settings"');
    expect(html).toContain('src="../../js/admin-products.js"');
    expect(html).toContain('src="../../js/admin-shell.js"');
  });

  it("is invoked explicitly by the settings controller, not by shell dispatch", () => {
    const source = readFileSync(path.join(PROJECT_ROOT, "js/settings.js"), "utf8");
    expect(source).toContain("AdminProducts.init");
  });
});
