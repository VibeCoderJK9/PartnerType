import { existsSync, readFileSync } from "node:fs";
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
}

function createShellRealm({ page = "admin-dashboard", user } = {}) {
  const window = new Window({ url: "http://localhost:8000/pages/admin/dashboard.html" });
  window.document.body.dataset.page = page;
  window.document.body.innerHTML = `
    <aside class="sidebar"></aside>
    <div class="main-area"><header class="top-header"></header></div>
    <div id="sidebar-user-name"></div>
    <div id="sidebar-user-role"></div>
    <div id="sidebar-avatar"></div>
    <div id="header-avatar"></div>
    <button id="mobile-menu-btn"></button>
    <button id="logout-btn"></button>
    <a class="nav-item" href="dashboard.html"></a>
    <a class="nav-item" href="analytics.html"></a>
    <a class="nav-item" href="leads.html"></a>
    <a class="nav-item" href="partners.html"></a>
    <a class="nav-item" href="commissions.html"></a>
    <a class="nav-item" href="audit.html"></a>
    <a class="nav-item" href="settings.html"></a>
    <a class="nav-item" href="profile.html"></a>
    <a class="nav-item" href="users.html"></a>
  `;

  const authoritativeUser = user === undefined
    ? { id: 7, role: "admin", name: "Ada Lovelace", company: "Analytical Engines" }
    : user;
  window.ApiClient = {
    get: vi.fn().mockResolvedValue({ user: authoritativeUser }),
    post: vi.fn().mockResolvedValue({}),
    clearToken: vi.fn(),
  };
  window.App = { ...appStub('pl'), applyTranslations: vi.fn() };
  window.getInitials = vi.fn((name) => name.split(/\s+/).map((part) => part[0]).join(""));
  window.AdminDashboard = { init: vi.fn().mockResolvedValue(undefined) };
  window.AdminLeads = { init: vi.fn().mockResolvedValue(undefined) };
  window.AdminPartners = { init: vi.fn().mockResolvedValue(undefined) };

  runClassicScript(window, "js/utils.js");
  window.Ui.safeNavigate = vi.fn().mockReturnValue(true);
  runClassicScript(window, "js/admin-shell.js");
  return window;
}

describe("AdminShell", () => {
  it("uses server identity instead of a cached admin role", async () => {
    const window = createShellRealm({ user: { id: 9, role: "partner", name: "Mallory" } });
    window.sessionStorage.setItem("ph_session", JSON.stringify({ role: "admin", name: "Cached Admin" }));

    await window.AdminShell.init();

    expect(window.ApiClient.get).toHaveBeenCalledWith("/api/auth/me");
    expect(window.Ui.safeNavigate).toHaveBeenCalledWith("../../index.html");
    expect(window.AdminDashboard.init).not.toHaveBeenCalled();
  });

  it("hydrates the shell and dispatches the matching controller only once", async () => {
    const window = createShellRealm();

    await Promise.all([window.AdminShell.init(), window.AdminShell.init()]);

    expect(window.ApiClient.get.mock.calls.filter(([url]) => url === "/api/auth/me")).toHaveLength(1);
    expect(window.App.applyTranslations).toHaveBeenCalledTimes(1);
    expect(window.document.getElementById("sidebar-user-name").textContent).toBe("Ada Lovelace");
    expect(window.document.getElementById("sidebar-user-role").textContent)
      .toBe("Administrator \u00b7 Analytical Engines");
    expect(window.AdminDashboard.init).toHaveBeenCalledTimes(1);
    expect(window.AdminDashboard.init).toHaveBeenCalledWith(
      expect.objectContaining({ role: "admin", name: "Ada Lovelace" }),
    );
  });

  it("uses safe navigation after cookie logout", async () => {
    const window = createShellRealm();
    await window.AdminShell.init();

    window.document.getElementById("logout-btn").click();
    await vi.waitFor(() => expect(window.ApiClient.clearToken).toHaveBeenCalledTimes(1));

    expect(window.ApiClient.post).toHaveBeenCalledWith("/api/auth/logout", {});
    expect(window.Ui.safeNavigate).toHaveBeenCalledWith("../../index.html");
  });

  it("keeps other page controllers isolated from dashboard dispatch", async () => {
    const window = createShellRealm({ page: "admin-leads" });

    await window.AdminShell.init();

    expect(window.AdminLeads.init).toHaveBeenCalledTimes(1);
    expect(window.AdminDashboard.init).not.toHaveBeenCalled();
    expect(window.AdminPartners.init).not.toHaveBeenCalled();
  });

  it("loads beside the final controllers after monolith retirement", () => {
    const window = createShellRealm();

    expect(existsSync(path.join(PROJECT_ROOT, "js/admin.js"))).toBe(false);
    expect(() => {
      runClassicScript(window, "js/admin-partners.js");
    }).not.toThrow();
  });
});

// ─── who reaches the shell, and what they see in it ─────────

function disabledHrefs(window) {
  return [...window.document.querySelectorAll(".nav-item-disabled")]
    .map((item) => item.getAttribute("href"))
    .sort();
}

describe("AdminShell — role scope in the left menu", () => {
  it("admits an observer to the shell at all", async () => {
    const window = createShellRealm({ user: { id: 22, role: "observer", name: "Ola Obserwator", company: "Firma" } });

    const user = await window.AdminShell.init();

    expect(user).not.toBeNull();
    expect(window.Ui.safeNavigate).not.toHaveBeenCalled();
  });

  it("greys out every entry outside an observer's scope and leaves leads and profile alive", async () => {
    const window = createShellRealm({ user: { id: 22, role: "observer", name: "Ola Obserwator", company: "Firma" } });

    await window.AdminShell.init();

    expect(disabledHrefs(window)).toEqual([
      "analytics.html", "audit.html", "commissions.html",
      "dashboard.html", "partners.html", "settings.html", "users.html",
    ]);
    const leads = window.document.querySelector('.nav-item[href="leads.html"]');
    expect(leads.classList.contains("nav-item-disabled")).toBe(false);
    const profile = window.document.querySelector('.nav-item[href="profile.html"]');
    expect(profile.classList.contains("nav-item-disabled")).toBe(false);
  });

  it("greys out only the audit log and the user roster for a moderator", async () => {
    // The moderator's exact boundary, drawn in the menu: everything except
    // the two admin-only things. Settings in particular must stay live.
    const window = createShellRealm({ user: { id: 21, role: "manager", name: "Marek Moderator", company: "Firma" } });

    await window.AdminShell.init();

    expect(disabledHrefs(window)).toEqual(["audit.html", "users.html"]);
    const settings = window.document.querySelector('.nav-item[href="settings.html"]');
    expect(settings.classList.contains("nav-item-disabled")).toBe(false);
  });

  it("greys out nothing for an administrator", async () => {
    const window = createShellRealm();

    await window.AdminShell.init();

    expect(disabledHrefs(window)).toEqual([]);
  });

  it("swallows a click on a greyed-out entry instead of following the link", async () => {
    const window = createShellRealm({ user: { id: 22, role: "observer", name: "Ola Obserwator", company: "Firma" } });
    await window.AdminShell.init();

    const settings = window.document.querySelector('.nav-item[href="settings.html"]');
    const event = new window.MouseEvent("click", { bubbles: true, cancelable: true });
    settings.dispatchEvent(event);

    expect(event.defaultPrevented).toBe(true);
    expect(settings.getAttribute("aria-disabled")).toBe("true");
  });

  it("names the acting role in the sidebar footer, so a moderator is not shown as an admin", async () => {
    const window = createShellRealm({ user: { id: 21, role: "manager", name: "Marek Moderator", company: "Firma" } });

    await window.AdminShell.init();

    // The footer line is the only place the acting role is stated -- losing
    // it here would hide the role.
    expect(window.document.getElementById("sidebar-user-role").textContent)
      .toBe("Moderator \u00b7 Firma");
  });
});
