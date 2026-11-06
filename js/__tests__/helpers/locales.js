/* The locale files as the browser receives them from /locales.js.

   In the app the server serves every locales/<code>.json as one script that
   sets window.PT_LOCALES before js/app.js runs. Tests do the same: the setup
   file below puts them on every test realm, and a test that builds its own
   window can call installLocales(window) before loading app.js. */
import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const PROJECT_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "..");
const LOCALE_DIR = path.join(PROJECT_ROOT, "locales");

export function readLocales() {
  const locales = {};
  for (const name of readdirSync(LOCALE_DIR).sort()) {
    if (/^[a-z]{2,3}\.json$/.test(name)) {
      locales[name.slice(0, -5)] = JSON.parse(readFileSync(path.join(LOCALE_DIR, name), "utf8"));
    }
  }
  return locales;
}

export function installLocales(target = globalThis) {
  target.PT_LOCALES = readLocales();
  return target.PT_LOCALES;
}
