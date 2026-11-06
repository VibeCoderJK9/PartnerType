import { readFileSync } from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { Window } from "happy-dom";
import { describe, expect, it, vi } from "vitest";
import { appStub } from "./helpers/copy.js";

const PROJECT_ROOT = path.resolve(import.meta.dirname, "..", "..");
const SETTINGS_HTML_PATH = path.join(PROJECT_ROOT, "pages/admin/settings.html");

function runClassicScript(window, relativePath) {
  const source = readFileSync(path.join(PROJECT_ROOT, relativePath), "utf8");
  const context = window.__vmContext || (window.__vmContext = vm.createContext(window));
  vm.runInContext(source, context, { filename: relativePath });
}

function invoke(window, source) {
  return vm.runInContext(source, window.__vmContext);
}

// The tabs and their expected label + icon, in order. Products is the
// seventh tab. The producer's team is not a tab: it lives on
// pages/admin/users.html, under the profile block in the left menu --- see
// js/__tests__/users-team.test.js.
const TABS = [
  { tab: "tab-general", label: "Ogólne", icon: "settings" },
  { tab: "tab-leads", label: "Konfiguracja leadów", icon: "clipboard-list" },
  { tab: "tab-duplicates", label: "Kolejkowanie duplikatów", icon: "shuffle" },
  { tab: "tab-stages", label: "Etapy leada", icon: "arrow-right" },
  { tab: "tab-tiers", label: "Poziomy (Tiery)", icon: "users" },
  { tab: "tab-bonuses", label: "Baza bonusów", icon: "gift" },
  { tab: "tab-registration", label: "Formularz rejestracji", icon: "user-plus" },
  { tab: "tab-products", label: "Produkty", icon: "package" },
];

// Every id read by populateSettings and saveSettings (js/settings.js). These
// controls live on several tabs; a duplicated id makes
// getElementById silently resolve to the wrong node and a dropped id makes
// the save button silently stop saving that field.
const SETTINGS_CONTROL_IDS = [
  "s-company-name", "s-company-email", "s-company-url",
  "s-currency", "s-auto-commission", "s-action-days",
  "s-register-link", "s-login-link", "save-all-btn",
];

// Every id the catalogue controller (js/admin-products.js) reads, in the
// Products pane. Checked the same way
// as SETTINGS_CONTROL_IDS above -- a dropped or duplicated id here is a
// silent breakage of the catalogue, not just a settings field.
const PRODUCTS_CONTROL_IDS = [
  "add-product-btn", "kpi-products-total", "kpi-products-offered", "kpi-products-hidden",
  "products-count-label", "products-search", "products-filter-offered",
  "products-loading-state", "products-empty-state", "products-error-state",
  "products-retry-btn", "products-table-wrapper", "products-tbody",
  "product-modal", "product-modal-title", "product-modal-close", "product-modal-error",
  "product-save-btn", "product-cancel-btn", "modal-product-id",
  "p-name", "p-code", "p-category", "p-price", "p-offered",
];

function tabsWindow() {
  const window = new Window({ url: "http://localhost/pages/admin/settings.html" });
  window.ApiClient = {
    get: vi.fn().mockResolvedValue({ settings: {} }),
    post: vi.fn().mockResolvedValue({}),
    patch: vi.fn().mockResolvedValue({ settings: {} }),
    delete: vi.fn().mockResolvedValue({}),
  };
  window.App = appStub("pl");
  window.Ui = {
    escapeHtml: (value) => String(value ?? "")
      .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
      .replaceAll("'", "&#x27;").replaceAll('"', "&quot;"),
  };
  window.showToast = vi.fn();
  window.handleApiError = vi.fn();
  window.confirm = vi.fn(() => true);
  window.document.body.dataset.page = "not-auto-init"; // avoid auto initSettingsPage() side effects
  window.document.body.innerHTML = `
    <div class="settings-tiles">
      ${TABS.map((t, i) => `<button class="settings-tile${i === 0 ? ' active' : ''}" data-tab="${t.tab}" data-icon="${t.icon}"><span class="settings-tile-label">${t.label}</span><span class="settings-tile-sub">co jest w środku</span></button>`).join("\n")}
    </div>
    ${TABS.map((t, i) => `<div id="${t.tab}" class="tab-pane${i === 0 ? ' active' : ''}"></div>`).join("\n")}
  `;
  runClassicScript(window, "js/icons.js");
  runClassicScript(window, "js/settings.js");
  return window;
}

function tabButtons(window) {
  return Array.from(window.document.querySelectorAll(".settings-tile"));
}

describe("settings tabs — icon rendering", () => {
  it("gives every tile exactly one svg while keeping its label and its subtitle", () => {
    const window = tabsWindow();

    invoke(window, "renderTabIcons()");

    const buttons = tabButtons(window);
    expect(buttons).toHaveLength(TABS.length);
    buttons.forEach((btn, i) => {
      expect(btn.querySelectorAll("svg")).toHaveLength(1);
      // The icon goes INSIDE the label line, and the subtitle survives. The
      // previous renderer used Icons.label, which empties the target and
      // rebuilds it as icon + one text node -- that would delete the subtitle,
      // which is the entire reason these became tiles.
      expect(btn.querySelector(".settings-tile-label").textContent.trim())
        .toBe(TABS[i].label);
      expect(btn.querySelector(".settings-tile-sub").textContent).toBe("co jest w środku");
    });
  });

  it("does not stack a second icon when the tiles are rendered twice", () => {
    const window = tabsWindow();

    invoke(window, "renderTabIcons()");
    invoke(window, "renderTabIcons()");

    tabButtons(window).forEach((btn) => {
      expect(btn.querySelectorAll("svg")).toHaveLength(1);
    });
  });

  it("only ever asks Icons for a name it recognizes, so a renamed icon fails here rather than logging into a dead button", () => {
    const window = tabsWindow();
    const dataIconValues = tabButtons(window).map((btn) => btn.dataset.icon);

    dataIconValues.forEach((name) => {
      expect(invoke(window, `Icons.has(${JSON.stringify(name)})`)).toBe(true);
    });
  });

  it("renders the person icon on the Tiers tab, per the owner's request for a little-person icon", () => {
    const window = tabsWindow();

    invoke(window, "renderTabIcons()");

    const tiersBtn = window.document.querySelector('.settings-tile[data-tab="tab-tiers"]');
    const svg = tiersBtn.querySelector("svg");
    expect(svg.getAttribute("class")).toContain("icon-users");
  });
});

describe("settings tabs — click behavior", () => {
  it("leaves exactly one active tab and one active pane, matching the clicked button's data-tab", () => {
    const window = tabsWindow();
    invoke(window, "renderTabIcons()");
    invoke(window, "setupTabs()");

    const duplicatesBtn = window.document.querySelector('.settings-tile[data-tab="tab-duplicates"]');
    duplicatesBtn.dispatchEvent(new window.Event("click", { bubbles: true }));

    const activeTabs = window.document.querySelectorAll(".settings-tile.active");
    const activePanes = window.document.querySelectorAll(".tab-pane.active");
    expect(activeTabs).toHaveLength(1);
    expect(activePanes).toHaveLength(1);
    expect(activeTabs[0].dataset.tab).toBe("tab-duplicates");
    expect(activePanes[0].id).toBe("tab-duplicates");
  });
});

describe("settings tabs — no control lost or duplicated by the restructure", () => {
  it("resolves every id populateSettings and saveSettings read to exactly one element in the real markup", () => {
    const html = readFileSync(SETTINGS_HTML_PATH, "utf8");

    SETTINGS_CONTROL_IDS.forEach((id) => {
      const matches = html.match(new RegExp(`id="${id}"`, "g")) || [];
      expect(matches, `expected exactly one id="${id}" in pages/admin/settings.html`).toHaveLength(1);
    });
  });

  it("resolves every id the catalogue controller reads to exactly one element in the real markup", () => {
    const html = readFileSync(SETTINGS_HTML_PATH, "utf8");

    PRODUCTS_CONTROL_IDS.forEach((id) => {
      const matches = html.match(new RegExp(`id="${id}"`, "g")) || [];
      expect(matches, `expected exactly one id="${id}" in pages/admin/settings.html`).toHaveLength(1);
    });
  });

});

// The full pointerdown -> mousedown -> mouseup -> click sequence, copied from
// js/__tests__/reported-regressions.test.js::pressLikeAMouse (not exported,
// not reimplemented from scratch) -- a scripted click() alone is not evidence
// a control works under a real hand.
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

describe("settings tabs — Products tab under a real mouse press", () => {
  it("survives mousedown and activates the Products pane", () => {
    const window = tabsWindow();
    invoke(window, "renderTabIcons()");
    invoke(window, "setupTabs()");

    const productsBtn = window.document.querySelector('.settings-tile[data-tab="tab-products"]');
    const survivedMousedown = pressLikeAMouse(window, productsBtn);

    expect(survivedMousedown).toBe(true);
    const activeTabs = window.document.querySelectorAll(".settings-tile.active");
    const activePanes = window.document.querySelectorAll(".tab-pane.active");
    expect(activeTabs).toHaveLength(1);
    expect(activePanes).toHaveLength(1);
    expect(activeTabs[0].dataset.tab).toBe("tab-products");
    expect(activePanes[0].id).toBe("tab-products");
  });
});

// ─── End-to-end: the restructured screen still loads, saves and renders ─────
// Builds the fixture from the REAL six-pane markup (not a synthetic mock),
// so the two save buttons living on two different tabs are exercised exactly
// as the browser would exercise them: a control that went missing
// would either come up empty here or vanish from a save body.

const SETTINGS_RESPONSE = {
  company_name: "Acme Sp. z o.o.",
  company_email: "kontakt@acme.pl",
  company_url: "https://acme.pl",
  currency: "EUR",
  lead_auto_commission: "1",
  lead_action_days: "5",
  lead_duplicate_policy: "reject",
  lead_queue_shares: "100,50,0",
  lead_queue_max: "3",
};

// The exact key set saveSettings() sends (js/settings.js). No mail server
// keys: mail is the operator's, not the producer's.
const EXPECTED_SAVE_ALL_KEYS = [
  "company_name", "company_email", "company_url", "currency",
  "lead_auto_commission", "lead_action_days",
  // The security panel: the producer's switch for
  // requiring a second factor from moderators and observers.
  "mfa_required_for_staff",
  // The address of the producer's own partner-programme
  // terms, linked from the partner sign-up form.
  "partner_terms_url",
].sort();

function fullSettingsWindow() {
  const window = new Window({ url: "http://localhost/pages/admin/settings.html" });
  window.ApiClient = {
    get: vi.fn((url) => {
      if (url === "/api/settings") return Promise.resolve({ settings: SETTINGS_RESPONSE });
      if (url === "/api/tiers") return Promise.resolve({ tiers: [] });
      if (url === "/api/bonus-defs") return Promise.resolve({ bonus_defs: [] });
      if (url === "/api/lead-stages") return Promise.resolve({ stages: [] });
      if (url === "/api/partner-bonuses") return Promise.resolve({ partners: [] });
      return Promise.resolve({});
    }),
    post: vi.fn().mockResolvedValue({}),
    patch: vi.fn().mockResolvedValue({ settings: SETTINGS_RESPONSE }),
    delete: vi.fn().mockResolvedValue({}),
  };
  window.App = appStub("pl");
  window.Ui = {
    escapeHtml: (value) => String(value ?? "")
      .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
      .replaceAll("'", "&#x27;").replaceAll('"', "&quot;"),
    safeNavigate: vi.fn().mockReturnValue(true),
  };
  window.AdminShell = {
    init: vi.fn().mockResolvedValue({ id: 1, role: "admin", name: "Test Admin" }),
  };
  window.showToast = vi.fn();
  window.handleApiError = vi.fn();
  window.confirm = vi.fn(() => true);
  window.document.body.dataset.page = "not-auto-init"; // drive initSettingsPage explicitly below

  const html = readFileSync(SETTINGS_HTML_PATH, "utf8");
  const bodyMatch = html.match(/<body[^>]*>([\s\S]*)<\/body>/);
  const bodyInner = bodyMatch[1].replace(/<script[\s\S]*?<\/script>/g, "");
  window.document.body.innerHTML = bodyInner;

  runClassicScript(window, "js/icons.js");
  runClassicScript(window, "js/settings.js");
  return window;
}

describe("settings screen — end to end after the restructure", () => {
  it("fills every company field across tabs, shows the partner links, saves the header button's full body, and saves the queue button's own three keys separately", async () => {
    const window = fullSettingsWindow();

    await invoke(window, "initSettingsPage()");

    // Company data (tab-general, left) and the partner links (right column).
    expect(window.document.getElementById("s-company-name").value).toBe(SETTINGS_RESPONSE.company_name);
    expect(window.document.getElementById("s-company-email").value).toBe(SETTINGS_RESPONSE.company_email);
    expect(window.document.getElementById("s-company-url").value).toBe(SETTINGS_RESPONSE.company_url);
    expect(window.document.getElementById("s-currency").value).toBe(SETTINGS_RESPONSE.currency);
    expect(window.document.getElementById("s-register-link").value).toBe("http://localhost/register.html");
    expect(window.document.getElementById("s-login-link").value).toBe("http://localhost/");
    expect(window.document.querySelector('[id^="s-smtp"]')).toBeNull();
    // Lead configuration moved from tab-general to its own tab-leads pane.
    expect(window.document.getElementById("s-auto-commission").checked).toBe(true);
    expect(window.document.getElementById("s-action-days").value).toBe(SETTINGS_RESPONSE.lead_action_days);
    // Duplicate queueing moved from tab-general to its own tab-duplicates pane.
    expect(window.document.getElementById("s-queue-policy").value).toBe(SETTINGS_RESPONSE.lead_duplicate_policy);
    expect(window.document.getElementById("s-queue-max").value).toBe(SETTINGS_RESPONSE.lead_queue_max);

    // Pressing the header save button issues exactly one PATCH carrying every
    // key the pre-restructure single-pane version sent -- verified key by key.
    window.ApiClient.patch.mockClear();
    await invoke(window, "saveSettings()");

    expect(window.ApiClient.patch).toHaveBeenCalledTimes(1);
    const [saveAllUrl, saveAllBody] = window.ApiClient.patch.mock.calls[0];
    expect(saveAllUrl).toBe("/api/settings");
    expect(Object.keys(saveAllBody).sort()).toEqual(EXPECTED_SAVE_ALL_KEYS);
    EXPECTED_SAVE_ALL_KEYS.forEach((key) => {
      expect(saveAllBody).toHaveProperty(key);
    });

    // Pressing the queue pane's own save button issues a SEPARATE PATCH
    // carrying only the three queue keys -- the real risk of two save
    // buttons now living on two different tabs is one silently sending a
    // partial body instead of its own scoped one.
    window.ApiClient.patch.mockClear();
    await invoke(window, "saveQueueSettings()");

    expect(window.ApiClient.patch).toHaveBeenCalledTimes(1);
    const [queueUrl, queueBody] = window.ApiClient.patch.mock.calls[0];
    expect(queueUrl).toBe("/api/settings");
    expect(Object.keys(queueBody).sort()).toEqual(["lead_duplicate_policy", "lead_queue_max", "lead_queue_shares"]);
  });
});

describe("settings — Pluginy sidebar link and logo reader", () => {
  it("lights the Plugins link while the plugins tab is open and Settings otherwise", () => {
    const window = tabsWindow();
    const nav = window.document.createElement("nav");
    nav.className = "sidebar-nav";
    nav.innerHTML = '<a class="nav-item" href="settings.html"></a><a class="nav-item" href="settings.html#plugins"></a>';
    window.document.body.appendChild(nav);
    const [settingsLink, pluginsLink] = nav.querySelectorAll(".nav-item");

    invoke(window, "syncPluginsNav(true)");
    expect(pluginsLink.classList.contains("active")).toBe(true);
    expect(settingsLink.classList.contains("active")).toBe(false);
    expect(window.location.hash).toBe("#plugins");

    invoke(window, "syncPluginsNav(false)");
    expect(pluginsLink.classList.contains("active")).toBe(false);
    expect(settingsLink.classList.contains("active")).toBe(true);
  });

  it("reads an uploaded logo as a data URL", async () => {
    const window = tabsWindow();
    const file = new window.File(["abc"], "logo.png", { type: "image/png" });
    window.__file = file;
    const result = await invoke(window, "readAsDataUrl(__file)");
    expect(result).toMatch(/^data:image\/png;base64,/);
  });
});
