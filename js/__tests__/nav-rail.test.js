import path from "node:path";
import vm from "node:vm";
import { readFileSync } from "node:fs";
import { Window } from "happy-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const PROJECT_ROOT = path.resolve(import.meta.dirname, "..", "..");

/*
   The menu can be collapsed and expanded; collapsed, only icons stay,
   hinting at what is there.

   The control is injected by js/app.js rather than added to the fourteen HTML
   files that each carry their own copy of the sidebar -- so these tests drive
   app.js against the real sidebar shape.
*/
function realm({ stored = null } = {}) {
  const window = new Window({ url: "http://localhost:8000/pages/admin/leads.html" });
  // happy-dom's own localStorage, which is real; the property is getter-only so
  // it cannot be replaced with a stub.
  const store = window.localStorage;
  store.clear();
  if (stored !== null) store.setItem("ph_nav", stored);
  window.document.body.innerHTML = `
    <div class="app">
      <aside class="sidebar">
        <div class="sidebar-logo"><span class="sidebar-logo-text">PartnerType</span></div>
        <nav class="sidebar-nav">
          <span class="sidebar-section-label">Menu</span>
          <a href="dashboard.html" class="nav-item"><svg class="nav-icon"></svg><span>Pulpit</span></a>
          <a href="leads.html" class="nav-item"><svg class="nav-icon"></svg><span>Leady</span></a>
        </nav>
        <div class="sidebar-footer">
          <div class="sidebar-user" id="logout-btn">
            <div class="avatar" id="sidebar-avatar">AA</div>
            <div class="sidebar-user-info"><div id="sidebar-user-name">Ada</div></div>
          </div>
        </div>
      </aside>
      <div class="main-area"><header class="top-header"><div class="header-right"></div></header></div>
    </div>
  `;
  const context = vm.createContext(window);
  for (const file of ["js/icons.js", "js/app.js"]) {
    vm.runInContext(readFileSync(path.join(PROJECT_ROOT, file), "utf8"), context, { filename: file });
  }
  window.document.dispatchEvent(new window.Event("DOMContentLoaded"));
  return { window, store };
}

describe("the collapsible navigation rail", () => {
  it("puts one toggle in the sidebar footer, above the user block", () => {
    const { window } = realm();

    const toggle = window.document.getElementById("sidebar-toggle");
    expect(toggle).not.toBeNull();
    // Above logout, closer to the other icons.
    const user = window.document.getElementById("logout-btn");
    expect(toggle.nextElementSibling).toBe(user);
    // In the footer, not the logo row, where it would fight the wordmark for
    // space in a 196px menu.
    expect(toggle.closest(".sidebar-footer")).not.toBeNull();
    expect(toggle.closest(".sidebar-logo")).toBeNull();
    expect(window.document.querySelectorAll("#sidebar-toggle")).toHaveLength(1);
  });

  it("stays outside the user block, which is the logout control", () => {
    const { window } = realm();

    // Nested inside #logout-btn, collapsing the menu would sign the producer out.
    expect(window.document.getElementById("sidebar-toggle").closest("#logout-btn")).toBeNull();
  });

  it("is labelled, so it is not one more unexplained arrow", () => {
    const { window } = realm();

    expect(window.document.getElementById("sidebar-toggle").textContent).toContain("Zwiń");
  });

  it("says Expand once collapsed", () => {
    const { window } = realm({ stored: "collapsed" });

    const toggle = window.document.getElementById("sidebar-toggle");
    expect(toggle.textContent).toContain("Rozwiń");
    expect(toggle.title).toBe("Rozwiń menu");
  });

  it("keeps every nav label as a tooltip and as the accessible name", () => {
    const { window } = realm();

    const items = [...window.document.querySelectorAll(".nav-item")];
    // Collapsed there is nothing else to go on: an icon rail whose icons cannot
    // be identified is worse than no rail.
    expect(items.map((item) => item.title)).toEqual(["Pulpit", "Leady"]);
    expect(items.map((item) => item.getAttribute("aria-label"))).toEqual(["Pulpit", "Leady"]);
  });

  it("starts expanded when nothing was stored", () => {
    const { window } = realm();

    expect(window.document.documentElement.dataset.nav).toBe("expanded");
    expect(window.document.getElementById("sidebar-toggle").getAttribute("aria-expanded")).toBe("true");
  });

  it("restores the collapsed rail the owner left behind", () => {
    const { window } = realm({ stored: "collapsed" });

    expect(window.document.documentElement.dataset.nav).toBe("collapsed");
    expect(window.document.getElementById("sidebar-toggle").getAttribute("aria-expanded")).toBe("false");
  });

  it("collapsing writes the choice down, so the next screen opens the same way", () => {
    const { window, store } = realm();

    window.document.getElementById("sidebar-toggle").click();

    expect(window.document.documentElement.dataset.nav).toBe("collapsed");
    expect(store.getItem("ph_nav")).toBe("collapsed");
  });

  it("toggles back, and says so on the button", () => {
    const { window, store } = realm({ stored: "collapsed" });
    const toggle = window.document.getElementById("sidebar-toggle");

    toggle.click();

    expect(window.document.documentElement.dataset.nav).toBe("expanded");
    expect(store.getItem("ph_nav")).toBe("expanded");
    expect(toggle.title).toBe("Zwiń menu");
  });

  it("survives storage that throws, which is what a private window does", () => {
    const window = new Window({ url: "http://localhost:8000/pages/admin/leads.html" });
    Object.defineProperty(window, "localStorage", {
      configurable: true,
      get() {
        // Exactly what a private window, or blocked site data, does.
        throw new Error("blocked");
      },
    });
    window.document.body.innerHTML = `
      <aside class="sidebar">
        <div class="sidebar-logo"></div><nav class="sidebar-nav"></nav>
        <div class="sidebar-footer"></div>
      </aside>`;
    const context = vm.createContext(window);
    for (const file of ["js/icons.js", "js/app.js"]) {
      vm.runInContext(readFileSync(path.join(PROJECT_ROOT, file), "utf8"), context, { filename: file });
    }

    expect(() => window.document.dispatchEvent(new window.Event("DOMContentLoaded"))).not.toThrow();
    expect(window.document.documentElement.dataset.nav).toBe("expanded");
    // The rail still works; it just forgets.
    expect(() => window.document.getElementById("sidebar-toggle").click()).not.toThrow();
    expect(window.document.documentElement.dataset.nav).toBe("collapsed");
  });
});
