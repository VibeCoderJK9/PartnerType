import path from "node:path";
import vm from "node:vm";
import { readFileSync } from "node:fs";
import { Window } from "happy-dom";
import { beforeEach, describe, expect, it } from "vitest";

const PROJECT_ROOT = path.resolve(import.meta.dirname, "..", "..");

/*
   A wide table must always offer a way to scroll sideways, also to somebody
   without a trackpad who has scrolled down the page.

   A table's own scrollbar is at its bottom edge, which on a long table is below
   the fold. Ui.attachTableScroll adds a second, sticky rail at the top of the
   scroll area and keeps the two in sync. These tests drive the rail the way a
   mouse does -- set scrollLeft, dispatch scroll -- because that is the exact
   interaction a trackpad user never needs and everybody else does.
*/
function realm({ scrollWidth = 1200, clientWidth = 600 } = {}) {
  const window = new Window({ url: "http://localhost:8000/pages/admin/audit.html" });
  window.document.body.innerHTML = `
    <div class="panel">
      <div class="table-scroll" id="scroller">
        <table class="data-table"><tbody><tr><td>Wiersz</td></tr></tbody></table>
      </div>
    </div>
  `;
  const context = vm.createContext(window);
  vm.runInContext(
    readFileSync(path.join(PROJECT_ROOT, "js/utils.js"), "utf8"),
    context,
    { filename: "js/utils.js" },
  );

  // happy-dom reports every layout box as zero, so the two widths the helper
  // measures are defined here instead. This is the only thing the rail's
  // behaviour depends on.
  const scroller = window.document.getElementById("scroller");
  Object.defineProperty(scroller, "scrollWidth", { value: scrollWidth, configurable: true });
  Object.defineProperty(scroller, "clientWidth", { value: clientWidth, configurable: true });
  return { window, scroller };
}

function railOf(window) {
  return window.document.querySelector(".table-scroll-rail");
}

describe("the sticky horizontal table scrollbar", () => {
  it("adds a rail before the table, carrying a spacer as wide as the table", () => {
    const { window, scroller } = realm();

    window.Ui.attachTableScroll(window.document);

    const rail = railOf(window);
    expect(rail).not.toBeNull();
    // Before the table, not after: the rail has to be reachable at the top of
    // the scroll area, which is the whole point.
    expect(rail.nextElementSibling).toBe(scroller);
    expect(rail.getAttribute("aria-hidden")).toBe("true");
    expect(rail.firstElementChild.style.width).toBe("1200px");
  });

  it("stays hidden when the table already fits", () => {
    const { window } = realm({ scrollWidth: 500, clientWidth: 600 });

    window.Ui.attachTableScroll(window.document);

    // A scrollbar for content that fits is noise.
    expect(railOf(window).dataset.overflowing).toBe("false");
  });

  it("shows itself once the table is wider than its container", () => {
    const { window } = realm({ scrollWidth: 1200, clientWidth: 600 });

    window.Ui.attachTableScroll(window.document);

    expect(railOf(window).dataset.overflowing).toBe("true");
  });

  it("dragging the rail scrolls the table", () => {
    const { window, scroller } = realm();
    window.Ui.attachTableScroll(window.document);
    const rail = railOf(window);

    rail.scrollLeft = 320;
    rail.dispatchEvent(new window.Event("scroll"));

    expect(scroller.scrollLeft).toBe(320);
  });

  it("swiping the table moves the rail, so the two never disagree", () => {
    const { window, scroller } = realm();
    window.Ui.attachTableScroll(window.document);
    const rail = railOf(window);

    scroller.scrollLeft = 180;
    scroller.dispatchEvent(new window.Event("scroll"));

    expect(rail.scrollLeft).toBe(180);
  });

  it("does not ping-pong: one scroll does not echo back and cancel itself", () => {
    const { window, scroller } = realm();
    window.Ui.attachTableScroll(window.document);
    const rail = railOf(window);

    rail.scrollLeft = 400;
    rail.dispatchEvent(new window.Event("scroll"));
    // The mirrored write fires the table's own scroll event, which would write
    // back to the rail. Without the guard the two fight and the position is
    // lost; with it, both keep the value that was asked for.
    scroller.dispatchEvent(new window.Event("scroll"));

    expect(scroller.scrollLeft).toBe(400);
    expect(rail.scrollLeft).toBe(400);
  });

  it("attaching twice leaves one rail, and re-measures instead", () => {
    const { window, scroller } = realm({ scrollWidth: 1200 });
    window.Ui.attachTableScroll(window.document);

    Object.defineProperty(scroller, "scrollWidth", { value: 2400, configurable: true });
    window.Ui.attachTableScroll(window.document);

    expect(window.document.querySelectorAll(".table-scroll-rail")).toHaveLength(1);
    // Every one of these tables is filled by a later fetch, so the width the
    // first call measured is always the empty one.
    expect(railOf(window).firstElementChild.style.width).toBe("2400px");
  });
});

describe("every data table on an admin screen can be scrolled sideways", () => {
  const PAGES = [
    "pages/admin/dashboard.html", "pages/admin/leads.html",
    "pages/admin/partners.html", "pages/admin/commissions.html",
    "pages/admin/audit.html", "pages/admin/analytics.html",
    "pages/admin/users.html", "pages/admin/settings.html",
    "pages/partner/leads.html",
  ];

  it.each(PAGES)("%s wraps each table in .table-scroll", (page) => {
    const html = readFileSync(path.join(PROJECT_ROOT, page), "utf8");
    const tables = [...html.matchAll(/<table[\s>]/g)].length;
    const wrappers = [...html.matchAll(/class="table-scroll"/g)].length;

    // Without a wrapper the Audit log and Reports tables would be cut off.
    expect(wrappers).toBe(tables);
  });
});
