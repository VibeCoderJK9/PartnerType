/* ============================================================
   PARTNERTYPE — PARTNER PANEL LOGIC
   All data fetched from /api/* endpoints.
   ============================================================ */

// ─── INIT ─────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  initPartnerPage();
});

let partnerInitializationPromise = null;

// ─── CONFIGURABLE STAGE PIPELINE (shared with the producer side) ──────
// The dashboard and the lead list both need "what does this lead's stage mean" -- fetched once per
// page load by whichever page-specific init function runs (only one of the
// three ever runs per page in this classic multi-page app), held here so
// every render/derivation helper below reads it without a parameter chain.
let partnerStagePipeline = null;
// The producer's own questions and the version they were fetched at. Kept
// module-scoped so the submit handler sends the version the form was actually
// built from, not whatever is current at click time.
let partnerLeadFields = [];
let partnerLeadFieldsVersion = null;
// The producer's own product names too -- kept so a language switch can
// relabel the already-rendered <option> elements (Ui.producerLabel) without
// throwing away whatever the partner has already picked and typed.
let partnerLeadProducts = [];
let updatePartnerLeadRecord = null;
// Re-applies the current search/filter to the registration table. Assigned by
// initPartnerLeads (it closes over that run's lead list) and read by the
// re-render hook, so it has to outlive the call.
let filterLeads = () => {};
// initPartnerLeads runs again on every retry; the hook must not stack up.
let partnerLeadsRerenderBound = false;
let partnerDashboardRerenderBound = false;
let newLeadRerenderBound = false;
// Every lead this partner owns, as last fetched (see initPartnerLeads).
let partnerAllLeads = [];

// ─── PARTNER KANBAN ─────────────────────────────────────
// Registration/Sales view split: a partner sees Sales by
// default. Board state is
// module-scoped for the same one-page-instance-per-load reason as the stage
// pipeline above.
let partnerLeadsView = 'sales';
let partnerSalesLeads = [];
let partnerKanbanDraggingId = null;
const partnerKanbanBusyIds = new Set();
// Held so a failed move can refetch from the server instead of only
// redrawing whatever was already in memory -- a
// conflict or an uncertain (timeout/dropped-connection) outcome both call
// for a refetch, since the local copy may now be stale either way, and an
// uncertain outcome specifically might mean the write actually landed and
// the response was what was lost, not the write itself.
let partnerLeadsSession = null;
document.addEventListener('partner-lead-updated', event => updatePartnerLeadRecord?.(event.detail?.lead));

// Deliberately does not swallow a rejection: both callers below run this
// inside the same Promise.all as their other page data, so a failed
// stage-list fetch surfaces through that page's existing error state and
// retry path exactly like a failed leads/settings/stats fetch already does
// -- never a screen of unknown-value badges.
async function loadPartnerStagePipeline() {
  const data = await ApiClient.get('/api/lead-stages');
  return Ui.buildStagePipeline(data.stages || []);
}

function initPartnerPage() {
  if (partnerInitializationPromise) return partnerInitializationPromise;
  partnerInitializationPromise = (async () => {
    let session;
    try {
      session = await Ui.rehydrateSession(ApiClient);
    } catch (err) {
      handleApiError(err, 'initPartnerPage');
      // The login page sends anyone with a cached session straight back here,
      // so the cache has to go or a failing /api/auth/me loops forever.
      sessionStorage.removeItem('ph_session');
      Ui.safeNavigate('../../index.html');
      return;
    }

    if (!session || session.role !== 'partner') {
      Ui.safeNavigate('../../index.html');
      return;
    }

    initPartnerSidebar(session);
    App.applyTranslations();

    const page = document.body.dataset.page;
    if (page === 'partner-dashboard') await initPartnerDashboard(session);
    if (page === 'partner-leads')     await initPartnerLeads(session);
    if (page === 'partner-new-lead')  initNewLeadForm(session);
    // See the same call in js/admin-shell.js: the rails measure real table
    // widths, so they attach after the page's own rows are painted.
    Ui.attachTableScroll(document);
    return session;
  })();
  return partnerInitializationPromise;
}

// ─── SIDEBAR ──────────────────────────────────────────────
function initPartnerSidebar(session) {
  const nameEl = document.getElementById('sidebar-user-name');
  const roleEl = document.getElementById('sidebar-user-role');
  const initEl = document.getElementById('sidebar-avatar');
  if (nameEl) nameEl.textContent = session.name;
  if (roleEl) roleEl.textContent = session.company;
  if (initEl) initEl.textContent = getInitials(session.name);

  document.getElementById('mobile-menu-btn')?.addEventListener('click', () => {
    document.querySelector('.sidebar').classList.toggle('open');
  });

  document.getElementById('logout-btn')?.addEventListener('click', async () => {
    try { await ApiClient.post('/api/auth/logout', {}); } catch {}
    ApiClient.clearToken();
    Ui.safeNavigate('../../index.html');
  });

  const currentHref = window.location.pathname.split('/').pop();
  document.querySelectorAll('.nav-item').forEach(item => {
    if (item.getAttribute('href') === currentHref) item.classList.add('active');
  });
}

// ─── PARTNER DASHBOARD ────────────────────────────────────
// Four partner-facing figures: leads needing the partner's own completion and
// decisions made by the producer (both counted from his own leads), then the
// money due and the wins awaiting the producer's confirmation -- read from
// GET /api/my-earnings, never summed here. No comparison against a programme
// average is shown to partners.

async function initPartnerDashboard(session) {
  const welcomeEl = document.getElementById('welcome-name');
  if (welcomeEl) welcomeEl.textContent = session.name.split(' ')[0];

  try {
    const [statsData, leadsData, stagePipeline, tierSummary, earnings] = await Promise.all([
      ApiClient.get('/api/stats'),
      ApiClient.get('/api/leads'),
      loadPartnerStagePipeline(),
      // Same source the earnings screen reads its rate from, so both screens
      // answer "what tier am I on?" with one voice.
      ApiClient.get(`/api/partner-bonuses/${session.id}`).catch(() => null),
      // The money figures: the same endpoint the earnings screen reads.
      ApiClient.get('/api/my-earnings').catch(() => null),
    ]);
    const leads = leadsData.leads || [];
    partnerStagePipeline = stagePipeline;

    renderPartnerKPIs(leads, earnings);
    renderMyRecentLeads(leads.slice(0, 4));

    renderDashboardTierBadge(tierSummary);

    // Acceptance rate bar -- the partner's own rate only, no programme
    // average is computed or rendered anywhere on this page.
    const total = statsData.total_leads || 0;
    const acc   = statsData.accepted_leads || 0;
    const rate  = total > 0 ? Math.round((acc / total) * 100) : 0;
    const rateEl = document.getElementById('acceptance-rate');
    if (rateEl) rateEl.textContent = rate + '%';
    const barEl = document.getElementById('acceptance-rate-bar');
    if (barEl) {
      setTimeout(() => { barEl.style.width = rate + '%'; }, 100);
      barEl.className = `progress-fill ${rate >= 70 ? 'green' : rate >= 40 ? '' : 'red'}`;
    }

    renderDashboardCommissionRate(session, tierSummary);

    // Everything above is drawn in code -- KPI values, lead cards, stage
    // badges, the rate rows -- so a language or theme switch has to run it
    // again. Bound once; initPartnerDashboard has no retry path of its own.
    if (!partnerDashboardRerenderBound) {
      partnerDashboardRerenderBound = true;
      App.onRerender(() => {
        renderPartnerKPIs(leads, earnings);
        renderMyRecentLeads(leads.slice(0, 4));
        renderDashboardTierBadge(tierSummary);
        renderDashboardCommissionRate(session, tierSummary);
      });
    }
  } catch (err) {
    handleApiError(err, 'initPartnerDashboard');
  }
}

// Tier badge.
//
// Name and colour come from the same endpoint the earnings screen reads, so
// the dashboard and the earnings screen answer "what tier am I on?" with one
// voice, never from the legacy `session.level` word.
//
// Its own function because the INK depends on the theme: a theme switch
// redraws the badge in the new theme's colour.
function renderDashboardTierBadge(tierSummary) {
  const levelEl = document.getElementById('partner-level');
  if (!levelEl) return;
  const tier = tierSummary?.partner || null;
  // Follows the reader's language: Ui.producerLabel returns ''
  // for a null tier and falls back to the Polish original when untranslated,
  // matching the badge's own no-tier logic below.
  const tierName = tier ? (Ui.producerLabel(tier, 'tier_name') || null) : null;
  levelEl.replaceChildren();
  const badge = document.createElement('span');
  badge.className = `level-badge${tierName ? '' : ' none'}`;
  // /api/partner-bonuses names the tier colour `color` (api/partner_bonuses.py:80).
  if (tierName && /^#[0-9a-fA-F]{6}$/.test(String(tier.color || ''))) {
    // Ui.readableInk, not the raw value: a tier colour is picked in a colour
    // picker with nothing to stop it vanishing on a white card.
    const ink = Ui.readableInk(tier.color);
    badge.style.color = ink;
    badge.style.border = `1px solid ${ink}`;
  }
  Icons.label(badge, 'star', tierName || App.t('ap_no_tier'), { size: 14 });
  levelEl.appendChild(badge);
}

// The "Commissions — how do they work?" panel answers "what do I earn?", so it has
// to answer it for THIS partner. Reads the same tier+bonus source as the
// Earnings screen instead of restating a
// static rate card that no partner's own rate has to appear in.
// `summary` is the already-fetched /api/partner-bonuses/{id} payload, or null
// when that call failed. It is passed in rather than re-fetched so the tier
// badge and this rate panel can never disagree about the same partner, and so
// the dashboard makes one request for it instead of two.
function renderDashboardCommissionRate(session, summary) {
  const container = document.getElementById('dashboard-commission-rows');
  if (!container || !session || !session.id) return;

  const partner = summary && summary.partner;
  if (!partner) return;  // leave the honest placeholder dash in place

  const rows = [];
  // "no tier" in plain words, not the "tier" jargon: the badge above this panel and the whole
  // earnings screen both say "poziom", and three names for one concept is how
  // a partner ends up unsure whether they are looking at the same thing.
  // Follows the reader's language, same as the badge above: this
  // panel and the badge read the same tierSummary.partner object, so they
  // must never disagree about which language a tier name is shown in.
  const tierName = Ui.producerLabel(partner, 'tier_name') || App.t('ap_no_tier');
  const baseRate = Number(partner.base_rate);
  const total    = Number(partner.total_percent);
  if (!Number.isFinite(total)) return;

  const bonusPct = Number.isFinite(baseRate) ? total - baseRate : 0;
  if (Number.isFinite(baseRate)) {
    rows.push([App.t('cm_base_rate', { tier: tierName }), `${formatShareValue(baseRate)}%`, 'var(--brand-400)']);
  }
  if (bonusPct > 0) {
    rows.push([App.t('pt_percent_bonuses'), `+${formatShareValue(bonusPct)}%`, 'var(--brand-300)']);
  }
  rows.push([App.t('pt_your_total_rate'), `${formatShareValue(total)}%`, 'var(--warning-400)']);

  container.replaceChildren(...rows.map(([label, value, color]) => {
    const row = document.createElement('div');
    row.className = 'stat-row';
    const l = document.createElement('span');
    l.className = 'stat-row-label';
    l.textContent = label;
    const v = document.createElement('span');
    v.className = 'stat-row-value';
    v.style.color = color;
    v.textContent = value;
    row.append(l, v);
    return row;
  }));
}

// The two counts read the registration axis, never a movable mid-pipeline
// stage:
//   needsCompletion -- the producer asked this partner for more data
//   newDecisions    -- the producer has ruled on these, either way
// The two amounts are the server's own totals: `due` (confirmed wins and
// granted bonuses not yet paid) and `awaiting` (wins the producer has not
// confirmed yet). Unreadable earnings render "—", never a guess.
function renderPartnerKPIs(leads, earnings) {
  const needsCompletion = leads.filter(l => l.registration_status === 'needs_info').length;
  const newDecisions = leads.filter(
    l => l.registration_status === 'accepted' || l.registration_status === 'rejected',
  ).length;
  const totals = earnings?.totals;

  setKPI('kpi-total',    needsCompletion);
  setKPI('kpi-pending',  newDecisions);
  setKPI('kpi-accepted', totals ? kpiMoney(totals.due) : '—');
  setKPI('kpi-earnings', totals ? kpiMoney(totals.awaiting) : '—');
}

// A real zero reads "0 PLN": formatCurrency turns 0 into "—", which on this
// dashboard would say "could not read" when the honest answer is "nothing".
function kpiMoney(value) {
  const amount = Number(value) || 0;
  return new Intl.NumberFormat(App.t('pk_en_gb'),
    { style: 'currency', currency: 'PLN', maximumFractionDigits: 0 }).format(amount);
}

function setKPI(id, value) {
  const el = document.getElementById(id);
  // Explicit String() -- assigning the bare number 0 to .textContent is
  // coerced to an empty string in some DOM implementations, which would
  // silently hide a genuine zero count instead of rendering it plainly.
  if (el) el.textContent = String(value);
}

const EMPTY_STATE_ICON_PATH = 'M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2';

function buildEmptyStateIcon() {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('width', '24');
  svg.setAttribute('height', '24');
  svg.setAttribute('fill', 'none');
  svg.setAttribute('stroke', 'currentColor');
  svg.setAttribute('stroke-width', '1.5');
  svg.setAttribute('viewBox', '0 0 24 24');
  const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
  path.setAttribute('d', EMPTY_STATE_ICON_PATH);
  const rect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
  rect.setAttribute('x', '9'); rect.setAttribute('y', '3');
  rect.setAttribute('width', '6'); rect.setAttribute('height', '4'); rect.setAttribute('rx', '1');
  svg.append(path, rect);
  return svg;
}

function renderMyRecentLeads(leads) {
  const container = document.getElementById('my-recent-leads');
  if (!container) return;

  if (!leads.length) {
    const empty = document.createElement('div');
    empty.className = 'empty-state';
    const iconWrap = document.createElement('div');
    iconWrap.className = 'empty-state-icon';
    iconWrap.appendChild(buildEmptyStateIcon());
    const message = document.createElement('p');
    message.className = 'text-sm text-secondary';
    message.textContent = App.t('pt_no_leads');
    const link = document.createElement('a');
    link.href = 'new-lead.html';
    link.className = 'btn btn-primary btn-sm';
    link.textContent = App.t('pt_submit_lead');
    empty.append(iconWrap, message, link);
    container.replaceChildren(empty);
    return;
  }

  container.replaceChildren(...leads.map(createLeadCard));
}

function appendTextElement(parent, tag, className, value) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  element.textContent = String(value ?? '');
  parent.appendChild(element);
  return element;
}

// Reuses the shared stage-record renderer (js/utils.js Ui.stageBadge), so producer and partner cannot drift apart on how a stage
// looks. A lead with no sales stage yet -- still waiting for the producer, or
// rejected -- shows its registration decision instead. A stage the pipeline does
// not recognise (e.g. a fetch that failed) keeps the honest fallback.
function createStatusBadge(lead) {
  if (!lead.status && lead.registration_status && lead.registration_status !== 'accepted') {
    return Ui.registrationBadge(lead.registration_status);
  }
  return Ui.stageBadge(Ui.stageFor(partnerStagePipeline, lead.status));
}

function createLeadCard(lead) {
  const card = document.createElement('div');
  card.className = 'lead-card animate-fadeIn';
  const header = document.createElement('div');
  header.className = 'lead-card-header';
  const identity = document.createElement('div');
  appendTextElement(identity, 'div', 'lead-card-id', lead.id);
  appendTextElement(identity, 'div', 'lead-card-name', lead.client_name);
  appendTextElement(identity, 'div', 'lead-card-company', lead.company);
  header.append(identity, createStatusBadge(lead));
  card.appendChild(header);

  const meta = document.createElement('div');
  meta.className = 'lead-card-meta';
  appendTextElement(meta, 'span', 'lead-card-value', formatCurrency(lead.value));
  appendTextElement(meta, 'span', 'text-xs text-tertiary', formatDate(lead.created_at));
  card.appendChild(meta);

  if (lead.commission != null) {
    const commission = document.createElement('div');
    commission.style.cssText = 'margin-top:12px;padding:10px 14px;background:var(--success-bg);border:1px solid rgba(16,185,129,0.2);border-radius:var(--radius-md);display:flex;justify-content:space-between;align-items:center';
    appendTextElement(commission, 'span', 'text-xs text-secondary', App.t('prowizja'));
    const value = appendTextElement(commission, 'span', '', formatCurrency(lead.commission));
    value.style.cssText = 'font-size:0.875rem;font-weight:700;color:var(--success-400)';
    card.appendChild(commission);
  }
  return card;
}

// ─── MY LEADS ─────────────────────────────────────────────
// The My leads required-action column is derived from the lead's stage record,
// never stored separately: the stage the producer designated for
// requesting more information means the partner must supply something,
// any other non-terminal stage sits with the producer, and a terminal
// stage carries no further action. Where the pipeline has no
// request-for-information stage configured, no lead is ever told to
// supply data it was never asked for -- the honest fallback below.
function requiredActionLabel(lead) {
  // Reads the producer's decision first, because that is what actually tells
  // a partner whether the ball is in their court. Only once a deal is
  // accepted does its sales stage say anything about what to do next.
  const registration = lead && lead.registration_status;
  if (registration === 'needs_info') return App.t('pt_supply_missing');
  if (registration === 'rejected') return App.t('pt_no_action');
  if (registration !== 'accepted') return App.t('pt_awaiting_vendor');

  const stage = Ui.stageFor(partnerStagePipeline, lead.status);
  if (!stage) return App.t('pt_run_the_sale');
  if (stage.is_terminal) return App.t('pt_no_action');
  return App.t('pt_run_the_sale');
}

// The single-source-of-truth rule (as on the producer's detail screen):
// an unlocked commission gets an explicit provisional label, never a bare
// dash next to a number elsewhere on the row. Shared by the registration
// table cell and the kanban card so both read one formula.
function commissionIndicator(lead) {
  if (lead.commission == null) {
    // A lead that ended in the terminal lose stage will never earn a
    // commission, so labelling it "expected" reads as a pending payout on a
    // dead lead. Only leads still in flight are genuinely provisional.
    const stage = Ui.stageFor(partnerStagePipeline, lead.status);
    const lost = stage && stage.is_terminal && stage.kind === 'lose';
    const badge = document.createElement('span');
    badge.className = 'badge badge-neutral';
    badge.textContent = App.t(lost ? 'pt_no_commission' : 'pt_projected');
    return badge;
  }
  const span = document.createElement('span');
  span.style.cssText = 'color:var(--success-400);font-weight:600';
  span.textContent = formatCurrency(lead.commission);
  return span;
}

function renderCommissionCell(lead) {
  const cell = document.createElement('td');
  cell.appendChild(commissionIndicator(lead));
  return cell;
}

let partnerLeadsQueueShares = '100';

// The Registration table's status filter offers one option per registration
// value the table can actually contain -- pending/needs_info/rejected, never
// 'accepted' (an accepted lead lives on the Sales board, not here). The "All statuses" default option stays in the page
// markup and is preserved across a re-populate.
function populateRegistrationFilter() {
  const select = document.getElementById('filter-status');
  if (!select) return;
  const previousValue = select.value;
  const allOption = select.querySelector('option[value=""]');
  select.replaceChildren();
  if (allOption) {
    select.appendChild(allOption);
  } else {
    const fallback = document.createElement('option');
    fallback.value = '';
    fallback.textContent = App.t('all_statuses');
    select.appendChild(fallback);
  }
  Ui.REGISTRATION_STATUSES.filter(status => status !== 'accepted').forEach(status => {
    const option = document.createElement('option');
    option.value = status;
    option.textContent = Ui.registrationLabel(status) || status;
    select.appendChild(option);
  });
  if ([...select.options].some(option => option.value === previousValue)) {
    select.value = previousValue;
  }
}

// Two distinct empty states share this function:
// 'empty' means the partner has never submitted anything -- "Submit your
// first..." is a true invitation. 'no-registration' means every lead
// they DO have is already accepted (or otherwise not pending/needs_info/
// rejected) -- there is nothing here to review, but claiming they have no
// leads at all would be false and would hide that Sales has their work.
function setPartnerLeadsState(state) {
  const loading = document.getElementById('leads-loading-state');
  const empty = document.getElementById('leads-empty-state');
  const noneAwaiting = document.getElementById('leads-no-registration-state');
  const error = document.getElementById('leads-error-state');
  const wrapper = document.getElementById('leads-table-wrapper');
  if (loading)     loading.hidden     = state !== 'loading';
  if (empty)       empty.hidden       = state !== 'empty';
  if (noneAwaiting) noneAwaiting.hidden = state !== 'no-registration';
  if (error)        error.hidden        = state !== 'error';
  if (wrapper)       wrapper.hidden      = state !== 'populated';
}

// Registration/Sales view toggle. aria-pressed carries the accessible state; the two views
// are separate DOM subtrees, not a filtered version of the same list, so
// nothing about the Registration table's search/filter state leaks into the
// board or back.
function setPartnerLeadsView(view) {
  partnerLeadsView = view === 'registration' ? 'registration' : 'sales';
  const regBtn = document.getElementById('view-registration-btn');
  const salesBtn = document.getElementById('view-sales-btn');
  const regView = document.getElementById('leads-registration-view');
  const salesView = document.getElementById('leads-sales-view');
  if (regBtn) regBtn.setAttribute('aria-pressed', String(partnerLeadsView === 'registration'));
  if (salesBtn) salesBtn.setAttribute('aria-pressed', String(partnerLeadsView === 'sales'));
  if (regView) regView.hidden = partnerLeadsView !== 'registration';
  if (salesView) salesView.hidden = partnerLeadsView !== 'sales';
}

function bindLeadsViewToggle() {
  const regBtn = document.getElementById('view-registration-btn');
  const salesBtn = document.getElementById('view-sales-btn');
  if (regBtn && !regBtn.dataset.bound) {
    regBtn.dataset.bound = '1';
    regBtn.addEventListener('click', () => setPartnerLeadsView('registration'));
  }
  if (salesBtn && !salesBtn.dataset.bound) {
    salesBtn.dataset.bound = '1';
    salesBtn.addEventListener('click', () => setPartnerLeadsView('sales'));
  }
  setPartnerLeadsView(partnerLeadsView);
}

// A lead not yet accepted has no sales stage to sit on (leads.status is
// NULL until the producer accepts it) -- it belongs in Registration. Only an
// accepted lead is ever on the Sales board.
function registrationLeadsOf(leads) {
  return leads.filter(l => l.registration_status !== 'accepted');
}

// A lead whose commission the producer has actually paid out is finished
// business: it leaves the funnel and lives on the Earnings screen.
// `commission_paid_at` is the one stamp that means that -- NOT
// `settlement_status`, which records whether the END CLIENT paid the
// producer's own invoice (api/payouts.py, api/leads.py).
function isSettledLead(lead) {
  return Boolean(lead && lead.commission_paid_at);
}

function salesLeadsOf(leads) {
  return leads.filter(l => l.registration_status === 'accepted' && !isSettledLead(l));
}

function settledLeadsOf(leads) {
  return leads.filter(l => l.registration_status === 'accepted' && isSettledLead(l));
}

// Leaving the board silently would read as lost data on a screen about money,
// so the count stays visible with a way through to where the leads went.
function renderSettledNote(count) {
  const note = document.getElementById('kanban-settled-note');
  const text = document.getElementById('kanban-settled-text');
  if (!note || !text) return;
  note.hidden = count < 1;
  text.textContent = count ? App.t('pt_settled_moved', { count }) : '';
}

// Which empty-state div belongs on screen when the (possibly filtered)
// registration list has zero rows. "Never submitted
// anything" and "has leads, just none awaiting a registration decision
// right now" read as the same blank table but are not the same fact, so
// they get different copy. A search/filter narrowing an otherwise
// non-empty queue to zero visible rows keeps the existing plain 'empty'
// state.
function registrationEmptyState(allLeads, isFiltering) {
  if (isFiltering) return 'empty';
  return allLeads.length ? 'no-registration' : 'empty';
}

async function initPartnerLeads(session) {
  // Module scope, not a local: initPartnerLeads runs again on every retry, and
  // the re-render hook below is registered only once -- a local binding would
  // leave that hook redrawing the first run's list forever.
  partnerAllLeads = [];
  partnerLeadsSession = session;

  const retry = document.getElementById('leads-retry-btn');
  if (retry && !retry.dataset.bound) {
    retry.dataset.bound = '1';
    retry.addEventListener('click', () => void initPartnerLeads(session));
  }
  const kanbanRetry = document.getElementById('kanban-retry-btn');
  if (kanbanRetry && !kanbanRetry.dataset.bound) {
    kanbanRetry.dataset.bound = '1';
    kanbanRetry.addEventListener('click', () => void initPartnerLeads(session));
  }

  bindLeadsViewToggle();
  setPartnerLeadsState('loading');
  setPartnerKanbanState('loading');
  try {
    const [leadsData, settingsData, stagePipeline] = await Promise.all([
      ApiClient.get('/api/leads'),
      ApiClient.get('/api/settings'),
      loadPartnerStagePipeline(),
    ]);
    partnerAllLeads = leadsData.leads || [];
    partnerLeadsQueueShares = settingsData.settings?.lead_queue_shares ?? '100';
    partnerStagePipeline = stagePipeline;
    populateRegistrationFilter();
    renderLeadsTable(registrationLeadsOf(partnerAllLeads), registrationEmptyState(partnerAllLeads, false));
    renderKanbanBoard(salesLeadsOf(partnerAllLeads));
    renderSettledNote(settledLeadsOf(partnerAllLeads).length);
    bindAnswerControls(session);
    // One hook for the whole screen: a language or theme switch redraws the
    // table, the board and the filter list, all of which are built in code and
    // would otherwise keep the old language until the next fetch.
    if (!partnerLeadsRerenderBound) {
      partnerLeadsRerenderBound = true;
      App.onRerender(() => {
        populateRegistrationFilter();
        filterLeads();
        renderKanbanBoard(salesLeadsOf(partnerAllLeads));
        renderSettledNote(settledLeadsOf(partnerAllLeads).length);
      });
    }
  } catch (err) {
    setPartnerLeadsState('error');
    setPartnerKanbanState('error');
    handleApiError(err, 'initPartnerLeads');
    return;
  }

  const searchEl = document.getElementById('leads-search');
  const statusEl = document.getElementById('filter-status');

  filterLeads = () => {
    const q = searchEl?.value.toLowerCase() || '';
    const s = statusEl?.value || '';
    const filtered = registrationLeadsOf(partnerAllLeads).filter(l => {
      const matchQ = !q || String(l.client_name || '').toLowerCase().includes(q) || String(l.company || '').toLowerCase().includes(q);
      const matchS = !s || l.registration_status === s;
      return matchQ && matchS;
    });
    renderLeadsTable(filtered, registrationEmptyState(partnerAllLeads, Boolean(q || s)));
  };

  updatePartnerLeadRecord = lead => {
    if (!lead) return;
    partnerAllLeads = partnerAllLeads.map(current => current.id === lead.id ? lead : current);
    filterLeads();
    renderKanbanBoard(salesLeadsOf(partnerAllLeads));
    renderSettledNote(settledLeadsOf(partnerAllLeads).length);
  };
  if (searchEl) searchEl.oninput = filterLeads;
  if (statusEl) statusEl.onchange = filterLeads;
}

function renderLeadsTable(leads, emptyState = 'empty') {
  const tbody = document.getElementById('leads-tbody');
  if (!tbody) return;

  if (!leads.length) {
    tbody.replaceChildren();
    setPartnerLeadsState(emptyState);
    return;
  }

  tbody.replaceChildren(...leads.map(lead => {
    const row = document.createElement('tr');

    const clientCell = document.createElement('td');
    const idLine = appendTextElement(clientCell, 'div', 'text-xs text-tertiary', lead.id);
    idLine.style.cssText = 'font-family:monospace';
    appendTextElement(clientCell, 'div', 'cell-primary', lead.client_name);
    const company = appendTextElement(clientCell, 'div', 'text-xs text-tertiary', lead.company);
    company.style.marginTop = '2px';
    const detail = document.createElement('button');
    detail.type = 'button';
    detail.className = 'btn btn-secondary btn-sm';
    detail.dataset.detailLead = lead.id;
    detail.textContent = App.t('pt_lead_details');
    detail.addEventListener('click', () => window.PartnerLeadDetails?.open(lead.id, detail));
    clientCell.append(detail);
    const position = Number(lead.queue_position || 1);
    if (position > 1) {
      const share = computeQueueShare(position, partnerLeadsQueueShares);
      const badgeWrap = document.createElement('div');
      badgeWrap.style.marginTop = '4px';
      badgeWrap.appendChild(renderQueueBadge(position, share));
      clientCell.appendChild(badgeWrap);
    }

    const dateCell = document.createElement('td');
    dateCell.className = 'text-tertiary text-sm';
    dateCell.textContent = formatDate(lead.created_at);

    // This table is the Registration view: every row here is
    // pending/needs_info/rejected -- none of them has a sales stage yet, so
    // the status column reads the registration decision, not lead.status.
    const statusCell = document.createElement('td');
    statusCell.appendChild(Ui.registrationBadge(lead.registration_status));

    const valueCell = document.createElement('td');
    valueCell.className = 'cell-primary';
    valueCell.textContent = formatCurrency(lead.value);

    const commissionCell = renderCommissionCell(lead);

    const updatedCell = document.createElement('td');
    updatedCell.className = 'text-tertiary text-sm';
    updatedCell.textContent = formatDate(lead.updated_at || lead.created_at);

    const actionCell = document.createElement('td');
    actionCell.className = 'text-sm';
    const actionLabel = document.createElement('span');
    actionLabel.className = 'required-action-label';
    actionLabel.textContent = requiredActionLabel(lead);
    actionCell.appendChild(actionLabel);
    // The one place a partner can act on a lead today: answering a producer
    // who asked for something. Every other state is theirs to read, not to
    // change --- the sale itself is still the producer's to move.
    if (lead.registration_status === 'needs_info') {
      const answer = document.createElement('button');
      answer.type = 'button';
      answer.className = 'btn btn-secondary btn-sm';
      answer.style.marginTop = '6px';
      answer.dataset.answerLead = lead.id;
      answer.dataset.revision = String(lead.revision ?? 0);
      answer.textContent = App.t('pt_answer');
      actionCell.appendChild(answer);
    }

    row.append(clientCell, dateCell, statusCell, valueCell, commissionCell, updatedCell, actionCell);
    return row;
  }));
  setPartnerLeadsState('populated');
}

// ─── PARTNER KANBAN — SALES BOARD ─────────────
//
// Own accepted leads only, in the producer's configured stage order.
// A card moves through PATCH /api/leads/{id}/stage: a drag
// handle for pointer/touch, or an equally capable native select + button
// for keyboard use -- both call the exact same function, so they can never
// disagree about what a move does.

function setPartnerKanbanState(state) {
  const loading  = document.getElementById('kanban-loading-state');
  const empty    = document.getElementById('kanban-empty-state');
  const error    = document.getElementById('kanban-error-state');
  const noStages = document.getElementById('kanban-no-stages-state');
  const wrapper  = document.getElementById('kanban-board-wrapper');
  if (loading)  loading.hidden  = state !== 'loading';
  if (empty)    empty.hidden    = state !== 'empty';
  if (error)    error.hidden    = state !== 'error';
  if (noStages) noStages.hidden = state !== 'no-stages';
  if (wrapper)  wrapper.hidden  = state !== 'populated';
}

// Forward/back through the whole configured pipeline is allowed, so
// the select offers every active stage, not just "later" ones -- the
// current stage stays visible but disabled.
function buildKanbanStageOptions(select, currentKey) {
  select.replaceChildren();
  (partnerStagePipeline?.list || []).filter(stage => stage.active).forEach(target => {
    const option = document.createElement('option');
    option.value = target.key;
    // Ui.stageLabel, never `target.name`: the producer's English column
    // (lead_stages.name_en) is what an EN reader has to see here too.
    option.textContent = Ui.stageLabel(target);
    if (target.key === currentKey) { option.disabled = true; option.selected = true; }
    select.appendChild(option);
  });
}

// Finds a lead's own card by id rather than a captured DOM reference, so a
// caller works the same way whichever card is currently on screen -- in
// particular after a board redraw has replaced the element entirely.
function findKanbanCard(leadId) {
  return [...document.querySelectorAll('.kanban-card')].find(el => el.dataset.leadId === leadId);
}

// Undoes a select's own in-progress edit after a cancelled terminal move.
function resetKanbanCardSelectToCurrentStage(lead) {
  const select = findKanbanCard(lead.id)?.querySelector('.kanban-card-stage-select');
  if (select) select.value = lead.status;
}

// One save per lead at a time: mutation controls disabled, aria-busy=true,
// "Zapisywanie" text. Unrelated cards keep working --
// this only ever touches the one card the in-flight move belongs to. Safe
// to call after the board has already been redrawn (success, or a refetch
// on failure): it either finds nothing, or sets a fresh, already-idle card
// back to idle -- a no-op either way.
function setKanbanCardBusy(leadId, busy) {
  const card = findKanbanCard(leadId);
  if (!card) return;
  const select = card.querySelector('.kanban-card-stage-select');
  const button = card.querySelector('.kanban-card-footer button');
  const handle = card.querySelector('.kanban-card-handle');
  card.setAttribute('aria-busy', busy ? 'true' : 'false');
  if (select) select.disabled = busy;
  if (handle) handle.disabled = busy;
  if (button) {
    button.disabled = busy;
    if (busy) {
      if (!button.dataset.idleLabel) button.dataset.idleLabel = button.textContent;
      button.textContent = App.t('pt_saving');
    } else if (button.dataset.idleLabel) {
      button.textContent = button.dataset.idleLabel;
    }
  }
}

function createKanbanCard(lead, stage) {
  const card = document.createElement('div');
  card.className = 'lead-card kanban-card animate-fadeIn';
  card.dataset.leadId = lead.id;
  card.draggable = false;
  const isTerminal = !!(stage && stage.is_terminal);
  const isMovable = !!(stage && !stage.is_terminal);

  const header = document.createElement('div');
  header.className = 'lead-card-header kanban-card-header';
  const identity = document.createElement('div');
  const nameBtn = document.createElement('button');
  nameBtn.type = 'button';
  nameBtn.className = 'kanban-card-name';
  nameBtn.dataset.noCardClick = '1';
  nameBtn.textContent = lead.company || lead.client_name || lead.id;
  nameBtn.addEventListener('click', () => window.PartnerLeadDetails?.open(lead.id, nameBtn));
  identity.appendChild(nameBtn);
  appendTextElement(identity, 'div', 'lead-card-id kanban-card-id', lead.id);
  header.appendChild(identity);

  if (isMovable) {
    const handle = document.createElement('button');
    handle.type = 'button';
    handle.className = 'kanban-card-handle';
    handle.dataset.noCardClick = '1';
    handle.setAttribute('aria-label', App.t('pt_move_drag'));
    Icons.into(handle, 'grip-vertical', { size: 18 });
    header.appendChild(handle);
    card.draggable = true;
    bindKanbanCardDrag(card, lead.id);
  }
  card.appendChild(header);

  const meta = document.createElement('div');
  meta.className = 'lead-card-meta kanban-card-meta';
  appendTextElement(meta, 'div', 'text-sm text-secondary', lead.client_name || '');
  appendTextElement(meta, 'span', 'lead-card-value', formatCurrency(lead.value));
  const commissionRow = document.createElement('div');
  appendTextElement(commissionRow, 'span', 'text-xs text-tertiary', `${Ui.escapeHtml(App.t('prowizja'))}: `);
  commissionRow.appendChild(commissionIndicator(lead));
  meta.appendChild(commissionRow);
  card.appendChild(meta);

  const footer = document.createElement('div');
  footer.className = 'kanban-card-footer';
  if (isMovable) {
    const select = document.createElement('select');
    select.className = 'form-select kanban-card-stage-select';
    select.dataset.noCardClick = '1';
    select.setAttribute('aria-label', App.t('pt_change_stage'));
    buildKanbanStageOptions(select, lead.status);
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'btn btn-secondary btn-sm';
    button.dataset.noCardClick = '1';
    button.textContent = App.t('pt_change_stage');
    button.addEventListener('click', () => {
      const target = select.value;
      if (!target || target === lead.status) return;
      void requestLeadStageMove(lead, target);
    });
    footer.append(select, button);
  } else {
    const note = document.createElement('p');
    note.className = 'kanban-card-readonly-note';
    note.textContent = App.t(isTerminal ? 'pt_lead_closed' : 'pt_stage_unavailable');
    footer.appendChild(note);
  }
  card.appendChild(footer);

  card.addEventListener('click', (event) => {
    if (event.target.closest('[data-no-card-click]')) return;
    // Suppress exactly the one click that may follow a drop -- cleared here
    // (not on a timer) so the very next real click still opens details.
    if (card.dataset.suppressClick === '1') { card.dataset.suppressClick = ''; return; }
    window.PartnerLeadDetails?.open(lead.id, card);
  });

  return card;
}

function createKanbanColumn(stage, leads) {
  const column = document.createElement('div');
  column.className = 'kanban-column';
  column.dataset.stageKey = stage.key;

  const header = document.createElement('div');
  header.className = 'kanban-column-header';
  const dot = document.createElement('span');
  dot.className = 'kanban-column-dot';
  // A dot is a FILL, so the producer's raw colour is exactly right here --
  // nothing has to be read out of it (unlike the badge text, see readableInk).
  dot.style.background = /^#[0-9a-fA-F]{6}$/.test(String(stage.color || '')) ? stage.color : '#6246ea';
  const name = document.createElement('span');
  name.className = 'kanban-column-name';
  name.textContent = Ui.stageLabel(stage);
  const count = document.createElement('span');
  count.className = 'kanban-column-count';
  count.textContent = String(leads.length);
  header.append(dot, name, count);

  const body = document.createElement('div');
  body.className = 'kanban-column-body';
  body.dataset.stageKey = stage.key;
  if (!leads.length) {
    const empty = document.createElement('div');
    empty.className = 'kanban-column-empty';
    empty.textContent = App.t('pt_no_leads_in_stage');
    body.appendChild(empty);
  } else {
    body.append(...leads.map(lead => createKanbanCard(lead, stage)));
  }
  bindKanbanColumnDragTarget(body);

  column.append(header, body);
  return column;
}

function renderKanbanBoard(leads) {
  const board = document.getElementById('kanban-board');
  const unknownSection = document.getElementById('kanban-unknown-section');
  const unknownCards = document.getElementById('kanban-unknown-cards');
  if (!board) return;
  partnerSalesLeads = leads;

  // A stage row exists but was deactivated (soft-removed) is not a movable
  // board column -- only truly active configured stages become columns.
  const activeStages = (partnerStagePipeline?.list || []).filter(stage => stage.active);
  if (!activeStages.length) {
    setPartnerKanbanState('no-stages');
    return;
  }
  if (!leads.length) {
    setPartnerKanbanState('empty');
    return;
  }

  const byStage = new Map(activeStages.map(stage => [stage.key, []]));
  const unknown = [];
  leads.forEach(lead => {
    if (lead.status && byStage.has(lead.status)) byStage.get(lead.status).push(lead);
    else unknown.push(lead);
  });

  board.replaceChildren(...activeStages.map(stage => createKanbanColumn(stage, byStage.get(stage.key) || [])));

  if (unknownSection && unknownCards) {
    if (unknown.length) {
      unknownSection.hidden = false;
      unknownCards.replaceChildren(...unknown.map(lead => createKanbanCard(lead, null)));
    } else {
      unknownSection.hidden = true;
      unknownCards.replaceChildren();
    }
  }

  setPartnerKanbanState('populated');
}

// ── Drag and drop: handle-only drag, click suppressed right after a drop ──

function bindKanbanCardDrag(card, leadId) {
  // Native HTML5 drag fires 'dragstart' with event.target set to the
  // draggable NODE ITSELF (the whole card, since that's what carries
  // draggable=true) -- never to whatever element the pointer actually went
  // down on. So "did the user grab the handle" cannot be read off the
  // dragstart event; it has to be captured on 'mousedown', before the
  // browser decides to start a drag at all, and read back here. (Checked
  // against real Chrome via CDP-driven mouse input, not just synthetic
  // events -- a `.closest('.kanban-card-handle')` check on dragstart's own
  // target can never match, which silently blocked every real mouse drag.)
  let dragStartedFromHandle = false;
  card.addEventListener('mousedown', (event) => {
    dragStartedFromHandle = !!event.target.closest('.kanban-card-handle');
  });
  card.addEventListener('dragstart', (event) => {
    if (!dragStartedFromHandle) { event.preventDefault(); return; }
    partnerKanbanDraggingId = leadId;
    if (event.dataTransfer) {
      event.dataTransfer.effectAllowed = 'move';
      try { event.dataTransfer.setData('text/plain', leadId); } catch { /* no full DataTransfer here */ }
    }
  });
  card.addEventListener('dragend', () => {
    partnerKanbanDraggingId = null;
    card.dataset.suppressClick = '1';
  });
}

function bindKanbanColumnDragTarget(body) {
  body.addEventListener('dragover', (event) => {
    if (!partnerKanbanDraggingId) return;
    event.preventDefault();
    if (event.dataTransfer) event.dataTransfer.dropEffect = 'move';
    body.classList.add('is-drag-over');
  });
  body.addEventListener('dragleave', () => { body.classList.remove('is-drag-over'); });
  body.addEventListener('drop', (event) => {
    event.preventDefault();
    body.classList.remove('is-drag-over');
    const leadId = partnerKanbanDraggingId || event.dataTransfer?.getData('text/plain') || null;
    partnerKanbanDraggingId = null;
    if (!leadId) return;
    const lead = partnerSalesLeads.find(l => l.id === leadId);
    const targetKey = body.dataset.stageKey;
    if (!lead || !targetKey || lead.status === targetKey) return;
    void requestLeadStageMove(lead, targetKey);
  });
}

// ── Terminal confirmation: a real, functional modal. Escape, the
// backdrop and "Keep current stage" all resolve false and send nothing;
// only the primary action resolves true. role=dialog, aria-modal,
// Escape cancels, initial focus on the non-destructive action. ──
function confirmTerminalMove(lead, targetStage) {
  return new Promise((resolve) => {
    const isWin = targetStage.kind === 'win';
    const overlay = document.createElement('div');
    overlay.className = 'modal-overlay show';
    const modal = document.createElement('div');
    modal.className = 'modal modal-sm';
    modal.setAttribute('role', 'dialog');
    modal.setAttribute('aria-modal', 'true');

    const header = document.createElement('div');
    header.className = 'modal-header';
    const title = document.createElement('h3');
    title.className = 'modal-title';
    title.textContent = isWin
      ? (App.t('pk_mark_as_won'))
      : (App.t('pk_mark_as_lost'));
    header.appendChild(title);

    const body = document.createElement('div');
    body.className = 'modal-body';
    const message = document.createElement('p');
    const company = lead.company || lead.client_name || lead.id;
    message.textContent = isWin
      ? App.t('pt_mark_won_q', { company })
      : App.t('pt_mark_lost_q', { company });
    body.appendChild(message);

    const footer = document.createElement('div');
    footer.className = 'modal-footer';
    const keepBtn = document.createElement('button');
    keepBtn.type = 'button';
    keepBtn.className = 'btn btn-ghost';
    keepBtn.textContent = App.t('pk_keep_current_stage');
    const confirmBtn = document.createElement('button');
    confirmBtn.type = 'button';
    confirmBtn.className = 'btn btn-primary';
    confirmBtn.textContent = isWin
      ? (App.t('pk_mark_as_won'))
      : (App.t('pk_mark_as_lost'));
    footer.append(keepBtn, confirmBtn);

    modal.append(header, body, footer);
    overlay.appendChild(modal);
    document.body.appendChild(overlay);

    const cleanup = (result) => {
      overlay.removeEventListener('keydown', handleEscapeKey);
      overlay.remove();
      resolve(result);
    };
    // Named "handle..." rather than "on..." so it reads as a plain local
    // function, not a dynamic DOM event-handler-attribute assignment.
    const handleEscapeKey = (event) => {
      if (event.key === 'Escape') { event.preventDefault(); cleanup(false); }
    };
    keepBtn.addEventListener('click', () => cleanup(false));
    confirmBtn.addEventListener('click', () => cleanup(true));
    overlay.addEventListener('click', (event) => { if (event.target === overlay) cleanup(false); });
    overlay.addEventListener('keydown', handleEscapeKey);
    keepBtn.focus();
  });
}

// The one function both the drag drop and the select+button path call, so
// they can never save a different result for the same gesture. Non-optimistic
// on purpose: the card only moves once the server confirms, so there is no
// snapshot to roll back on failure.
async function requestLeadStageMove(lead, targetKey, confirmedTerminal = false) {
  if (partnerKanbanBusyIds.has(lead.id)) return;
  const targetStage = Ui.stageFor(partnerStagePipeline, targetKey);

  if (targetStage && targetStage.is_terminal && !confirmedTerminal) {
    const confirmed = await confirmTerminalMove(lead, targetStage);
    if (!confirmed) {
      // The select+button path already changed the <select>'s value to the
      // terminal target before the modal opened -- cancel (keep button,
      // Escape, or a backdrop click; confirmTerminalMove resolves false for
      // all three the same way) must not leave that control claiming a
      // stage the card was never actually moved to.
      resetKanbanCardSelectToCurrentStage(lead);
      return;
    }
    confirmedTerminal = true;
  }

  partnerKanbanBusyIds.add(lead.id);
  setKanbanCardBusy(lead.id, true);
  try {
    const response = await ApiClient.patch(`/api/leads/${encodeURIComponent(lead.id)}/stage`, {
      target_stage: targetKey,
      expected_revision: lead.revision ?? 0,
      operation_id: `stage-${lead.id}-${lead.revision ?? 0}-${targetKey}`,
      confirmed_terminal: confirmedTerminal,
    });
    showToast('success', App.t('toast_changes_saved'),
      App.t('pt_moved_to_stage', { stage: targetStage ? Ui.stageLabel(targetStage) : targetKey }));
    updatePartnerLeadRecord?.(response.lead);
  } catch (err) {
    if (err && err.status === 409) {
      showToast('error', App.t('pt_lead_changed'),
        App.t('pt_refresh_before_save'));
    } else {
      showToast('error', App.t('pt_save_failed'),
        App.t('pt_check_connection'));
    }
    handleApiError(err, 'requestLeadStageMove');
    // A 409 conflict and an uncertain outcome (network/timeout)
    // both call for a refetch, not a redraw of whatever the client already
    // had -- the local copy may be stale in either direction, and after a
    // dropped connection the write may actually have landed on the server.
    // No mutation to undo either way (no optimistic move happened, so there
    // is no local guess to roll back before asking the server for the
    // truth). If the session is not known (should not happen outside a
    // test double) fall back to redrawing local data rather than throwing.
    if (partnerLeadsSession) {
      await initPartnerLeads(partnerLeadsSession);
    } else {
      renderKanbanBoard(partnerSalesLeads);
    }
  } finally {
    partnerKanbanBusyIds.delete(lead.id);
    setKanbanCardBusy(lead.id, false);
  }
}

// ─── ANSWERING A REQUEST FOR INFORMATION ──────────────────
//
// The producer asks for something; this is where the partner replies. The
// answer is text and only text: it never decides the lead, because handing a
// partner the power to clear their own "needs_info" would hand them the
// producer's decision.

let answeringLeadId = null;
let answeringRevision = 0;

function ensureAnswerDialog() {
  let dialog = document.getElementById('answer-modal');
  if (dialog) return dialog;

  dialog = document.createElement('div');
  dialog.id = 'answer-modal';
  dialog.className = 'modal-overlay';
  dialog.innerHTML = `
    <div class="modal">
      <div class="modal-header"><h3 class="modal-title" data-i18n="pk_answer_title">${Ui.escapeHtml(App.t('pk_answer_title'))}</h3></div>
      <div class="modal-body">
        <p class="text-sm text-tertiary" id="answer-hint" data-i18n="pk_answer_hint">${Ui.escapeHtml(App.t('pk_answer_hint'))}</p>
        <label class="form-label" for="answer-text" data-i18n="pk_answer_label">${Ui.escapeHtml(App.t('pk_answer_label'))}</label>
        <textarea id="answer-text" class="form-input" rows="5" maxlength="4000"></textarea>
        <p class="text-sm" id="answer-error" hidden></p>
      </div>
      <div class="modal-footer">
        <button type="button" class="btn btn-ghost" id="answer-cancel-btn" data-i18n="anuluj">${Ui.escapeHtml(App.t('anuluj'))}</button>
        <button type="button" class="btn btn-primary" id="answer-submit-btn" data-i18n="pk_answer_submit">${Ui.escapeHtml(App.t('pk_answer_submit'))}</button>
      </div>
    </div>`;
  document.body.appendChild(dialog);
  dialog.querySelector('#answer-cancel-btn').addEventListener('click', () => toggleAnswerDialog(false));
  return dialog;
}

// Shows and hides the dialog through the same `show` class app.js toggles.
// Done here rather than by calling openModal() so this flow does not depend
// on a script the partner leads screen does not otherwise need.
function toggleAnswerDialog(visible) {
  const dialog = document.getElementById('answer-modal');
  if (!dialog) return;
  dialog.classList.toggle('show', visible);
  document.body.style.overflow = visible ? 'hidden' : '';
}

function bindAnswerControls(session) {
  const tbody = document.getElementById('leads-tbody');
  if (tbody && !tbody.dataset.answerBound) {
    tbody.dataset.answerBound = '1';
    tbody.addEventListener('click', (event) => {
      const button = event.target.closest('[data-answer-lead]');
      if (!button) return;
      answeringLeadId = button.dataset.answerLead;
      answeringRevision = Number(button.dataset.revision || 0);
      const dialog = ensureAnswerDialog();
      dialog.querySelector('#answer-text').value = '';
      const error = dialog.querySelector('#answer-error');
      error.hidden = true;
      error.textContent = '';
      bindAnswerSubmit(session);
      toggleAnswerDialog(true);
    });
  }
}

function bindAnswerSubmit(session) {
  const submit = document.getElementById('answer-submit-btn');
  if (!submit || submit.dataset.bound) return;
  submit.dataset.bound = '1';
  submit.addEventListener('click', () => void sendAnswer(session));
}

async function sendAnswer(session) {
  const field = document.getElementById('answer-text');
  const error = document.getElementById('answer-error');
  const submit = document.getElementById('answer-submit-btn');
  const text = (field?.value || '').trim();

  if (!text) {
    if (error) {
      error.hidden = false;
      error.textContent = App.t('pt_answer_required');
    }
    return;
  }

  // Derived from the lead and the version being answered, so pressing the
  // button twice — or retrying after a lost response — is the SAME operation
  // to the server rather than a second answer.
  const operationId = `answer-${answeringLeadId}-${answeringRevision}`;

  if (submit) submit.disabled = true;
  try {
    await ApiClient.post(`/api/leads/${encodeURIComponent(answeringLeadId)}/information`, {
      message: text,
      expected_revision: answeringRevision,
      operation_id: operationId,
    });
    toggleAnswerDialog(false);
    if (field) field.value = '';
    showToast('success', App.t('pt_answer_sent'), App.t('pt_vendor_will_see'));
    await initPartnerLeads(session);
  } catch (err) {
    // The draft survives every failure. Losing what somebody just typed
    // because a request failed is its own bug.
    if (err && err.status === 409) {
      if (error) {
        error.hidden = false;
        error.textContent = App.t('pt_submission_changed');
      }
      await initPartnerLeads(session);
    } else {
      if (error) {
        error.hidden = false;
        error.textContent = App.t('pt_answer_failed');
      }
      handleApiError(err, 'sendAnswer');
    }
  } finally {
    if (submit) submit.disabled = false;
  }
}

// ─── DUPLICATE QUEUE BADGE (shared: new-lead precheck + leads table) ──────
const SUBMIT_LEAD_TIMEOUT_MS = 15000;
// Functions, not constants: copy captured at parse time keeps whatever
// language was active then, and App.setLang only re-runs applyTranslations
// over [data-i18n] nodes.
const duplicateWarningText = () =>
  App.t('pt_duplicate_client') + App.t('pt_duplicate_queue');
const duplicateRejectBlockedText = () => App.t('pt_duplicate_blocked');

function formatShareValue(share) {
  const num = Number(share);
  return Number.isFinite(num) ? String(Math.round(num * 100) / 100) : '0';
}

function renderQueueBadge(position, share) {
  const badge = document.createElement('span');
  badge.className = 'badge badge-warning';
  badge.textContent = App.t('pt_queue_position', { position, share: formatShareValue(share) });
  return badge;
}

// Delegates to the shared implementation in js/utils.js so the producer's
// lead detail and this screen always report the same share for one lead.
function computeQueueShare(position, sharesCsv) {
  return Ui.computeQueueShare(position, sharesCsv);
}

// ─── NEW LEAD FORM ────────────────────────────────────────
function initNewLeadForm(session) {
  const form = document.getElementById('new-lead-form');
  if (!form) return;

  let duplicateBlocksSubmit = false;
  let productCatalogBlocksSubmit = true;
  let isSubmittingLead = false;
  let lastPrecheckKey = null;
  let precheckTimer = null;

  // Fresh screen, fresh preview: never inherit a rate or queue share
  // resolved for a previous initialisation of this form.
  previewRatePercent = null;
  previewQueueShare = 100;

  // The producer's own questions. A failure here leaves the base form fully
  // usable -- a producer who never opened that screen has none, and a fetch
  // that fails must not block a partner from filing a deal.
  void (async function loadLeadFieldSchema() {
    const container = document.getElementById('lead-custom-fields');
    if (!container || !window.LeadFields) return;
    try {
      const data = await ApiClient.get('/api/lead-fields');
      partnerLeadFields = data.fields || [];
      partnerLeadFieldsVersion = data.version ?? null;
      LeadFields.render(container, partnerLeadFields, {});
    } catch {
      partnerLeadFields = [];
      partnerLeadFieldsVersion = null;
    }
  })();

  function syncSubmitAvailability() {
    const submitBtn = document.getElementById('submit-lead-btn');
    if (submitBtn) {
      submitBtn.disabled = isSubmittingLead || duplicateBlocksSubmit || productCatalogBlocksSubmit;
    }
  }

  async function loadProductCatalog() {
    const select = document.getElementById('lead-product');
    const message = document.getElementById('product-catalog-message');
    if (!select) return;

    const placeholder = document.createElement('option');
    placeholder.value = '';
    placeholder.textContent = App.t('pt_choose_product');
    select.replaceChildren(placeholder);
    select.disabled = true;
    productCatalogBlocksSubmit = true;
    if (message) message.hidden = true;
    syncSubmitAvailability();

    try {
      const data = await ApiClient.get('/api/products');
      const products = Array.isArray(data && data.products) ? data.products : [];
      if (!products.length) throw new Error('empty-product-catalogue');

      partnerLeadProducts = products;
      const options = products.map(product => {
        const option = document.createElement('option');
        option.value = String(product.id);
        option.textContent = Ui.producerLabel(product, 'name');
        return option;
      });
      select.replaceChildren(placeholder, ...options);
      select.disabled = false;
      productCatalogBlocksSubmit = false;
      if (message) {
        message.textContent = '';
        message.hidden = true;
      }
    } catch {
      partnerLeadProducts = [];
      const unavailable = document.createElement('option');
      unavailable.value = '';
      unavailable.textContent = App.t('pt_no_products');
      select.replaceChildren(unavailable);
      select.disabled = true;
      productCatalogBlocksSubmit = true;
      if (message) {
        message.textContent = App.t('pt_vendor_no_products');
        message.hidden = false;
      }
    }
    syncSubmitAvailability();
  }

  function renderDuplicateWarning(result) {
    const warning = document.getElementById('duplicate-warning');
    const textEl = document.getElementById('duplicate-warning-text');
    const badgeSlot = document.getElementById('duplicate-warning-badge');
    const blockedReason = document.getElementById('submit-blocked-reason');

    if (!result || !result.duplicate) {
      if (warning) warning.hidden = true;
      if (textEl) textEl.textContent = '';
      if (badgeSlot) badgeSlot.replaceChildren();
      duplicateBlocksSubmit = false;
      if (blockedReason) blockedReason.hidden = true;
      syncSubmitAvailability();
      setPreviewQueueShare(100);
      return;
    }

    if (warning) warning.hidden = false;
    if (result.own_lead_id) {
      // His own earlier lead for this client: the server refuses a second
      // one, so say which lead it is and stop the submit here.
      if (textEl) textEl.textContent = App.t('pt_duplicate_own', { id: result.own_lead_id });
      if (badgeSlot) badgeSlot.replaceChildren();
      duplicateBlocksSubmit = true;
      if (blockedReason) {
        blockedReason.hidden = false;
        blockedReason.textContent = App.t('pt_duplicate_own', { id: result.own_lead_id });
      }
      syncSubmitAvailability();
      return;
    }
    if (textEl) textEl.textContent = duplicateWarningText();
    if (badgeSlot) badgeSlot.replaceChildren(renderQueueBadge(result.queue_position, result.share));
    // A queued duplicate is paid a share of the rate -- the preview must
    // drop with it, not keep quoting the full amount.
    setPreviewQueueShare(result.share);

    duplicateBlocksSubmit = result.policy === 'reject';
    if (blockedReason) {
      blockedReason.hidden = !duplicateBlocksSubmit;
      if (duplicateBlocksSubmit) blockedReason.textContent = duplicateRejectBlockedText();
    }
    syncSubmitAvailability();
  }

  async function runDuplicatePrecheck() {
    const nipEl = document.getElementById('lead-nip');
    const websiteEl = document.getElementById('lead-website');
    if (!nipEl || !websiteEl) return;

    const nip = nipEl.value.trim();
    const website = websiteEl.value.trim();
    if (!/^\d{10}$/.test(nip) || !website) return;

    const key = `${nip}|${website}`;
    if (key === lastPrecheckKey) return;
    lastPrecheckKey = key;

    try {
      const result = await ApiClient.post('/api/leads/precheck', { nip, website });
      nipEl.classList.remove('error');
      renderDuplicateWarning(result);
    } catch {
      // Malformed identity: surface the existing inline format hint,
      // never the raw server message (see form-hint under #lead-nip).
      lastPrecheckKey = null;
      nipEl.classList.add('error');
      renderDuplicateWarning(null);
    }
  }

  function schedulePrecheck() {
    if (precheckTimer) clearTimeout(precheckTimer);
    precheckTimer = setTimeout(() => {
      precheckTimer = null;
      void runDuplicatePrecheck();
    }, 250);
  }

  document.getElementById('lead-nip')?.addEventListener('blur', schedulePrecheck);
  document.getElementById('lead-website')?.addEventListener('blur', schedulePrecheck);

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (isSubmittingLead || duplicateBlocksSubmit || productCatalogBlocksSubmit) return;

    // Validate
    let valid = true;
    const required = [
      'lead-client-name', 'lead-company', 'lead-email', 'lead-value',
      'lead-nip', 'lead-website', 'lead-product', 'lead-quantity',
    ];
    required.forEach(id => {
      const el = document.getElementById(id);
      if (!el) return;
      if (!el.value.trim()) { el.classList.add('error'); valid = false; }
      else el.classList.remove('error');
    });

    if (!valid) {
      showToast('error', App.t('pt_fill_required'), App.t('pt_fill_starred'));
      return;
    }

    isSubmittingLead = true;
    const btn = document.getElementById('submit-lead-btn');
    const originalChildren = Array.from(btn.childNodes);
    btn.disabled = true;
    const spinner = document.createElement('span');
    spinner.className = 'spinner';
    spinner.style.cssText = 'width:18px;height:18px;border-width:2px';
    btn.replaceChildren(spinner, document.createTextNode(' ' + App.t('pt_saving')));

    const body = {
      client_name:  document.getElementById('lead-client-name').value.trim(),
      client_email: document.getElementById('lead-email').value.trim(),
      client_phone: document.getElementById('lead-phone').value.trim(),
      company:      document.getElementById('lead-company').value.trim(),
      value:        parseFloat(document.getElementById('lead-value').value) || 0,
      notes:        document.getElementById('lead-notes').value.trim(),
      sector:       document.getElementById('lead-sector').value,
      nip:          document.getElementById('lead-nip').value.trim(),
      website:      document.getElementById('lead-website').value.trim(),
      product_id:   Number(document.getElementById('lead-product').value),
      quantity:     parseInt(document.getElementById('lead-quantity').value, 10) || 0,
    };

    // The producer's own questions, plus the version of the question set this
    // form was built from. Sending the version is what stops a form filled in
    // before the producer edited it from being saved against rules the partner
    // never saw.
    const customContainer = document.getElementById('lead-custom-fields');
    if (customContainer && window.LeadFields) {
      const custom = LeadFields.serialize(customContainer);
      if (custom.missing.length) {
        const firstMissing = LeadFields.highlightMissing(customContainer, custom.missing);
        btn.disabled = false;
        btn.replaceChildren(...originalChildren);
        isSubmittingLead = false;
        showToast('error', App.t('pt_fill_required'),
          App.t('pt_vendor_marked_required'));
        firstMissing?.focus();
        return;
      }
      body.lead_field_values = custom.values;
      if (partnerLeadFieldsVersion !== null) {
        body.lead_fields_version = partnerLeadFieldsVersion;
      }
    }

    let timeoutId;
    const timedOut = new Promise(resolve => {
      timeoutId = setTimeout(() => resolve({ __timedOut: true }), SUBMIT_LEAD_TIMEOUT_MS);
    });

    try {
      const outcome = await Promise.race([ApiClient.post('/api/leads', body), timedOut]);
      clearTimeout(timeoutId);
      if (outcome && outcome.__timedOut) {
        btn.disabled = false;
        btn.replaceChildren(...originalChildren);
        showToast(
          'error', App.t('pk_timeout'),
          App.t('pt_save_failed_kept')
        );
        return;
      }
      btn.disabled = false;
      btn.replaceChildren(...originalChildren);
      showToast('success', App.t('pt_lead_submitted'), App.t('pt_submitted_detail', { id: outcome.lead.id }));
      setTimeout(() => { Ui.safeNavigate('leads.html'); }, 1500);
    } catch (err) {
      clearTimeout(timeoutId);
      btn.disabled = false;
      btn.replaceChildren(...originalChildren);
      // Every submission failure path must say entered data survived
      // (Copywriting Contract's generic form/API-failure copy), not only
      // the timeout path -- the specific server reason still leads the
      // toast title, the reassurance is always the body.
      console.error('[API Error]', 'submitLead', err);
      showToast(
        'error', (err && err.message) || App.t('pt_save_failed'),
        App.t('pt_data_kept')
      );
    } finally {
      isSubmittingLead = false;
    }
  });

  // Live validation clear
  form.querySelectorAll('.form-input, .form-select, .form-textarea').forEach(el => {
    el.addEventListener('input', () => el.classList.remove('error'));
  });

  // Live commission preview. Mirrors the producer-side formula in
  // api/leads.py (value * total_percent% * queue_share%) so the number the
  // partner reads before submitting is the number they are actually paid --
  // never a flat house rate that ignores their tier.
  document.getElementById('lead-value')?.addEventListener('input', renderCommissionPreview);

  void loadProductCatalog();

  // A language switch has to reach everything this form draws in code: the
  // select's own placeholder, the producer's custom-field captions (their
  // English column is data, not dictionary copy), the duplicate warning and
  // the commission preview. Deliberately NOT a re-render of the fields or a
  // refetch of the catalogue -- both would throw away a half-filled form.
  if (!newLeadRerenderBound) {
    newLeadRerenderBound = true;
    App.onRerender(() => {
      const select = document.getElementById('lead-product');
      const placeholder = select && select.querySelector('option[value=""]');
      if (placeholder) {
        placeholder.textContent = App.t(select.disabled ? 'pt_no_products' : 'pt_choose_product');
      }
      // Only the label changes: option.value (the product_id actually
      // submitted) and the select's own chosen value are never touched, so a
      // half-picked form survives a language switch exactly like the
      // producer's own custom-field captions above.
      if (select && partnerLeadProducts.length) {
        Array.from(select.options).forEach((option) => {
          if (!option.value) return;
          const product = partnerLeadProducts.find(
            (candidate) => String(candidate.id) === option.value,
          );
          if (product) option.textContent = Ui.producerLabel(product, 'name');
        });
      }
      const message = document.getElementById('product-catalog-message');
      if (message && !message.hidden) message.textContent = App.t('pt_vendor_no_products');
      if (window.LeadFields) {
        LeadFields.relabel(document.getElementById('lead-custom-fields'), partnerLeadFields);
      }
      const warningText = document.getElementById('duplicate-warning-text');
      const warning = document.getElementById('duplicate-warning');
      if (warning && !warning.hidden && warningText) warningText.textContent = duplicateWarningText();
      const blockedReason = document.getElementById('submit-blocked-reason');
      if (blockedReason && !blockedReason.hidden) blockedReason.textContent = duplicateRejectBlockedText();
      renderCommissionPreview();
    });
  }
  void loadPreviewRate(session);
}

// ─── NEW-LEAD COMMISSION PREVIEW ────────────────────────────────────
// Tier rate and queue share are resolved once per screen, then reused by
// every re-render: typing a value must never re-hit the network.
let previewRatePercent = null;   // null until /api/partner-bonuses answers
let previewQueueShare  = 100;    // no duplicate yet -> full share

async function loadPreviewRate(session) {
  const partnerId = session && session.id;
  if (!partnerId) return;
  try {
    const data = await ApiClient.get(`/api/partner-bonuses/${partnerId}`);
    const total = Number(data && data.partner && data.partner.total_percent);
    if (Number.isFinite(total)) previewRatePercent = total;
  } catch {
    // Leave the rate unresolved: renderCommissionPreview then shows the
    // honest dash rather than inventing a percentage.
  }
  renderCommissionPreview();
}

function setPreviewQueueShare(share) {
  const num = Number(share);
  previewQueueShare = Number.isFinite(num) ? num : 100;
  renderCommissionPreview();
}

function renderCommissionPreview() {
  const valueEl     = document.getElementById('lead-value');
  const preview     = document.getElementById('value-preview');
  const hintValue   = document.getElementById('commission-preview-hint');
  const sourceValue = document.getElementById('commission-source-value');
  const hint        = document.getElementById('value-preview-hint');
  const rateLabel   = document.getElementById('commission-rate-label');
  const rateHint    = document.getElementById('commission-rate-hint');
  if (!valueEl) return;

  const val = parseFloat(valueEl.value) || 0;
  const rateText = previewRatePercent === null
    ? '—'
    : `${formatShareValue(previewRatePercent)}%${previewQueueShare < 100
        ? App.t('pt_share_multiplier', { share: formatShareValue(previewQueueShare) })
        : ''}`;
  if (rateLabel) rateLabel.textContent = rateText;
  if (rateHint)  rateHint.textContent  = rateText;

  const amount = previewRatePercent === null
    ? null
    : val * (previewRatePercent / 100) * (previewQueueShare / 100);
  const amountText = amount === null ? '—' : formatCurrency(amount);

  if (preview)     preview.textContent     = amountText;
  if (hintValue)   hintValue.textContent   = amountText;
  if (sourceValue) sourceValue.textContent = formatCurrency(val);
  if (hint)        hint.style.display      = val > 0 ? 'block' : 'none';
}
