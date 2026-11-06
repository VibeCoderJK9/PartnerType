/*
 * The assignment modal as MARKUP: what pages/admin/leads.html and the
 * stylesheets promise before a single line of controller code runs: overflow,
 * long text, PL/EN copy and label binding.
 *
 * These are deliberately source assertions rather than layout assertions.
 * happy-dom has no layout engine: it cannot tell us that a 320px-wide viewport
 * does not scroll sideways, or that the footer buttons actually wrapped onto a
 * second line. What it CAN tell us is that the rules which produce that
 * behaviour are present and scoped to this modal, and that no element opts out
 * of them with a fixed pixel width. The genuinely visual half is listed at the
 * bottom of this file as open manual checks -- not asserted, not claimed.
 */
import { readFileSync } from "node:fs";
import path from "node:path";
import { Window } from "happy-dom";
import { beforeAll, describe, expect, it } from "vitest";

const PROJECT_ROOT = path.resolve(import.meta.dirname, "..", "..");

function read(relativePath) {
  return readFileSync(path.join(PROJECT_ROOT, relativePath), "utf8");
}

const LEADS_HTML = read("pages/admin/leads.html");
const COMPONENTS_CSS = read("css/components.css");

/**
 * The modal's own rules live in a stylesheet, because `style-src-elem 'self'`
 * refuses a page that carries its own.
 */
function pageStyles() {
  const href = "css/assign-lead-modal.css";
  // Fail loudly if the page stops loading it, rather than asserting against an
  // empty string and passing every rule check by accident.
  expect(LEADS_HTML).toContain(href);
  return read(href);
}

/** Collapse whitespace so a rule can be asserted regardless of formatting. */
function normalise(css) {
  return css.replace(/\s+/g, " ");
}

let document;
let modal;

beforeAll(() => {
  const window = new Window({ url: "http://localhost:8000/pages/admin/leads.html" });
  window.document.body.innerHTML = LEADS_HTML
    .replace(/[\s\S]*<body[^>]*>/, "")
    .replace(/<\/body>[\s\S]*/, "");
  document = window.document;
  modal = document.getElementById("assign-lead-modal");
  expect(modal).not.toBeNull();
});

describe("assignment modal -- overflow at 320px", () => {
  it("scrolls inside the viewport rather than growing the page", () => {
    // The shared modal shell already caps its height and scrolls its own
    // content; the assignment modal must be using that shell, not a bespoke
    // box that grows until the confirm button is off-screen.
    const shell = /\.modal\s*\{([^}]*)\}/.exec(COMPONENTS_CSS);
    expect(shell).not.toBeNull();
    expect(normalise(shell[1])).toContain("max-height: 90vh");
    expect(normalise(shell[1])).toContain("overflow-y: auto");
    expect(modal.querySelector(".modal").className).toContain("modal-lg");
  });

  it("lets the footer actions wrap instead of pushing one off the edge", () => {
    const styles = normalise(pageStyles());
    expect(styles).toMatch(/#assign-lead-modal \.modal-footer\s*\{[^}]*flex-wrap: wrap/);
  });

  it("keeps every action reachable by giving them a tap target and room to wrap", () => {
    const styles = normalise(pageStyles());
    expect(styles).toMatch(/#assign-lead-modal[^{]*\.btn\s*\{[^}]*min-height: 44px/);
  });

  it("tightens the modal padding on a narrow screen", () => {
    const styles = normalise(pageStyles());
    expect(styles).toMatch(/@media \(max-width: 420px\)[\s\S]*#assign-lead-modal/);
  });

  it("never gives a control a fixed width wider than a 320px screen", () => {
    const controls = modal.querySelectorAll("input, select, textarea, button, .modal, .modal-body");
    const offenders = [];
    controls.forEach((el) => {
      const inline = el.getAttribute("style") || "";
      if (/(?:^|;)\s*(?:min-)?width\s*:\s*\d{3,}px/.test(inline)) {
        offenders.push(`${el.tagName}#${el.id || "(anonymous)"}: ${inline}`);
      }
    });
    expect(offenders).toEqual([]);
  });

  it("makes the form controls shrink with the modal", () => {
    const styles = normalise(pageStyles());
    expect(styles).toMatch(
      /#assign-lead-modal[^{]*\{[^}]*max-width: 100%[^}]*box-sizing: border-box/,
    );
  });
});

describe("assignment modal -- long text", () => {
  it("wraps the summary values instead of letting them run off the panel", () => {
    const styles = normalise(pageStyles());
    expect(styles).toMatch(/#assign-preview strong\s*\{[^}]*overflow-wrap: anywhere/);
  });

  it("keeps the notes and message text readable with pre-wrap", () => {
    const styles = normalise(pageStyles());
    expect(styles).toMatch(/#assign-preview strong\s*\{[^}]*white-space: pre-wrap/);
  });

  it("wraps the error and empty-state copy too", () => {
    const styles = normalise(pageStyles());
    expect(styles).toMatch(
      /#assign-error-message[^{]*#assign-empty-message\s*\{[^}]*overflow-wrap: anywhere/,
    );
  });
});

describe("assignment modal -- labels are bound to their controls (a11y)", () => {
  it("gives every visible control in the form a label that points at it", () => {
    const unlabelled = [];
    modal.querySelectorAll("#assign-lead-form input, #assign-lead-form select, #assign-lead-form textarea")
      .forEach((el) => {
        const label = modal.querySelector(`label[for="${el.id}"]`);
        if (!label || !label.textContent.trim()) unlabelled.push(el.id || el.tagName);
      });
    expect(unlabelled).toEqual([]);
  });

  it("names the close button for a screen reader", () => {
    const close = modal.querySelector(".modal-close");
    expect(close.getAttribute("aria-label")).toBeTruthy();
  });

  it("marks the modal as a dialog with an accessible name", () => {
    const dialog = modal.querySelector('[role="dialog"]');
    expect(dialog).not.toBeNull();
    const labelledBy = dialog.getAttribute("aria-labelledby");
    expect(labelledBy).toBeTruthy();
    expect(document.getElementById(labelledBy)).not.toBeNull();
  });

  it("announces the loading, error and empty regions politely", () => {
    ["assign-loading-state", "assign-error-state", "assign-empty-state"].forEach((id) => {
      expect(document.getElementById(id).getAttribute("aria-live")).toBe("polite");
    });
  });
});

describe("assignment flow -- PL/EN (i18n)", () => {
  function dictionaryKeys(language) {
    const file = path.join(PROJECT_ROOT, "locales", `${language}.json`);
    return new Set(Object.keys(JSON.parse(readFileSync(file, "utf8")).messages));
  }

  it("translates every piece of static copy the flow shows", () => {
    // A hardcoded Polish string here is a bug an English producer sees.
    const targets = [
      ...modal.querySelectorAll(
        ".modal-title, .modal-header .text-sm, label.form-label, .modal-footer button, " +
        "#assign-loading-state p, #assign-retry-btn, #assign-preview .assign-preview-label, " +
        ".form-label",
      ),
      document.querySelector("#assign-lead-btn span"),
    ];
    expect(targets.length).toBeGreaterThan(10);
    const untranslated = targets
      .filter((el) => el && el.textContent.trim())
      .filter((el) => !el.hasAttribute("data-i18n"))
      .map((el) => `${el.tagName}.${el.className}: ${el.textContent.trim().slice(0, 40)}`);
    expect(untranslated).toEqual([]);
  });

  it("has both languages for every assignment key it uses", () => {
    const pl = dictionaryKeys("pl");
    const en = dictionaryKeys("en");
    const used = [
      ...[...LEADS_HTML.matchAll(/data-i18n="([^"]+)"/g)].map((m) => m[1]),
    ].filter((key) => key.startsWith("assign_"));
    expect(used.length).toBeGreaterThan(10);
    const missing = used.filter((key) => !pl.has(key) || !en.has(key));
    expect(missing).toEqual([]);
  });

  it("does not translate the close button by overwriting its icon", () => {
    // data-i18n replaces textContent; on a button holding an <svg> that would
    // erase the icon. The aria-label form must be used instead.
    const close = modal.querySelector(".modal-close");
    expect(close.getAttribute("data-i18n")).toBeTruthy();
    // Without data-i18n-attr the translator would replace the button's
    // children with a text node and the X icon would vanish.
    expect(close.getAttribute("data-i18n-attr")).toBe("aria-label");
    expect(close.querySelector("svg")).not.toBeNull();
  });
});

/*
 * OPEN MANUAL / BROWSER CHECKS -- deliberately not asserted here.
 *
 * happy-dom computes no layout, so none of the following can be honestly
 * turned into a passing assertion in this suite. Check them in a browser:
 *
 *  1. At a real 320x568 viewport: document.scrollingElement.scrollWidth is not
 *     wider than the viewport while the assignment modal is open.
 *  2. "Create and accept" is reachable by scrolling and is not covered by
 *     the on-screen keyboard when the notes textarea has focus.
 *  3. The footer buttons visibly wrap onto two rows at 320px rather than
 *     shrinking below a 44px tap target.
 *  4. A 300-character client company name wraps inside the summary panel and
 *     does not widen the modal.
 *  5. The modal body scrolls while the page behind it does not.
 *  6. Dark theme contrast of the error and empty-state copy.
 */
