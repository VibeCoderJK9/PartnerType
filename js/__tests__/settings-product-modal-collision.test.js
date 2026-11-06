import { readFileSync } from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { Window } from "happy-dom";
import { describe, expect, it, vi } from "vitest";
import { appStub } from "./helpers/copy.js";

// Closes the openModal collision deferred-items.md item 1
// recorded. pages/admin/settings.html loads js/admin-products.js and
// js/settings.js as classic scripts sharing one global scope; js/settings.js
// declared its own generic modal builder under the same global name
// js/app.js's page-level string-id opener owns, so the later declaration
// silently won and the catalogue controller's product-modal calls built a
// broken generic modal instead of showing the real one. This file loads the
// real page markup and all four scripts, in the page's own order, into ONE
// shared VM context -- the shape no existing test exercised -- and presses
// the controls the way a hand does.

const PROJECT_ROOT = path.resolve(import.meta.dirname, "..", "..");
const SETTINGS_PAGE_PATH = path.join(PROJECT_ROOT, "pages/admin/settings.html");

function runClassicScript(window, relativePath) {
  const absolutePath = path.join(PROJECT_ROOT, relativePath);
  const source = readFileSync(absolutePath, "utf8");
  const context = window.__vmContext || (window.__vmContext = vm.createContext(window));
  vm.runInContext(source, context, { filename: absolutePath });
}

// Read the real settings.html body markup rather than a hand-written
// fixture -- a fixture would reproduce the exact blind spot that let this
// bug ship (no test has ever exercised the real markup with both
// controllers present).
function realSettingsBodyMarkup() {
  const html = readFileSync(SETTINGS_PAGE_PATH, "utf8");
  const bodyMatch = html.match(/<body[^>]*>([\s\S]*)<\/body>/);
  if (!bodyMatch) throw new Error("could not locate <body>...</body> in settings.html");
  // Strip every classic <script> tag -- the four real scripts are loaded
  // separately below, into the shared VM context, in the page's own order.
  return bodyMatch[1].replace(/<script[^>]*>[\s\S]*?<\/script>/g, "");
}

function productData() {
  return [
    { id: 1, name: "Wdrożenie ERP", code: "ERP-01", category: "Wdrożenie", base_price: 40000, offered: 1, active: 1, sort_order: 0 },
    { id: 2, name: "Licencja CRM", code: "CRM-12", category: "Licencja", base_price: 1200, offered: 1, active: 1, sort_order: 0 },
    { id: 4, name: "Pakiet wsparcia 24/7", code: "SUP-24", category: "Wsparcie", base_price: 9600, offered: 0, active: 1, sort_order: 0 },
  ];
}

// Copied from js/__tests__/reported-regressions.test.js::pressLikeAMouse and
// extended with a leading pointer-down dispatch -- the real page's filter
// dropdowns and other document-level mousedown listeners have rebuilt a
// pressed control mid-press before on this page family. Never fall back to a
// bare click(): that omits the mouse-down entirely, which is exactly the
// failure mode this file exists to catch.
function pressLikeAMouse(window, control) {
  const rect = control.getBoundingClientRect();
  const init = {
    bubbles: true,
    cancelable: true,
    view: window,
    button: 0,
    clientX: Math.round(rect.left + rect.width / 2),
    clientY: Math.round(rect.top + rect.height / 2),
  };
  if (typeof window.PointerEvent === "function") {
    control.dispatchEvent(new window.PointerEvent("pointerdown", { ...init, pointerId: 1 }));
  } else {
    control.dispatchEvent(new window.MouseEvent("pointerdown", init));
  }
  control.dispatchEvent(new window.MouseEvent("mousedown", init));
  const survived = control.isConnected;
  control.dispatchEvent(new window.MouseEvent("mouseup", init));
  control.dispatchEvent(new window.MouseEvent("click", init));
  return survived;
}

function createRealm({ products = productData(), getImpl = null } = {}) {
  const window = new Window({ url: "http://localhost:8000/pages/admin/settings.html" });
  // Suppress BOTH files' automatic DOMContentLoaded initialisation (the same
  // suppression js/__tests__/settings-tabs.test.js uses) -- initialisation
  // is driven explicitly below via AdminProducts.init(), mirroring
  // js/__tests__/admin-products.test.js.
  window.document.body.dataset.page = "settings-modal-collision-test";
  window.document.body.innerHTML = realSettingsBodyMarkup();

  window.ApiClient = {
    get: vi.fn(getImpl || (() => Promise.resolve({ products, total: products.length }))),
    post: vi.fn().mockResolvedValue({ product: {} }),
    patch: vi.fn().mockResolvedValue({ product: {} }),
    delete: vi.fn().mockResolvedValue({ product: {} }),
  };
  window.App = appStub("pl");
  window.Ui = {
    escapeHtml: (value) => String(value ?? "")
      .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
      .replaceAll("'", "&#x27;").replaceAll('"', "&quot;"),
    // The catalogue table reads this for its name cell -- mirrors
    // the real js/utils.js contract closely enough for this collision test's
    // own purpose, which is the modal wiring, not translation.
    producerLabel: (record, field) => {
      if (!record) return "";
      const base = String(record[field] ?? "");
      if (window.App.lang !== "en") return base;
      const english = String(record[`${field}_en`] ?? "").trim();
      return english || base;
    },
  };
  window.showToast = vi.fn();
  window.handleApiError = vi.fn();
  window.confirm = vi.fn(() => true);

  // The page's own order: icons, then app (owns the page-level string-id
  // modal opener), then the catalogue controller, then settings.js (whose
  // generic builder is the shadowing declaration this plan renames).
  runClassicScript(window, "js/icons.js");
  runClassicScript(window, "js/app.js");
  runClassicScript(window, "js/admin-products.js");
  runClassicScript(window, "js/settings.js");

  return { window, products };
}

async function initCatalogue(window, user = { id: 1, role: "admin" }) {
  await window.AdminProducts.init(user);
}

describe("settings + catalogue on one page: the openModal collision", () => {
  it("pressing Add product gives #product-modal the show class and leaves the control connected", async () => {
    const { window } = createRealm();
    await initCatalogue(window);

    const addBtn = window.document.getElementById("add-product-btn");
    const survived = pressLikeAMouse(window, addBtn);

    expect(survived).toBe(true);
    expect(window.document.getElementById("product-modal").classList.contains("show")).toBe(true);
  });

  it("creates no #generic-modal-overlay when the add control is pressed", async () => {
    const { window } = createRealm();
    await initCatalogue(window);

    pressLikeAMouse(window, window.document.getElementById("add-product-btn"));

    expect(window.document.getElementById("generic-modal-overlay")).toBeNull();
  });

  it("sets the product modal title to the Polish new-product heading after the add press", async () => {
    const { window } = createRealm();
    await initCatalogue(window);

    pressLikeAMouse(window, window.document.getElementById("add-product-btn"));

    expect(window.document.getElementById("product-modal-title").textContent).toBe("Nowy produkt");
  });

  it("the surviving global string-id opener shows #product-modal when called directly", async () => {
    const { window } = createRealm();
    await initCatalogue(window);

    window.openModal("product-modal");

    expect(window.document.getElementById("product-modal").classList.contains("show")).toBe(true);
  });

  it("the generic tier/bonus/stage modal builder is still reachable under its new name", async () => {
    const { window } = createRealm();
    await initCatalogue(window);

    window.openGenericModal({ title: "Test", fields: [] });

    expect(window.document.getElementById("generic-modal-overlay")).not.toBeNull();
  });

  it("pressing a row's edit control opens the same product modal with the edit title", async () => {
    const { window } = createRealm();
    await initCatalogue(window);

    const editBtn = window.document.querySelector('[data-product-action="edit"]');
    const survived = pressLikeAMouse(window, editBtn);

    expect(survived).toBe(true);
    expect(window.document.getElementById("product-modal").classList.contains("show")).toBe(true);
    expect(window.document.getElementById("product-modal-title").textContent).toBe("Edytuj produkt");
  });

  it("pressing Add product twice leaves exactly one modal overlay showing and no leftover generic overlay", async () => {
    const { window } = createRealm();
    await initCatalogue(window);

    const addBtn = window.document.getElementById("add-product-btn");
    pressLikeAMouse(window, addBtn);
    pressLikeAMouse(window, addBtn);

    const shown = window.document.querySelectorAll(".modal-overlay.show");
    expect(shown).toHaveLength(1);
    expect(shown[0].id).toBe("product-modal");
    expect(window.document.getElementById("generic-modal-overlay")).toBeNull();
  });

  it("with an empty catalogue, Add product still opens the product modal", async () => {
    const { window } = createRealm({
      products: [],
      getImpl: () => Promise.resolve({ products: [], total: 0 }),
    });
    await initCatalogue(window);

    pressLikeAMouse(window, window.document.getElementById("add-product-btn"));

    expect(window.document.getElementById("product-modal").classList.contains("show")).toBe(true);
  });

  it("pressing the product modal's cancel control removes the show class", async () => {
    const { window } = createRealm();
    await initCatalogue(window);

    pressLikeAMouse(window, window.document.getElementById("add-product-btn"));
    expect(window.document.getElementById("product-modal").classList.contains("show")).toBe(true);

    pressLikeAMouse(window, window.document.getElementById("product-cancel-btn"));

    expect(window.document.getElementById("product-modal").classList.contains("show")).toBe(false);
  });

  it("carries no team markup or team opener any more, so that collision surface is gone from this page", async () => {
    // The producer's team lives on pages/admin/users.html. The coexistence
    // guard for the two modals that now share pages/admin/partners.html
    // lives in admin-partners.test.js. Re-adding a team modal here would
    // reopen the shadowing risk this whole file exists to pin, so its
    // absence is asserted rather than left implicit.
    const { window } = createRealm();
    await initCatalogue(window);

    expect(window.document.getElementById("team-invite-modal")).toBeNull();
    expect(window.document.getElementById("tab-team")).toBeNull();
    expect(typeof window.openTeamInviteModal).toBe("undefined");
    // The product modal still opens on its own, which is the property the
    // original collision broke.
    pressLikeAMouse(window, window.document.getElementById("add-product-btn"));
    expect(window.document.getElementById("product-modal").classList.contains("show")).toBe(true);
  });
});
