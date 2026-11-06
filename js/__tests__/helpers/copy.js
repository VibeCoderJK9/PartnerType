/* ============================================================
   copy — the real dictionaries, for tests that stub App
   ============================================================

   Most controller tests mount one module in a bare realm and stub App with
   `t: (key) => key`. That was harmless while the copy lived in the controllers
   themselves; since the JS layer reads its text from the dictionary, an
   identity stub makes every assertion about user-visible text compare against
   a raw key instead.

   Rather than duplicating strings into the tests — a second copy is how these
   screens drifted apart before — this reads the locale files themselves
   (locales/<code>.json), so a reworded message fails the test that depends on
   it, which is the point. Lookups follow the app: the language, then English.
   ============================================================ */
import { readLocales } from "./locales.js";

const LOCALES = readLocales();

/** The messages of one language, as the app itself would read them. */
export function dictionary(lang = "pl") {
  return LOCALES[lang].messages;
}

/**
 * A stand-in for App that resolves real copy, including {placeholders} and
 * plural forms. Drop-in replacement for `{ t: (key) => key }` stubs.
 */
export function appStub(lang = "pl") {
  const own = LOCALES[lang].messages;
  const english = LOCALES.en.messages;
  const locale = LOCALES[lang].meta.locale;
  const t = (key, params) => {
    const value = own[key] !== undefined ? own[key] : english[key];
    if (value === undefined) return key;
    if (!params) return value;
    return String(value).replace(/\{(\w+)\}/g, (whole, name) =>
      (params[name] === undefined ? whole : String(params[name])));
  };
  const plural = (count, key) => {
    const useOwn = own[key] && typeof own[key] === "object";
    const forms = useOwn ? own[key] : english[key];
    if (!forms || typeof forms !== "object") return key;
    const n = Math.abs(Number(count));
    const category = new Intl.PluralRules(useOwn ? locale : "en").select(Number.isFinite(n) ? n : 0);
    return forms[category] ?? forms.other ?? Object.values(forms).pop();
  };
  return {
    lang,
    t,
    plural,
    pluralize: (count, key) => `${count} ${plural(count, key)}`,
    languages: () => Object.keys(LOCALES),
    languageName: (code) => LOCALES[code]?.meta?.name || code.toUpperCase(),
    applyTranslations: () => {},
    // Screens register their redraw here; a test realm never switches
    // language, so collecting and ignoring them is the honest stand-in.
    onRerender: () => {},
    rerender: () => {},
  };
}
