/* ============================================================
   No inline scripts, anywhere in the served pages
   ============================================================

   `script-src 'self'` — without `unsafe-inline` — is what stops a script that
   arrives inside a piece of data from running. The price is that every page
   must keep its JavaScript in a file: one `<script>` block or one `onclick=`
   attribute and that page's own code stops working, in a way nobody notices
   until a browser is actually opened on it.

   So this is the guard rail for a policy the tests cannot otherwise feel. It
   reads the real files that get served.
   ============================================================ */
import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const PROJECT_ROOT = path.resolve(import.meta.dirname, "..", "..");

/** Every .html file the server hands to a browser. */
function servedPages() {
  const pages = readdirSync(PROJECT_ROOT).filter((name) => name.endsWith(".html"));
  for (const area of ["admin", "partner", "oauth"]) {
    const directory = path.join("pages", area);
    for (const name of readdirSync(path.join(PROJECT_ROOT, directory))) {
      if (name.endsWith(".html")) pages.push(path.join(directory, name));
    }
  }
  return pages.sort();
}

// `on` followed by letters, then `=`, as an attribute — onclick, onmouseover,
// onerror and the rest. Bounded by whitespace on the left so `data-i18n-attr`
// and a `content="..."` ending in "on" cannot match.
const EVENT_ATTRIBUTE = /\son[a-z]+\s*=\s*["']/gi;
const INLINE_SCRIPT = /<script(?![^>]*\bsrc=)[^>]*>/gi;

describe("served pages carry no inline JavaScript", () => {
  const pages = servedPages();

  it("finds the pages to check, so an empty sweep cannot pass as a clean one", () => {
    expect(pages.length).toBeGreaterThanOrEqual(17);
    expect(pages).toContain("index.html");
    expect(pages).toContain(path.join("pages", "admin", "leads.html"));
    expect(pages).toContain(path.join("pages", "oauth", "consent.html"));
  });

  it.each(pages)("%s has no <script> block without src", (page) => {
    const html = readFileSync(path.join(PROJECT_ROOT, page), "utf8");
    expect([...html.matchAll(INLINE_SCRIPT)].map((match) => match[0])).toEqual([]);
  });

  it.each(pages)("%s has no inline event-handler attribute", (page) => {
    const html = readFileSync(path.join(PROJECT_ROOT, page), "utf8");
    expect([...html.matchAll(EVENT_ATTRIBUTE)].map((match) => match[0].trim())).toEqual([]);
  });

  // `style-src-elem 'self'` refuses these too. An injected <style> runs no
  // script, but it can read a page out one attribute selector at a time.
  it.each(pages)("%s has no <style> block", (page) => {
    const html = readFileSync(path.join(PROJECT_ROOT, page), "utf8");
    expect([...html.matchAll(/<style[\s>]/gi)].map((match) => match[0])).toEqual([]);
  });

  it("keeps the theme bootstrap first and render-blocking on every page", () => {
    // It sets the theme before the first paint; deferred or moved down, a
    // dark-theme user gets a white flash on every navigation.
    for (const page of pages) {
      const html = readFileSync(path.join(PROJECT_ROOT, page), "utf8");
      const first = html.match(/<script\s+[^>]*src="([^"]+)"[^>]*>/);
      expect(first, `${page} loads no script at all`).not.toBeNull();
      expect(path.basename(first[1]), page).toBe("theme-boot.js");
      expect(first[0], `${page}: theme-boot must not be deferred`).not.toMatch(/\b(defer|async)\b/);
    }
  });
});

describe("the policy this buys", () => {
  it("names no inline allowance for scripts or style elements in server.py", () => {
    const server = readFileSync(path.join(PROJECT_ROOT, "server.py"), "utf8");
    expect(server).toContain(`"script-src 'self'"`);
    expect(server).not.toContain(`"script-src 'self' 'unsafe-inline'"`);
    expect(server).toContain(`"style-src-elem 'self'"`);
  });

  it("builds no <style> element in code either", () => {
    // A <style> created and appended at runtime is the same thing to the
    // browser as one that shipped in the page, and refused the same way.
    const scripts = readdirSync(path.join(PROJECT_ROOT, "js"))
      .filter((name) => name.endsWith(".js"));
    const offenders = scripts.filter((name) => {
      const source = readFileSync(path.join(PROJECT_ROOT, "js", name), "utf8");
      return /createElement\(\s*['"]style['"]\s*\)/.test(source);
    });
    expect(offenders).toEqual([]);
  });
});
