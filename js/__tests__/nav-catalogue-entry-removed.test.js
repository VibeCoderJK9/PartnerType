import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

/*
 * Repository sweep: no file under pages/ may link to the deleted standalone
 * catalogue page or carry its retired translation key.
 *
 * Why this exists as its own test, not a spot-check: the sidebar nav block is
 * duplicated verbatim into every admin page -- there is no shared layout
 * partial the removal could go through once. A sweep across nine files by
 * hand missing even one leaves a dead link to a page that no longer exists,
 * and nothing else in the suite would catch it (each page's own tests only
 * ever look at itself). This test walks every HTML file under pages/ instead
 * of naming files individually, so a tenth admin page added later is covered
 * automatically.
 */

const PROJECT_ROOT = path.resolve(import.meta.dirname, "..", "..");
const PAGES_ROOT = path.join(PROJECT_ROOT, "pages");

function walkHtmlFiles(dir) {
  const files = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) files.push(...walkHtmlFiles(full));
    else if (entry.name.endsWith(".html")) files.push(full);
  }
  return files;
}

describe("nav sweep — the deleted Products page has no surviving link", () => {
  it("contains no href to the deleted products.html anywhere under pages/", () => {
    const offenders = [];
    for (const file of walkHtmlFiles(PAGES_ROOT)) {
      const html = readFileSync(file, "utf8");
      if (html.includes('href="products.html"')) {
        offenders.push(path.relative(PROJECT_ROOT, file));
      }
    }
    expect(offenders).toEqual([]);
  });

  it("contains no nav_products translation key anywhere under pages/", () => {
    const offenders = [];
    for (const file of walkHtmlFiles(PAGES_ROOT)) {
      const html = readFileSync(file, "utf8");
      if (html.includes("nav_products")) {
        offenders.push(path.relative(PROJECT_ROOT, file));
      }
    }
    expect(offenders).toEqual([]);
  });

  it("the standalone catalogue page no longer exists in the repository", () => {
    const files = walkHtmlFiles(PAGES_ROOT).map((f) => path.relative(PROJECT_ROOT, f));
    expect(files).not.toContain(path.join("pages", "admin", "products.html"));
  });
});
