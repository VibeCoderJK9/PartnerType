/* ============================================================
   PARTNERTYPE — ADMIN LEADS CONTROLLER
   Closure-owned lead list, detail, filter, sort and action state.
   ============================================================ */

(function initAdminLeadsNamespace(global) {
  'use strict';

let currentPage   = 1;
const perPage     = 25;
// The rows the server sent for the current page -- not the whole collection.
// Filtering, sorting and slicing happen in the database. Anything that needs a lead which is
// not on the visible page has to ask for it by id.
let pageLeads     = [];
let totalLeads    = 0;
// Only the newest answer may draw. Typing in the search box fires a request per
// pause, and without this a slow early one could land last and repaint the
// table with results for a prefix of what the producer has since typed.
let loadSequence  = 0;
let selectedLead  = null;
const reopenBusy = new Set();
const reopenDrafts = new Map();
const settlementDrafts = new Map();
const reopenOperations = new Map();
const reopenRefreshNeeded = new Set();
let reopenDialogOpen = false;

function renderReopenRecovery() {
  const section = document.getElementById('lead-reopen-section');
  if (!section) return;
  let message = document.getElementById('lead-reopen-recovery-message');
  let retry = document.getElementById('lead-reopen-refresh-btn');
  if (!message) {
    message = document.createElement('p');
    message.id = 'lead-reopen-recovery-message';
    message.setAttribute('role', 'alert');
    section.appendChild(message);
    retry = document.createElement('button');
    retry.id = 'lead-reopen-refresh-btn';
    retry.type = 'button';
    retry.className = 'btn btn-secondary';
    retry.addEventListener('click', async () => {
      const id = selectedLead?.id;
      if (!id || reopenBusy.has(id)) return;
      reopenBusy.add(id);
      updateReopenBusyState();
      try { await refreshReopenLead(id); }
      catch (err) { handleApiError(err, 'refreshReopenLead'); }
      finally { reopenBusy.delete(id); updateReopenBusyState(); }
    });
    section.appendChild(retry);
  }
  const stale = reopenRefreshNeeded.has(selectedLead?.id);
  message.hidden = !stale;
  message.textContent = App.t('al_state_check_failed');
  retry.hidden = !stale;
  retry.disabled = reopenBusy.has(selectedLead?.id);
  retry.textContent = App.t('al_refresh_state');
}

async function refreshReopenLead(leadId) {
  const selection = selectedLead;
  try {
    const data = await ApiClient.get(`/api/leads/${encodeURIComponent(leadId)}`);
    const stages = await ApiClient.get('/api/lead-stages');
    if (!data.lead || data.lead.id !== leadId || !Array.isArray(stages.stages)) {
      throw new Error(App.t('al_incomplete_recovery_data'));
    }
    stagePipeline = Ui.buildStagePipeline(stages.stages);
    reopenRefreshNeeded.delete(leadId);
    if (selectedLead === selection && selection?.id === leadId) {
      selectedLead = data.lead;
      syncLead(data.lead);
      renderDecisionControls(data.lead);
      renderReopenSection(data.lead);
      renderCommission(data.lead);
      document.getElementById('modal-lead-status')?.replaceChildren(createStatusSummary(data.lead));
    }
  } catch (err) {
    if (err?.status === 403 || err?.status === 404) discardInaccessibleLead(leadId);
    throw err;
  }
}

function reopenEnvelope(lead, action, payload) {
  const key = `${lead.id}:${action}`;
  const signature = JSON.stringify([lead.revision, payload]);
  let operation = reopenOperations.get(key);
  if (!operation || operation.signature !== signature) {
    operation = { signature, id: global.crypto.randomUUID() };
    reopenOperations.set(key, operation);
  }
  return { expected_revision: Number(lead.revision), operation_id: operation.id };
}

function reopenDestinations() {
  return (stagePipeline?.nonTerminal || []).filter((stage) => stage.active === true || stage.active === 1);
}

function rememberSettlementDraft() {
  if (!selectedLead) return;
  settlementDrafts.set(selectedLead.id, {
    result: document.querySelector('input[name="reopen-review-result"]:checked')?.value || '',
    reason: document.getElementById('lead-reopen-review-reason')?.value || '',
    open: !document.getElementById('lead-reopen-review-form')?.hidden,
  });
  updateReopenReviewSaveState();
}

function restoreSettlementDraft(lead) {
  const draft = settlementDrafts.get(lead.id);
  document.querySelectorAll('input[name="reopen-review-result"]').forEach((el) => { el.checked = el.value === draft?.result; });
  const reason = document.getElementById('lead-reopen-review-reason');
  if (reason) reason.value = draft?.reason || '';
  const confirm = document.getElementById('lead-reopen-review-confirm');
  if (confirm) confirm.checked = false;
  const form = document.getElementById('lead-reopen-review-form');
  if (form) form.hidden = !draft?.open;
}

function discardInaccessibleLead(leadId) {
  reopenDrafts.delete(leadId);
  settlementDrafts.delete(leadId);
  reopenRefreshNeeded.delete(leadId);
  reopenOperations.delete(`${leadId}:review`);
  reopenOperations.delete(`${leadId}:reopen`);
  if (pageLeads.some((lead) => lead.id === leadId)) {
    pageLeads = pageLeads.filter((lead) => lead.id !== leadId);
    totalLeads = Math.max(0, totalLeads - 1);
  }
  if (document.body.dataset.page === 'admin-leads') renderLeadsTable();
  if (selectedLead?.id !== leadId) return;
  selectedLead = null;
  closeModal('lead-modal');
  document.querySelectorAll('[id^="modal-lead-"]').forEach((el) => { el.textContent = ''; });
  document.querySelectorAll('[id^="modal-commission"]').forEach((el) => {
    if ('value' in el) el.value = '';
    else el.textContent = '';
  });
  document.getElementById('modal-comments-container')?.replaceChildren();
  document.getElementById('lead-reopen-section')?.setAttribute('hidden', '');
  restoreSettlementDraft({ id: null });
}

function updateReopenBusyState() {
  const busy = reopenBusy.has(selectedLead?.id);
  document.getElementById('lead-reopen-section')?.setAttribute('aria-busy', String(busy));
  document.querySelectorAll('#lead-reopen-review-form input, #lead-reopen-review-form textarea').forEach((el) => { el.disabled = busy; });
  const check = document.getElementById('lead-reopen-check-btn');
  const open = document.getElementById('lead-reopen-open-btn');
  if (check) check.disabled = busy || reopenRefreshNeeded.has(selectedLead?.id);
  // The dialog latch prevents duplicate popups without disabling its focus-return target.
  if (open) open.disabled = busy || reopenRefreshNeeded.has(selectedLead?.id) || !reopenDestinations().length;
  updateReopenReviewSaveState();
  renderReopenRecovery();
}

async function handleReopenFailure(err, context, leadId) {
  if (err?.status === 403 || err?.status === 404) {
    discardInaccessibleLead(leadId);
    handleApiError(err, context);
    return;
  }
  // A failed response does not prove that the transaction failed. Read the
  // current version, but never retry the write without a new confirmation.
  const selection = selectedLead;
  if (selection?.id === leadId && (err?.status === 409 || !err?.status || err.status >= 500)) {
    reopenRefreshNeeded.add(leadId);
    try {
      await refreshReopenLead(leadId);
    } catch (refreshError) {
      if (refreshError?.status === 403 || refreshError?.status === 404) discardInaccessibleLead(leadId);
      handleApiError(refreshError, 'refreshReopenLead');
    }
    showToast('error', App.t('al_verify_the_current_lead_state'),
      App.t('al_the_write_was_not_retried'));
  }
  handleApiError(err, context);
}
let leadInitialization = null;
const leadRecords = new Map();
let pendingLeadId = '';
const queueStateBindings = new WeakSet();
let stagePipeline = null;


// ─── LEADS PAGE ───────────────────────────────────────────
async function initLeadsPage() {
  if (!leadInitialization) {
    // Re-reads the filter controls, so the redraw keeps the producer's
    // current view rather than resetting it.
    App.onRerender(() => {
      void loadLeads();
      // An open "przypisz lead" window follows the language too (ledger #55):
      // product names and the placeholders are relabelled in place, and
      // renderAssignOptions keeps whatever was already chosen or typed.
      if (assignState.partners.length || assignState.products.length) renderAssignOptions();
    });
    leadInitialization = (async () => {
      setupLeadModal();
      setupLeadActions();
      setupAdvanceControls();
      setupReopenControls();
      setupAssignControls();
      setupLeadsFilters();
      setupLeadsSorting();
      setupQueueStates();
      await Promise.all([loadStages(), loadQueueShares()]);
      initStatusDropdown();
      applyQueryState();
      await Promise.all([loadLeads(), loadPartnersForFilter()]);
      // A notification links straight to one lead, which may sit on any page.
      // The detail endpoint is asked for it by id, and answers 404 through
      // the usual error path if the lead is gone or not ours to see.
      if (pendingLeadId) await openLeadDetail(pendingLeadId);
    })();
  }
  return leadInitialization;
}

async function loadStages() {
  try {
    const data = await ApiClient.get('/api/lead-stages');
    stagePipeline = Ui.buildStagePipeline(data.stages || []);
  } catch (_) {
    stagePipeline = Ui.buildStagePipeline([]);
  }
}

// The queue-share percentages the producer configured. Without these the
// lead detail rendered a bare dash where the partner's own screen showed a
// real percentage for the same lead.
let adminQueueShares = '100';

async function loadQueueShares() {
  try {
    const data = await ApiClient.get('/api/settings');
    adminQueueShares = data.settings?.lead_queue_shares ?? '100';
  } catch (_) {
    adminQueueShares = '100';
  }
}

function applyQueryState() {
  const query = new URLSearchParams(global.location.search);
  // Accepts either name so a link written before the split still works.
  const requestedStatus = query.get('registration_status') || query.get('status') || '';
  pendingLeadId = query.get('lead') || query.get('lead_id') || '';

  // The dashboard's "Po terminie" card links here with ?overdue=1. Tick the
  // existing filter so the list matches the number on the card and the reason
  // for the filtering is visible rather than implicit.
  const overdueBox = document.getElementById('filter-overdue');
  if (overdueBox && query.get('overdue') === '1') overdueBox.checked = true;

  if (!Ui.REGISTRATION_STATUSES.includes(requestedStatus)) return;
  const hidden = document.getElementById('filter-status');
  const visible = document.getElementById('status-search-input');
  if (hidden) hidden.value = requestedStatus;
  if (visible) visible.value = Ui.registrationLabel(requestedStatus);
}

// There is no "Channel manager" column: guessing a lead's owner from the audit log
// would need the whole log, which a moderator cannot read. If lead ownership
// is wanted, it belongs on the lead.

function initStatusDropdown() {
  const wrapper = document.getElementById('status-select-wrapper');
  const input = document.getElementById('status-search-input');
  const hidden = document.getElementById('filter-status');
  const dropdown = document.getElementById('status-dropdown-list');
  if (!wrapper || !input || !hidden || !dropdown) return;

  // The dropdown lists the four fixed registration decisions, not the
  // producer-configurable sales stages: this filter is how a producer finds
  // "everything still waiting on me". The sales stage stays
  // visible on each row.
  function pipelineStages() {
    return Ui.REGISTRATION_STATUSES.map(key => ({
      key,
      name: Ui.registrationLabel(key),
    }));
  }

  function renderList(query = '') {
    const q = query.toLowerCase();
    const filtered = pipelineStages().filter(s => s.name.toLowerCase().includes(q));

    dropdown.replaceChildren();
    const allOption = document.createElement('div');
    allOption.className = 'custom-select-option';
    allOption.style.cssText = 'font-weight:600; border-bottom:1px solid var(--border-subtle); margin-bottom:4px; padding-bottom:8px';
    allOption.dataset.id = '';
    allOption.textContent = App.t('all_statuses');
    dropdown.appendChild(allOption);

    if (filtered.length === 0 && q) {
      const empty = document.createElement('div');
      empty.className = 'custom-select-option empty-msg';
      empty.textContent = App.t('no_data');
      dropdown.appendChild(empty);
    } else {
      filtered.forEach((stage) => {
        const option = document.createElement('div');
        option.className = 'custom-select-option';
        option.dataset.id = stage.key;
        option.textContent = Ui.stageLabel(stage);
        dropdown.appendChild(option);
      });
    }
  }

  input.addEventListener('focus', () => {
    renderList('');
    input.select();
    dropdown.classList.add('open');
    wrapper.classList.add('open');
  });

  input.addEventListener('input', (e) => {
    renderList(e.target.value);
    dropdown.classList.add('open');
    wrapper.classList.add('open');
    hidden.value = '';
    if (window.applyLocalFilters) window.applyLocalFilters();
  });

  dropdown.addEventListener('mousedown', (e) => {
    if (e.target.classList.contains('custom-select-option') && !e.target.classList.contains('empty-msg')) {
      hidden.value = e.target.dataset.id;
      input.value = e.target.textContent === App.t('all_statuses') ? '' : e.target.textContent;
      dropdown.classList.remove('open');
      wrapper.classList.remove('open');
      if (window.applyLocalFilters) window.applyLocalFilters();
    }
  });

  document.addEventListener('mousedown', (e) => {
    // Only act while this dropdown is actually open. Without this guard the
    // handler ran on EVERY mousedown anywhere on the page and, in the
    // no-selection branch below, re-ran the filter -- which re-rendered the
    // table rows out from under whatever button the user was pressing. The
    // button was destroyed between mousedown and mouseup, so the browser never
    // completed the click, and "Aktywuj" / "Akceptuj" / the detail icon did
    // nothing at all for a real mouse. A scripted .click() dispatches no
    // mousedown, which is why those same buttons always worked from a test.
    if (!wrapper.classList.contains('open') && !dropdown.classList.contains('open')) return;
    if (!wrapper.contains(e.target)) {
      dropdown.classList.remove('open');
      wrapper.classList.remove('open');
      if (hidden.value) {
        const sel = Ui.stageFor(stagePipeline, hidden.value);
        if (sel) input.value = sel.name;
      } else {
        input.value = '';
        if (window.applyLocalFilters) window.applyLocalFilters();
      }
    }
  });
}

let partnersForFilter = [];

async function loadPartnersForFilter() {
  try {
    const data = await ApiClient.get('/api/partners');
    partnersForFilter = data.partners || [];
    initPartnerDropdown();
  } catch(e) {}
}

function initPartnerDropdown() {
  const wrapper = document.getElementById('partner-select-wrapper');
  const input = document.getElementById('partner-search-input');
  const hidden = document.getElementById('filter-partner');
  const dropdown = document.getElementById('partner-dropdown-list');
  if (!wrapper || !input || !hidden || !dropdown) return;

  function renderList(query = '') {
    const q = query.toLowerCase();
    const filtered = partnersForFilter.filter(p => 
      p.name.toLowerCase().includes(q) || (p.company && p.company.toLowerCase().includes(q))
    );
    
    // Always show the "All partners" option at the top
    dropdown.replaceChildren();
    const allOption = document.createElement('div');
    allOption.className = 'custom-select-option';
    allOption.style.cssText = 'font-weight:600; border-bottom:1px solid var(--border-subtle); margin-bottom:4px; padding-bottom:8px';
    allOption.dataset.id = '';
    allOption.textContent = App.t('all_partners');
    dropdown.appendChild(allOption);

    if (filtered.length === 0 && q) {
      const empty = document.createElement('div');
      empty.className = 'custom-select-option empty-msg';
      empty.textContent = App.t('no_data');
      dropdown.appendChild(empty);
    } else {
      filtered.forEach((partner) => {
        const option = document.createElement('div');
        option.className = 'custom-select-option';
        option.dataset.id = partner.id;
        const displayName = partner.company ? `${partner.company} (${partner.name})` : partner.name;
        option.textContent = displayName;
        dropdown.appendChild(option);
      });
    }
  }

  input.addEventListener('focus', () => {
    renderList(''); // Show the full list to start with
    input.select();
    dropdown.classList.add('open');
    wrapper.classList.add('open');
  });

  input.addEventListener('input', (e) => {
    renderList(e.target.value);
    dropdown.classList.add('open');
    wrapper.classList.add('open');
    hidden.value = ''; // clear selection if typing
    if (window.applyLocalFilters) window.applyLocalFilters();
  });

  dropdown.addEventListener('mousedown', (e) => {
    // mousedown prevents input blur from firing before click registers
    if (e.target.classList.contains('custom-select-option') && !e.target.classList.contains('empty-msg')) {
      hidden.value = e.target.dataset.id;
      input.value = e.target.textContent === App.t('all_partners') ? '' : e.target.textContent;
      dropdown.classList.remove('open');
      wrapper.classList.remove('open');
      if (window.applyLocalFilters) window.applyLocalFilters();
    }
  });

  document.addEventListener('mousedown', (e) => {
    // Only act while this dropdown is actually open. Without this guard the
    // handler ran on EVERY mousedown anywhere on the page and, in the
    // no-selection branch below, re-ran the filter -- which re-rendered the
    // table rows out from under whatever button the user was pressing. The
    // button was destroyed between mousedown and mouseup, so the browser never
    // completed the click, and "Aktywuj" / "Akceptuj" / the detail icon did
    // nothing at all for a real mouse. A scripted .click() dispatches no
    // mousedown, which is why those same buttons always worked from a test.
    if (!wrapper.classList.contains('open') && !dropdown.classList.contains('open')) return;
    if (!wrapper.contains(e.target)) {
      dropdown.classList.remove('open');
      wrapper.classList.remove('open');
      
      // If the user clicked away while an ID is selected, restore its name
      if (hidden.value) {
        const selectedPartner = partnersForFilter.find(p => String(p.id) === hidden.value);
        if (selectedPartner) {
          input.value = selectedPartner.company ? `${selectedPartner.company} (${selectedPartner.name})` : selectedPartner.name;
        }
      } else {
        // With no selected ID, clear the field
        input.value = '';
        if (window.applyLocalFilters) window.applyLocalFilters();
      }
    }
  });
}

/** Read the filter controls into the query the server understands. */
function collectLeadFilters() {
  const value = (id) => document.getElementById(id)?.value?.trim() || '';
  const params = {
    q:                   value('leads-search'),
    registration_status: value('filter-status'),
    partner_id:          value('filter-partner'),
    date_from:           value('filter-date-from'),
    date_to:             value('filter-date-to'),
    min_value:           value('filter-value'),
  };
  if (document.getElementById('filter-overdue')?.checked) params.overdue = '1';
  // A blank control is not a filter. Sending `min_value=` would ask the server
  // to read an empty string as a number.
  return Object.fromEntries(Object.entries(params).filter(([, v]) => v !== ''));
}

async function loadLeads() {
  const sequence = ++loadSequence;
  setQueueState('loading');
  try {
    const params = new URLSearchParams(collectLeadFilters());
    params.set('sort', currentSort.column);
    params.set('dir', currentSort.dir);
    params.set('page', String(currentPage));
    params.set('per_page', String(perPage));

    const data = await ApiClient.get('/api/leads?' + params.toString());
    if (sequence !== loadSequence) return;  // a newer request is already in flight

    pageLeads  = data.leads || [];
    totalLeads = Number(data.total ?? pageLeads.length);

    // Narrowing a filter while on page 9 can leave the producer past the end
    // of the new result. Land them on the last page that has rows rather than
    // on an empty table with no clue where their leads went.
    const lastPage = Math.max(1, Math.ceil(totalLeads / perPage));
    if (currentPage > lastPage) {
      currentPage = lastPage;
      return loadLeads();
    }

    renderLeadsCount();
    renderLeadsTable();
  } catch (err) {
    if (sequence !== loadSequence) return;
    setQueueState('error');
    handleApiError(err, 'loadLeads');
  }
}

/**
 * Say which nothing this is.
 *
 * An empty table means one of two opposite things: the programme has no leads
 * yet, or the producer's filters matched none of the ones it has. The screen
 * said "all submissions have been handled" for both -- which, to somebody
 * who has just typed a client's name into the search box, is a false statement
 * about their data rather than an answer about their search.
 *
 * The key is swapped on the elements themselves, not just their text, so a
 * language switch re-translates whichever message is showing.
 */
function renderEmptyState() {
  const filtered = Object.keys(collectLeadFilters()).length > 0;
  const copy = filtered
    ? { title: 'leads_empty_filtered_title', body: 'leads_empty_filtered_body' }
    : { title: 'brak_nowych_leadow', body: 'wszystkie_zgloszenia_zostaly_obsluzone_nowe_lead' };

  for (const [id, key] of [['leads-empty-title', copy.title], ['leads-empty-body', copy.body]]) {
    const element = document.getElementById(id);
    if (!element) continue;
    element.dataset.i18n = key;
    element.textContent = App.t(key);
  }
}

function renderLeadsCount() {
  const countEl = document.getElementById('leads-count');
  if (countEl) countEl.textContent = totalLeads;
  const countUnit = document.getElementById('leads-count-unit');
  if (countUnit) countUnit.textContent = App.plural(totalLeads, 'unit_lead');
}

function setupQueueStates() {
  const retry = document.getElementById('leads-retry-btn');
  if (retry && !queueStateBindings.has(retry)) {
    retry.addEventListener('click', () => void loadLeads());
    queueStateBindings.add(retry);
  }
}

function setQueueState(state) {
  const loading = document.getElementById('leads-loading-state');
  const empty = document.getElementById('leads-empty-state');
  const error = document.getElementById('leads-error-state');
  if (loading) loading.hidden = state !== 'loading';
  if (empty) empty.hidden = state !== 'empty';
  if (error) error.hidden = state !== 'error';
}

// Long enough that a typed word travels as one request rather than six, short
// enough that the list still feels like it is answering the typing.
const SEARCH_DEBOUNCE_MS = 300;
let searchDebounce = null;

function setupLeadsFilters() {
  // Every filter is a condition in the query, so the browser never holds the
  // whole programme in memory.
  const applyFilters = () => {
    currentPage = 1;
    void loadLeads();
  };

  const search = document.getElementById('leads-search');
  if (search) {
    search.addEventListener('input', () => {
      if (searchDebounce) clearTimeout(searchDebounce);
      searchDebounce = setTimeout(applyFilters, SEARCH_DEBOUNCE_MS);
    });
    // Enter means "now", without waiting out the pause.
    search.addEventListener('keydown', (event) => {
      if (event.key !== 'Enter') return;
      event.preventDefault();
      if (searchDebounce) clearTimeout(searchDebounce);
      applyFilters();
    });
  }
  document.getElementById('filter-status')?.addEventListener('change', applyFilters);
  ['filter-date-from', 'filter-date-to', 'filter-value', 'filter-overdue'].forEach(id => {
    document.getElementById(id)?.addEventListener('change', applyFilters);
  });
  // partner select is handled by custom dropdown events

  window.applyLocalFilters = applyFilters;
}

let currentSort = { column: 'attention', dir: 'asc' };

function setupLeadsSorting() {
  document.querySelectorAll('#leads-table-head th[data-sort]').forEach(th => {
    th.style.cursor = 'pointer';
    th.addEventListener('click', () => {
      const col = th.dataset.sort;
      if (currentSort.column === col) {
        currentSort.dir = currentSort.dir === 'asc' ? 'desc' : 'asc';
      } else {
        currentSort.column = col;
        currentSort.dir = 'asc';
      }
      document.querySelectorAll('#leads-table-head th').forEach(h => {
        // textContent skips the chevron element, so the stored label stays
        // clean even after a header has been sorted.
        if (!h.dataset.label) h.dataset.label = h.textContent.trim();
        h.textContent = h.dataset.label;
      });
      const chevron = Icons.el(currentSort.dir === 'asc' ? 'chevron-up' : 'chevron-down', { size: 14 });
      if (chevron) {
        chevron.style.cssText = 'vertical-align:-3px;margin-left:4px';
        th.appendChild(chevron);
      }
      sortLeads();
    });
  });
}

// Sorting orders the whole collection, not the visible page -- clicking
// "Value" has to bring the largest deal in the programme to the top, not the
// largest of the twenty-five already on screen. That makes it the database's
// job, so this asks for the first page of the new order.
function sortLeads() {
  currentPage = 1;
  void loadLeads();
}

function renderLeadsTable() {
  const tbody = document.getElementById('leads-tbody');
  const paginationEl = document.getElementById('pagination');

  const start      = (currentPage - 1) * perPage;
  const totalPages = Math.ceil(totalLeads / perPage);

  if (!tbody) return;

  if (!pageLeads.length) {
    tbody.replaceChildren();
    renderEmptyState();
    setQueueState('empty');
    if (paginationEl) renderPagination(paginationEl, 0);
    return;
  }

  leadRecords.clear();
  const rows = pageLeads.map((lead, index) => createLeadRow(lead, `lead-${start + index}`));
  tbody.replaceChildren(...rows);
  setQueueState('populated');

  if (paginationEl) renderPagination(paginationEl, totalPages);
}

function textElement(tag, className, value) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  element.textContent = String(value ?? '');
  return element;
}

function createActionButton(action, className, label, icon) {
  // btn-icon is a fixed 36x36 square, so a text label inside one overflows and
  // gets clipped -- pass an icon name for those and keep the words in the
  // accessible label and tooltip.
  const button = icon ? textElement('button', className, '') : textElement('button', className, label);
  if (icon) Icons.into(button, icon, { size: 16 });
  button.type = 'button';
  button.dataset.action = action;
  button.title = label;
  button.setAttribute('aria-label', label);
  return button;
}

// A lead carries two answers. In the TABLE this cell states the producer's
// decision only -- the thing the filter and the dashboard links work on --
// because the sales stage now has a column of its own (createFunnelCell).
function createStatusCell(lead) {
  const wrap = document.createElement('div');
  wrap.appendChild(Ui.registrationBadge(lead.registration_status));
  return wrap;
}

// In the detail MODAL there is no second column to move the stage into, so both
// answers stay together. Splitting the table's cell without this would have
// quietly dropped the stage from the modal.
function createStatusSummary(lead) {
  const wrap = createStatusCell(lead);
  if (!lead.status) return wrap;
  const stage = Ui.stageFor(stagePipeline, lead.status);
  wrap.appendChild(textElement(
    'div', 'text-xs text-tertiary', stage ? Ui.stageLabel(stage) : App.t('al_stage_unknown'),
  ));
  return wrap;
}

/*
   Where this lead stands in the producer's own funnel.

   The lead table shows where each lead sits in the funnel. A stage NAME alone
   does not answer that -- "Offer" says nothing about whether that is halfway or
   nearly closed. The position and the count do, and the segmented bar shows it
   without reading.

   Three states, because a lead is not always in the funnel at all:
     * not yet accepted -> it is waiting outside the funnel, said plainly
     * on a progress stage -> position, total, and the bar
     * on a terminal stage -> closed, with no position (a win is not step 5 of 4)
*/
function createFunnelCell(lead) {
  const cell = document.createElement('td');
  const wrap = document.createElement('div');
  wrap.className = 'funnel-cell';

  if (!lead.status) {
    wrap.appendChild(textElement('div', 'text-xs text-tertiary', App.t('chart_before_funnel')));
    cell.appendChild(wrap);
    return cell;
  }

  const stage = Ui.stageFor(stagePipeline, lead.status);
  if (!stage) {
    // A producer who deleted a stage out from under a live lead must be able to
    // see that is what happened, not a row that quietly shows less.
    wrap.appendChild(textElement('div', 'text-xs text-tertiary', App.t('al_stage_unknown')));
    cell.appendChild(wrap);
    return cell;
  }

  wrap.appendChild(Ui.stageBadge(stage));

  if (stage.is_terminal) {
    wrap.appendChild(textElement('div', 'text-xs text-tertiary', App.t('al_closed')));
    cell.appendChild(wrap);
    return cell;
  }

  const steps = (stagePipeline && stagePipeline.nonTerminal) || [];
  const position = steps.findIndex((candidate) => candidate.key === stage.key) + 1;
  if (position > 0 && steps.length) {
    wrap.appendChild(textElement('div', 'text-xs text-tertiary',
      App.t('stage_progress_position', { position, total: steps.length })));

    const bar = document.createElement('div');
    bar.className = 'funnel-steps';
    bar.setAttribute('aria-hidden', 'true');
    steps.forEach((step, index) => {
      const seg = document.createElement('span');
      seg.className = 'funnel-step';
      // Everything up to and including the current stage is filled, in that
      // stage's own colour -- so the bar reads the same way the kanban does.
      if (index < position) seg.style.background = safeStageColor(stage.color);
      bar.appendChild(seg);
    });
    wrap.appendChild(bar);
  }

  cell.appendChild(wrap);
  return cell;
}

function safeStageColor(value) {
  return /^#[0-9a-fA-F]{6}$/.test(String(value || '')) ? value : 'var(--brand-500)';
}

// What the deal is worth to the partner, on the row rather than three
// clicks away. States plainly whether the figure is a forecast or a
// recorded amount --- the same distinction the detail modal draws, so the
// two can never disagree about one lead.
function createCommissionCell(lead) {
  const cell = document.createElement('td');
  const recorded = lead.commission != null;
  const amount = recorded ? lead.commission : (lead.commission_projected ?? 0);
  cell.appendChild(textElement('div', 'cell-primary', formatCurrency(amount)));
  const note = textElement(
    'div', 'text-xs text-tertiary', App.t(recorded ? 'leads_commission_recorded_short' : 'leads_commission_projected_short'),
  );
  if (lead.commission_override_pct != null) {
    note.textContent += ` · ${App.t('leads_override_short', { rate: lead.commission_override_pct })}`;
  }
  cell.appendChild(note);
  return cell;
}

function createLeadRow(lead, key) {
  leadRecords.set(key, lead);
  const row = document.createElement('tr');
  row.dataset.recordKey = key;
  row.dataset.id = String(lead.id ?? '');
  if (Number.isSafeInteger(lead.revision) && lead.revision >= 0) {
    row.dataset.revision = String(lead.revision);
  }

  const clientCell = document.createElement('td');
  const clientName = textElement('div', 'cell-primary', lead.company || '—');
  const companyName = textElement('div', 'text-xs text-tertiary', lead.client_name || '—');
  [clientName, companyName].forEach(element => {
    element.title = element.textContent;
    element.style.cssText = 'max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap';
  });
  clientCell.append(clientName, companyName);
  clientCell.lastElementChild.style.marginTop = '2px';

  const partnerCell = document.createElement('td');
  const partnerWrap = document.createElement('div');
  partnerWrap.style.cssText = 'display:flex;align-items:center;gap:8px';
  const partnerCompany = lead.partner_company || lead.partner_name || '—';
  const avatar = textElement('div', `avatar avatar-sm ${getAvatarColorClass(partnerCompany)}`, getInitials(partnerCompany));
  const partnerLabel = textElement('span', '', partnerCompany);
  partnerLabel.title = partnerCompany;
  partnerWrap.append(avatar, partnerLabel);
  partnerCell.appendChild(partnerWrap);

  const statusCell = document.createElement('td');
  statusCell.appendChild(createStatusCell(lead));

  const actionsCell = document.createElement('td');
  const actions = document.createElement('div');
  actions.className = 'row-actions';
  // All three decisions are reachable from the row itself. Reject and
  // request-info open an inline form directly under this row, so the
  // producer keeps their place in the list.
  if (Ui.isLeadUnhandled(lead)) {
    actions.append(
      createActionButton('accept', 'btn-row btn-row-accept', App.t('al_confirm'), 'check'),
      createActionButton('reject', 'btn-row btn-row-reject', App.t('al_reject_with_reason'), 'x'),
    );
    if (lead.registration_status === 'pending') {
      actions.append(createActionButton(
        'request_info', 'btn-row btn-row-info', App.t('al_request_info'), 'message-square',
      ));
    }
  } else if (lead.registration_status === 'rejected') {
    // A refusal is the one decision a producer can make by accident and could
    // not take back: the lead left the list and there was no door back in.
    // Undo returns it to "pending" -- awaiting a decision, not approved.
    actions.append(createActionButton(
      'undo_reject', 'btn-row', App.t('al_undo_reject'), 'rotate-ccw',
    ));
  }
  actions.appendChild(createActionButton('detail', 'btn-row', App.t('al_details'), 'eye'));
  actionsCell.appendChild(actions);

  row.append(
    clientCell,
    partnerCell,
    textElement('td', 'text-tertiary text-sm', formatLeadAge(lead.created_at)),
    textElement('td', 'cell-primary', formatCurrency(lead.value)),
    createCommissionCell(lead),
    statusCell,
    createFunnelCell(lead),
    actionsCell,
  );
  return row;
}

// "Overdue" is decided by the query, against the same setting as the
// dashboard card -- api/leads.py::OVERDUE_LEAD_CONDITION. A second definition
// here would let the list and the card disagree about the same lead.

function formatLeadAge(createdAt) {
  const elapsedHours = Math.max(0, Math.floor((Date.now() - Ui.parseServerDate(createdAt).getTime()) / 3600000));
  if (elapsedHours < 24) return App.pluralize(elapsedHours, 'unit_hour_age');
  const days = Math.floor(elapsedHours / 24);
  return App.pluralize(days, 'unit_day_age');
}

/**
 * Which page numbers to draw: the first, the last, and a window around the
 * current one, with a gap marker where numbers were left out.
 *
 * Ten thousand leads at twenty-five a page is four hundred pages, and a row of
 * four hundred buttons is not navigation -- it is a wall the real controls hide
 * behind.
 */
function paginationWindow(totalPages, page, radius = 2) {
  if (totalPages < 1) return [];
  const wanted = new Set([1, totalPages]);
  for (let candidate = page - radius; candidate <= page + radius; candidate += 1) {
    if (candidate >= 1 && candidate <= totalPages) wanted.add(candidate);
  }
  const shown = [...wanted].sort((a, b) => a - b);
  const items = [];
  let previous = 0;
  for (const value of shown) {
    if (previous && value - previous > 1) items.push('gap');
    items.push(value);
    previous = value;
  }
  return items;
}

function renderPagination(container, totalPages) {
  const infoEl = container.querySelector('.pagination-info');
  const btnsEl = container.querySelector('.pagination-btns');
  const start  = totalLeads ? (currentPage - 1) * perPage + 1 : 0;
  const end    = Math.min(currentPage * perPage, totalLeads);
  if (infoEl) infoEl.textContent = App.t('leads_pagination', { start, end, total: totalLeads });
  if (!btnsEl) return;

  const buttons = [];
  const previous = textElement('button', 'page-btn', '‹');
  previous.type = 'button';
  previous.dataset.page = String(currentPage - 1);
  previous.disabled = currentPage === 1 || totalPages === 0;
  previous.setAttribute('aria-label', App.t('leads_page_previous'));
  buttons.push(previous);
  for (const item of paginationWindow(totalPages, currentPage)) {
    if (item === 'gap') {
      const gap = textElement('span', 'page-gap', '…');
      gap.setAttribute('aria-hidden', 'true');
      buttons.push(gap);
      continue;
    }
    const button = textElement('button', `page-btn ${item === currentPage ? 'active' : ''}`.trim(), item);
    button.type = 'button';
    button.dataset.page = String(item);
    if (item === currentPage) button.setAttribute('aria-current', 'page');
    buttons.push(button);
  }
  const next = textElement('button', 'page-btn', '›');
  next.type = 'button';
  next.dataset.page = String(currentPage + 1);
  next.disabled = currentPage === totalPages || totalPages === 0;
  next.setAttribute('aria-label', App.t('leads_page_next'));
  buttons.push(next);
  btnsEl.replaceChildren(...buttons);
}

function goToPage(page) {
  const totalPages = Math.ceil(totalLeads / perPage);
  if (page < 1 || page > totalPages || page === currentPage) return;
  currentPage = page;
  void loadLeads();
}

// ─── LEAD DETAIL MODAL ────────────────────────────────────
function setupLeadModal() {
  document.getElementById('lead-accept-btn')?.addEventListener('click', async () => {
    if (selectedLead) { await acceptLead(selectedLead); closeModal('lead-modal'); }
  });
  document.getElementById('lead-reject-btn')?.addEventListener('click', () => {
    const form = document.getElementById('lead-reject-form');
    if (form) form.hidden = false;
  });
  const reason = document.getElementById('lead-reject-reason');
  const rejectConfirm = document.getElementById('lead-reject-confirm-btn');
  reason?.addEventListener('input', () => {
    if (rejectConfirm) rejectConfirm.disabled = !reason.value.trim();
  });
  rejectConfirm?.addEventListener('click', async () => {
    if (!selectedLead || !reason?.value.trim()) return;
    await rejectLead(selectedLead, reason.value.trim());
    closeModal('lead-modal');
  });
  document.getElementById('lead-more-info-btn')?.addEventListener('click', () => {
    const form = document.getElementById('lead-more-info-form');
    if (form) form.hidden = false;
  });
  document.getElementById('lead-more-info-confirm-btn')?.addEventListener('click', requestMoreInfo);
  document.getElementById('save-commission-btn')?.addEventListener('click', saveCommission);
  document.getElementById('save-commission-pct-btn')?.addEventListener('click', saveCommissionRate);
}

function setupLeadActions() {
  document.getElementById('leads-tbody')?.addEventListener('click', (event) => {
    const row = event.target.closest('tr[data-record-key]');
    if (!row) return;
    const lead = leadRecords.get(row.dataset.recordKey);
    if (!lead) return;
    const action = event.target.closest('[data-action]')?.dataset.action || 'detail';
    if (action === 'accept') {
      Ui.closeRowDecisionForm(row.parentElement);
      void acceptLead(lead);
    } else if (action === 'reject') {
      Ui.openRowDecisionForm(row, action, (reason) => rejectLead(lead, reason));
    } else if (action === 'request_info') {
      Ui.openRowDecisionForm(row, action, (message) => requestInformation(lead, message));
    } else if (action === 'undo_reject') {
      Ui.closeRowDecisionForm(row.parentElement);
      void undoRejection(lead);
    } else {
      void openLeadDetail(lead.id);
    }
  });
  document.querySelector('#pagination .pagination-btns')?.addEventListener('click', (event) => {
    const button = event.target.closest('[data-page]');
    if (button && !button.disabled) goToPage(Number(button.dataset.page));
  });
}

// One advance control per pipeline stage reachable from a lead's current
// position, beyond the three Copywriting-Contract-owned buttons (accept ->
// commission trigger, reject-with-reason -> lose terminal, request-more-info
// -> the stage immediately preceding the trigger). Delegated so re-rendering
// the container on every openLeadDetail() needs no rebinding.
function setupAdvanceControls() {
  document.getElementById('lead-advance-controls')?.addEventListener('click', (event) => {
    const button = event.target.closest('[data-advance-key]');
    if (!button || !selectedLead) return;
    void advanceLead(selectedLead.id, button.dataset.advanceKey);
  });
}

function renderDecisionControls(lead) {
  const container = document.getElementById('lead-advance-controls');
  const acceptBtn = document.getElementById('lead-accept-btn');
  const rejectBtn = document.getElementById('lead-reject-btn');
  const moreInfoBtn = document.getElementById('lead-more-info-btn');

  const currentStage = Ui.stageFor(stagePipeline, lead.status);
  const winTerminal = stagePipeline?.winTerminal || null;
  const alreadyTerminal = !!(currentStage && currentStage.is_terminal);

  // The three decision controls act on the REGISTRATION axis, so they are
  // offered exactly while the deal is still undecided, whatever its place in
  // the sales pipeline.
  const undecided = Ui.isAwaitingDecision(lead);
  if (acceptBtn) acceptBtn.style.display = undecided ? 'flex' : 'none';
  if (rejectBtn) rejectBtn.style.display = undecided ? 'flex' : 'none';
  if (moreInfoBtn) {
    // Asking for information only makes sense before a decision, and only
    // once --- a lead already sitting on that request has nothing to add.
    const show = lead.registration_status === 'pending';
    moreInfoBtn.style.display = show ? 'flex' : 'none';
  }

  if (!container) return;
  container.replaceChildren();
  if (!stagePipeline) return;

  // Sales stages are offered only for an approved deal: an unapproved one is
  // not in the sale, and the server refuses the move anyway.
  if (lead.registration_status !== 'accepted') return;

  const extras = Ui.reachableAdvanceStages(lead, stagePipeline);
  if (winTerminal && (!currentStage || currentStage.key !== winTerminal.key)) extras.push(winTerminal);

  extras.forEach((stage) => {
    const button = createActionButton(`advance-${stage.key}`, 'btn btn-secondary btn-sm', App.t('al_move_to', { stage: Ui.stageLabel(stage) }));
    button.dataset.advanceKey = stage.key;
    container.appendChild(button);
  });
}

async function openLeadDetail(id) {
  try {
    const data = await ApiClient.get(`/api/leads/${encodeURIComponent(id)}`);
    const l    = data.lead;
    selectedLead = l;

    document.getElementById('modal-lead-id').textContent      = l.id;
    document.getElementById('modal-lead-name').textContent    = l.client_name;
    document.getElementById('modal-lead-company').textContent = l.company;
    setText('modal-lead-contact', l.client_name);
    document.getElementById('modal-lead-email').textContent   = l.client_email;
    document.getElementById('modal-lead-phone').textContent   = l.client_phone || '—';
    document.getElementById('modal-lead-value').textContent   = formatCurrency(l.value);
    document.getElementById('modal-lead-date').textContent    = formatDate(l.created_at);
    document.getElementById('modal-lead-partner').textContent = l.partner_name || '—';
    document.getElementById('modal-lead-notes').textContent   = l.notes || '—';
    document.getElementById('modal-lead-sector').textContent  = l.sector || '—';
    setText('modal-lead-nip', l.nip || '—');
    setText('modal-lead-website', l.website || '—');
    setText('modal-lead-product', Ui.producerLabel(l, 'product') || '—');
    setText('modal-lead-quantity', Number(l.quantity) > 0 ? l.quantity : '—');
    document.getElementById('modal-lead-status').replaceChildren(createStatusSummary(l));
    document.getElementById('modal-commission').value         = l.commission || '';
    renderQueuePosition(l);
    renderCommission(l);

    const rejectForm = document.getElementById('lead-reject-form');
    const moreInfoForm = document.getElementById('lead-more-info-form');
    const rejectReason = document.getElementById('lead-reject-reason');
    const rejectConfirm = document.getElementById('lead-reject-confirm-btn');
    if (rejectForm) rejectForm.hidden = true;
    if (moreInfoForm) moreInfoForm.hidden = true;
    if (rejectReason) rejectReason.value = '';
    if (rejectConfirm) rejectConfirm.disabled = true;

    renderDecisionControls(l);
    renderReopenSection(l);

    openModal('lead-modal');

    // Load comments into modal
    if (typeof Comments !== 'undefined') {
      const session = JSON.parse(sessionStorage.getItem('ph_session') || '{}');
      Comments.load(l.id, '#modal-comments-container', session);
    }
  } catch (err) {
    handleApiError(err, 'openLeadDetail');
  }
}

function setText(id, value) {
  const element = document.getElementById(id);
  if (element) element.textContent = String(value ?? '—');
}

function renderQueuePosition(lead) {
  const badge = document.getElementById('modal-queue-position');
  if (!badge) return;
  const position = Number(lead.queue_position || 1);
  badge.hidden = position <= 1;
  if (position <= 1) {
    badge.textContent = '';
    return;
  }
  const share = lead.queue_share ?? lead.queue_modifier
    ?? Ui.computeQueueShare(position, adminQueueShares);
  badge.textContent = App.t('al_queue_position', { position, share });
}

function renderCommission(lead) {
  const breakdown = lead.commission_breakdown || {};
  const tier = breakdown.tier ?? lead.tier_commission ?? lead.commission_projected ?? 0;
  const recurring = breakdown.recurring ?? lead.recurring_bonus ?? 0;
  const cash = breakdown.cash ?? lead.cash_bonus ?? 0;
  setText('modal-commission-tier', formatCurrency(tier));
  setText('modal-commission-recurring', formatCurrency(recurring));
  setText('modal-commission-cash', formatCurrency(cash));

  // Two figures, two meanings: `commission` is what is actually recorded
  // against the deal (only once it is won or a producer overrides it), and
  // `commission_projected` is the forecast the backend recomputes on every
  // read. Showing the forecast when nothing is recorded is what makes the
  // number visible at every stage.
  const rateField = document.getElementById('modal-commission-pct');
  if (rateField) {
    rateField.value = lead.commission_override_pct ?? '';
  }
  const rateHint = document.getElementById('modal-commission-pct-hint');
  if (rateHint) {
    rateHint.textContent = lead.commission_override_pct != null
      // The tier the rate came from, in the reader's language.
      ? App.t('leads_rate_override_hint', { rate: lead.commission_override_pct, tier: Ui.producerLabel(lead, 'tier_name') || '—' })
      : App.t('puste_pole_stawka_z_poziomu_partnera');
  }

  // The server refuses a correction once the commission is paid, and lets
  // only the account owner change it after the win was confirmed, so the
  // controls are not offered where the write would be refused.
  const paid = !!lead.commission_paid_at || lead.settlement_status === 'paid';
  const lockedReason = paid
    ? 'al_commission_locked_paid'
    : (lead.win_confirmed_at && !isProducerSession() ? 'al_commission_locked_confirmed' : null);
  ['modal-commission', 'save-commission-btn', 'modal-commission-pct', 'save-commission-pct-btn']
    .forEach((id) => {
      const control = document.getElementById(id);
      if (control) control.disabled = !!lockedReason;
    });
  if (rateHint && lockedReason) rateHint.textContent = App.t(lockedReason);

  const recorded = lead.commission != null;
  setText(
    'modal-commission-total',
    formatCurrency(recorded ? lead.commission : (lead.commission_projected ?? 0)),
  );
  setText('modal-commission-state', App.t(recorded ? 'leads_commission_recorded' : 'leads_commission_projected'));
}

// Every decision carries the version it was made against and a replay key.
// The key is derived from the lead, the action and that version rather than
// randomly generated, so a double-click — or a retry after a lost response —
// is the SAME operation to the server, not a second decision.
function decisionEnvelope(lead, action) {
  const revision = Number(lead?.revision ?? 0);
  return {
    expected_revision: revision,
    operation_id: `${action}-${lead?.id}-${revision}`,
  };
}

// A conflict means the screen is stale, so the only honest response is to
// re-read the lead rather than retry the write against a version that is
// already gone.
async function handleDecisionFailure(err, context, leadId) {
  if (err && err.status === 409) {
    showToast('error', App.t('al_submission_changed'),
      App.t('al_someone_changed'));
    try { await openLeadDetail(leadId); } catch { /* surfaced below */ }
    return;
  }
  handleApiError(err, context);
}

/*
   One writer for all three registration decisions, taking the lead RECORD
   rather than an id.

   The envelope has to carry the revision of the lead the producer actually
   acted on. Every decision used to read the module-level `selectedLead`, which
   only holds a value once the detail modal has been opened -- so deciding
   straight from a table row sent `expected_revision: 0` and an operation_id of
   `accept-undefined-0`, while the correct revision sat unread in the row's own
   dataset. Passing the record makes the row path and the modal path equally
   safe, and keeps the replay key stable so a double-click is still ONE
   decision to the server.
*/
async function postDecision(record, action, message) {
  const body = { action, ...decisionEnvelope(record, action) };
  if (message) body.message = message;
  const data = await ApiClient.post(
    `/api/leads/${encodeURIComponent(record?.id)}/registration`,
    body,
  );
  const lead = data.lead;
  selectedLead = lead;
  syncLead(lead);
  renderDecisionControls(lead);
  return lead;
}

async function acceptLead(record) {
  try {
    const lead = await postDecision(record, 'accept');
    showToast('success', App.t('al_lead_accepted'), `${lead.client_name} — ${lead.company}`);
  } catch (err) {
    await handleDecisionFailure(err, 'acceptLead', record?.id);
  }
}

async function rejectLead(record, reason) {
  try {
    const lead = await postDecision(record, 'reject', reason);
    showToast('error', App.t('al_lead_rejected'), `${lead.client_name} — ${lead.company}`);
  } catch (err) {
    await handleDecisionFailure(err, 'rejectLead', record?.id);
  }
}

async function undoRejection(record) {
  try {
    const lead = await postDecision(record, 'undo_reject');
    showToast('success', App.t('al_rejection_undone'),
      App.t('al_back_for_decision', { name: lead.client_name }));
  } catch (err) {
    await handleDecisionFailure(err, 'undoRejection', record?.id);
  }
}

async function requestInformation(record, message) {
  try {
    const lead = await postDecision(record, 'request_info', message);
    showToast('success', App.t('al_request_sent'), lead.client_name);
  } catch (err) {
    await handleDecisionFailure(err, 'requestInformation', record?.id);
  }
}

// The modal path composes its message from the "missing field" checkboxes
// before handing over to the shared writer above.
async function requestMoreInfo() {
  if (!selectedLead) return;
  // The words come from each box's (translated) label, not its value: the
  // message reaches the partner in the language on screen.
  const fields = [...document.querySelectorAll('[name="missing-field"]:checked')]
    .map(control => control.closest('label')?.textContent.trim() || control.value);
  const note = document.getElementById('lead-more-info-note')?.value.trim() || '';
  if (!fields.length && !note) return;
  const listed = fields.length ? fields.join(', ') : App.t('lead_info_request_default');
  const body = `${App.t('lead_info_request_prefix')} ${listed}${note ? `. ${note}` : ''}`;
  await requestInformation(selectedLead, body);
}

async function advanceLead(id, targetKey) {
  if (!targetKey) return;
  try {
    const data = await ApiClient.patch(
      `/api/leads/${encodeURIComponent(id)}`,
      { status: targetKey, ...decisionEnvelope(selectedLead, `stage-${targetKey}`) },
    );
    const lead = data.lead;
    if (selectedLead && selectedLead.id === lead.id) {
      selectedLead = lead;
      renderDecisionControls(lead);
      document.getElementById('modal-lead-status')?.replaceChildren(createStatusSummary(lead));
    }
    syncLead(lead);
    showToast('success', App.t('al_lead_moved'), `${lead.client_name} — ${lead.company}`);
  } catch (err) {
    await handleDecisionFailure(err, 'advanceLead', id);
  }
}

// ─── REOPENING A CLOSED LEAD ────────────────
//
// Producer-admin-only: entirely absent from the DOM for every other role
// and for a lead that is not currently terminal (won/lost), mirroring the
// server's own can_reopen_lead()/_terminal_exit_attempted gates rather than
// only hiding a button a forged request could still reach.

function reopenSettlementEligibility(lead) {
  const hasHistory = lead.commission != null || lead.won_at || lead.settlement_status != null
    || lead.settlement_reviewed_by != null || lead.settlement_reviewed_at;
  if (!hasHistory) return { eligible: true, blocked: null };
  if (lead.settlement_status === 'unpaid') return { eligible: true, blocked: null };
  if (lead.settlement_status === 'paid') return { eligible: false, blocked: 'paid' };
  return { eligible: false, blocked: 'unknown' };
}

function renderReopenSection(lead) {
  const section = document.getElementById('lead-reopen-section');
  if (!section) return;
  restoreSettlementDraft(lead);
  updateReopenBusyState();
  const checkBtn = document.getElementById('lead-reopen-check-btn');
  const openBtn = document.getElementById('lead-reopen-open-btn');
  const stateEl = document.getElementById('lead-reopen-settlement-state');
  const reviewForm = document.getElementById('lead-reopen-review-form');

  const currentStage = Ui.stageFor(stagePipeline, lead.status);
  const isTerminal = !!(currentStage && currentStage.is_terminal);
  const session = JSON.parse(sessionStorage.getItem('ph_session') || '{}');
  const isAdmin = session.role === 'admin';

  if (!isTerminal || !isAdmin || lead.registration_status !== 'accepted') {
    section.hidden = true;
    if (reviewForm) reviewForm.hidden = true;
    return;
  }
  section.hidden = false;

  const eligibility = reopenSettlementEligibility(lead);
  if (eligibility.eligible) {
    if (stateEl) stateEl.textContent = reopenDestinations().length ? '' : (App.t('al_no_active_destination_stage_configure'));
    if (checkBtn) checkBtn.hidden = true;
    if (openBtn) openBtn.hidden = false;
    if (reviewForm) reviewForm.hidden = true;
  } else if (eligibility.blocked === 'paid') {
    if (stateEl) {
      stateEl.textContent = App.t('al_this_lead_cannot_be_reopened');
    }
    if (checkBtn) checkBtn.hidden = true;
    if (openBtn) openBtn.hidden = true;
    if (reviewForm) reviewForm.hidden = true;
  } else {
    if (stateEl) {
      stateEl.textContent = App.t('al_the_commission_settlement_status_is');
    }
    if (checkBtn) checkBtn.hidden = false;
    if (openBtn) openBtn.hidden = true;
  }
}

function updateReopenReviewSaveState() {
  const selected = document.querySelector('input[name="reopen-review-result"]:checked');
  const result = selected ? selected.value : null;
  const confirmLabel = document.getElementById('lead-reopen-review-confirm-label');
  const confirmBox = document.getElementById('lead-reopen-review-confirm');
  const saveBtn = document.getElementById('lead-reopen-review-save-btn');
  // The confirmation checkbox's own worded text is specifically about
  // confirming NON-payment, so it is only offered for the
  // 'unpaid' result -- 'paid' still requires the same reason field, just
  // not that specific sentence.
  if (confirmLabel) confirmLabel.hidden = result !== 'unpaid';
  if (!saveBtn) return;
  if (!result) { saveBtn.disabled = true; return; }
  const reasonInput = document.getElementById('lead-reopen-review-reason');
  const reasonOk = !!(reasonInput && reasonInput.value.trim() && reasonInput.value.trim().length <= 4000);
  const confirmOk = result !== 'unpaid' || !!(confirmBox && confirmBox.checked);
  saveBtn.disabled = reopenBusy.has(selectedLead?.id) || reopenRefreshNeeded.has(selectedLead?.id) || !(reasonOk && confirmOk);
}

async function saveSettlementReview() {
  if (!selectedLead || reopenBusy.has(selectedLead.id) || reopenRefreshNeeded.has(selectedLead.id)) return;
  const lead = { ...selectedLead };
  const selected = document.querySelector('input[name="reopen-review-result"]:checked');
  const result = selected ? selected.value : null;
  const reason = document.getElementById('lead-reopen-review-reason')?.value.trim();
  if (!['paid', 'unpaid'].includes(result) || !reason || reason.length > 4000) return;
  if (result === 'unpaid' && !document.getElementById('lead-reopen-review-confirm')?.checked) return;
  reopenBusy.add(lead.id);
  updateReopenBusyState();
  try {
    const data = await ApiClient.post(
      `/api/leads/${encodeURIComponent(lead.id)}/reopen-eligibility`,
      { result, reason, confirmed: true, ...reopenEnvelope(lead, 'review', { result, reason }) },
    );
    settlementDrafts.delete(lead.id);
    syncLead(data.lead);
    if (selectedLead?.id === lead.id) {
      selectedLead = data.lead;
      renderReopenSection(data.lead);
      const form = document.getElementById('lead-reopen-review-form');
      if (form) form.hidden = true;
    }
    showToast(
      'success',
      App.t('al_review_result_saved'),
      data.lead.client_name,
    );
  } catch (err) {
    await handleReopenFailure(err, 'saveSettlementReview', lead.id);
  } finally {
    reopenBusy.delete(lead.id);
    updateReopenBusyState();
  }
}

// A real, functional modal (confirmed=true is the actual security
// boundary, not this popup) -- role=dialog, aria-modal, Escape/backdrop/the
// neutral "Keep lead closed" button all resolve null and send
// nothing; only the primary action resolves a choice. Mirrors
// js/partner.js::confirmTerminalMove's shape.
function confirmReopenLead(lead) {
  return new Promise((resolve) => {
    const previousFocus = document.activeElement;
    const draft = reopenDrafts.get(lead.id);
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
    title.textContent = App.t('al_reopen_lead');
    const titleId = `reopen-modal-title-${lead.id}`.replace(/[^A-Za-z0-9_-]/g, '');
    title.id = titleId;
    modal.setAttribute('aria-labelledby', titleId);
    header.appendChild(title);

    const body = document.createElement('div');
    body.className = 'modal-body';

    const targetLabel = document.createElement('label');
    targetLabel.className = 'form-label';
    targetLabel.style.display = 'block';
    targetLabel.textContent = App.t('al_destination_stage');
    const select = document.createElement('select');
    select.className = 'form-input';
    select.id = `${titleId}-stage`;
    targetLabel.htmlFor = select.id;
    reopenDestinations().forEach((stage) => {
      const opt = document.createElement('option');
      opt.value = stage.key;
      opt.textContent = Ui.stageLabel(stage);
      select.appendChild(opt);
    });
    if (draft) {
      if ([...select.options].some((option) => option.value === draft.targetStage)) select.value = draft.targetStage;
      else {
        const placeholder = document.createElement('option');
        placeholder.value = '';
        placeholder.textContent = App.t('al_choose_a_new_destination_stage');
        placeholder.disabled = true;
        select.prepend(placeholder);
        select.value = '';
      }
    }

    const reasonLabel = document.createElement('label');
    reasonLabel.className = 'form-label';
    reasonLabel.style.display = 'block';
    reasonLabel.style.marginTop = '10px';
    reasonLabel.textContent = App.t('al_reason_for_reopening');
    const reasonInput = document.createElement('textarea');
    reasonInput.className = 'form-input';
    reasonInput.id = `${titleId}-reason`;
    reasonInput.maxLength = 4000;
    reasonInput.value = draft?.reason || '';
    reasonLabel.htmlFor = reasonInput.id;
    const initialReason = reasonInput.value;
    const initialStage = select.value;

    const message = document.createElement('p');
    message.className = 'text-sm text-tertiary';
    message.style.marginTop = '10px';

    const hasUnpaidWin = Ui.stageFor(stagePipeline, lead.status)?.kind === 'win'
      && lead.commission != null && lead.settlement_status === 'unpaid';
    const updateMessage = () => {
      const company = lead.company || lead.client_name || lead.id;
      const stageName = select.options[select.selectedIndex]?.textContent || '';
      let text = App.t('al_reopen_q', { company, stage: stageName });
      if (hasUnpaidWin) {
        text += App.t('al_unpaid_excluded');
      }
      message.textContent = text;
    };
    select.addEventListener('change', updateMessage);
    updateMessage();

    body.append(targetLabel, select, reasonLabel, reasonInput, message);

    const footer = document.createElement('div');
    footer.className = 'modal-footer';
    const keepBtn = document.createElement('button');
    keepBtn.type = 'button';
    keepBtn.className = 'btn btn-ghost';
    keepBtn.textContent = App.t('al_keep_lead_closed');
    const confirmBtn = document.createElement('button');
    confirmBtn.type = 'button';
    confirmBtn.className = 'btn btn-primary';
    confirmBtn.disabled = !select.value || !reasonInput.value.trim();
    confirmBtn.textContent = App.t('al_reopen_lead_2');
    footer.append(keepBtn, confirmBtn);

    const updateConfirmState = () => {
      confirmBtn.disabled = !select.value || !reasonInput.value.trim() || reasonInput.value.trim().length > 4000;
    };
    reasonInput.addEventListener('input', updateConfirmState);
    select.addEventListener('change', updateConfirmState);

    modal.append(header, body, footer);
    overlay.appendChild(modal);
    document.body.appendChild(overlay);

    const cleanup = (result) => {
      overlay.removeEventListener('keydown', handleEscapeKey);
      overlay.remove();
      if (previousFocus?.isConnected) previousFocus.focus();
      resolve(result);
    };
    let discardPanel = null;
    const requestClose = () => {
      if (discardPanel) return;
      if (reasonInput.value === initialReason && select.value === initialStage) { cleanup(null); return; }
      discardPanel = document.createElement('div');
      const text = document.createElement('p');
      text.textContent = App.t('al_discard_unsaved_changes');
      const back = document.createElement('button');
      back.type = 'button';
      back.className = 'btn btn-secondary';
      back.setAttribute('data-reopen-discard-back', '');
      back.textContent = App.t('al_continue_editing');
      const discard = document.createElement('button');
      discard.type = 'button';
      discard.className = 'btn btn-danger';
      discard.setAttribute('data-reopen-discard', '');
      discard.textContent = App.t('al_discard_changes');
      [select, reasonInput, keepBtn, confirmBtn].forEach((el) => { el.disabled = true; });
      back.addEventListener('click', () => {
        discardPanel.remove();
        discardPanel = null;
        [select, reasonInput, keepBtn].forEach((el) => { el.disabled = false; });
        updateConfirmState();
        reasonInput.focus();
      });
      discard.addEventListener('click', () => { reopenDrafts.delete(lead.id); cleanup(null); });
      discardPanel.append(text, back, discard);
      modal.appendChild(discardPanel);
      back.focus();
    };
    const handleEscapeKey = (event) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        event.stopPropagation();
        requestClose();
      }
      if (event.key === 'Tab') {
        const controls = [...modal.querySelectorAll('select, textarea, button')].filter((el) => !el.disabled);
        const first = controls[0];
        const last = controls[controls.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
    };
    keepBtn.addEventListener('click', requestClose);
    confirmBtn.addEventListener('click', () => {
      if (!select.value || !reasonInput.value.trim() || reasonInput.value.trim().length > 4000) return;
      cleanup({ targetStage: select.value, reason: reasonInput.value.trim() });
    });
    overlay.addEventListener('click', (event) => { if (event.target === overlay) requestClose(); });
    overlay.addEventListener('keydown', handleEscapeKey);
    keepBtn.focus();
  });
}

async function openReopenModal() {
  if (!selectedLead || !reopenDestinations().length || reopenDialogOpen || reopenBusy.has(selectedLead.id) || reopenRefreshNeeded.has(selectedLead.id)) return;
  const lead = { ...selectedLead };
  reopenDialogOpen = true;
  updateReopenBusyState();
  let choice;
  try { choice = await confirmReopenLead(lead); }
  finally {
    reopenDialogOpen = false;
    updateReopenBusyState();
  }
  if (!choice) return;
  reopenDrafts.set(lead.id, choice);
  reopenBusy.add(lead.id);
  updateReopenBusyState();
  try {
    const data = await ApiClient.post(
      `/api/leads/${encodeURIComponent(lead.id)}/reopen`,
      {
        target_stage: choice.targetStage, reason: choice.reason, confirmed: true,
        ...reopenEnvelope(lead, 'reopen', choice),
      },
    );
    reopenDrafts.delete(lead.id);
    syncLead(data.lead);
    if (selectedLead?.id === lead.id) {
      selectedLead = data.lead;
      renderDecisionControls(data.lead);
      renderReopenSection(data.lead);
      document.getElementById('modal-lead-status')?.replaceChildren(createStatusSummary(data.lead));
    }
    showToast(
      'success',
      App.t('al_lead_reopened'),
      data.lead.client_name,
    );
  } catch (err) {
    await handleReopenFailure(err, 'openReopenModal', lead.id);
  } finally {
    reopenBusy.delete(lead.id);
    updateReopenBusyState();
  }
}

function setupReopenControls() {
  document.getElementById('lead-reopen-check-btn')?.addEventListener('click', () => {
    const form = document.getElementById('lead-reopen-review-form');
    if (form) form.hidden = false;
    rememberSettlementDraft();
  });
  document.querySelectorAll('input[name="reopen-review-result"]').forEach((radio) => {
    radio.addEventListener('change', rememberSettlementDraft);
  });
  document.getElementById('lead-reopen-review-reason')?.addEventListener('input', rememberSettlementDraft);
  document.getElementById('lead-reopen-review-confirm')?.addEventListener('change', updateReopenReviewSaveState);
  document.getElementById('lead-reopen-review-save-btn')?.addEventListener('click', saveSettlementReview);
  document.getElementById('lead-reopen-open-btn')?.addEventListener('click', openReopenModal);
}

async function saveCommission() {
  if (!selectedLead) return;
  const val = parseFloat(document.getElementById('modal-commission').value);
  if (isNaN(val) || val < 0) return;
  try {
    const data = await ApiClient.patch(
      `/api/leads/${encodeURIComponent(selectedLead.id)}`,
      { commission: val, ...decisionEnvelope(selectedLead, `commission-${val}`) },
    );
    selectedLead = data.lead;
    syncLead(data.lead);
    renderCommission(data.lead);
    showToast('success', App.t('al_commission_saved'), `${formatCurrency(val)} dla ${selectedLead.client_name}`);
  } catch (err) {
    await handleDecisionFailure(err, 'saveCommission', selectedLead.id);
  }
}

// A rate correction states WHAT was agreed and lets the backend recompute
// the money --- the browser never multiplies a rate of its own, so no screen
// can show a rate other than the partner's real tier. An emptied field clears the
// correction and hands the deal back to its tier rate.
async function saveCommissionRate() {
  if (!selectedLead) return;
  const raw = document.getElementById('modal-commission-pct').value.trim();
  let pct = null;
  if (raw !== '') {
    pct = parseFloat(raw);
    if (!Number.isFinite(pct) || pct < 0 || pct > 100) {
      showToast('error', App.t('al_invalid_rate'), App.t('al_rate_range'));
      return;
    }
  }
  try {
    const data = await ApiClient.patch(
      `/api/leads/${encodeURIComponent(selectedLead.id)}`,
      { commission_override_pct: pct,
        ...decisionEnvelope(selectedLead, `rate-${pct === null ? 'clear' : pct}`) },
    );
    selectedLead = data.lead;
    syncLead(data.lead);
    renderCommission(data.lead);
    showToast(
      'success',
      pct === null ? App.t('al_adjustment_removed') : App.t('al_rate_saved'),
      pct === null
        ? App.t('al_tier_rate_applies', { name: selectedLead.client_name })
        : App.t('al_rate_for_client', { pct, name: selectedLead.client_name }),
    );
  } catch (err) {
    await handleDecisionFailure(err, 'saveCommissionRate', selectedLead.id);
  }
}

// ─── PRODUCER ASSIGNMENT ─────────────
//
// The producer fills in the partner's own form, picks an active partner and
// confirms. What the server does with that is one transaction: create the
// lead for that partner and accept it into the first configured stage, with
// the producer recorded as the actor. New work only -- nothing here moves an
// existing lead to somebody else.
//
// The screen is advisory about every one of those facts: the partner list,
// the catalogue and the first stage are re-read and re-checked server-side
// before anything is written, so a stale dropdown cannot assign to a partner
// who was deactivated while the form sat open.

const assignState = {
  loading: false, loaded: false, failed: false, busy: false, open: false,
  // `uncertain` is the one that matters: a save whose outcome we do not know
  // (5xx, timeout) must not be replayed on a hunch. It blocks the next write
  // until the producer explicitly refetches -- and a refetch is a GET, so
  // clicking "try again" can never turn one lead into two.
  uncertain: false,
  partners: [], products: [], fields: [], schemaRevision: null,
  operation: null,
};

const ASSIGN_REQUIRED_FIELDS = [
  'assign-partner', 'assign-client-name', 'assign-company', 'assign-email',
  'assign-nip', 'assign-website', 'assign-product', 'assign-quantity',
  'assign-value',
];

function isProducerSession() {
  try {
    return JSON.parse(sessionStorage.getItem('ph_session') || '{}').role === 'admin';
  } catch (_) {
    return false;
  }
}

/** Where an accepted deal starts -- the first ACTIVE nonterminal stage, the
 *  same one the server reads inside the write. */
function assignFirstStage() {
  return (stagePipeline?.nonTerminal || []).find(
    (stage) => stage.active === true || stage.active === 1,
  ) || null;
}

function assignNode(id) { return document.getElementById(id); }

function showAssignState(state, message) {
  const nodes = {
    loading: assignNode('assign-loading-state'),
    error: assignNode('assign-error-state'),
    empty: assignNode('assign-empty-state'),
  };
  Object.entries(nodes).forEach(([name, node]) => {
    if (node) node.hidden = name !== state;
  });
  if (state === 'error') {
    const target = assignNode('assign-error-message');
    if (target) target.textContent = message;
  }
  if (state === 'empty') {
    const target = assignNode('assign-empty-message');
    if (target) target.textContent = message;
  }
}

/** Why saving is impossible right now, or null when it is possible. Missing
 *  data disables the save and says which piece is missing; it never hides the
 *  form the producer has already typed into. */
/** A schema with no revision cannot be answered: the server has nothing to
 *  compare the submission against, so a required question added a second ago
 *  would be skipped in silence. Editing the answers is blocked, the rest of
 *  the form stays exactly as typed. */
function assignSchemaMissing() {
  const revision = assignState.schemaRevision;
  return assignState.loaded
    && (revision === null || revision === undefined || revision === '');
}

function assignBlockingMessage() {
  if (assignState.loading) {
    return App.t('al_loading');
  }
  if (assignState.failed) {
    return App.t('al_form_load_failed');
  }
  if (assignState.uncertain) {
    return App.t('al_creation_unknown');
  }
  if (assignSchemaMissing()) {
    return App.t('al_no_schema_version');
  }
  if (!assignState.partners.length) {
    return App.t('al_no_active_partner');
  }
  if (!assignState.products.length) {
    return App.t('al_no_offered_product');
  }
  if (!assignFirstStage()) {
    return App.t('al_no_active_stage');
  }
  return null;
}

/** Turn the producer's own questions on or off without touching the standard
 *  fields -- "missing schema blocks editing" must not mean "hides base data". */
function setAssignAnswersEnabled(enabled) {
  const container = assignNode('assign-custom-fields');
  if (!container) return;
  container.querySelectorAll('[data-field-id]').forEach((input) => {
    input.disabled = !enabled;
  });
}

function updateAssignAvailability() {
  const blocking = assignBlockingMessage();
  const submit = assignNode('assign-submit-btn');
  if (submit) {
    submit.disabled = !!blocking || assignState.busy;
    submit.setAttribute('aria-busy', assignState.busy ? 'true' : 'false');
  }
  assignNode('assign-lead-modal')
    ?.setAttribute('aria-busy', assignState.busy ? 'true' : 'false');
  setAssignAnswersEnabled(!assignSchemaMissing());
  if (assignState.loading) showAssignState('loading');
  // An uncertain or failed load is the state that owns the retry button; an
  // ordinary "nothing configured yet" is an empty state with no retry, because
  // clicking again would change nothing.
  else if (assignState.failed || assignState.uncertain) showAssignState('error', blocking);
  else if (blocking) showAssignState('empty', blocking);
  else showAssignState('none');
  return blocking;
}

function renderAssignPreview() {
  const dash = '—';
  const company = assignNode('assign-company')?.value.trim();
  const partnerSelect = assignNode('assign-partner');
  const partner = assignState.partners.find(
    (item) => String(item.id) === String(partnerSelect?.value || ''),
  );
  const stage = assignFirstStage();
  const companyEl = assignNode('assign-preview-company');
  const partnerEl = assignNode('assign-preview-partner');
  const stageEl = assignNode('assign-preview-stage');
  if (companyEl) companyEl.textContent = company || dash;
  if (partnerEl) partnerEl.textContent = partner ? (partner.name || dash) : dash;
  if (stageEl) stageEl.textContent = stage ? Ui.stageLabel(stage) : dash;

  // Money is where "missing" and "zero" are easiest to confuse, and confusing
  // them here would show a producer a 0 PLN deal they never entered. An empty
  // field says so in words; a typed 0 is formatted like any other amount.
  const valueEl = assignNode('assign-preview-value');
  if (valueEl) {
    const raw = String(assignNode('assign-value')?.value ?? '').trim();
    const parsed = raw === '' ? null : Number(raw);
    if (parsed === null || !Number.isFinite(parsed)) {
      // Keep the key so switching language re-translates "not provided"...
      valueEl.setAttribute('data-i18n', 'assign_value_missing');
      valueEl.textContent = App.t('al_not_provided');
    } else {
      // ...and drop it once there is a real amount, or App.applyTranslations()
      // would replace the producer's own figure with the placeholder wording.
      valueEl.removeAttribute('data-i18n');
      valueEl.textContent = formatCurrency(parsed);
    }
  }
}

function renderAssignOptions() {
  const partnerSelect = assignNode('assign-partner');
  if (partnerSelect) {
    const chosen = partnerSelect.value;
    partnerSelect.replaceChildren();
    // Never preselected: assigning somebody else's lead to whoever happens to
    // sort first is exactly the mistake this empty first option prevents.
    const placeholder = document.createElement('option');
    placeholder.value = '';
    placeholder.textContent = App.t('al_choose_partner');
    partnerSelect.appendChild(placeholder);
    assignState.partners.forEach((partner) => {
      const option = document.createElement('option');
      option.value = String(partner.id);
      option.textContent = partner.company
        ? `${partner.name} · ${partner.company}`
        : partner.name;
      partnerSelect.appendChild(option);
    });
    if (assignState.partners.some((partner) => String(partner.id) === chosen)) {
      partnerSelect.value = chosen;
    }
  }

  const productSelect = assignNode('assign-product');
  if (productSelect) {
    const chosen = productSelect.value;
    productSelect.replaceChildren();
    const placeholder = document.createElement('option');
    placeholder.value = '';
    placeholder.textContent = App.t('al_choose_product');
    productSelect.appendChild(placeholder);
    assignState.products.forEach((product) => {
      const option = document.createElement('option');
      // The value stays the id, so the same product is submitted whichever
      // language the producer is reading, and a language switch
      // with the window open relabels it through App.onRerender above.
      option.value = String(product.id);
      option.textContent = Ui.producerLabel(product, 'name');
      productSelect.appendChild(option);
    });
    if (assignState.products.some((product) => String(product.id) === chosen)) {
      productSelect.value = chosen;
    }
  }

  const container = assignNode('assign-custom-fields');
  if (container && global.LeadFields) {
    // Re-rendered from the current schema, keeping what is already typed:
    // a producer editing their own questions in another tab must not silently
    // lose the answers on this one.
    const typed = global.LeadFields.serialize(container).values;
    const existing = {};
    Object.entries(typed).forEach(([id, value]) => { existing[Number(id)] = value; });
    global.LeadFields.render(container, assignState.fields, existing);
  }
  renderAssignPreview();
}

async function loadAssignData() {
  assignState.loading = true;
  assignState.failed = false;
  updateAssignAvailability();
  try {
    const [partners, products, fields] = await Promise.all([
      ApiClient.get('/api/partners'),
      ApiClient.get('/api/products'),
      ApiClient.get('/api/lead-fields'),
    ]);
    assignState.partners = partners.partners || [];
    assignState.products = (products.products || []).filter(
      (product) => product.active !== 0 && product.offered !== 0,
    );
    assignState.fields = fields.fields || [];
    assignState.schemaRevision = fields.version ?? null;
    assignState.loaded = true;
    // A successful read is the only thing that clears an unknown outcome: we
    // have now seen the real state of the world again.
    assignState.uncertain = false;
  } catch (err) {
    assignState.failed = true;
    console.error('[API Error]', 'loadAssignData', err);
  } finally {
    assignState.loading = false;
    renderAssignOptions();
    updateAssignAvailability();
  }
}

async function openAssignModal() {
  if (!isProducerSession()) return;
  assignState.open = true;
  openModal('assign-lead-modal');
  updateAssignAvailability();
  // Re-read every time it opens: partners deactivate, products stop being
  // offered and questions change while a list screen sits open.
  await loadAssignData();
}

function assignOperationId(payload) {
  const signature = JSON.stringify(payload);
  if (!assignState.operation || assignState.operation.signature !== signature) {
    assignState.operation = { signature, id: global.crypto.randomUUID() };
  }
  return assignState.operation.id;
}

function collectAssignPayload() {
  const missing = [];
  let firstInvalid = null;
  ASSIGN_REQUIRED_FIELDS.forEach((id) => {
    const el = assignNode(id);
    if (!el) return;
    // Emptiness is tested as emptiness, never as falsiness: "0" is a perfectly
    // good quantity and a perfectly good contract value.
    if (String(el.value ?? '').trim() === '') {
      el.classList.add('error');
      el.setAttribute('aria-invalid', 'true');
      if (!firstInvalid) firstInvalid = el;
      missing.push(id);
    } else {
      el.classList.remove('error');
      el.removeAttribute('aria-invalid');
    }
  });
  if (missing.length) return { missing, firstInvalid };

  const payload = {
    partner_id: Number(assignNode('assign-partner').value),
    client_name: assignNode('assign-client-name').value.trim(),
    client_email: assignNode('assign-email').value.trim(),
    client_phone: assignNode('assign-phone')?.value.trim() || '',
    company: assignNode('assign-company').value.trim(),
    value: parseFloat(assignNode('assign-value').value) || 0,
    notes: assignNode('assign-notes')?.value.trim() || '',
    sector: assignNode('assign-sector')?.value.trim() || '',
    nip: assignNode('assign-nip').value.trim(),
    website: assignNode('assign-website').value.trim(),
    product_id: Number(assignNode('assign-product').value),
    quantity: parseInt(assignNode('assign-quantity').value, 10) || 0,
  };

  const container = assignNode('assign-custom-fields');
  if (container && global.LeadFields) {
    const custom = global.LeadFields.serialize(container);
    if (custom.missing.length) {
      return { missing: custom.missing, customContainer: container, custom };
    }
    payload.lead_field_values = custom.values;
  }
  payload.schema_revision = assignState.schemaRevision;
  return { payload };
}

function resetAssignForm() {
  assignNode('assign-lead-form')?.reset();
  assignState.operation = null;
  const container = assignNode('assign-custom-fields');
  if (container && global.LeadFields) {
    global.LeadFields.render(container, assignState.fields, {});
  }
  renderAssignPreview();
}

async function submitAssignment() {
  if (assignState.busy || !isProducerSession()) return;
  if (updateAssignAvailability()) return;

  const collected = collectAssignPayload();
  if (collected.missing) {
    // Focus goes to the first thing that actually has to change, so a keyboard
    // user is not left hunting for a red border somewhere below the fold.
    let focusTarget = collected.firstInvalid || null;
    if (collected.customContainer) {
      const first = global.LeadFields.highlightMissing(
        collected.customContainer, collected.custom.missing,
      );
      if (!focusTarget) focusTarget = first;
    }
    focusTarget?.focus();
    showToast('error',
      App.t('al_fill_required'),
      App.t('al_fill_starred'));
    return;
  }

  const body = {
    ...collected.payload,
    confirm_accept: true,
    operation_id: assignOperationId(collected.payload),
  };

  assignState.busy = true;
  updateAssignAvailability();
  try {
    const data = await ApiClient.post('/api/leads/assign', body);
    assignState.operation = null;
    showToast('success',
      App.t('al_lead_assigned'),
      `${data.lead.id} — ${data.lead.partner_name || ''}`.trim());
    assignState.open = false;
    closeModal('assign-lead-modal');
    resetAssignForm();
    await loadLeads();
  } catch (err) {
    await handleAssignFailure(err);
  } finally {
    assignState.busy = false;
    updateAssignAvailability();
  }
}

/**
 * Say what went wrong in the producer's own language and leave the screen in a
 * state that cannot double-charge anyone.
 *
 * The rules, in order of how much damage getting them wrong does:
 *
 *  - The draft is never cleared. Not on a 409, not on a 403, not on a dropped
 *    connection. Retyping a whole lead because the server hiccuped is how a
 *    producer stops using the feature.
 *  - Nothing here re-sends the mutation. Where a refetch happens it is a GET;
 *    the second POST only ever comes from the producer clicking again.
 *  - When the outcome is genuinely unknown, the save is blocked rather than
 *    offered, because "try again" on a request that already committed is a
 *    duplicate lead with a real commission attached to it.
 */
async function handleAssignFailure(err) {
  const status = err && err.status;
  const code = err && err.code;

  // The producer's questions moved under the form. Same payload, same
  // operation id: this retry is the one the id exists for.
  if (status === 409 && (code === 'stale_lead_fields' || code === 'stale_revision')) {
    showToast('error',
      App.t('al_form_changed'),
      App.t('al_questions_changed'));
    await loadAssignData();
    return;
  }

  // The server has already bound this operation id to a different payload.
  // Reusing it would 409 forever, so the next attempt gets a fresh one --
  // which is safe precisely because the server told us this one is not ours.
  if (status === 409) {
    assignState.operation = null;
    showToast('error',
      App.t('al_operation_conflict'),
      App.t('al_write_mismatch'));
    await loadAssignData();
    return;
  }

  // Something in the draft points at a row this producer may not use, or that
  // is simply gone. Re-read the lists: renderAssignOptions only restores a
  // selection that still exists, so the inaccessible one disappears by itself
  // instead of sitting there looking valid.
  if (status === 403 || status === 404) {
    assignState.operation = null;
    showToast('error',
      status === 403
        ? App.t('al_forbidden')
        : App.t('al_not_found'),
      status === 403
        ? App.t('al_lost_access')
        : App.t('al_gone'));
    await loadAssignData();
    return;
  }

  // No status at all is js/api.js's own "the fetch never happened": the
  // request did not reach the server, so the draft and the operation id both
  // stay, and the same click can simply be repeated.
  if (status === undefined || status === null || status === 0) {
    console.error('[API Error]', 'submitAssignment', err);
    showToast('error',
      App.t('al_no_connection'),
      App.t('al_not_sent'));
    return;
  }

  // 5xx and anything else unexpected: the request left, the answer did not.
  // We cannot tell a rejected write from a committed one whose response was
  // lost, so the save stays shut until a refetch says what really happened.
  if (status >= 500) {
    assignState.uncertain = true;
    console.error('[API Error]', 'submitAssignment', err);
    showToast('error',
      App.t('al_unknown_save_result'),
      App.t('al_check_list_first'));
    return;
  }

  console.error('[API Error]', 'submitAssignment', err);
  showToast('error',
    (err && err.message)
      || App.t('al_save_failed'),
    App.t('al_data_kept'));
}

function setupAssignControls() {
  const button = assignNode('assign-lead-btn');
  if (!button) return;
  // The button is hidden markup by default; only a producer session unhides
  // it, and the server refuses the call regardless of what is on screen.
  if (!isProducerSession()) return;
  button.hidden = false;
  button.addEventListener('click', openAssignModal);
  assignNode('assign-retry-btn')?.addEventListener('click', loadAssignData);
  assignNode('assign-submit-btn')?.addEventListener('click', submitAssignment);
  assignNode('assign-partner')?.addEventListener('change', renderAssignPreview);
  assignNode('assign-company')?.addEventListener('input', renderAssignPreview);
  assignNode('assign-value')?.addEventListener('input', renderAssignPreview);
  assignNode('assign-lead-form')?.addEventListener('submit', (event) => {
    // The confirm button is a type=button, but a stray Enter in a text field
    // would otherwise navigate away and take the draft with it.
    event.preventDefault();
    submitAssignment();
  });
  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape' || !assignState.open) return;
    // Escape while a save is in flight would leave the producer with no view
    // of an outcome they still need; everything typed survives either way,
    // because closing the modal never touches the form.
    if (assignState.busy) return;
    assignState.open = false;
    closeModal('assign-lead-modal');
  });
  ['assign-cancel-btn', 'assign-close-btn'].forEach((id) => {
    assignNode(id)?.addEventListener('click', () => { assignState.open = false; });
  });
}

function syncLead(lead) {
  // Refresh the row in place if it is on the page the producer is looking at.
  // A lead that an action moved out of the current filter or order stays where
  // it is until the next load: redrawing the row is what the producer just
  // asked for, and pulling it out from under them mid-action is not.
  const index = pageLeads.findIndex(l => l.id === lead.id);
  if (index >= 0) pageLeads[index] = lead;

  if (document.body.dataset.page === 'admin-leads') renderLeadsTable();
}

  /**
   * Initialize the admin leads controller once for the current page.
   * @returns {Promise<void>} shared initialization promise
   */
  function init() {
    return initLeadsPage();
  }

  /** Reload the lead collection without rebinding controller listeners. */
  function reload() {
    setupQueueStates();
    return loadLeads();
  }

  global.AdminLeads = Object.freeze({ init, reload });
})(window);
