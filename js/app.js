/* ============================================================
   PARTNERTYPE — APP CORE
   i18n, routing helpers, utility functions
   ============================================================ */

// ─── TRANSLATIONS ─────────────────────────────────────────
/* The dictionaries live in locales/<code>.json, one file per language, and the
   server hands all of them to the page as one script (/locales.js ->
   window.PT_LOCALES) before this file runs. English is the source and the
   fallback; see TRANSLATING.md. */
const i18n = {};
const LANGUAGE_META = {};
(function loadLocales(source) {
  Object.keys(source || {}).forEach((code) => {
    const locale = source[code];
    if (!locale || typeof locale.messages !== 'object') return;
    i18n[code] = locale.messages;
    LANGUAGE_META[code] = locale.meta || {};
  });
  if (!i18n.en) i18n.en = {};
})(typeof window !== 'undefined' ? window.PT_LOCALES : undefined);

/** Languages the app can show, English first, then by code. */
function availableLanguages() {
  return Object.keys(i18n).sort((x, y) => (x === 'en' ? -1 : y === 'en' ? 1 : x.localeCompare(y)));
}

/** The first of the browser's preferred languages the app speaks, else English. */
function detectLanguage(preferred) {
  const list = Array.isArray(preferred) ? preferred : [];
  for (const tag of list) {
    const code = String(tag || '').toLowerCase().split('-')[0];
    if (code && i18n[code]) return code;
  }
  return 'en';
}

/** The language for this visit: the user's own choice if it is still offered,
    otherwise what the browser asks for. */
function initialLanguage() {
  const stored = storedPreference('ph_lang', '');
  if (stored && i18n[stored]) return stored;
  const nav = typeof navigator !== 'undefined'
    ? (navigator.languages && navigator.languages.length ? navigator.languages : [navigator.language])
    : [];
  return detectLanguage(nav);
}

/* Reading and writing a stored preference must never be able to take the app
   down. `localStorage` is not merely empty in a private window or with site data
   blocked -- touching the property THROWS. These two reads sat at the top level
   of this file, so in that state js/app.js failed to evaluate at all and every
   screen lost its translations, its theme and its toolbar. */
function storedPreference(key, fallback) {
  try {
    return localStorage.getItem(key) || fallback;
  } catch (error) {
    return fallback;
  }
}

function storePreference(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch (error) {
    // The preference is lost at the next load; nothing else is.
  }
}

// ─── APP STATE ────────────────────────────────────────────
// Screens that know how to draw themselves again (see App.onRerender).
const rerenderHooks = new Set();

const App = {
  lang: initialLanguage(),
  theme: storedPreference('ph_theme', 'light'),
  role: null,
  user: null,

  setTheme(theme) {
    // Boot calls this with the value already in effect (see the init block at
    // the bottom of this file). Redrawing then would re-issue every screen's
    // first request a second time, so only a real change redraws.
    const changed = this.theme !== theme;
    this.theme = theme;
    storePreference('ph_theme', theme);
    document.documentElement.setAttribute('data-theme', theme);
    window.dispatchEvent(new CustomEvent('themeChanged', { detail: theme }));
    // Canvas charts bake the theme's colours in at draw time, so nothing on
    // screen follows a theme switch until it is drawn again.
    if (changed) this.rerender();
  },
  
  toggleTheme() {
    this.setTheme(this.theme === 'light' ? 'dark' : 'light');
  },

  /**
   * Look up one piece of copy.
   *
   * `params` fills {placeholders} in the dictionary value, so a sentence that
   * mentions a name or an amount stays ONE translatable string instead of
   * being glued together from fragments — word order differs between
   * languages, and concatenation freezes the Polish one.
   *
   *   App.t('confirm_delete_partner', { name: 'Acme' })
   */
  t(key, params) {
    const own = i18n[this.lang] && i18n[this.lang][key];
    // A key a translation has not caught up with shows the English text,
    // never the key name.
    const value = own !== undefined ? own : i18n.en[key];
    if (value === undefined) return key;
    if (!params) return value;
    return String(value).replace(/\{(\w+)\}/g, (whole, name) =>
      (params[name] === undefined ? whole : String(params[name])));
  },

  /**
   * The right plural form for `count`.
   *
   * A counted noun is an object keyed by the language's plural categories
   * (Intl.PluralRules: `one`, `few`, `many`, `other` ...): Polish needs three
   * forms, English two, and a new language brings whatever its rules need.
   * `other` is the fallback category.
   */
  plural(count, key) {
    const own = i18n[this.lang] && i18n[this.lang][key];
    const useOwn = own && typeof own === 'object';
    const forms = useOwn ? own : i18n.en[key];
    if (!forms || typeof forms !== 'object') return key;
    const n = Math.abs(Number(count));
    let category = 'other';
    try {
      category = new Intl.PluralRules(useOwn ? activeLocale() : 'en').select(Number.isFinite(n) ? n : 0);
    } catch (error) {
      category = n === 1 ? 'one' : 'other';
    }
    return forms[category] ?? forms.other ?? forms.many ?? Object.values(forms).pop();
  },

  /** `5 partners` in any language — the count and its plural form in one call. */
  pluralize(count, key) {
    return `${count} ${this.plural(count, key)}`;
  },

  /** Codes of every language a locale file was shipped for. */
  languages() {
    return availableLanguages();
  },

  /** A language's own name ("Polski", "Deutsch"), for the switcher. */
  languageName(code) {
    return (LANGUAGE_META[code] && LANGUAGE_META[code].name) || code.toUpperCase();
  },

  setLang(lang) {
    if (!i18n[lang]) lang = 'en';
    const changed = this.lang !== lang;
    this.lang = lang;
    storePreference('ph_lang', lang);
    this.applyTranslations();
    // applyTranslations only touches [data-i18n] nodes -- everything a screen
    // BUILDS in code (table cells, badges, charts, modals) keeps the old
    // language until it is rendered again. Without this, switching to English
    // would leave most of the app in Polish until the next data load.
    if (changed) this.rerender();
  },

  /**
   * Screens register how to draw themselves again.
   *
   * One hook per screen, registered on load; re-running it is what makes a
   * language or theme switch reach content that is not a [data-i18n] node.
   * A throw in one screen must not stop the others, so each is isolated.
   */
  onRerender(fn) {
    if (typeof fn === 'function') rerenderHooks.add(fn);
  },

  rerender() {
    rerenderHooks.forEach((fn) => {
      try {
        fn();
      } catch (error) {
        console.error('rerender hook', error);
      }
    });
  },

  applyTranslations() {
    document.querySelectorAll('[data-i18n]').forEach(el => {
      const key = el.getAttribute('data-i18n');
      const attr = el.getAttribute('data-i18n-attr');
      if (attr) {
        // One key may label several attributes of the same element, e.g. an
        // icon button's title and aria-label: data-i18n-attr="title,aria-label".
        attr.split(',').map((name) => name.trim()).filter(Boolean)
          .forEach((name) => el.setAttribute(name, this.t(key)));
      } else {
        el.textContent = this.t(key);
      }
    });

    document.documentElement.lang = this.lang;
    // Update lang buttons. `.gt-lang-btn` is the pill in the global toolbar --
    // the one on every producer screen. It was missing from this list, so the
    // chip lit up whichever language the markup happened to ship with and then
    // only corrected itself if the user clicked it: on load, and after any
    // language change made from code, it named the wrong language.
    document.querySelectorAll('.header-lang-btn, .lang-switcher-btn, .gt-lang-btn')
      .forEach(btn => {
        btn.classList.toggle('active', btn.dataset.lang === this.lang);
      });
  }
};

// Status badges are NOT rendered here. Both axes live in js/utils.js --
// Ui.registrationBadge for the producer's decision and Ui.stageBadge for the
// sales pipeline -- and they build DOM nodes rather than HTML strings. A
// second status-to-class map is how screens drift apart.

function formatCurrency(value) {
  if (!value) return '—';
  return new Intl.NumberFormat(activeLocale(), { style: 'currency', currency: 'PLN', maximumFractionDigits: 0 }).format(value);
}

/**
 * An amount with thousands separators but no currency symbol, for the places
 * that append their own unit ("+2 500 PLN" on a cash bonus) or that must show
 * a real zero rather than formatCurrency's em-dash.
 *
 * `.toFixed(0)` groups nothing, so a five-figure amount would run together as
 * one block of digits. Uses the same pl-PL grouping as formatCurrency, so the
 * two never disagree about how a number is spaced.
 */
function formatAmount(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return '0';
  return new Intl.NumberFormat(activeLocale(), { maximumFractionDigits: 0 }).format(number);
}

/**
 * The locale every date and amount is formatted in.
 *
 * Formatters must never be pinned to 'pl-PL' regardless of the language in
 * effect. Read per call, never cached, so a language switch
 * reaches the next render.
 */
function activeLocale() {
  const meta = LANGUAGE_META[App.lang];
  return (meta && meta.locale) || App.lang || 'en';
}

function formatDate(dateStr) {
  // Server timestamps are naive UTC; read as local time they can land on the
  // wrong calendar day for anything filed late in the evening. Ui loads after
  // this file, so resolve it at call time and fall back when it is absent.
  const parsed = (typeof Ui !== 'undefined' && Ui.parseServerDate)
    ? Ui.parseServerDate(dateStr)
    : new Date(dateStr);
  return new Intl.DateTimeFormat(activeLocale(), { day: '2-digit', month: 'short', year: 'numeric' }).format(parsed);
}

function getInitials(name) {
  if (!name) return '??';
  const parts = name.trim().split(' ');
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  return name.substring(0, 2).toUpperCase();
}

function getAvatarColorClass(name) {
  if (!name) return 'bg-nebula-indigo';
  const nebulaColors = ['bg-nebula-indigo', 'bg-nebula-cyan', 'bg-nebula-rose', 'bg-nebula-amber', 'bg-nebula-emerald'];
  let sum = 0;
  for (let i = 0; i < name.length; i++) {
    sum += name.charCodeAt(i);
  }
  return nebulaColors[sum % nebulaColors.length];
}

// ─── TOAST ───────────────────────────────────────────────
function showToast(type, title, message) {
  const icons = {
    success: `<svg width="18" height="18" fill="none" stroke="var(--success-400)" stroke-width="2" viewBox="0 0 24 24"><polyline points="20 6 9 17 4 12"/></svg>`,
    error:   `<svg width="18" height="18" fill="none" stroke="var(--error-400)" stroke-width="2" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>`,
    warning: `<svg width="18" height="18" fill="none" stroke="var(--warning-400)" stroke-width="2" viewBox="0 0 24 24"><path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>`,
  };

  const container = document.getElementById('toast-container');
  if (!container) return;

  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  const icon = document.createElement('div');
  icon.className = 'toast-icon';
  icon.innerHTML = icons[type] || icons.success;

  const body = document.createElement('div');
  body.className = 'toast-body';
  const titleElement = document.createElement('div');
  titleElement.className = 'toast-title';
  titleElement.textContent = title ?? '';
  body.appendChild(titleElement);

  if (message) {
    const messageElement = document.createElement('div');
    messageElement.className = 'toast-msg';
    messageElement.textContent = message;
    body.appendChild(messageElement);
  }

  toast.append(icon, body);
  container.appendChild(toast);

  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transform = 'translateY(8px) scale(0.96)';
    toast.style.transition = 'all 0.25s ease';
    setTimeout(() => toast.remove(), 300);
  }, 3200);
}

// ─── MODAL ────────────────────────────────────────────────
function openModal(id) {
  const overlay = document.getElementById(id);
  if (overlay) { overlay.classList.add('show'); document.body.style.overflow = 'hidden'; }
}

function closeModal(id) {
  const overlay = document.getElementById(id);
  if (overlay) { overlay.classList.remove('show'); document.body.style.overflow = ''; }
}

// Close on overlay click
document.addEventListener('click', e => {
  if (e.target.classList.contains('modal-overlay')) {
    e.target.classList.remove('show');
    document.body.style.overflow = '';
  }
});

// Close from a button that names its modal: data-close-modal="lead-modal".
//
// Not inline onclick attributes: those need `script-src 'unsafe-inline'`,
// which trusts every other inline script on the page too.
// Delegated from the document, so a button inside a modal that is built in code
// works the same as one that shipped in the HTML, with nothing to re-bind.
document.addEventListener('click', e => {
  const trigger = e.target.closest('[data-close-modal]');
  if (trigger) closeModal(trigger.dataset.closeModal);
});

// ─── LANG SWITCHER (delegated) ────────────────────────────
document.addEventListener('click', e => {
  const btn = e.target.closest('[data-lang]');
  if (btn && (btn.classList.contains('header-lang-btn') || btn.classList.contains('lang-switcher-btn') || btn.classList.contains('gt-lang-btn'))) {
    // setLang -> applyTranslations marks the active chip, for every switcher
    // and for every way the language can change. One place, not two.
    App.setLang(btn.dataset.lang);
  }
});

// ─── LANGUAGE SWITCHERS ───────────────────────────────────
// The markup ships one placeholder button per switcher; the real buttons are
// drawn from the locale files, so a new language appears here with no code
// change. Each button shows the code and names the language in itself.
function renderLanguageSwitchers(root) {
  const scope = root || document;
  const groups = new Set();
  scope.querySelectorAll('.header-lang-btn, .lang-switcher-btn, .gt-lang-btn')
    .forEach((button) => groups.add(button.parentElement));
  groups.forEach((group) => {
    const placeholders = Array.from(group.querySelectorAll(':scope > [data-lang]'));
    if (!placeholders.length) return;
    const first = placeholders[0];
    const classes = Array.from(first.classList).filter((name) => name !== 'active');
    const spacing = placeholders[1] ? placeholders[1].getAttribute('style') : null;
    placeholders.forEach((button) => button.remove());
    App.languages().forEach((code, index) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = classes.join(' ');
      button.dataset.lang = code;
      button.lang = code;
      button.textContent = code.toUpperCase();
      button.title = App.languageName(code);
      button.setAttribute('aria-label', App.languageName(code));
      if (index > 0 && spacing) button.setAttribute('style', spacing);
      button.classList.toggle('active', code === App.lang);
      group.appendChild(button);
    });
  });
}

// ─── GLOBAL TOOLBAR ───────────────────────────────────────
function injectGlobalToolbar() {
  const headerRight = document.querySelector('.header-right');
  const authSwitcher = document.querySelector('.auth-lang-switcher');
  const target = headerRight || authSwitcher;
  if (!target || document.getElementById('global-toolbar')) return;

  // Hide old inline lang switcher
  const oldLang = target.querySelector('.header-lang');
  if (oldLang) oldLang.style.display = 'none';

  // Build toolbar
  const toolbar = document.createElement('div');
  toolbar.id = 'global-toolbar';
  toolbar.className = 'global-toolbar';

  // Sun/Moon icons
  const sunIcon = `<svg width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/></svg>`;
  const moonIcon = `<svg width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M21 12.79A9 9 0 1111.21 3 7 7 0 0021 12.79z"/></svg>`;

  toolbar.innerHTML = `
    <div class="gt-group"><button class="gt-lang-btn" data-lang="en">EN</button></div>
    <div class="gt-divider"></div>
    <button class="gt-theme-btn" id="gt-theme-btn"></button>
  `;

  function renderThemeButton() {
    const isDark = App.theme === 'dark';
    themeBtn.innerHTML = isDark
      ? `${sunIcon}<span class="gt-theme-label">${App.t('theme_light')}</span>`
      : `${moonIcon}<span class="gt-theme-label">${App.t('theme_dark')}</span>`;
    themeBtn.title = App.t(isDark ? 'theme_light_title' : 'theme_dark_title');
  }

  // Theme toggle handler
  const themeBtn = toolbar.querySelector('#gt-theme-btn');
  renderThemeButton();
  themeBtn.addEventListener('click', () => {
    App.toggleTheme();
    renderThemeButton();
  });
  App.onRerender(renderThemeButton);

  renderLanguageSwitchers(toolbar);

  if (headerRight) {
    // Insert before notifications/avatar
    headerRight.insertBefore(toolbar, headerRight.firstChild);
  } else if (authSwitcher) {
    authSwitcher.innerHTML = '';
    authSwitcher.appendChild(toolbar);
  }
}

/* ─── NAVIGATION RAIL ─────────────────────────────────────────
   The menu can be collapsed and expanded. Collapsed, only icons stay, hinting
   at what is there.

   Lives here rather than in the page shells because the sidebar markup is
   duplicated across fourteen HTML files: injecting the control and reading the
   stored preference from one place is the only way this stays in step.

   The label is moved into `title`, never deleted -- an icon rail whose icons
   cannot be identified is worse than no rail.
   ─────────────────────────────────────────────────────────── */
const NAV_STORAGE_KEY = 'ph_nav';

function readNavPreference() {
  return storedPreference(NAV_STORAGE_KEY, 'expanded') === 'collapsed'
    ? 'collapsed'
    : 'expanded';
}

function applyNavPreference(state) {
  document.documentElement.dataset.nav = state;
  const toggle = document.getElementById('sidebar-toggle');
  if (!toggle) return;
  const collapsed = state === 'collapsed';
  toggle.setAttribute('aria-expanded', String(!collapsed));
  const label = App.t(collapsed ? 'nav_expand' : 'nav_collapse');
  toggle.title = App.t(collapsed ? 'nav_expand_menu' : 'nav_collapse_menu');
  toggle.setAttribute('aria-label', toggle.title);
  // The word is written out when there is room for it, and drops to the icon
  // alone on the rail -- where the tooltip above carries it instead.
  if (window.Icons) {
    Icons.label(toggle, collapsed ? 'chevron-right' : 'chevron-left', label, { size: 15 });
  }
}

// The sidebar lives as a copy in each of the fourteen pages, so its icons are
// named there (data-icon) and drawn here from the one icon set, js/icons.js.
// Every icon in the bar comes from our set and none repeats;
// js/__tests__/nav-icons.test.js holds each page to that.
function hydrateNavIcons() {
  if (!window.Icons) return;
  document.querySelectorAll('.sidebar-nav svg.nav-icon[data-icon]').forEach((placeholder) => {
    const icon = Icons.el(placeholder.dataset.icon, { cls: 'nav-icon' });
    if (!icon) return;
    icon.dataset.icon = placeholder.dataset.icon;
    placeholder.replaceWith(icon);
  });
}

function setupNavRail() {
  // In the footer, above the user block, close to the other icons. In the
  // logo row the wordmark would fight it for space in a 196px menu.
  const footer = document.querySelector('.sidebar-footer');
  if (!footer || document.getElementById('sidebar-toggle')) return;

  const toggle = document.createElement('button');
  toggle.type = 'button';
  toggle.id = 'sidebar-toggle';
  toggle.className = 'sidebar-toggle';
  // A sibling placed before .sidebar-user, NOT inside it -- that block is the
  // logout control, and a collapse button nested in it would log the producer out.
  footer.insertBefore(toggle, footer.firstChild);

  // Every nav entry keeps its words as a tooltip and as its accessible name,
  // because collapsed there is nothing else to go on.
  document.querySelectorAll('.sidebar-nav .nav-item').forEach((item) => {
    const text = item.textContent.trim();
    if (!text || item.title) return;
    item.title = text;
    if (!item.getAttribute('aria-label')) item.setAttribute('aria-label', text);
  });

  toggle.addEventListener('click', () => {
    const next = document.documentElement.dataset.nav === 'collapsed' ? 'expanded' : 'collapsed';
    storePreference(NAV_STORAGE_KEY, next);
    applyNavPreference(next);
  });

  applyNavPreference(readNavPreference());
  App.onRerender(() => applyNavPreference(readNavPreference()));
  // Only now may the rail animate; before this the stored state would visibly
  // swing shut on every page load.
  requestAnimationFrame(() => document.documentElement.classList.add('nav-ready'));
}

// ─── INIT ─────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  // Theme already set by inline script in <head>, just dispatch event
  App.setTheme(App.theme);
  renderLanguageSwitchers();
  App.applyTranslations();

  // Inject global toolbar
  injectGlobalToolbar();
  hydrateNavIcons();
  setupNavRail();

  // Reveal body (anti-flash)
  requestAnimationFrame(() => {
    document.body.classList.add('ready');
  });
});
