/**
 * PartnerType -- The manager (Moderator) role, browser half of the tracer.
 *
 * Follows the isolated-realm classic-script VM harness used by
 * js/__tests__/admin-shell.test.js and js/__tests__/settings-tabs.test.js:
 * every test gets its own happy-dom Window, with the real classic scripts
 * executed into it via vm.runInContext so top-level declarations (functions,
 * IIFE-attached namespaces) behave exactly as they do in the browser.
 *
 * The role-tab interaction is pressed with the full mousedown/mouseup/click
 * sequence from js/__tests__/reported-regressions.test.js's pressLikeAMouse
 * helper, never a bare click dispatch -- a scripted click alone is not
 * evidence a control survives a human mouse press.
 */
import { readFileSync } from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { Window } from "happy-dom";
import { describe, expect, it, vi } from "vitest";
import { appStub } from "./helpers/copy.js";

const PROJECT_ROOT = path.resolve(import.meta.dirname, "..", "..");
const SETTINGS_HTML_PATH = path.join(PROJECT_ROOT, "pages/admin/settings.html");

function runClassicScript(window, relativePath) {
  const absolutePath = path.join(PROJECT_ROOT, relativePath);
  const source = readFileSync(absolutePath, "utf8");
  const context = window.__vmContext || (window.__vmContext = vm.createContext(window));
  vm.runInContext(source, context, { filename: absolutePath });
}

function invoke(window, source) {
  return vm.runInContext(source, window.__vmContext);
}

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

// ─── index.html + auth.js: the third role tab and its redirect target ─────

function loginRealm() {
  const window = new Window({ url: "http://localhost:8000/index.html" });
  window.document.body.innerHTML = `
    <div class="role-tabs">
      <button class="role-tab active" data-role="producent" id="tab-producent">
        <span class="role-tab-label" data-i18n="tab_producer">Producent</span>
      </button>
      <button class="role-tab" data-role="partner" id="tab-partner">
        <span class="role-tab-label" data-i18n="tab_partner">Partner</span>
      </button>
    </div>
    <form id="auth-form">
      <input id="auth-email" type="email" />
      <div class="password-field">
        <input id="auth-password" type="password" />
        <button type="button" id="toggle-password"></button>
      </div>
      <div id="auth-error"><span data-i18n="login_error"></span></div>
      <button type="submit" id="login-btn">Zaloguj się</button>
      <button type="button" id="demo-fill-btn"></button>
      <span id="demo-email"></span>
      <span id="demo-pass"></span>
    </form>
  `;
  window.ApiClient = { post: vi.fn().mockResolvedValue({}) };
  window.App = { applyTranslations: vi.fn(), t: vi.fn((key) => key) };
  runClassicScript(window, "js/auth.js");
  return window;
}

describe("index.html — two login doors, no channel-manager tab", () => {
  // The separate channel-manager door is gone. A
  // moderator is a person the producer invited into their own account
  // (pages/admin/users.html, role=manager), so they sign in through the Vendor door
  // like the account owner does. The server maps the door to the roles it admits
  // (api/auth.py::LOGIN_DOORS) — the form never asks a user to name their
  // own staff role.
  it("offers exactly the Vendor and Partner doors, and no tab named after a staff role", () => {
    const window = loginRealm();

    const tabs = [...window.document.querySelectorAll(".role-tab")];
    expect(tabs.map((tab) => tab.dataset.role)).toEqual(["producent", "partner"]);
    expect(window.document.getElementById("tab-manager")).toBeNull();
    expect(
      window.document.getElementById("tab-producent").querySelector(".role-tab-label").textContent,
    ).toBe("Producent");
  });

  it("survives a real mouse press (mousedown -> mouseup -> click) and becomes the active door", () => {
    const window = loginRealm();
    invoke(window, "setupRoleTabs()");

    const partnerTab = window.document.getElementById("tab-partner");
    const producerTab = window.document.getElementById("tab-producent");
    const survivedMousedown = pressLikeAMouse(window, partnerTab);

    expect(survivedMousedown).toBe(true);
    expect(partnerTab.classList.contains("active")).toBe(true);
    expect(producerTab.classList.contains("active")).toBe(false);
  });

  it("sends the vendor door ('producent'), not a role name, in the login payload", async () => {
    const window = loginRealm();
    invoke(window, "setupRoleTabs()");
    invoke(window, "setupForm()");
    window.ApiClient.post.mockResolvedValue({ user: { id: 1, role: "manager" } });
    window.document.getElementById("auth-email").value = "moderator@firma.pl";
    window.document.getElementById("auth-password").value = "haslo1234";

    window.document
      .getElementById("auth-form")
      .dispatchEvent(new window.Event("submit", { cancelable: true, bubbles: true }));
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(window.ApiClient.post).toHaveBeenCalledWith("/api/auth/login", {
      email: "moderator@firma.pl",
      password: "haslo1234",
      role: "producent",
    });
  });
});

describe("redirectAfterLogin", () => {
  it("sends a manager to the admin dashboard, same as admin", () => {
    const window = loginRealm();

    invoke(window, "redirectAfterLogin('manager')");

    expect(window.location.href).toContain("pages/admin/dashboard.html");
  });

  it("sends an observer to the admin dashboard too — the shell greys their surfaces out, the login does not bounce them", () => {
    const window = loginRealm();

    invoke(window, "redirectAfterLogin('observer')");

    expect(window.location.href).toContain("pages/admin/dashboard.html");
  });

  it("still sends a partner to the partner dashboard", () => {
    const window = loginRealm();

    invoke(window, "redirectAfterLogin('partner')");

    expect(window.location.href).toContain("pages/partner/dashboard.html");
  });
});

// ─── admin-shell.js: the whole-app gate admits staff, refuses partner ──────

function createShellRealm({ page = "admin-dashboard", user } = {}) {
  const window = new Window({ url: "http://localhost:8000/pages/admin/dashboard.html" });
  window.document.body.dataset.page = page;
  window.document.body.innerHTML = `
    <aside class="sidebar"></aside>
    <div id="sidebar-user-name"></div>
    <div id="sidebar-user-role"></div>
    <div id="sidebar-avatar"></div>
    <div id="header-avatar"></div>
    <button id="mobile-menu-btn"></button>
    <button id="logout-btn"></button>
    <a class="nav-item" href="dashboard.html"></a>
  `;

  window.ApiClient = {
    get: vi.fn().mockResolvedValue({ user }),
    post: vi.fn().mockResolvedValue({}),
    clearToken: vi.fn(),
  };
  window.App = appStub("pl");
  window.getInitials = vi.fn((name) => name.split(/\s+/).map((part) => part[0]).join(""));
  window.AdminDashboard = { init: vi.fn().mockResolvedValue(undefined) };
  window.AdminLeads = { init: vi.fn().mockResolvedValue(undefined) };
  window.AdminPartners = { init: vi.fn().mockResolvedValue(undefined) };

  runClassicScript(window, "js/utils.js");
  window.Ui.safeNavigate = vi.fn().mockReturnValue(true);
  runClassicScript(window, "js/admin-shell.js");
  return window;
}

describe("AdminShell.init() — the widened staff gate", () => {
  it("admits a rehydrated manager identity without navigating to the login screen, and initialises the sidebar", async () => {
    const window = createShellRealm({
      user: { id: 11, role: "manager", name: "Test Opiekun", company: "PartnerType" },
    });

    const user = await window.AdminShell.init();

    expect(window.Ui.safeNavigate).not.toHaveBeenCalled();
    expect(user).toEqual(expect.objectContaining({ role: "manager" }));
    expect(window.document.getElementById("sidebar-user-name").textContent).toBe("Test Opiekun");
  });

  it("still navigates a rehydrated partner identity to the login screen", async () => {
    const window = createShellRealm({
      user: { id: 12, role: "partner", name: "Test Partner" },
    });

    const user = await window.AdminShell.init();

    expect(window.Ui.safeNavigate).toHaveBeenCalledWith("../../index.html");
    expect(user).toBeNull();
  });
});

// ─── settings.js: the page itself refuses a manager ────────────────

function settingsRealm(user) {
  const window = new Window({ url: "http://localhost/pages/admin/settings.html" });
  window.document.body.dataset.page = "not-auto-init"; // drive initSettingsPage explicitly below

  const html = readFileSync(SETTINGS_HTML_PATH, "utf8");
  const bodyMatch = html.match(/<body[^>]*>([\s\S]*)<\/body>/);
  const bodyInner = bodyMatch[1].replace(/<script[\s\S]*?<\/script>/g, "");
  window.document.body.innerHTML = bodyInner;

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
    safeNavigate: vi.fn().mockReturnValue(true),
  };
  window.AdminShell = { init: vi.fn().mockResolvedValue(user) };
  window.showToast = vi.fn();
  window.handleApiError = vi.fn();
  window.confirm = vi.fn(() => true);

  runClassicScript(window, "js/icons.js");
  runClassicScript(window, "js/settings.js");
  return window;
}

describe("initSettingsPage() — admits a moderator, refuses an observer", () => {
  it("admits a manager identity and loads the settings", async () => {
    // The moderator gets everything except inviting users and reading logs.
    // The two things withheld
    // from the role — the audit log and user management — are asserted in
    // api/tests/integration/test_manager_role.py, and neither lives on this
    // page.
    const window = settingsRealm({ id: 21, role: "manager", name: "Test Moderator" });

    await invoke(window, "initSettingsPage()");

    expect(window.Ui.safeNavigate).not.toHaveBeenCalled();
    expect(window.ApiClient.get).toHaveBeenCalledWith("/api/settings");
  });

  it("navigates an observer identity away and issues no /api/settings request", async () => {
    const window = settingsRealm({ id: 22, role: "observer", name: "Test Obserwator" });

    await invoke(window, "initSettingsPage()");

    expect(window.Ui.safeNavigate).toHaveBeenCalledWith("dashboard.html");
    expect(window.ApiClient.get).not.toHaveBeenCalledWith("/api/settings");
  });

  it("navigates away when AdminShell resolves no identity at all", async () => {
    const window = settingsRealm(null);

    await invoke(window, "initSettingsPage()");

    expect(window.Ui.safeNavigate).toHaveBeenCalledWith("dashboard.html");
    expect(window.ApiClient.get).not.toHaveBeenCalledWith("/api/settings");
  });

  it("behaves exactly as before for an admin identity", async () => {
    const window = settingsRealm({ id: 1, role: "admin", name: "Test Admin" });

    await invoke(window, "initSettingsPage()");

    expect(window.Ui.safeNavigate).not.toHaveBeenCalled();
    expect(window.ApiClient.get).toHaveBeenCalledWith("/api/settings");
  });
});
