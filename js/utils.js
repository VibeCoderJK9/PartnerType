/* ============================================================
   PARTNERTYPE — SHARED UI SAFETY HELPERS
   One collision-free namespace for classic scripts.
   ============================================================ */

(function initUiNamespace(global) {
  'use strict';

  const Ui = global.Ui || {};
  const SAFE_DESTINATION = /^(?:\.\.\/){0,2}(?:[A-Za-z0-9_-]+\/)*[A-Za-z0-9_-]+\.html(?:[?#][^\s\\]*)?$/;

  Ui.escapeHtml = function escapeHtml(value) {
    return String(value ?? '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll("'", '&#x27;')
      .replaceAll('"', '&quot;');
  };

  Ui.normalizeDestination = function normalizeDestination(value) {
    if (typeof value !== 'string') return null;
    const destination = value.trim().replace(/^\.\//, '');
    if (!destination || /[\u0000-\u001f\u007f\\]/.test(destination)) return null;
    if (/%(?:0[0-9a-f]|1[0-9a-f]|7f)/i.test(destination)) return null;
    if (!SAFE_DESTINATION.test(destination)) return null;
    return destination;
  };

  Ui.safeNavigate = function safeNavigate(value, assign) {
    const destination = Ui.normalizeDestination(value);
    if (!destination) return false;
    const navigate = typeof assign === 'function'
      ? assign
      : (next) => { global.location.href = next; };
    navigate(destination);
    return true;
  };

  Ui.rehydrateSession = async function rehydrateSession(apiClient) {
    const api = apiClient || global.ApiClient;
    if (!api || typeof api.get !== 'function') {
      throw new TypeError('ApiClient.get is required');
    }
    const data = await api.get('/api/auth/me');
    const user = data && data.user ? data.user : null;
    sessionStorage.removeItem('ph_token');
    if (user) sessionStorage.setItem('ph_session', JSON.stringify(user));
    else sessionStorage.removeItem('ph_session');
    return user;
  };

  /* ── Configurable stage pipeline ─────────────────
     One shared implementation of "what does a lead's stage mean" so no
     producer-side controller compares a lead's stage against a literal
     English word. Every controller fetches GET /api/lead-stages once and
     hands the raw array to Ui.buildStagePipeline(); everything else below
     reads roles (kind / is_terminal / sort_order)
     off that structure, never a hardcoded key. */

  Ui.buildStagePipeline = function buildStagePipeline(stages) {
    const list = (Array.isArray(stages) ? stages.slice() : []).sort((a, b) => (
      (a.is_terminal ? 1 : 0) - (b.is_terminal ? 1 : 0)
      || (Number(a.sort_order) || 0) - (Number(b.sort_order) || 0)
      || (Number(a.id) || 0) - (Number(b.id) || 0)
    ));
    const byKey = new Map(list.map((stage) => [stage.key, stage]));
    const nonTerminal = list.filter((stage) => !stage.is_terminal);
    const winTerminal = list.find((stage) => stage.is_terminal && stage.kind === 'win') || null;
    const loseTerminal = list.find((stage) => stage.is_terminal && stage.kind === 'lose') || null;
    // Where an accepted deal starts its sale.
    const firstStage = nonTerminal.length ? nonTerminal[0] : null;

    return { list, byKey, nonTerminal, firstStage, winTerminal, loseTerminal };
  };

  // ── The registration axis ────────────────────────────────
  // Four FIXED values, unlike the sales pipeline above them: this is the
  // producer's decision about a submitted deal, and each value has real
  // behaviour attached server-side (a notification, an email, an audit key),
  // so it is not producer-configurable. Mirrors
  // api/leads.py::REGISTRATION_STATUSES.
  Ui.REGISTRATION_STATUSES = Object.freeze(['pending', 'needs_info', 'accepted', 'rejected']);
  /**
   * The decision's name, resolved when it is rendered.
   *
   * This was a frozen object of Polish strings built at load time, so a
   * language switch could never reach it: every screen that named a decision
   * kept saying it in Polish. Reading through App.t at call time is what makes
   * the label follow the language.
   */
  Ui.registrationLabel = function registrationLabel(status) {
    if (!Ui.REGISTRATION_STATUSES.includes(status)) return null;
    return (typeof App !== 'undefined' && App.t)
      ? App.t('reg_status_' + status)
      : status;
  };

  /** Every decision with its current label, for building a filter list. */
  Ui.registrationLabels = function registrationLabels() {
    return Ui.REGISTRATION_STATUSES.reduce((all, status) => {
      all[status] = Ui.registrationLabel(status);
      return all;
    }, {});
  };
  Ui.REGISTRATION_BADGE_CLASSES = Object.freeze({
    pending: 'badge-warning',
    needs_info: 'badge-info',
    accepted: 'badge-success',
    rejected: 'badge-error',
  });

  /**
   * Render a lead's registration decision as an inert badge element.
   *
   * Deliberately the LOUD form (`badge-solid`, a filled block). This axis is
   * the producer's own decision about whether a deal enters the pipeline at
   * all, and it has to out-shout the sales stage beside it in the same table
   * cell -- Ui.stageBadge below is the quiet, outline-only counterpart.
   */
  Ui.registrationBadge = function registrationBadge(value) {
    const badge = document.createElement('span');
    const tone = Ui.REGISTRATION_BADGE_CLASSES[value] || 'badge-warning';
    badge.className = `badge badge-solid ${tone}`;
    badge.textContent = Ui.registrationLabel(value) || value || "—";
    return badge;
  };

  /**
   * Is this lead still waiting for the producer's decision?
   *
   * A single value read now, not a position inferred from the sales
   * pipeline: a deal is waiting while it is 'pending' or while the producer
   * has asked for more information and not yet decided. This is the rule
   * behind the "Po terminie" KPI and the overdue filter. Mirrored by
   * api/stats.py::count_overdue_leads.
   */
  Ui.isAwaitingDecision = function isAwaitingDecision(lead) {
    const value = lead && lead.registration_status;
    return value === 'pending' || value === 'needs_info';
  };

  Ui.stageFor = function stageFor(pipeline, key) {
    if (!pipeline || !pipeline.byKey || key === undefined || key === null) return null;
    return pipeline.byKey.get(key) || null;
  };

  /** Product-owned terminals follow the UI language; producer stage names are data. */
  Ui.stageLabel = function stageLabel(stage) {
    if (!stage) return '';
    if (stage.is_terminal && stage.kind === 'win') return App.t('stage_terminal_win');
    if (stage.is_terminal && stage.kind === 'lose') return App.t('stage_terminal_lose');
    return Ui.producerLabel(stage, 'name') || String(stage.key ?? '');
  };

  /**
   * One producer-written label, in the reader's language.
   *
   * The app's own copy lives in a dictionary; the words a PRODUCER invents --
   * a stage he called "Negocjacje", a question he wrote himself -- are data,
   * and the English version is a second column he fills in (by hand, or by
   * asking the assistant). Empty means "not translated yet", and then the
   * reader gets the Polish original: a name he can at least act on beats an
   * empty caption above an input.
   */
  Ui.producerLabel = function producerLabel(record, field) {
    if (!record) return '';
    const base = String(record[field] ?? '');
    if (App.lang !== 'en') return base;
    const english = String(record[`${field}_en`] ?? '').trim();
    return english || base;
  };

  /** The producer's own label for one custom field (lead or registration). */
  Ui.fieldLabel = function fieldLabel(field) {
    return Ui.producerLabel(field, 'label');
  };

  // The queue share a given submission position earns, read from the
  // producer's configured lead_queue_shares. Shared so the producer's lead
  // detail and the partner's own screens cannot disagree about one lead.
  Ui.computeQueueShare = function computeQueueShare(position, sharesCsv) {
    const shares = String(sharesCsv ?? '100')
      .split(',')
      .map(part => parseFloat(part.trim()))
      .filter(Number.isFinite);
    if (!shares.length) return 0;
    const index = Math.min(Math.max(position, 1), shares.length) - 1;
    return shares[index];
  };

  // The API writes timestamps with datetime.utcnow().isoformat(), which
  // carries no timezone marker -- so `new Date(iso)` reads them as local
  // time and every age/deadline is wrong by the UTC offset (a lead filed
  // seconds ago reads as "2 godz." in CEST). Parse them as the UTC they
  // are. Values that already carry a marker are passed through untouched.
  Ui.parseServerDate = function parseServerDate(iso) {
    if (iso instanceof Date) return iso;
    if (typeof iso !== 'string' || !iso) return new Date(NaN);
    const hasZone = /(?:Z|[+-]\d{2}:?\d{2})$/.test(iso);
    return new Date(hasZone ? iso : `${iso}Z`);
  };

  // Which rows offer the producer an Accept/Reject control. Identical to
  // isAwaitingDecision, since the decision is its own column.
  Ui.isLeadUnhandled = function isLeadUnhandled(lead) {
    return Ui.isAwaitingDecision(lead);
  };

  // Reachability among non-terminal stages only: their sort_order values
  // are internally consistent with one another. Terminal stages sit
  // outside that ordering (a producer's terminal sort_order is not
  // guaranteed to be numerically larger than every non-terminal one), so
  // a lead already on a terminal stage has nothing left to "advance" to.
  Ui.reachableAdvanceStages = function reachableAdvanceStages(lead, pipeline) {
    if (!pipeline) return [];
    const current = Ui.stageFor(pipeline, lead && lead.status);
    if (current && current.is_terminal) return [];
    const currentSort = current ? Number(current.sort_order) : -Infinity;
    return pipeline.nonTerminal.filter((stage) => Number(stage.sort_order) > currentSort);
  };

  /**
   * A producer-chosen colour, adjusted so it can actually be READ as text on
   * the current theme's card.
   *
   * Colours like the tier indigo or a pale
   * stage grey are picked in a colour picker, where nothing stops the
   * producer choosing something that disappears on a white card -- and the
   * same colour that reads on graphite is the one that vanishes on paper.
   *
   * Only LIGHTNESS is clamped, never hue or saturation: the badge stays
   * recognisably "his" colour, it just stops being invisible. The raw value
   * is still the right thing for a fill (a dot, a column marker), where the
   * colour is the whole point and nothing has to be read out of it.
   *
   * @param {string} hex a #rrggbb colour, or anything else for the fallback
   * @returns {string} a css colour safe to use as `color:` on a card
   */
  Ui.readableInk = function readableInk(hex, theme) {
    const raw = String(hex || '');
    if (!/^#[0-9a-fA-F]{6}$/.test(raw)) return 'var(--text-secondary)';
    const dark = (theme || (typeof App !== 'undefined' && App.theme) || 'light') === 'dark';
    const [r, g, b] = [1, 3, 5].map(i => parseInt(raw.slice(i, i + 2), 16) / 255);
    const max = Math.max(r, g, b), min = Math.min(r, g, b);
    const l = (max + min) / 2;
    const d = max - min;
    // A grey has no hue to preserve, so there is nothing to gain from
    // keeping it: it becomes plain body text rather than a washed-out smudge.
    if (d < 0.04) return 'var(--text-secondary)';
    const s = d / (1 - Math.abs(2 * l - 1));
    let h;
    if (max === r)      h = ((g - b) / d) % 6;
    else if (max === g) h = (b - r) / d + 2;
    else                h = (r - g) / d + 4;
    h = (h * 60 + 360) % 360;
    // Light card: nothing lighter than 42% reads at badge size. Graphite card:
    // nothing darker than 62%. Inside the band the producer's colour is used
    // exactly as he picked it.
    const clamped = dark ? Math.max(l, 0.62) : Math.min(l, 0.42);
    // The comma form on purpose: the space-separated CSS Color 4 syntax is fine
    // in browsers but is dropped outright by some DOM implementations (the test
    // realm among them), which turns a colour into no colour at all.
    return `hsl(${Math.round(h)}, ${Math.round(Math.min(s, 0.95) * 100)}%, ${Math.round(clamped * 100)}%)`;
  };

  /**
   * One shared badge renderer for a lead's stage, consumed by every
   * producer controller. Colour is applied as a style value, never
   * interpolated into markup; an unrecognised stage renders an
   * explicit, honestly-labelled neutral badge rather than a blank.
   *
   * The QUIET form: hairline outline in the producer's own stage colour, no
   * fill. Registration decisions are filled blocks (Ui.registrationBadge), so
   * the two axes stay distinguishable at a glance even when they sit in the
   * same cell.
   */
  Ui.stageBadge = function stageBadge(stage) {
    const badge = document.createElement('span');
    badge.className = 'badge';
    if (!stage) {
      badge.classList.add('badge-neutral');
      badge.appendChild(document.createTextNode(App.t('al_stage_unknown')));
      return badge;
    }
    const color = Ui.readableInk(stage.color);
    badge.style.color = color;
    badge.style.background = 'transparent';
    badge.style.borderColor = color;
    const dot = document.createElement('span');
    dot.className = 'dot';
    badge.appendChild(dot);
    badge.appendChild(document.createTextNode(Ui.stageLabel(stage)));
    return badge;
  };

  /**
   * Pick the right Polish plural form for a count.
   *
   * Polish has three plural forms, not two (one, few, many). Every call site in
   * this app used the English two-form shape and produced the "many" form for
   * 2, 3 and 4 — wrong in a way a Polish reader notices
   * immediately, on screens the product owner reads every day.
   *
   * The `few` form covers counts ending in 2–4, except the teens (12, 13, 14),
   * which take `many` like every other teen.
   *
   * @param {number} count
   * @param {string} one  form for exactly 1
   * @param {string} few  form for 2–4, 22–24, …
   * @param {string} many form for 0, 5–21, 25–31, …
   * @returns {string} the chosen form, without the number
   */
  /*
     The inline decision form: a row spliced in beneath a lead so the producer
     can type a rejection reason or an information request without leaving the
     screen they are working on.

     It lives here rather than in a controller because two screens need it --
     the leads list and the dashboard's "Recent leads" widget -- and a second
     copy is how the status badges drifted apart before. Both messages reach the
     partner who submitted the deal, which is why the placeholders say so.
  */
  // Built on demand rather than frozen at load time, for the same reason as
  // the decision labels above: the copy has to be read in the current language
  // at the moment the form is opened.
  Ui.rowDecisionForm = function rowDecisionForm(action) {
    const t = (key) => (typeof App !== 'undefined' && App.t) ? App.t(key) : key;
    if (action === 'reject') {
      return {
        title: t('decision_reject_title'),
        hint: t('decision_reject_hint'),
        placeholder: t('decision_reject_placeholder'),
        confirm: t('decision_reject_confirm'),
        confirmClass: 'btn btn-danger btn-sm',
        missing: t('decision_reject_missing'),
      };
    }
    if (action === 'request_info') {
      return {
        title: t('decision_info_title'),
        hint: t('decision_info_hint'),
        placeholder: t('decision_info_placeholder'),
        confirm: t('decision_info_confirm'),
        confirmClass: 'btn btn-primary btn-sm',
        missing: t('decision_info_missing'),
      };
    }
    // Anything else is not a decision this form knows how to ask about.
    return null;
  };

  Ui.closeRowDecisionForm = function closeRowDecisionForm(container) {
    container?.querySelector('.row-inline-form')?.remove();
    container?.querySelectorAll('.btn-row[aria-expanded="true"]')
      .forEach((button) => button.setAttribute('aria-expanded', 'false'));
  };

  /**
   * Open (or toggle shut) the decision form under one row.
   *
   * Opening one closes any other: two open forms would let a producer type a
   * rejection reason into the wrong lead. An empty message is refused rather
   * than sent, because the partner has to be told what to fix.
   *
   * @param {HTMLTableRowElement} row   the lead's own row
   * @param {'reject'|'request_info'} action
   * @param {(message: string) => Promise<unknown>} submit
   */
  Ui.openRowDecisionForm = function openRowDecisionForm(row, action, submit) {
    const container = row.parentElement;
    const trigger = row.querySelector(`[data-action="${action}"]`);
    const wasOpen = trigger?.getAttribute('aria-expanded') === 'true';
    Ui.closeRowDecisionForm(container);
    if (wasOpen) return;
    trigger?.setAttribute('aria-expanded', 'true');

    const copy = Ui.rowDecisionForm(action);
    if (!copy) return null;
    const formRow = document.createElement('tr');
    formRow.className = 'row-inline-form';
    const cell = document.createElement('td');
    cell.colSpan = row.children.length;

    const inner = document.createElement('div');
    inner.className = 'row-inline-form-inner';

    const title = document.createElement('div');
    title.className = 'row-inline-form-title';
    title.appendChild(document.createTextNode(`${copy.title} `));
    const hint = document.createElement('span');
    hint.textContent = `· ${copy.hint}`;
    title.appendChild(hint);

    const field = document.createElement('textarea');
    field.className = 'form-input';
    field.id = `inline-${action}-${row.dataset.recordKey || 'row'}`;
    field.placeholder = copy.placeholder;
    field.maxLength = 1000;

    const error = document.createElement('div');
    error.className = 'row-inline-form-error';
    error.hidden = true;

    const confirm = document.createElement('button');
    confirm.type = 'button';
    confirm.className = copy.confirmClass;
    confirm.textContent = copy.confirm;

    const cancel = document.createElement('button');
    cancel.type = 'button';
    cancel.className = 'btn btn-ghost btn-sm';
    cancel.textContent = App.t('anuluj');

    confirm.addEventListener('click', async () => {
      const message = field.value.trim();
      if (!message) {
        error.textContent = copy.missing;
        error.hidden = false;
        field.focus();
        return;
      }
      confirm.disabled = true;
      cancel.disabled = true;
      await submit(message);
      Ui.closeRowDecisionForm(container);
    });
    cancel.addEventListener('click', () => Ui.closeRowDecisionForm(container));

    const buttons = document.createElement('div');
    buttons.className = 'row-inline-form-actions';
    buttons.append(confirm, cancel);

    inner.append(title, field, error, buttons);
    cell.appendChild(inner);
    formRow.appendChild(cell);
    row.after(formRow);
    field.focus();
  };

  /*
     A second, STICKY horizontal scrollbar for a wide table.

     It must stay reachable for somebody without a trackpad who has scrolled
     down the page.

     A table's own scrollbar sits at its bottom edge, so on a long table it is
     below the fold. This adds a thin rail pinned to the top of the visible
     scroll area and keeps the two in sync in both directions. The rail holds a
     spacer as wide as the table, which is the only way a scrollbar can exist
     without the content it scrolls.

     Idempotent: called again after a re-render it only refreshes widths. Safe
     without ResizeObserver (older engines just keep the width from the last
     call plus the window resize listener).
  */
  const tableScrollHosts = new WeakSet();

  function syncRailWidth(scroller, rail, inner) {
    const width = scroller.scrollWidth;
    inner.style.width = `${width}px`;
    const overflowing = width - scroller.clientWidth > 1;
    rail.dataset.overflowing = String(overflowing);
    // Match the rail's own scrollable width to the table's, or dragging the
    // thumb to its end would stop short of the table's last column.
    if (overflowing) rail.scrollLeft = scroller.scrollLeft;
  }

  Ui.attachTableScroll = function attachTableScroll(root) {
    const scope = root || document;
    scope.querySelectorAll('.table-scroll').forEach((scroller) => {
      if (tableScrollHosts.has(scroller)) {
        const existing = scroller.previousElementSibling;
        if (existing && existing.classList.contains('table-scroll-rail')) {
          syncRailWidth(scroller, existing, existing.firstElementChild);
        }
        return;
      }
      tableScrollHosts.add(scroller);

      const rail = document.createElement('div');
      rail.className = 'table-scroll-rail';
      rail.setAttribute('aria-hidden', 'true');
      const inner = document.createElement('div');
      inner.className = 'table-scroll-rail-inner';
      rail.appendChild(inner);
      scroller.parentNode.insertBefore(rail, scroller);

      // One guard for both directions: without it each scroll event sets the
      // other element, which fires its own event, and the two fight.
      let syncing = false;
      const mirror = (from, to) => {
        if (syncing) return;
        syncing = true;
        to.scrollLeft = from.scrollLeft;
        requestAnimationFrame(() => { syncing = false; });
      };
      rail.addEventListener('scroll', () => mirror(rail, scroller));
      scroller.addEventListener('scroll', () => mirror(scroller, rail));

      syncRailWidth(scroller, rail, inner);
      window.addEventListener('resize', () => syncRailWidth(scroller, rail, inner));

      if (typeof ResizeObserver === 'function') {
        // The table's width changes when rows render, not only when the window
        // does -- every one of these tables is filled by a later fetch.
        const observer = new ResizeObserver(() => syncRailWidth(scroller, rail, inner));
        observer.observe(scroller);
        const table = scroller.querySelector('table');
        if (table) observer.observe(table);
      }
    });
  };

  Ui.plPlural = function plPlural(count, one, few, many) {
    const n = Math.abs(Number(count));
    if (!Number.isFinite(n)) return many;
    if (n === 1) return one;
    const lastTwo = n % 100;
    if (lastTwo >= 12 && lastTwo <= 14) return many;
    const last = n % 10;
    return last >= 2 && last <= 4 ? few : many;
  };

  global.Ui = Ui;
})(window);
