import { installLocales } from "./locales.js";
import { BrowserWindow } from "happy-dom";
import Navigator from "happy-dom/lib/navigator/Navigator.js";
import { readLocales } from "./locales.js";

installLocales(globalThis);
if (typeof window !== "undefined" && window !== globalThis) installLocales(window);

// Most screen tests assert the Polish interface, so the test browser is a
// Polish-speaking one (first visit -> Polish). Tests for language detection
// set navigator.languages themselves.
if (typeof navigator !== "undefined") {
  Object.defineProperty(navigator, "languages", { value: ["pl-PL", "pl"], configurable: true });
  Object.defineProperty(navigator, "language", { value: "pl-PL", configurable: true });
}

// Many tests build their own happy-dom Window and run real page scripts in it.
// The page would have loaded /locales.js first, so every such window gets the
// locale files, and the same Polish-speaking browser, through its prototype.

const LOCALES = readLocales();
Object.defineProperty(BrowserWindow.prototype, "PT_LOCALES", {
  configurable: true,
  get() { return LOCALES; },
  set(value) { Object.defineProperty(this, "PT_LOCALES", { value, writable: true, configurable: true }); },
});
Object.defineProperty(Navigator.prototype, "languages", { configurable: true, get: () => ["pl-PL", "pl"] });
Object.defineProperty(Navigator.prototype, "language", { configurable: true, get: () => "pl-PL" });
