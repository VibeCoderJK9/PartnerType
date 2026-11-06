import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { loadScript } from "./helpers/load-script.js";

// leads6.js wraps its whole body in an IIFE and exposes nothing -- its
// internals cannot be reached by any loader. Every assertion below is
// against observable DOM side effects only. The sector option labels are
// built from the shared app module's translate function at injection
// time, so app.js must be loaded first.
loadScript("js/app.js");
loadScript("js/leads6.js");

function buildContainers() {
  document.body.innerHTML = `
    <div class="page-header">
      <div class="page-header-actions"></div>
    </div>
    <div class="main-area"></div>
  `;
}

function setPage(marker) {
  if (marker === null) {
    delete document.body.dataset.page;
  } else {
    document.body.dataset.page = marker;
  }
}

function initPage() {
  document.dispatchEvent(new Event("DOMContentLoaded"));
}

function click(el) {
  el.dispatchEvent(new MouseEvent("click", { bubbles: true }));
}

beforeEach(() => {
  buildContainers();
  setPage("admin-leads");
});

afterEach(() => {
  delete window.applyLocalFilters;
  vi.restoreAllMocks();
});

describe("page gate", () => {
  it("injects the filter panel and the bulk bar when the admin-leads marker is present", () => {
    initPage();

    expect(document.getElementById("filter-panel")).not.toBeNull();
    expect(document.getElementById("bulk-bar")).not.toBeNull();
  });

  it("injects neither when the body carries a different page marker", () => {
    setPage("admin-users");

    initPage();

    expect(document.getElementById("filter-panel")).toBeNull();
    expect(document.getElementById("bulk-bar")).toBeNull();
  });

  it("injects neither when the body carries no page marker at all", () => {
    setPage(null);

    initPage();

    expect(document.getElementById("filter-panel")).toBeNull();
    expect(document.getElementById("bulk-bar")).toBeNull();
  });
});

describe("filter panel", () => {
  it("places the filter toggle button inside the page header actions container", () => {
    initPage();

    const actions = document.querySelector(".page-header-actions");
    expect(actions.querySelector("#filter-toggle-btn")).not.toBeNull();
  });

  it("positions the filter panel immediately after the page header", () => {
    initPage();

    const header = document.querySelector(".page-header");
    const panel = document.getElementById("filter-panel");
    expect(header.nextElementSibling).toBe(panel);
  });

  it("builds the panel with the date-from, date-to, apply and clear controls", () => {
    initPage();

    const panel = document.getElementById("filter-panel");
    expect(panel.querySelector("#filter-date-from")).not.toBeNull();
    expect(panel.querySelector("#filter-date-to")).not.toBeNull();
    expect(panel.querySelector("#filter-apply-btn")).not.toBeNull();
    expect(panel.querySelector("#filter-clear-btn")).not.toBeNull();
  });

  it("offers no sector filter, which the owner replaced with the partner filter", () => {
    initPage();

    expect(document.getElementById("filter-sector")).toBeNull();
  });

  it("toggles the open class on the panel and the active class on the button, and clears both on a second click", () => {
    initPage();
    const toggleBtn = document.getElementById("filter-toggle-btn");
    const panel = document.getElementById("filter-panel");

    click(toggleBtn);
    expect(panel.classList.contains("open")).toBe(true);
    expect(toggleBtn.classList.contains("active")).toBe(true);

    click(toggleBtn);
    expect(panel.classList.contains("open")).toBe(false);
    expect(toggleBtn.classList.contains("active")).toBe(false);
  });
});

describe("bulk action bar", () => {
  it("places the bulk bar inside the main area with its count badge and action buttons", () => {
    initPage();

    const mainArea = document.querySelector(".main-area");
    const bar = document.getElementById("bulk-bar");
    expect(mainArea.contains(bar)).toBe(true);
    expect(bar.querySelector("#bulk-count")).not.toBeNull();
    expect(bar.querySelector("#bulk-accept-btn")).not.toBeNull();
    expect(bar.querySelector("#bulk-reject-btn")).not.toBeNull();
    expect(bar.querySelector("#bulk-clear-btn")).not.toBeNull();
  });

  it("starts the count badge at zero", () => {
    initPage();

    expect(document.getElementById("bulk-count").textContent).toBe("0");
  });
});

describe("clear filters", () => {
  it("resets the date-from and date-to inputs to the empty string", () => {
    initPage();
    document.getElementById("filter-date-from").value = "2026-01-01";
    document.getElementById("filter-date-to").value = "2026-02-01";

    click(document.getElementById("filter-clear-btn"));

    expect(document.getElementById("filter-date-from").value).toBe("");
    expect(document.getElementById("filter-date-to").value).toBe("");
  });

  it("clears the value and overdue controls shared with the queue controller", () => {
    initPage();
    const panel = document.getElementById("filter-panel");
    const value = document.createElement("input");
    value.id = "filter-value";
    value.value = "5000";
    const overdue = document.createElement("input");
    overdue.id = "filter-overdue";
    overdue.type = "checkbox";
    overdue.checked = true;
    panel.append(value, overdue);

    click(document.getElementById("filter-clear-btn"));

    expect(value.value).toBe("");
    expect(overdue.checked).toBe(false);
  });

  it("invokes the global filter callback the admin page provides", () => {
    initPage();
    const callback = vi.fn();
    window.applyLocalFilters = callback;

    click(document.getElementById("filter-clear-btn"));

    expect(callback).toHaveBeenCalled();
  });
});

describe("resilience", () => {
  it("does not throw when the admin-leads marker is set but the header actions and main area containers are absent", () => {
    document.body.innerHTML = "";
    setPage("admin-leads");

    expect(() => initPage()).not.toThrow();
  });
});
