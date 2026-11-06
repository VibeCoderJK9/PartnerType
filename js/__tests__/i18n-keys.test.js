import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

/*
 * Every data-i18n attribute in the markup must resolve to a dictionary entry.
 * A missing key does not fall back to the text already in the HTML -- the
 * translator overwrites the element with the raw key, so the screen ships
 * showing "nav_products" or "form_lead_nip" to the user. That shipped twice
 * already (broken windows #25 and the Products nav item), which is why this
 * guards the whole class rather than the individual keys.
 */

const PROJECT_ROOT = path.resolve(import.meta.dirname, "..", "..");

function dictionaryKeys(language) {
  const file = path.join(PROJECT_ROOT, "locales", `${language}.json`);
  return new Set(Object.keys(JSON.parse(readFileSync(file, "utf8")).messages));
}

function htmlFiles() {
  const files = ["index.html", "register.html", "set-password.html"];
  for (const area of ["admin", "partner", "oauth"]) {
    const dir = path.join(PROJECT_ROOT, "pages", area);
    for (const name of readdirSync(dir)) {
      if (name.endsWith(".html")) files.push(path.join("pages", area, name));
    }
  }
  return files;
}

function keysUsedIn(relativePath) {
  const html = readFileSync(path.join(PROJECT_ROOT, relativePath), "utf8");
  return [...html.matchAll(/data-i18n="([^"]+)"/g)].map((m) => m[1]);
}

describe("Translations — every key used in the markup exists in the dictionary", () => {
  it("resolves every data-i18n key used anywhere in the app (PL)", () => {
    const known = dictionaryKeys("pl");
    const missing = [];
    for (const file of htmlFiles()) {
      for (const key of keysUsedIn(file)) {
        if (!known.has(key)) missing.push(`${file}: ${key}`);
      }
    }
    expect(missing).toEqual([]);
  });

  it("keeps the PL and EN dictionaries in step", () => {
    const pl = dictionaryKeys("pl");
    const en = dictionaryKeys("en");
    const onlyPl = [...pl].filter((k) => !en.has(k));
    const onlyEn = [...en].filter((k) => !pl.has(k));
    expect({ onlyPl, onlyEn }).toEqual({ onlyPl: [], onlyEn: [] });
  });
});

describe("Translations — copy lives in the dictionary", () => {
  // Interface text written inline as App.lang === 'en' ? 'X' : 'Y' is
  // invisible to the dictionary-sync check above. A language-dependent locale
  // or number format is fine; a sentence is not.
  it("has no inline bilingual text outside js/app.js", () => {
    const offenders = [];
    for (const name of readdirSync(path.join(PROJECT_ROOT, "js"))) {
      if (!name.endsWith(".js") || name === "app.js") continue;
      const source = readFileSync(path.join(PROJECT_ROOT, "js", name), "utf8");
      const pattern = /lang\s*===\s*['"]en['"]\s*\?\s*(['"`])([^'"`]*)\1\s*:\s*(['"`])([^'"`]*)\3/g;
      for (const match of source.matchAll(pattern)) {
        const [english, polish] = [match[2], match[4]];
        if (/^[a-z]{2}-[A-Z]{2}$/.test(english) && /^[a-z]{2}-[A-Z]{2}$/.test(polish)) continue;
        offenders.push(`${name}: ${match[0]}`);
      }
    }
    expect(offenders).toEqual([]);
  });
});

describe("Translations — every page loads the locale files first", () => {
  it("loads /locales.js right before js/app.js, and ships English markup", () => {
    const problems = [];
    for (const file of htmlFiles()) {
      const html = readFileSync(path.join(PROJECT_ROOT, file), "utf8");
      const scripts = [...html.matchAll(/<script\s+src="([^"]+)"/g)].map((m) => m[1]);
      const app = scripts.findIndex((src) => src.endsWith("js/app.js"));
      if (app < 1 || scripts[app - 1] !== "/locales.js") problems.push(`${file}: /locales.js not right before app.js`);
      if (!/<html lang="en">/.test(html)) problems.push(`${file}: <html lang> is not "en"`);
    }
    expect(problems).toEqual([]);
  });
});
