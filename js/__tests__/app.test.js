import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import vm from "node:vm";
import { loadScript } from "./helpers/load-script.js";

loadScript("js/app.js");

beforeEach(() => {
  document.body.innerHTML = "";
  localStorage.clear();
  // App is a singleton loaded once for this whole test file; reset its
  // in-memory state directly (no side effects) so tests stay independent.
  App.lang = "pl";
  App.theme = "light";
});

describe("formatCurrency", () => {
  it("formats a five-figure amount as a Polish currency string", () => {
    const result = formatCurrency(45000).replace(/ /g, " ");
    expect(result).toBe("45 000 zł");
  });

  it("returns an em dash for zero", () => {
    expect(formatCurrency(0)).toBe("—");
  });

  it("returns an em dash for null", () => {
    expect(formatCurrency(null)).toBe("—");
  });

  it("returns an em dash for undefined", () => {
    expect(formatCurrency(undefined)).toBe("—");
  });
});

describe("formatDate", () => {
  it("formats a known ISO date as a Polish short-month string", () => {
    expect(formatDate("2026-07-05")).toBe("05 lip 2026");
  });
});

describe("getInitials", () => {
  it("returns the first letter of each word for a two-word name", () => {
    expect(getInitials("Anna Nowak")).toBe("AN");
  });

  it("returns the first two letters for a one-word name", () => {
    expect(getInitials("Anna")).toBe("AN");
  });

  it("returns the placeholder for an empty name", () => {
    expect(getInitials("")).toBe("??");
  });
});

describe("getAvatarColorClass", () => {
  it("is deterministic for the same name", () => {
    expect(getAvatarColorClass("Anna Nowak")).toBe(
      getAvatarColorClass("Anna Nowak"),
    );
  });

  it("returns the indigo default for an empty name", () => {
    expect(getAvatarColorClass("")).toBe("bg-nebula-indigo");
  });
});

describe("App.t translation lookup", () => {
  it("returns the key itself when the key is missing", () => {
    expect(App.t("this_key_does_not_exist")).toBe("this_key_does_not_exist");
  });

  it("returns the known key's value for the current language and updates on switch", () => {
    App.lang = "pl";
    expect(App.t("login_title")).toBe(i18n.pl.login_title);

    App.lang = "en";
    expect(App.t("login_title")).toBe(i18n.en.login_title);

    App.lang = "pl";
    expect(App.t("login_title")).toBe(i18n.pl.login_title);
  });
});

describe("i18n table parity", () => {
  it("exposes an identical set of keys in both translation tables", () => {
    const plKeys = Object.keys(i18n.pl).sort();
    const enKeys = Object.keys(i18n.en).sort();
    expect(enKeys).toEqual(plKeys);
  });
});

describe("App.setTheme / App.toggleTheme", () => {
  it("writes the theme attribute onto the document element and persists it", () => {
    App.setTheme("dark");
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
    expect(localStorage.getItem("ph_theme")).toBe("dark");
  });

  it("dispatches the themeChanged event with the new theme", () => {
    let received;
    window.addEventListener("themeChanged", (e) => {
      received = e.detail;
    });

    App.setTheme("dark");

    expect(received).toBe("dark");
  });

  it("toggles between light and dark", () => {
    App.setTheme("light");
    App.toggleTheme();
    expect(App.theme).toBe("dark");
    App.toggleTheme();
    expect(App.theme).toBe("light");
  });
});

describe("App.applyTranslations", () => {
  it("rewrites the text content of an element carrying the translation attribute", () => {
    document.body.innerHTML = '<h1 data-i18n="login_title"></h1>';
    App.lang = "pl";

    App.applyTranslations();

    expect(document.querySelector("h1").textContent).toBe(
      i18n.pl.login_title,
    );
  });

  it("rewrites the named attribute when the attribute-target marker is present", () => {
    document.body.innerHTML =
      '<input data-i18n="password_placeholder" data-i18n-attr="placeholder">';
    App.lang = "pl";

    App.applyTranslations();

    expect(document.querySelector("input").getAttribute("placeholder")).toBe(
      i18n.pl.password_placeholder,
    );
  });
});

describe("showToast", () => {
  it("appends a toast element to the toast container", () => {
    document.body.innerHTML = '<div id="toast-container"></div>';

    showToast("success", "Zapisano", "Zmiany zostały zapisane.");

    const container = document.getElementById("toast-container");
    expect(container.children.length).toBe(1);
    expect(container.children[0].className).toContain("toast");
    expect(container.children[0].className).toContain("success");
  });
});

describe("openModal / closeModal", () => {
  it("toggles the show class and body overflow on the target overlay", () => {
    document.body.innerHTML =
      '<div id="my-modal" class="modal-overlay"></div>';

    openModal("my-modal");
    expect(document.getElementById("my-modal").classList.contains("show")).toBe(
      true,
    );
    expect(document.body.style.overflow).toBe("hidden");

    closeModal("my-modal");
    expect(document.getElementById("my-modal").classList.contains("show")).toBe(
      false,
    );
    expect(document.body.style.overflow).toBe("");
  });
});

describe("DOMContentLoaded init / injectGlobalToolbar", () => {
  it("does not throw when the DOM-ready event fires against a header container, and injects the toolbar exactly once even when dispatched twice", () => {
    document.body.innerHTML = '<div class="header-right"></div>';

    expect(() => {
      document.dispatchEvent(new Event("DOMContentLoaded"));
      document.dispatchEvent(new Event("DOMContentLoaded"));
    }).not.toThrow();

    expect(document.querySelectorAll("#global-toolbar").length).toBe(1);
  });

  it("falls back to the auth lang switcher target when no header-right container exists", () => {
    document.body.innerHTML =
      '<div class="auth-lang-switcher"><span class="header-lang">old</span></div>';

    injectGlobalToolbar();

    const switcher = document.querySelector(".auth-lang-switcher");
    expect(switcher.querySelector("#global-toolbar")).not.toBeNull();
  });

  it("toggles the theme and updates the toolbar button when its theme button is clicked", () => {
    document.body.innerHTML = '<div class="header-right"></div>';
    App.theme = "light";

    injectGlobalToolbar();
    const themeBtn = document.getElementById("gt-theme-btn");
    themeBtn.dispatchEvent(new MouseEvent("click", { bubbles: true }));

    expect(App.theme).toBe("dark");
    expect(themeBtn.title).toBe("Tryb jasny");
  });
});

describe("App.setLang", () => {
  it("updates App.lang, persists it, re-applies translations and toggles lang buttons", () => {
    document.body.innerHTML =
      '<h1 data-i18n="login_title"></h1>' +
      '<button class="header-lang-btn" data-lang="pl"></button>' +
      '<button class="header-lang-btn" data-lang="en"></button>';

    App.setLang("en");

    expect(App.lang).toBe("en");
    expect(localStorage.getItem("ph_lang")).toBe("en");
    expect(document.querySelector("h1").textContent).toBe(i18n.en.login_title);

    const buttons = document.querySelectorAll(".header-lang-btn");
    expect(buttons[0].classList.contains("active")).toBe(false);
    expect(buttons[1].classList.contains("active")).toBe(true);
  });
});

describe("delegated click handlers", () => {
  it("closes an open modal overlay when the overlay itself is clicked", () => {
    document.body.innerHTML =
      '<div id="my-modal" class="modal-overlay show"></div>';
    document.body.style.overflow = "hidden";

    const overlay = document.getElementById("my-modal");
    overlay.dispatchEvent(new MouseEvent("click", { bubbles: true }));

    expect(overlay.classList.contains("show")).toBe(false);
    expect(document.body.style.overflow).toBe("");
  });

  it("switches the language when a lang-switcher button is clicked", () => {
    document.body.innerHTML =
      '<button class="header-lang-btn" data-lang="en"></button>';
    App.lang = "pl";

    const btn = document.querySelector(".header-lang-btn");
    btn.dispatchEvent(new MouseEvent("click", { bubbles: true }));

    expect(App.lang).toBe("en");
  });
});

describe("showToast auto-dismiss", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("fades and removes the toast after its timeout", () => {
    vi.useFakeTimers();
    document.body.innerHTML = '<div id="toast-container"></div>';

    showToast("success", "Zapisano");
    const container = document.getElementById("toast-container");
    expect(container.children.length).toBe(1);

    vi.advanceTimersByTime(3200);
    expect(container.children[0].style.opacity).toBe("0");

    vi.advanceTimersByTime(300);
    expect(container.children.length).toBe(0);
  });
});

// ─── COPY LOOKUP ────────────────────────────────────────────
//
// Placeholders and plural forms live in the dictionary, so a sentence with a
// number or a name is never glued together at the call site and word order
// stays in each language's hands.

describe("App.t with parameters", () => {
  it("fills a named placeholder", () => {
    App.lang = "pl";
    expect(App.t("confirm_delete_partner", { name: "Acme" }))
      .toContain("Acme");
  });

  it("leaves an unknown placeholder visible rather than printing undefined", () => {
    const result = App.t("confirm_delete_partner", {});
    expect(result).toContain("{name}");
    expect(result).not.toContain("undefined");
  });

  it("returns the key itself when the copy is missing", () => {
    expect(App.t("nie_ma_takiego_klucza")).toBe("nie_ma_takiego_klucza");
  });
});

describe("App.plural", () => {
  // Polish: 1 / 2-4 / 5+, with 12-14 taking the last form.
  it.each([
    [1, "partner"],
    [2, "partnerzy"],
    [4, "partnerzy"],
    [5, "partnerów"],
    [12, "partnerów"],
    [14, "partnerów"],
    [22, "partnerzy"],
    [25, "partnerów"],
    [0, "partnerów"],
  ])("picks the Polish form for %i", (count, expected) => {
    App.lang = "pl";
    expect(App.plural(count, "unit_partner")).toBe(expected);
  });

  it.each([
    [1, "partner"],
    [2, "partners"],
    [5, "partners"],
    [0, "partners"],
  ])("picks the English form for %i", (count, expected) => {
    App.lang = "en";
    expect(App.plural(count, "unit_partner")).toBe(expected);
  });

  it("pluralize puts the count in front of the form", () => {
    App.lang = "pl";
    expect(App.pluralize(5, "unit_partner")).toBe("5 partnerów");
    App.lang = "en";
    expect(App.pluralize(5, "unit_partner")).toBe("5 partners");
  });
});

// ─── REDRAW ON LANGUAGE / THEME CHANGE ──────────────────────
//
// Labels must refresh after a theme switch, and month names after switching
// to English.
//
// applyTranslations only touches [data-i18n] nodes, and canvas charts bake the
// theme's colours in at draw time — so everything a screen BUILDS in code kept
// the old language and the old colours until the next data load. Screens now
// register a redraw; these pin that it runs, and that it does NOT run when
// nothing actually changed (boot calls setTheme with the current value, which
// would otherwise re-issue every screen's first request).

describe("App.rerender", () => {
  it("runs a registered hook when the language actually changes", () => {
    const hook = vi.fn();
    App.onRerender(hook);
    App.lang = "pl";

    App.setLang("en");

    expect(hook).toHaveBeenCalledTimes(1);
  });

  it("does not run hooks when the language is set to what it already is", () => {
    App.lang = "pl";
    const hook = vi.fn();
    App.onRerender(hook);

    App.setLang("pl");

    expect(hook).not.toHaveBeenCalled();
  });

  it("runs hooks on a real theme change and not on a repeat", () => {
    App.theme = "light";
    const hook = vi.fn();
    App.onRerender(hook);

    App.setTheme("dark");
    App.setTheme("dark");

    expect(hook).toHaveBeenCalledTimes(1);
  });

  it("keeps going when one screen's redraw throws", () => {
    const broken = vi.fn(() => { throw new Error("boom"); });
    const healthy = vi.fn();
    App.onRerender(broken);
    App.onRerender(healthy);
    App.lang = "pl";

    expect(() => App.setLang("en")).not.toThrow();
    expect(healthy).toHaveBeenCalled();
  });
});

describe("shipped sample data", () => {
  it("defines no in-browser sample leads or partners", () => {
    expect(typeof globalThis.MockData).toBe("undefined");
    expect(() => vm.runInThisContext("MockData")).toThrow(ReferenceError);
  });
});

describe("App.applyTranslations — one key for several attributes", () => {
  it("writes the translation into every attribute named in data-i18n-attr", () => {
    document.body.innerHTML =
      '<button data-i18n="usun_etap" data-i18n-attr="title, aria-label" title="x" aria-label="x"></button>';
    App.lang = "en";
    App.applyTranslations();
    const button = document.querySelector("button");
    expect(button.getAttribute("title")).toBe(App.t("usun_etap"));
    expect(button.getAttribute("aria-label")).toBe(App.t("usun_etap"));
    expect(button.textContent).toBe("");
  });
});

describe("language detection and fallback", () => {
  it("picks the first of the browser's languages the app speaks", () => {
    expect(detectLanguage(["de-DE", "pl-PL", "en"])).toBe("pl");
    expect(detectLanguage(["en-US", "pl"])).toBe("en");
  });

  it("falls back to English when the browser asks for nothing the app speaks", () => {
    expect(detectLanguage(["de-DE", "fr"])).toBe("en");
    expect(detectLanguage([])).toBe("en");
    expect(detectLanguage(undefined)).toBe("en");
  });

  it("keeps the user's own choice over the browser's languages", () => {
    localStorage.setItem("ph_lang", "en");
    expect(initialLanguage()).toBe("en");
    localStorage.setItem("ph_lang", "pl");
    expect(initialLanguage()).toBe("pl");
  });

  it("ignores a stored choice for a language no longer shipped", () => {
    localStorage.setItem("ph_lang", "xx");
    expect(initialLanguage()).toBe("pl"); // the test browser speaks Polish
  });

  it("shows the English text for a key a translation has not caught up with", () => {
    const saved = i18n.pl.login_title;
    delete i18n.pl.login_title;
    try {
      App.lang = "pl";
      expect(App.t("login_title")).toBe(i18n.en.login_title);
    } finally {
      i18n.pl.login_title = saved;
    }
  });

  it("falls back to English for a language that is not shipped", () => {
    App.setLang("xx");
    expect(App.lang).toBe("en");
  });

  it("sets <html lang> with the language", () => {
    App.setLang("pl");
    expect(document.documentElement.lang).toBe("pl");
    App.setLang("en");
    expect(document.documentElement.lang).toBe("en");
  });

  it("uses each language's own plural rules", () => {
    App.lang = "pl";
    expect(App.pluralize(1, "unit_lead")).toBe(`1 ${i18n.pl.unit_lead.one}`);
    expect(App.pluralize(3, "unit_lead")).toBe(`3 ${i18n.pl.unit_lead.few}`);
    expect(App.pluralize(12, "unit_lead")).toBe(`12 ${i18n.pl.unit_lead.many}`);
    App.lang = "en";
    expect(App.pluralize(1, "unit_lead")).toBe(`1 ${i18n.en.unit_lead.one}`);
    expect(App.pluralize(3, "unit_lead")).toBe(`3 ${i18n.en.unit_lead.other}`);
  });
});

describe("language switchers come from the locale files", () => {
  it("draws one button per shipped language, named in that language", () => {
    document.body.innerHTML =
      '<div class="header-lang"><button class="header-lang-btn active" data-lang="pl">PL</button></div>';
    App.lang = "pl";
    renderLanguageSwitchers();
    const buttons = [...document.querySelectorAll(".header-lang-btn")];
    expect(buttons.map((b) => b.dataset.lang)).toEqual(App.languages());
    expect(buttons.find((b) => b.dataset.lang === "pl").title).toBe(PT_LOCALES.pl.meta.name);
    expect(buttons.find((b) => b.dataset.lang === "pl").classList.contains("active")).toBe(true);
  });
});
