/* ============================================================
   PARTNERTYPE — SETTINGS PAGE
   ============================================================ */


let state = { tiers: [], bonusDefs: [], partners: [], stages: [] };
const settingsActionRoots = new WeakSet();
document.addEventListener('DOMContentLoaded', () => {
  if (document.body.dataset.page === 'admin-settings') {
    // Navigation must be usable while session and API requests are still in
    // flight. It is entirely local UI and has no reason to await the shell.
    renderTabIcons();
    setupTabs();
    initSettingsPage();
  }
});

async function initSettingsPage() {
  // Settings self-initializes on its own DOMContentLoaded outside
  // the shell's per-page CONTROLLERS dispatch, so the shell's gate alone
  // cannot cover it; this page must ask the shell for the authoritative,
  // server-confirmed identity itself before doing anything else.
  const user = await AdminShell.init();
  // The moderator owns the Settings surface too — the two things withheld
  // from the role are the audit log and user management, neither of which
  // lives on this page. An observer has no business here.
  if (!user || (user.role !== 'admin' && user.role !== 'manager')) {
    Ui.safeNavigate('dashboard.html');
    return;
  }
  await loadPartners();
  loadTiers();
  loadBonusDefs();
  setupQueueControls();
  setupStageControls();
  loadStages();
  setupRegistrationFields();
  setupLeadFields();
  loadRegistrationFields();
  loadLeadFields();
  setupAddButtons();
  setupPartnerLinks();
  setupLogo();
  // The Products tab's catalogue controller (js/admin-products.js) is not in
  // AdminShell's per-page CONTROLLERS map -- this page self-initializes
  // outside that dispatch -- so it must be invoked explicitly, once, with the
  // identity this page already awaited above. Guarded so a missing script tag
  // cannot throw and take the rest of Settings down with it.
  try {
    if (typeof AdminProducts !== 'undefined' && AdminProducts && typeof AdminProducts.init === 'function') {
      AdminProducts.init(user);
    }
  } catch (err) {
    console.error('AdminProducts.init', err);
  }

  try {
    const data = await ApiClient.get('/api/settings');
    populateSettings(data.settings);
    populateQueueSettings(data.settings);
    document.getElementById('save-all-btn')?.addEventListener('click', saveSettings);
  } catch (err) { handleApiError(err, 'initSettingsPage'); }
}

// The three "dodaj" buttons on this page. Bound here, not as onclick
// attributes, so `script-src` never has to allow inline scripts.
//
// Bound here rather than beside save-all-btn on purpose: that binding sits
// inside the /api/settings try block, so a failed settings fetch would leave
// these three buttons dead. Adding a tier does not depend on that request.
function setupAddButtons() {
  document.getElementById('add-tier-btn')?.addEventListener('click', () => createTier());
  document.getElementById('add-percent-btn')?.addEventListener('click', () => createBonus('percent'));
  document.getElementById('add-cash-btn')?.addEventListener('click', () => createBonus('cash'));
}

function populateSettings(s) {
  const sv = (id, v) => { const el = document.getElementById(id); if (el) el.value = v || ''; };
  sv('s-company-name',  s.company_name);
  sv('s-company-email', s.company_email);
  sv('s-company-url',   s.company_url);
  sv('s-partner-terms-url', s.partner_terms_url);
  const currEl = document.getElementById('s-currency');
  if (currEl) currEl.value = s.currency || 'PLN';
  const autoEl = document.getElementById('s-auto-commission');
  if (autoEl) autoEl.checked = s.lead_auto_commission === '1';
  sv('s-action-days', s.lead_action_days || '2');
  // Off unless the stored value says otherwise: a switch that decides whether
  // somebody can sign in must never default to "on" because a value was missing.
  const mfaEl = document.getElementById('s-mfa-required');
  if (mfaEl) mfaEl.checked = s.mfa_required_for_staff === '1';
}

// ─── COMPANY LOGO ──────────────────────────────────────────────
// The producer's own logo on the partners' sign-in page,
// in the menu and in the mails (api/branding.py). Saved as soon as a file is
// chosen; the server checks it is really a PNG or JPEG, the size check here
// only spares an upload that would be refused anyway.
const LOGO_MAX_BYTES = 200 * 1024;

function showLogo(info) {
  const img = document.getElementById('s-logo-img');
  const empty = document.getElementById('s-logo-empty');
  const remove = document.getElementById('s-logo-remove');
  if (!img || !empty || !remove) return;
  const has = Boolean(info && info.logo_version);
  img.hidden = !has;
  empty.hidden = has;
  remove.hidden = !has;
  if (has) img.src = `/api/branding/logo?v=${encodeURIComponent(info.logo_version)}`;
  else img.removeAttribute('src');
}

// The name and logo on this very page (sidebar) follow a change at once.
async function refreshBranding() {
  try {
    const info = await ApiClient.get('/api/branding');
    showLogo(info);
    if (window.Branding) { window.Branding.remember(info); window.Branding.apply(info); }
    return info;
  } catch (err) {
    return null;
  }
}

function readAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.addEventListener('load', () => resolve(reader.result));
    reader.addEventListener('error', () => reject(reader.error));
    reader.readAsDataURL(file);
  });
}

function setupLogo() {
  const input = document.getElementById('s-logo-file');
  const remove = document.getElementById('s-logo-remove');
  if (!input || !remove) return;
  refreshBranding();
  input.addEventListener('change', async () => {
    const file = input.files && input.files[0];
    input.value = '';
    if (!file) return;
    if (!['image/png', 'image/jpeg'].includes(file.type)) {
      showToast('error', App.t('st_error'), App.t('st_logo_wrong_type'));
      return;
    }
    if (file.size > LOGO_MAX_BYTES) {
      showToast('error', App.t('st_error'), App.t('st_logo_too_big'));
      return;
    }
    try {
      await ApiClient.put('/api/branding/logo', { data: await readAsDataUrl(file) });
      await refreshBranding();
      showToast('success', App.t('st_saved'), App.t('st_logo_saved'));
    } catch (err) {
      showToast('error', App.t('st_error'), err.message);
    }
  });
  remove.addEventListener('click', async () => {
    try {
      await ApiClient.delete('/api/branding/logo');
      await refreshBranding();
      // Back to PartnerType's mark in the sidebar: the images still hold the old logo.
      document.querySelectorAll('img.brand-logo-light').forEach(img => { img.src = '../../img/logo-light-128.png'; });
      document.querySelectorAll('img.brand-logo-dark').forEach(img => { img.src = '../../img/logo-dark-128.png'; });
      showToast('success', App.t('st_saved'), App.t('st_logo_removed'));
    } catch (err) {
      showToast('error', App.t('st_error'), err.message);
    }
  });
}

// One place to pick up the address the producer's partners register at. Each
// instance lives at its own address, so this page's origin already is that
// producer's: whoever registers there lands in their partner base and nobody
// else's.
function setupPartnerLinks() {
  const links = { 's-register-link': '/register.html', 's-login-link': '/' };
  Object.entries(links).forEach(([id, path]) => {
    const input = document.getElementById(id);
    if (input) input.value = window.location.origin + path;
  });
  document.querySelectorAll('[data-copy-link]').forEach(btn => {
    btn.addEventListener('click', () => copyPartnerLink(document.getElementById(btn.dataset.copyLink)));
  });
}

async function copyPartnerLink(input) {
  if (!input?.value) return;
  if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
    try {
      await navigator.clipboard.writeText(input.value);
      showToast('success', App.t('toast_copied'), App.t('st_link_copied'));
      return;
    } catch {
      // Falls through to selecting the text, as on the invitation screens.
    }
  }
  // No Clipboard API on a plain-http origin: select it for a manual copy.
  input.focus();
  input.select();
}


async function saveSettings() {
  // Mirrors api/settings_api.py::_parse_action_days. The router turns a
  // backend ValueError into a generic 500, so catching it here is what gives
  // the admin an explanation instead of "unexpected server error".
  const actionDays = Number.parseInt(document.getElementById('s-action-days')?.value ?? '', 10);
  if (!Number.isFinite(actionDays) || actionDays < 1 || actionDays > 90) {
    showToast('error', App.t('st_error'), App.t('st_deadline_range'));
    return;
  }

  const btn = document.getElementById('save-all-btn');
  btn.disabled = true;
  btn.innerHTML = `<span class="spinner" style="width:16px;height:16px;border-width:2px"></span> ${App.t('st_saving')}`;
  const gv = id => document.getElementById(id)?.value?.trim() || '';
  const body = {
    company_name:        gv('s-company-name'),
    company_email:       gv('s-company-email'),
    company_url:         gv('s-company-url'),
    partner_terms_url:   gv('s-partner-terms-url'),
    currency:            document.getElementById('s-currency')?.value || 'PLN',
    lead_auto_commission: document.getElementById('s-auto-commission')?.checked ? '1' : '0',
    lead_action_days:     gv('s-action-days') || '2',
    mfa_required_for_staff: document.getElementById('s-mfa-required')?.checked ? '1' : '0',
  };
  try {
    const data = await ApiClient.patch('/api/settings', body);
    populateSettings(data.settings);
    refreshBranding();
    showToast('success', App.t('st_saved'), App.t('st_settings_saved'));
  } catch (err) {
    showToast('error', App.t('st_error'), err.message);
  } finally {
    btn.disabled = false;
    Icons.label(btn, 'check', App.t('st_save_settings'), { size: 15 });
  }
}


// ─── QUEUE CONFIGURATION (duplicate policy, depth, position modifiers) ──────
// Each value is a modifier on the partner's OWN tier rate, never a flat
// percentage of the contract value. Client-side validation mirrors
// api/settings_api.py::_parse_queue_shares / _parse_queue_max exactly, so a
// value this form accepts is never rejected on save.

const QUEUE_POLICIES = ['queue', 'reject', 'allow'];
const queueErrorCopy = () => App.t('st_save_failed');

function populateQueueSettings(s) {
  const policyEl = document.getElementById('s-queue-policy');
  if (policyEl && s.lead_duplicate_policy) policyEl.value = s.lead_duplicate_policy;
  const maxEl = document.getElementById('s-queue-max');
  if (maxEl && s.lead_queue_max != null) maxEl.value = s.lead_queue_max;

  const shares = String(s.lead_queue_shares ?? '').split(',').map(v => v.trim()).filter(v => v !== '');
  renderQueueShareInputs(shares.length || Number(s.lead_queue_max) || 5, shares);
  updateQueueSummary();
}

function renderQueueShareInputs(count, values) {
  const list = document.getElementById('queue-shares-list');
  if (!list) return;
  const safeCount = Math.max(1, Math.min(5, Math.trunc(Number(count)) || 1));

  list.textContent = '';
  for (let i = 1; i <= safeCount; i++) {
    const hasValue = values && values[i - 1] != null && String(values[i - 1]).trim() !== '';
    const raw = hasValue ? values[i - 1] : (i === safeCount ? '0' : '');

    const wrap = document.createElement('div');
    wrap.className = 'form-group';
    wrap.style.minWidth = '110px';
    wrap.dataset.queuePosition = String(i);

    const label = document.createElement('label');
    label.className = 'form-label';
    label.setAttribute('for', `queue-share-${i}`);
    label.textContent = App.t('st_nth_submission', { n: i });

    const inputWrap = document.createElement('div');
    inputWrap.style.position = 'relative';

    const input = document.createElement('input');
    input.type = 'number';
    input.className = 'form-input';
    input.id = `queue-share-${i}`;
    input.min = '0';
    input.max = '100';
    input.step = '0.1';
    input.value = String(raw);
    input.addEventListener('input', updateQueueSummary);

    const suffix = document.createElement('span');
    suffix.style.cssText = 'position:absolute;right:12px;top:50%;transform:translateY(-50%);color:var(--text-tertiary)';
    suffix.textContent = '%';

    inputWrap.appendChild(input);
    inputWrap.appendChild(suffix);
    wrap.appendChild(label);
    wrap.appendChild(inputWrap);
    list.appendChild(wrap);
  }
}

function getQueueShareValues() {
  return Array.from(document.querySelectorAll('#queue-shares-list input')).map(input => input.value);
}

function setupQueueControls() {
  document.getElementById('s-queue-max')?.addEventListener('input', handleQueueDepthChange);
  document.getElementById('s-queue-policy')?.addEventListener('change', updateQueueSummary);
  document.getElementById('save-queue-btn')?.addEventListener('click', saveQueueSettings);
}

function handleQueueDepthChange() {
  const raw = document.getElementById('s-queue-max')?.value ?? '';
  const n = Number(raw);
  if (!Number.isFinite(n)) return;
  const rounded = Math.trunc(n);
  if (rounded < 1 || rounded > 5) return; // out-of-range values are caught by validation on save, not by resizing
  renderQueueShareInputs(rounded, getQueueShareValues());
}

function formatQueuePercent(n) {
  if (!Number.isFinite(n)) return '0';
  return Number.isInteger(n) ? String(n) : String(Math.round(n * 10) / 10);
}

function updateQueueSummary() {
  const el = document.getElementById('queue-summary');
  if (!el) return;
  el.textContent = '';
  getQueueShareValues().forEach((raw, idx) => {
    const pct = formatQueuePercent(Number(raw));
    const line = document.createElement('div');
    line.textContent = App.t('st_nth_submission_share', { n: idx + 1, pct });
    el.appendChild(line);
  });
}

function showQueueError(message) {
  const el = document.getElementById('queue-save-error');
  if (!el) return;
  el.textContent = message;
  el.hidden = false;
}

function hideQueueError() {
  const el = document.getElementById('queue-save-error');
  if (!el) return;
  el.hidden = true;
  el.textContent = '';
}

// Mirrors api/settings_api.py::_parse_queue_shares / _parse_queue_max /
// _validate_queue_updates exactly. Returns an error message string, or null
// when the configuration is valid.
function validateQueueConfig(policy, shareRawList, maxRaw) {
  if (!QUEUE_POLICIES.includes(policy)) {
    return App.t('st_duplicate_policy_values');
  }

  if (!Array.isArray(shareRawList) || shareRawList.length < 1 || shareRawList.length > 5
      || shareRawList.some(v => String(v).trim() === '')) {
    return App.t('st_queue_shares_count');
  }
  const shares = shareRawList.map(v => Number(v));
  if (shares.some(v => !Number.isFinite(v) || v < 0 || v > 100)) {
    return App.t('st_queue_share_range');
  }
  for (let i = 1; i < shares.length; i++) {
    if (shares[i] > shares[i - 1]) {
      return App.t('st_queue_shares_monotonic');
    }
  }
  if (shares[shares.length - 1] !== 0) {
    return App.t('st_queue_last_zero');
  }

  const maxTrim = String(maxRaw ?? '').trim();
  if (!/^\d+$/.test(maxTrim)) {
    return App.t('st_queue_max_integer');
  }
  const maxNum = parseInt(maxTrim, 10);
  if (maxNum < 1 || maxNum > 5) {
    return App.t('st_queue_max_range');
  }
  if (maxNum !== shares.length) {
    return App.t('st_queue_max_matches');
  }
  return null;
}

async function saveQueueSettings() {
  const policy = document.getElementById('s-queue-policy')?.value || '';
  const shareRaw = getQueueShareValues();
  const maxRaw = document.getElementById('s-queue-max')?.value ?? '';

  const error = validateQueueConfig(policy, shareRaw, maxRaw);
  if (error) {
    showQueueError(error);
    return;
  }
  hideQueueError();

  const btn = document.getElementById('save-queue-btn');
  if (btn) btn.disabled = true;
  const body = {
    lead_duplicate_policy: policy,
    lead_queue_shares: shareRaw.map(v => String(Number(v))).join(','),
    lead_queue_max: String(parseInt(maxRaw, 10)),
  };
  try {
    const data = await ApiClient.patch('/api/settings', body);
    if (data && data.settings) populateQueueSettings(data.settings);
    else updateQueueSummary();
    showToast('success', App.t('st_saved'), App.t('st_queue_saved'));
  } catch (err) {
    showQueueError(queueErrorCopy());
  } finally {
    if (btn) btn.disabled = false;
  }
}

// ─── SALES PIPELINE (Sales stages) ───────────────────────────────────────────
// Mirrors api/lead_stages.py's server-enforced rules in the browser so the
// screen never accepts what the server would reject: at most six
// producer-defined stages. The two terminals (Won / Lost) are immutable.
//
// The producer does not pick which stage pays: commission is projected at
// every stage and recorded when the deal reaches the win terminal. "Which
// stage means accepted?" is the fixed registration axis on the lead itself,
// not a stage at all.

const MAX_PRODUCER_STAGES = 6;
const stageErrorCopy = () => App.t('st_save_failed');
const stageListenerRoots = new WeakSet();

// Stages save on change with no save button, so the saved marker is
// the only signal a producer gets that an edit was kept. Keyed by stage id,
// valued by the timestamp of its last successful save; renderStages() reads
// this map on every row it rebuilds, so the marker survives the full-list
// re-render patchStage() already performs and disappears on its own once its
// lifetime elapses -- there is no other place that clears it.
const STAGE_SAVED_MARKER_MS = 3000;
const stageSavedAt = new Map();
let staleStageMarkerTimer = null;

function scheduleStaleStageMarkerCleanup() {
  if (staleStageMarkerTimer) return;
  const now = Date.now();
  let soonest = Infinity;
  stageSavedAt.forEach(savedAt => {
    const remaining = STAGE_SAVED_MARKER_MS - (now - savedAt);
    if (remaining > 0 && remaining < soonest) soonest = remaining;
  });
  if (!Number.isFinite(soonest)) return;
  staleStageMarkerTimer = setTimeout(() => {
    staleStageMarkerTimer = null;
    renderStages();
  }, soonest);
}

// Fills or hides a row's .stage-saved span based on stageSavedAt, and prunes
// the map entry once its lifetime has elapsed so it never leaks. Only ever
// called from renderStages(), never on a direct DOM write, which is what
// lets the marker survive that function's full row rebuild.
function applyStageSavedMarker(node, stageId) {
  const marker = node.querySelector('.stage-saved');
  if (!marker) return;
  const savedAt = stageSavedAt.get(stageId);
  const remaining = savedAt != null ? STAGE_SAVED_MARKER_MS - (Date.now() - savedAt) : -1;
  if (remaining > 0) {
    Icons.label(marker, 'check', App.t('st_saved'), { size: 14 });
    marker.hidden = false;
  } else {
    stageSavedAt.delete(stageId);
    marker.textContent = '';
    marker.hidden = true;
  }
}

function getProducerStages() {
  return state.stages.filter(s => !s.is_terminal).sort((a, b) => a.sort_order - b.sort_order);
}

function getTerminalStages() {
  return state.stages.filter(s => s.is_terminal).sort((a, b) => a.sort_order - b.sort_order);
}

function mergeStage(updated) {
  if (!updated || updated.id == null) return;
  const idx = state.stages.findIndex(s => s.id === updated.id);
  if (idx === -1) state.stages.push(updated);
  else state.stages[idx] = updated;
}

async function loadStages() {
  try {
    const data = await ApiClient.get('/api/lead-stages');
    state.stages = (data.stages || []).slice();
    renderStages();
    hideStageError();
  } catch (err) {
    showStageError(stageErrorCopy());
  }
}

/*
   The four fixed intake stages, rendered read-only above the funnel.

   They are not rows in `lead_stages` and never will be: they are the values of
   leads.registration_status, each with server behaviour attached (a
   notification, an e-mail, an audit key), which is why they are not
   producer-configurable. But they ARE stages a lead passes through, and leaving
   them off this screen made them look missing.
   Ui.registrationLabel is the same source the leads list renders from, so the
   words here and the badge there cannot drift apart.
*/
function renderRegistrationStages() {
  const host = document.getElementById('stage-registration-list');
  if (!host || !window.Ui) return;
  host.replaceChildren();
  Ui.REGISTRATION_STATUSES.forEach((status) => {
    host.appendChild(Ui.registrationBadge(status));
  });
}

function renderStages() {
  renderRegistrationStages();
  const listEl = document.getElementById('stage-list');
  const terminalEl = document.getElementById('stage-terminal-list');
  const rowTemplate = document.getElementById('stage-row-template');
  if (!listEl || !terminalEl || !rowTemplate) return;

  const producer = getProducerStages();
  const terminal = getTerminalStages();

  listEl.textContent = '';
  producer.forEach((stage, idx) => {
    const node = rowTemplate.content.firstElementChild.cloneNode(true);
    node.dataset.stageId = String(stage.id);
    const nameInput = node.querySelector('[data-field="name"]');
    const colorInput = node.querySelector('[data-field="color"]');
    nameInput.value = stage.name;
    const nameEnInput = node.querySelector('[data-field="name_en"]');
    if (nameEnInput) {
      nameEnInput.value = stage.name_en || '';
      nameEnInput.hidden = !englishNamesVisible;
    }
    colorInput.value = safeHexColor(stage.color);
    const upBtn = node.querySelector('[data-action="move-up"]');
    if (upBtn) upBtn.disabled = idx === 0;
    const downBtn = node.querySelector('[data-action="move-down"]');
    if (downBtn) downBtn.disabled = idx === producer.length - 1;
    applyStageSavedMarker(node, stage.id);
    listEl.appendChild(node);
  });

  // Won and Lost as fixed chips, like the intake stages above them: nothing
  // to type into, and drawn by the same Ui.stageBadge the
  // leads list and the kanban use, so this row shows exactly what the producer
  // sees there. Their colours are fixed too (migration 0011) and chosen apart
  // from the green/red of Zaakceptowany/Odrzucony.
  terminalEl.textContent = '';
  terminal.forEach(stage => {
    const chip = Ui.stageBadge(stage);
    chip.dataset.stageId = String(stage.id);
    terminalEl.appendChild(chip);
  });

  [listEl, terminalEl].forEach(root => {
    root.querySelectorAll('[data-field="name"]').forEach(input => {
      input.title = App.t('st_stage_name');
      input.placeholder = App.t('st_stage_name');
    });
    // Rows are cloned from a <template>, so every re-render brings back the
    // markup's own placeholder -- the English one it happens to ship with.
    // Setting it here is what the Polish name field already does above.
    root.querySelectorAll('[data-field="name_en"]').forEach(input => {
      input.title = App.t('st_stage_name_en');
      input.placeholder = App.t('st_stage_name_en');
    });
    root.querySelectorAll('[data-field="color"]').forEach(input => { input.title = App.t('st_stage_color'); });
    root.querySelectorAll('[data-action]').forEach(button => {
      const key = { 'move-up': 'przesun_etap_w_gore', 'move-down': 'przesun_etap_w_dol', 'remove-stage': 'usun_etap' }[button.dataset.action];
      if (key) { button.title = App.t(key); button.setAttribute('aria-label', App.t(key)); }
    });
  });

  scheduleStaleStageMarkerCleanup();

  const addBtn = document.getElementById('add-stage-btn');
  if (addBtn) addBtn.disabled = producer.length >= MAX_PRODUCER_STAGES;
  const countEl = document.getElementById('stage-count');
  if (countEl) {
    countEl.textContent = producer.length >= MAX_PRODUCER_STAGES
      ? App.t('st_stages_limit_reached', { used: producer.length, max: MAX_PRODUCER_STAGES })
      : App.t('st_stages_used', { used: producer.length, max: MAX_PRODUCER_STAGES });
  }
}

function showStageError(message) {
  const el = document.getElementById('stage-save-error');
  const textEl = document.getElementById('stage-error-text');
  if (!el) return;
  if (textEl) textEl.textContent = message;
  el.hidden = false;
}

function hideStageError() {
  const el = document.getElementById('stage-save-error');
  const textEl = document.getElementById('stage-error-text');
  if (!el) return;
  el.hidden = true;
  if (textEl) textEl.textContent = '';
}

// Shared PATCH path for a field-level mutation (rename/recolour/reorder):
// re-renders from the server response on success, and on failure shows the
// shared error copy without touching the DOM -- the value the user entered
// stays exactly where they left it.
async function patchStage(stageId, body) {
  try {
    const res = await ApiClient.patch(`/api/lead-stages/${stageId}`, body);
    if (res && res.stage) mergeStage(res.stage);
    stageSavedAt.set(stageId, Date.now());
    renderStages();
    hideStageError();
  } catch (err) {
    // Clears any marker left by an earlier successful edit on this row, so a
    // save that just failed can never sit next to "Zapisano" from a previous
    // one.
    stageSavedAt.delete(stageId);
    showStageError(stageErrorCopy());
  }
}

async function renameStage(stageId, name) {
  await patchStage(stageId, { name });
}

async function recolorStage(stageId, color) {
  await patchStage(stageId, { color });
}

// Issues the minimal set of updates needed to keep sort_order contiguous
// (0..n-1) with the swapped pair, and re-renders from the PATCH responses
// rather than optimistic local state -- the server is the order authority.
async function moveStage(stageId, direction) {
  const producer = getProducerStages();
  const idx = producer.findIndex(s => s.id === stageId);
  if (idx === -1) return;
  const swapIdx = direction === 'up' ? idx - 1 : idx + 1;
  if (swapIdx < 0 || swapIdx >= producer.length) return;

  const reordered = producer.slice();
  const tmp = reordered[idx];
  reordered[idx] = reordered[swapIdx];
  reordered[swapIdx] = tmp;

  const changes = [];
  reordered.forEach((stage, i) => {
    if (stage.sort_order !== i) changes.push({ id: stage.id, sort_order: i });
  });

  try {
    const responses = await Promise.all(
      changes.map(change => ApiClient.patch(`/api/lead-stages/${change.id}`, { sort_order: change.sort_order }))
    );
    responses.forEach(res => { if (res && res.stage) mergeStage(res.stage); });
    renderStages();
    hideStageError();
  } catch (err) {
    showStageError(stageErrorCopy());
  }
}

// Removal is guarded behind a destructive confirmation naming the stage and
// stating up front that it will be refused while leads still occupy it; the
// server's own refusal text (with the blocking lead count) is surfaced
// verbatim on failure rather than replaced with a generic message, and the
// row is left in place since loadStages() is only called on success.
async function removeStage(stageId) {
  const stage = state.stages.find(s => s.id === stageId);
  if (!stage) return;
  const ok = confirm(App.t('st_stage_remove_confirm', { name: stage.name }));
  if (!ok) return;
  try {
    await ApiClient.delete(`/api/lead-stages/${stageId}`);
    await loadStages();
    showToast('success', App.t('st_removed'), App.t('st_stage_removed_detail', { name: stage.name }));
  } catch (err) {
    showStageError(err.message || stageErrorCopy());
  }
}

function handleAddStageClick() {
  hideStageError();
  if (getProducerStages().length >= MAX_PRODUCER_STAGES) {
    showStageError(App.t('st_stages_all_used', { max: MAX_PRODUCER_STAGES }));
    return;
  }
  openGenericModal({
    title: App.t('st_new_stage'),
    fields: [
      { id: 'modal-stage-name',  label: App.t('st_stage_name'), type: 'text', placeholder: App.t('st_stage_example'), required: true },
      { id: 'modal-stage-color', label: App.t('st_stage_color'), type: 'color', value: '#6246ea' },
    ],
    submitLabel: App.t('st_add_stage'),
    onSubmit: async (vals) => {
      const name = vals['modal-stage-name'].trim();
      if (!name) { showToast('error', App.t('st_error'), App.t('st_name_required')); return false; }
      if (getProducerStages().length >= MAX_PRODUCER_STAGES) {
        showStageError(App.t('st_stages_all_used', { max: MAX_PRODUCER_STAGES }));
        return false;
      }
      const color = vals['modal-stage-color'];
      await ApiClient.post('/api/lead-stages', { name, color });
      await loadStages();
      showToast('success', App.t('st_added'), App.t('st_stage_added', { name }));
      return true;
    },
  });
}

function bindStageRowEvents(root) {
  if (!root || stageListenerRoots.has(root)) return;
  stageListenerRoots.add(root);

  root.addEventListener('change', event => {
    const row = event.target.closest('[data-stage-id]');
    if (!row) return;
    const stageId = Number(row.dataset.stageId);
    if (event.target.matches('[data-field="name"]')) {
      const name = event.target.value.trim();
      if (name) renameStage(stageId, name);
    } else if (event.target.matches('[data-field="name_en"]')) {
      // Empty is a legitimate value here: it means "not translated", and the
      // reader falls back to the producer's Polish name.
      patchStage(stageId, { name_en: event.target.value.trim() });
    } else if (event.target.matches('[data-field="color"]')) {
      recolorStage(stageId, event.target.value);
    }
  });

  root.addEventListener('click', event => {
    const actionEl = event.target.closest('[data-action]');
    if (!actionEl) return;
    const row = event.target.closest('[data-stage-id]');
    if (!row) return;
    const stageId = Number(row.dataset.stageId);
    if (actionEl.dataset.action === 'move-up') moveStage(stageId, 'up');
    if (actionEl.dataset.action === 'move-down') moveStage(stageId, 'down');
    if (actionEl.dataset.action === 'remove-stage') removeStage(stageId);
  });
}

// The producer names his own stages and his own questions. This switch shows
// the second box where he writes each of those names in English -- the app's
// own copy has a dictionary, his words do not.
let englishNamesVisible = false;

function applyEnglishNameVisibility() {
  document.querySelectorAll('[data-field="name_en"], [data-field="label_en"], [data-field="description_en"]')
    .forEach(input => { input.hidden = !englishNamesVisible; });
}

// Called once per section (stages, lead fields, registration fields), so it
// has to be safe to call again: a second listener on the same box would run
// the same switch twice.
function setupEnglishNameToggles() {
  document.querySelectorAll('[id$="-en-toggle"]').forEach(toggle => {
    toggle.checked = englishNamesVisible;
    if (toggle.dataset.enToggleBound === 'yes') return;
    toggle.dataset.enToggleBound = 'yes';
    toggle.addEventListener('change', () => {
      englishNamesVisible = toggle.checked;
      document.querySelectorAll('[id$="-en-toggle"]').forEach(other => {
        other.checked = englishNamesVisible;
      });
      applyEnglishNameVisibility();
    });
  });
}

function setupStageControls() {
  setupEnglishNameToggles();
  document.getElementById('add-stage-btn')?.addEventListener('click', handleAddStageClick);
  document.getElementById('stage-error-retry')?.addEventListener('click', loadStages);
  bindStageRowEvents(document.getElementById('stage-list'));
}

// ─── TABS & TIERS & BONUSES ─────────────────────────────────

async function loadTiers() {
  try {
    const data = await ApiClient.get('/api/tiers');
    state.tiers = data.tiers || [];
    renderTiers();
  } catch(e) { console.error('loadTiers', e); }
}

async function loadBonusDefs() {
  try {
    const data = await ApiClient.get('/api/bonus-defs');
    // `bonuses`, not `bonus_defs`. api/bonus_defs.py::handle_list returns
    // {"bonuses": [...]} and always has; reading the wrong key meant
    // state.bonusDefs was permanently [] on this screen, so a bonus the
    // producer created was saved and then never appeared. js/commissions.js
    // reads the same endpoint and always had it right, which is why the base
    // looked fine from Commissions.
    state.bonusDefs = data.bonuses || [];
    renderBonuses();
  } catch(e) { console.error('loadBonusDefs', e); }
}

// Puts the generated icon in front of each tile's label.
//
// Deliberately NOT Icons.label here: that helper empties the target and
// rebuilds it as icon + one text node, which would throw away the tile's
// subtitle -- the whole reason these are tiles rather than bare words. The icon
// is inserted before the existing label text instead. A data-icon name
// Icons.has does not recognize is left alone, so the tile degrades to
// text-only rather than losing its label.
function renderTabIcons() {
  document.querySelectorAll('.settings-tile').forEach(btn => {
    const iconName = btn.dataset.icon;
    const labelEl = btn.querySelector('.settings-tile-label');
    if (!iconName || !labelEl || !Icons.has(iconName)) return;
    if (labelEl.querySelector('svg')) return;
    const icon = Icons.el(iconName, { size: 14 });
    if (icon) labelEl.insertBefore(icon, labelEl.firstChild);
  });
}

function setupTabs() {
  const tiles = [...document.querySelectorAll('.settings-tile')];
  tiles.forEach(btn => {
    btn.setAttribute('role', 'tab');
    btn.setAttribute('aria-selected', String(btn.classList.contains('active')));
    btn.addEventListener('click', () => {
      tiles.forEach(b => {
        b.classList.remove('active');
        b.setAttribute('aria-selected', 'false');
      });
      document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
      btn.classList.add('active');
      btn.setAttribute('aria-selected', 'true');
      const pane = document.getElementById(btn.dataset.tab);
      if (pane) pane.classList.add('active');
      syncPluginsNav(btn.dataset.tab === PLUGINS_TAB);
    });
  });

  // The sidebar's Plugins entry is settings.html#plugins: open that tab on
  // arrival, and again when the hash changes while already on this page.
  openPluginsFromHash();
  if (!setupTabs.hashListener) {
    setupTabs.hashListener = true;
    window.addEventListener('hashchange', openPluginsFromHash);
  }
}

const PLUGINS_TAB = 'tab-blocks';
const PLUGINS_HASH = '#plugins';
const SETTINGS_NAV_SELECTOR = '.sidebar-nav .nav-item[href="settings.html"]';
const PLUGINS_NAV_SELECTOR = '.sidebar-nav .nav-item[href="settings.html#plugins"]';

function openPluginsFromHash() {
  if (window.location.hash !== PLUGINS_HASH) return;
  const tile = document.querySelector(`.settings-tile[data-tab="${PLUGINS_TAB}"]`);
  if (tile && !tile.classList.contains('active')) tile.click();
}

// Light Plugins or Settings in the sidebar to match the open tab, and keep
// the address in step so the sidebar link works again after switching away.
function syncPluginsNav(pluginsOpen) {
  const wanted = pluginsOpen ? PLUGINS_HASH : '';
  if ((window.location.hash || '') !== wanted && window.history?.replaceState) {
    window.history.replaceState(null, '', window.location.pathname + window.location.search + wanted);
  }
  const settingsLink = document.querySelector(SETTINGS_NAV_SELECTOR);
  const pluginsLink = document.querySelector(PLUGINS_NAV_SELECTOR);
  if (!settingsLink || !pluginsLink) return;
  settingsLink.classList.toggle('active', !pluginsOpen);
  pluginsLink.classList.toggle('active', pluginsOpen);
}

// A brand-new system has no tiers and no bonuses at all. Rendering nothing
// left the whole tab blank, which reads as a broken screen rather than as an
// empty list waiting for its first entry.
//
// Built with DOM nodes and textContent rather than an innerHTML template
// string -- the text passed in is a fixed constant at both call sites today,
// but this is still a markup sink the security audit watches, and a text
// node cannot become one no matter what string later ends up here.
function emptyStateNode(text) {
  const panel = document.createElement('div');
  panel.className = 'panel';
  // grid-column spans the whole tier/bonus grid: without it the notice sits in
  // the first column and reads as a stray card rather than as the state of the
  // list. Harmless on a non-grid parent.
  panel.style.gridColumn = '1/-1';

  const body = document.createElement('div');
  body.className = 'panel-body';
  body.style.cssText = 'text-align:center;padding:32px 24px;color:var(--text-tertiary);font-size:0.9rem';
  body.textContent = text;

  panel.appendChild(body);
  return panel;
}

function renderTiers() {
  const grid = document.getElementById('tiers-grid');
  if (!grid) return;

  if (state.tiers.length === 0) {
    grid.textContent = '';
    grid.appendChild(emptyStateNode(
      App.t('st_no_tiers')
    ));
    const addBtnEmpty = document.getElementById('add-tier-btn');
    if (addBtnEmpty) { addBtnEmpty.disabled = false; addBtnEmpty.title = App.t('st_add_first_tier'); }
    return;
  }

  grid.innerHTML = '<div style="display:flex;flex-direction:column;gap:16px">' + state.tiers.map(t => {
    const tierId = safePositiveId(t.id);
    return `
    <div class="panel ${t.active ? '' : 'inactive'}" data-tier-id="${tierId ?? ''}">
      <div class="panel-body" style="display:flex;align-items:center;gap:16px;flex-wrap:wrap">
        <div style="display:flex;align-items:center;gap:12px;flex:1;min-width:200px">
          <input type="color" value="${safeHexColor(t.color)}" data-field="color" title="${escHtml(App.t('st_tier_color'))}" style="width:36px;height:36px;padding:0;border:none;border-radius:4px;cursor:pointer;background:transparent;">
          <div style="display:flex;flex-direction:column;gap:6px;flex:1">
            <input class="form-input" value="${escHtml(t.name)}" data-field="name" placeholder="${escHtml(App.t('st_tier_name'))}" title="${escHtml(App.t('st_tier_name'))}">
            <input class="form-input" value="${escHtml(t.name_en || '')}" data-field="name_en"
                   placeholder="${escHtml(App.t('st_tier_name_en'))}" title="${escHtml(App.t('st_tier_name_en'))}"
                   ${englishNamesVisible ? '' : 'hidden'}>
          </div>
        </div>
        <div style="display:flex;align-items:center;gap:12px;flex:1;min-width:150px">
          <span style="font-size:0.85rem;color:var(--text-secondary);white-space:nowrap">${escHtml(App.t('st_base_rate'))}</span>
          <div style="position:relative;flex:1">
            <input type="number" class="form-input" min="0" max="100" step="0.1" value="${finiteValue(t.base_rate)}" data-field="base_rate">
            <span style="position:absolute;right:12px;top:50%;transform:translateY(-50%);color:var(--text-tertiary)">%</span>
          </div>
        </div>
        <div style="min-width:100px;text-align:center;font-size:0.85rem;color:var(--text-tertiary)" data-tier-count="${tierId ?? ''}">
          ${escHtml(App.t('st_loading'))}
        </div>
        <div style="display:flex;gap:8px">
          <button type="button" class="btn btn-secondary btn-sm" data-settings-action="save-tier" data-tier-id="${tierId ?? ''}">${escHtml(App.t('st_save'))}</button>
          <button type="button" class="btn btn-ghost btn-sm" data-settings-action="delete-tier" data-tier-id="${tierId ?? ''}" style="color:var(--danger-500)" title="${escHtml(App.t('st_delete_tier'))}">
            <svg width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" viewBox="0 0 24 24"><path d="M10 11v6"/><path d="M14 11v6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/><path d="M3 6h18"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
          </button>
        </div>
      </div>
    </div>
  `; }).join('') + '</div>';
  bindSettingsActions(grid);

  // The add button stays clickable at the limit. A disabled button gives no
  // reason for itself -- the producer's report of the old behaviour was
  // literally "I click add tier and nothing happens" -- so the limit is
  // explained by createTier()'s toast plus this tooltip instead.
  const addBtn = document.getElementById('add-tier-btn');
  const activeTiers = state.tiers.filter(t => t.active).length;
  if (addBtn) {
    addBtn.disabled = false;
    addBtn.title = activeTiers >= 5
      ? App.t('st_tier_limit')
      : App.t('st_add_new_tier');
  }

  // Populate partner counts
  if (state.partners) {
    state.tiers.forEach(t => {
      const tierId = safePositiveId(t.id);
      const el = grid.querySelector(`[data-tier-count="${tierId ?? ''}"]`);
      if (el) {
        const count = state.partners.filter(x => x.tier_id === t.id).length;
        el.textContent = App.pluralize(count, 'unit_partner');
      }
    });
  }
}

async function saveTier(tierId) {
  const card = document.querySelector(`[data-tier-id="${tierId}"]`);
  if (!card) return;
  const name      = card.querySelector('[data-field="name"]').value.trim();
  const base_rate = parseFloat(card.querySelector('[data-field="base_rate"]').value);
  const color     = card.querySelector('[data-field="color"]').value;
  // His own English wording, sent as its own field: an empty box clears the
  // translation, it never overwrites or copies the Polish original.
  const name_en   = card.querySelector('[data-field="name_en"]')?.value.trim() ?? '';
  if (!name) { showToast('error', App.t('st_error'), App.t('st_tier_name_required')); return; }
  try {
    await ApiClient.patch(`/api/tiers/${tierId}`, { name, name_en, base_rate, color });
    showToast('success', App.t('st_saved'), App.t('st_tier_updated', { name }));
    await loadTiers();
    await loadPartners();
  } catch(e) { showToast('error', App.t('st_error'), e.message); }
}

function createTier() {
  if (state.tiers.filter(t => t.active).length >= 5) {
    showToast('warning', App.t('st_limit'), App.t('st_tier_max')); return;
  }
  openGenericModal({
    title: App.t('st_new_partner_tier'),
    fields: [
      { id: 'modal-tier-name',  label: App.t('st_tier_name'), type: 'text', placeholder: App.t('st_tier_example'), required: true },
      { id: 'modal-tier-name-en', label: App.t('st_tier_name_en'), type: 'text' },
      { id: 'modal-tier-rate',  label: App.t('st_base_rate_percent'), type: 'number', placeholder: '5', step: '0.1', min: '0', max: '100', value: '5' },
      { id: 'modal-tier-color', label: App.t('st_tier_color'), type: 'color', value: '#da9348' },
    ],
    submitLabel: App.t('st_create_tier'),
    onSubmit: async (vals) => {
      const name      = vals['modal-tier-name'].trim();
      const name_en   = vals['modal-tier-name-en']?.trim() || '';
      const base_rate = parseFloat(vals['modal-tier-rate']) || 5;
      const color     = vals['modal-tier-color'];
      if (!name) { showToast('error', App.t('st_error'), App.t('st_name_required')); return false; }
      await ApiClient.post('/api/tiers', { name, name_en, base_rate, color });
      showToast('success', App.t('st_added'), App.t('st_tier_added', { name }));
      await loadTiers();
      return true; // close modal
    },
  });
}

async function deleteTier(tierId) {
  const tier = state.tiers.find(t => t.id === tierId);
  if (!tier) return;
  if (!confirm(App.t('st_tier_delete_confirm', { name: tier.name }))) return;
  try {
    await ApiClient.delete(`/api/tiers/${tierId}`);
    showToast('success', App.t('st_removed'), App.t('st_tier_removed', { name: tier.name }));
    await loadTiers();
  } catch(e) { showToast('error', App.t('st_error'), e.message); }
}



async function saveBonus(bonusId) {
  const card = document.querySelector(`[data-bonus-id="${bonusId}"]`);
  if (!card) return;
  const name          = card.querySelector('[data-field="name"]').value.trim();
  const default_value = parseFloat(card.querySelector('[data-field="default_value"]').value);
  const description   = card.querySelector('[data-field="description"]').value.trim();
  // His English wording, sent as its own field: an empty box clears the
  // translation, it never overwrites or copies the Polish original.
  const name_en        = card.querySelector('[data-field="name_en"]')?.value.trim() ?? '';
  const description_en = card.querySelector('[data-field="description_en"]')?.value.trim() ?? '';
  if (!name) { showToast('error', App.t('st_error'), App.t('st_bonus_name_required')); return; }
  try {
    await ApiClient.patch(`/api/bonus-defs/${bonusId}`, {
      name, name_en, default_value, description, description_en,
    });
    showToast('success', App.t('st_saved'), App.t('st_bonus_updated', { name }));
    await loadBonusDefs();
  } catch(e) { showToast('error', App.t('st_error'), e.message); }
}

function createBonus(type) {
  const limit = type === 'percent' ? 10 : 5;
  const count = state.bonusDefs.filter(b => b.type === type).length;
  if (count >= limit) {
    showToast('warning', App.t('st_limit'), App.t('st_bonus_limit', { limit, kind: App.t(type === 'percent' ? 'st_kind_percent' : 'st_kind_cash') }));
    return;
  }
  const isPercent = type === 'percent';
  openGenericModal(() => ({
    title: isPercent ? App.t('st_new_percent_bonus') : App.t('st_new_cash_bonus'),
    subtitle: isPercent
      ? App.t('st_percent_bonus_hint')
      : App.t('st_cash_bonus_hint'),
    fields: [
      { id: 'modal-bonus-name',  label: App.t('st_bonus_name'), type: 'text', placeholder: App.t(isPercent ? 'st_bonus_webinar_example' : 'st_bonus_quarter_example'), required: true },
      { id: 'modal-bonus-name-en', label: App.t('st_bonus_name_en'), type: 'text' },
      { id: 'modal-bonus-value', label: isPercent ? App.t('st_value_percent') : App.t('st_amount_pln'), type: 'number',
        placeholder: isPercent ? '2.0' : '500', step: isPercent ? '0.1' : '1', min: '0',
        value: isPercent ? '1.0' : '500' },
      { id: 'modal-bonus-desc',  label: App.t('st_description_optional'), type: 'text', placeholder: App.t('st_bonus_conditions') },
      { id: 'modal-bonus-desc-en', label: App.t('st_bonus_description_en'), type: 'text' },
    ],
    submitLabel: App.t(isPercent ? 'st_create_percent_bonus' : 'st_create_cash_bonus'),
    onSubmit: async (vals) => {
      const name          = vals['modal-bonus-name'].trim();
      const default_value = parseFloat(vals['modal-bonus-value']) || 0;
      const description   = vals['modal-bonus-desc']?.trim() || '';
      const name_en        = vals['modal-bonus-name-en']?.trim() || '';
      const description_en = vals['modal-bonus-desc-en']?.trim() || '';
      if (!name) { showToast('error', App.t('st_error'), App.t('st_name_required')); return false; }
      await ApiClient.post('/api/bonus-defs', {
        name, name_en, type, default_value, description, description_en,
      });
      showToast('success', App.t('st_added'), App.t('st_bonus_added', { name }));
      await loadBonusDefs();
      return true;
    },
  }));
}

async function deleteBonus(bonusId) {
  const bonus = state.bonusDefs.find(b => b.id === bonusId);
  if (!bonus) return;
  if (!confirm(App.t('st_bonus_delete_confirm', { name: bonus.name }))) return;
  try {
    await ApiClient.delete(`/api/bonus-defs/${bonusId}`);
    showToast('success', App.t('st_removed'), App.t('st_bonus_removed', { name: bonus.name }));
    await loadBonusDefs();
  } catch(e) { showToast('error', App.t('st_error'), e.message); }
}

// ─── PARTNERS & MISSING FUNCTIONS ────────────────────────────────────────────

async function loadPartners() {
  try {
    const data = await ApiClient.get('/api/partner-bonuses');
    state.partners = data.partners || [];
  } catch(e) { console.error('loadPartners', e); }
}

function renderBonuses() {
  const percentBonuses = state.bonusDefs.filter(b => b.type === 'percent');
  const cashBonuses = state.bonusDefs.filter(b => b.type === 'cash');

  const percentSummary = document.getElementById('percent-bonus-summary');
  const cashSummary = document.getElementById('cash-bonus-summary');
  if (percentSummary) percentSummary.textContent = App.t('st_percent_bonuses_sub', { count: percentBonuses.length });
  if (cashSummary) cashSummary.textContent = App.t('st_cash_bonuses_sub', { count: cashBonuses.length });

  renderBonusSection('bonus-percent-grid', percentBonuses, 'percent', 'add-percent-btn', 10);
  renderBonusSection('bonus-cash-grid', cashBonuses, 'cash', 'add-cash-btn', 5);
}

function renderBonusSection(gridId, bonuses, type, addBtnId, max) {
  const grid = document.getElementById(gridId);
  if (!grid) return;

  const unit = type === 'percent' ? '%' : ' PLN';
  if (bonuses.length === 0) {
    grid.textContent = '';
    grid.appendChild(emptyStateNode(
      type === 'percent'
        ? App.t('st_no_percent_bonuses')
        : App.t('st_no_cash_bonuses')
    ));
    const addBtnEmpty = document.getElementById(addBtnId);
    if (addBtnEmpty) { addBtnEmpty.disabled = false; addBtnEmpty.title = App.t('st_add_first_bonus'); }
    return;
  }

  grid.innerHTML = '<div style="display:flex;flex-direction:column;gap:16px">' + bonuses.map(b => {
    const bonusId = safePositiveId(b.id);
    return `
    <div class="panel" data-bonus-id="${bonusId ?? ''}">
      <div class="panel-body" style="display:flex;flex-direction:column;gap:16px">
        <div style="display:flex;align-items:center;gap:16px;flex-wrap:wrap">
          <div style="display:flex;align-items:center;gap:12px;flex:2;min-width:200px">
            <span style="background:var(--bg-secondary);padding:6px 10px;border-radius:6px;font-size:0.85rem;font-weight:600;color:var(--text-secondary);border:1px solid var(--border-subtle)">
              ${type === 'percent' ? '%' : 'PLN'}
            </span>
            <div style="display:flex;flex-direction:column;gap:6px;flex:1">
              <input class="form-input" value="${escHtml(b.name)}" data-field="name" placeholder="${escHtml(App.t('st_bonus_name'))}">
              <input class="form-input" value="${escHtml(b.name_en || '')}" data-field="name_en"
                     placeholder="${escHtml(App.t('st_bonus_name_en'))}" title="${escHtml(App.t('st_bonus_name_en'))}"
                     ${englishNamesVisible ? '' : 'hidden'}>
            </div>
          </div>
          <div style="display:flex;align-items:center;gap:12px;flex:1;min-width:150px">
            <div style="position:relative;width:100%">
              <input type="number" class="form-input" min="0" step="${type === 'percent' ? '0.1' : '1'}" value="${finiteValue(b.default_value)}" data-field="default_value">
              <span style="position:absolute;right:12px;top:50%;transform:translateY(-50%);color:var(--text-tertiary)">${unit}</span>
            </div>
          </div>
          <div style="display:flex;gap:8px">
            <button type="button" class="btn btn-secondary btn-sm" data-settings-action="save-bonus" data-bonus-id="${bonusId ?? ''}">${escHtml(App.t('st_save'))}</button>
            <button type="button" class="btn btn-ghost btn-sm" data-settings-action="delete-bonus" data-bonus-id="${bonusId ?? ''}" style="color:var(--danger-500)" title="${escHtml(App.t('st_delete_bonus'))}">
              <svg width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" viewBox="0 0 24 24"><path d="M10 11v6"/><path d="M14 11v6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/><path d="M3 6h18"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
            </button>
          </div>
        </div>
        <div style="width:100%;display:flex;flex-direction:column;gap:6px">
          <input class="form-input" value="${escHtml(b.description || '')}" data-field="description" placeholder="${escHtml(App.t('st_bonus_description_optional'))}">
          <input class="form-input" value="${escHtml(b.description_en || '')}" data-field="description_en"
                 placeholder="${escHtml(App.t('st_bonus_description_en'))}" title="${escHtml(App.t('st_bonus_description_en'))}"
                 ${englishNamesVisible ? '' : 'hidden'}>
        </div>
      </div>
    </div>
  `; }).join('') + '</div>';
  bindSettingsActions(grid);

  // Same reasoning as the tier add button: stay clickable and let
  // createBonus() say why nothing was added, rather than going silently dead.
  const addBtn = document.getElementById(addBtnId);
  if (addBtn) {
    addBtn.disabled = false;
    addBtn.title = bonuses.length >= max
      ? App.t('st_bonus_limit_hint', { max })
      : App.t('st_add_new_bonus');
  }
}

// ─── TEAM ────────────────────────────────────────────────────────────────────
// The producer's team lives on pages/admin/users.html, in the Konto block
// under the profile, and js/users.js owns the roster, the invite modal and
// the role selector. Nothing team-related belongs on the settings page.


function escHtml(str) {
  return Ui.escapeHtml(str);
}

function safePositiveId(value) {
  const id = Number(value);
  return Number.isInteger(id) && id > 0 ? id : null;
}

function finiteValue(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : 0;
}

function safeHexColor(value) {
  return /^#[0-9a-f]{6}$/i.test(String(value || '')) ? String(value) : '#da9348';
}

function bindSettingsActions(root) {
  if (settingsActionRoots.has(root)) return;
  settingsActionRoots.add(root);
  root.addEventListener('click', event => {
    const control = event.target.closest('[data-settings-action]');
    if (!control || !root.contains(control)) return;
    const tierId = safePositiveId(control.dataset.tierId);
    const bonusId = safePositiveId(control.dataset.bonusId);
    if (control.dataset.settingsAction === 'save-tier' && tierId) saveTier(tierId);
    if (control.dataset.settingsAction === 'delete-tier' && tierId) deleteTier(tierId);
    if (control.dataset.settingsAction === 'save-bonus' && bonusId) saveBonus(bonusId);
    if (control.dataset.settingsAction === 'delete-bonus' && bonusId) deleteBonus(bonusId);
  });
}

// An open modal is copy too. Its title, labels and button were resolved with
// App.t() at the moment it opened, so a language switch left the producer
// looking at a Polish dialog on an English screen. A caller that passes a
// FUNCTION instead of an object says "you may rebuild me": the rerender hook
// re-runs it and puts back what he had already typed. Callers that pass a
// plain object behave exactly as before.
let activeModal = null;

function openGenericModal(options) {
  const spec = typeof options === 'function' ? options() : options;
  const { title, subtitle = '', fields = [], submitLabel = App.t('st_save'), onSubmit } = spec;
  document.getElementById('generic-modal-overlay')?.remove();
  activeModal = typeof options === 'function'
    ? { factory: options, fieldIds: fields.map(f => f.id) }
    : null;

  const fieldsHtml = fields.map(f => {
    const val = f.value != null ? `value="${escHtml(String(f.value))}"` : '';
    const placeholder = f.placeholder ? `placeholder="${escHtml(f.placeholder)}"` : '';
    const step  = f.step  ? `step="${f.step}"`   : '';
    const min   = f.min   ? `min="${f.min}"`     : '';
    const max   = f.max   ? `max="${f.max}"`     : '';
    const req   = f.required ? 'required' : '';

    let inputHtml = '';
    if (f.type === 'color') {
      const colorVal = f.value || '#da9348';
      inputHtml = `
        <div style="display:flex;align-items:center;gap:10px">
          <input type="color" id="${f.id}" ${val}
                 style="width:44px;height:44px;border-radius:8px;border:1px solid var(--border-subtle);
                        background:transparent;cursor:pointer;padding:2px">
          <span style="font-size:0.8rem;color:var(--text-tertiary)" id="${f.id}-label">${colorVal}</span>
        </div>`;
    } else {
      inputHtml = `<input type="${f.type}" id="${f.id}" class="form-input"
                          ${val} ${placeholder} ${step} ${min} ${max} ${req}>`;
    }

    return `
      <div class="form-group">
        <label class="form-label" for="${f.id}">${escHtml(Ui.fieldLabel(f))}${f.required ? ' <span style="color:var(--error-400)">*</span>' : ''}</label>
        ${inputHtml}
      </div>`;
  }).join('');

  const overlay = document.createElement('div');
  overlay.id = 'generic-modal-overlay';
  overlay.style.cssText = `
    position:fixed;inset:0;background:rgba(0,0,0,0.65);z-index:200;
    display:flex;align-items:center;justify-content:center;
    backdrop-filter:blur(4px);animation:fadeIn .15s ease;`;

  overlay.innerHTML = `
    <div style="
      background:var(--bg-card);border:1px solid var(--border-subtle);
      border-radius:16px;width:440px;max-width:94vw;
      box-shadow:0 24px 80px rgba(0,0,0,0.6);
      animation:fadeSlideDown .2s ease;overflow:hidden;">

      <div style="padding:24px 24px 0;display:flex;align-items:flex-start;justify-content:space-between">
        <div>
          <div style="font-size:1.05rem;font-weight:700;color:var(--text-primary)">${escHtml(title)}</div>
          ${subtitle ? `<div style="font-size:0.8rem;color:var(--text-tertiary);margin-top:3px">${escHtml(subtitle)}</div>` : ''}
        </div>
        <button id="modal-close-btn" aria-label="${escHtml(App.t('st_close'))}" style="
          width:32px;height:32px;display:flex;align-items:center;justify-content:center;
          background:transparent;border:none;color:var(--text-tertiary);cursor:pointer;
          border-radius:8px;font-size:1.1rem;flex-shrink:0;margin-left:12px;
          transition:background .15s"></button>
      </div>

      <div style="padding:20px 24px">
        <form id="generic-modal-form" autocomplete="off">
          ${fieldsHtml}
        </form>
      </div>

      <div style="padding:16px 24px;border-top:1px solid var(--border-subtle);display:flex;gap:10px">
        <button id="modal-submit-btn" class="btn btn-primary" style="flex:1">
          ${escHtml(submitLabel)}
        </button>
        <button id="modal-cancel-btn" class="btn btn-ghost">${escHtml(App.t('anuluj'))}</button>
      </div>
    </div>`;

  // Built after the markup so the icon is a real element rather than another
  // string spliced into innerHTML.
  Icons.into(overlay.querySelector('#modal-close-btn'), 'x', { size: 18 });

  document.body.appendChild(overlay);

  fields.filter(f => f.type === 'color').forEach(f => {
    const inp = document.getElementById(f.id);
    const lbl = document.getElementById(`${f.id}-label`);
    if (inp && lbl) inp.addEventListener('input', () => { lbl.textContent = inp.value; });
  });

  const close = () => { activeModal = null; overlay.remove(); };

  document.getElementById('modal-close-btn').addEventListener('click', close);
  document.getElementById('modal-close-btn').addEventListener('mouseenter', event => { event.currentTarget.style.background = 'var(--bg-hover)'; });
  document.getElementById('modal-close-btn').addEventListener('mouseleave', event => { event.currentTarget.style.background = 'transparent'; });
  document.getElementById('modal-cancel-btn').addEventListener('click', close);
  overlay.addEventListener('click', e => { if (e.target === overlay) close(); });

  document.getElementById('modal-submit-btn').addEventListener('click', async () => {
    const btn = document.getElementById('modal-submit-btn');
    const form = document.getElementById('generic-modal-form');

    if (form && !form.checkValidity()) { form.reportValidity(); return; }

    const vals = {};
    fields.forEach(f => {
      const el = document.getElementById(f.id);
      if (el) vals[f.id] = el.value;
    });

    btn.disabled = true;
    btn.innerHTML = `<span class="spinner" style="width:14px;height:14px;border-width:2px"></span> ${escHtml(App.t('st_saving'))}`;

    try {
      const shouldClose = await onSubmit(vals);
      if (shouldClose !== false) close();
    } catch(e) {
      showToast('error', App.t('st_error'), e.message);
    } finally {
      if (document.getElementById('modal-submit-btn')) {
        btn.disabled = false;
        btn.innerHTML = escHtml(submitLabel);
      }
    }
  });

  setTimeout(() => {
    // Only on a genuinely fresh modal: after a language rebuild the producer
    // is already back in the box he was typing in, and jumping him to the
    // first field would undo exactly what the rebuild preserved.
    if (overlay.contains(document.activeElement)) return;
    const first = overlay.querySelector('input[type="text"], input[type="number"]');
    if (first) first.focus();
  }, 50);
}


// ─── REGISTRATION FORM FIELDS ────────────────────────────
// Up to five producer-authored questions asked on top of the four mandatory
// identity fields. Each is switchable without losing what has already been
// answered, so a producer can stop asking something without erasing the
// answers already given.

const MAX_REGISTRATION_FIELDS = 5;
let registrationFields = [];

async function loadRegistrationFields() {
  try {
    const data = await ApiClient.get('/api/registration-fields');
    registrationFields = data.fields || [];
    renderRegistrationFields();
  } catch (err) {
    showRegFieldError(App.t('st_fields_load_failed'));
  }
}

function showRegFieldError(message) {
  const el = document.getElementById('reg-fields-error');
  if (!el) return;
  el.textContent = message;
  el.hidden = false;
}

// DOM nodes and textContent only: the labels are producer-authored text and
// are echoed back to a public page, so nothing here assembles markup.
function renderRegistrationFields() {
  const list = document.getElementById('reg-fields-list');
  const empty = document.getElementById('reg-fields-empty');
  const addBtn = document.getElementById('add-reg-field-btn');
  if (!list) return;
  list.textContent = '';
  if (empty) empty.hidden = registrationFields.length > 0;
  if (addBtn) addBtn.disabled = registrationFields.length >= MAX_REGISTRATION_FIELDS;

  registrationFields.forEach((field) => {
    const row = document.createElement('li');
    row.className = 'panel';
    row.style.cssText = 'padding:12px 16px;display:flex;align-items:center;gap:12px;flex-wrap:wrap';
    row.dataset.regFieldId = String(field.id);

    const label = document.createElement('input');
    label.type = 'text';
    label.className = 'form-input';
    label.style.cssText = 'flex:1;min-width:180px';
    label.value = field.label;
    label.maxLength = 100;
    label.dataset.regFieldAction = 'rename';
    label.setAttribute('aria-label', App.t('st_field_name_aria', { name: field.label }));

    // The English wording of the same question, revealed by the EN switch.
    const labelEn = document.createElement('input');
    labelEn.type = 'text';
    labelEn.className = 'form-input';
    labelEn.style.cssText = 'flex:1;min-width:180px';
    labelEn.value = field.label_en || '';
    labelEn.maxLength = 100;
    labelEn.placeholder = App.t('st_field_name_en');
    labelEn.dataset.field = 'label_en';
    labelEn.dataset.regFieldAction = 'rename-en';
    labelEn.hidden = !englishNamesVisible;

    const toggleWrap = document.createElement('label');
    toggleWrap.style.cssText = 'display:flex;align-items:center;gap:6px;font-size:0.8rem;white-space:nowrap';
    const toggle = document.createElement('input');
    toggle.type = 'checkbox';
    toggle.checked = !!field.active;
    toggle.dataset.regFieldAction = 'toggle';
    toggleWrap.append(toggle, document.createTextNode(App.t('st_ask_this')));

    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'btn btn-ghost btn-sm';
    remove.style.color = 'var(--danger-500)';
    remove.textContent = App.t('st_delete');
    remove.dataset.regFieldAction = 'remove';

    row.append(label, labelEn, toggleWrap, remove);
    list.appendChild(row);
  });
}

function setupRegistrationFields() {
  setupEnglishNameToggles();
  document.getElementById('add-reg-field-btn')?.addEventListener('click', async () => {
    try {
      await ApiClient.post('/api/registration-fields', { label: App.t('st_new_question') });
      await loadRegistrationFields();
    } catch (err) {
      showRegFieldError(err.message);
    }
  });

  const list = document.getElementById('reg-fields-list');
  if (!list) return;
  // Delegated, so rows rebuilt by a re-render never lose their handlers.
  list.addEventListener('change', async (event) => {
    const control = event.target.closest('[data-reg-field-action]');
    const row = event.target.closest('[data-reg-field-id]');
    if (!control || !row) return;
    const id = row.dataset.regFieldId;
    const action = control.dataset.regFieldAction;
    try {
      if (action === 'toggle') {
        await ApiClient.patch(`/api/registration-fields/${id}`, { active: control.checked });
      } else if (action === 'rename') {
        await ApiClient.patch(`/api/registration-fields/${id}`, { label: control.value.trim() });
      } else if (action === 'rename-en') {
        // Empty clears the translation rather than failing: the question then
        // reads in Polish for everyone, which is where it started.
        await ApiClient.patch(`/api/registration-fields/${id}`, { label_en: control.value.trim() });
      }
      await loadRegistrationFields();
    } catch (err) {
      showRegFieldError(err.message);
      await loadRegistrationFields();
    }
  });
  list.addEventListener('click', async (event) => {
    const control = event.target.closest('[data-reg-field-action="remove"]');
    const row = event.target.closest('[data-reg-field-id]');
    if (!control || !row) return;
    try {
      await ApiClient.delete(`/api/registration-fields/${row.dataset.regFieldId}`);
      await loadRegistrationFields();
    } catch (err) {
      showRegFieldError(err.message);
    }
  });
}


// ─── LEAD FIELDS ──────────────────────────────────────────
//
// What the producer asks about a DEAL. A sibling of the signup fields above
// and deliberately not a reuse of them: those are keyed by partner and capped
// at five, these are keyed by lead and capped at fifty.

const MAX_LEAD_FIELDS = 50;
let leadFields = [];

function showLeadFieldError(message) {
  const box = document.getElementById('lead-fields-error');
  if (!box) return;
  box.textContent = message || App.t('st_field_save_failed');
  box.hidden = !message;
}

async function loadLeadFields() {
  const loading = document.getElementById('lead-fields-loading');
  try {
    const data = await ApiClient.get('/api/lead-fields');
    leadFields = data.fields || [];
    showLeadFieldError('');
    renderLeadFields();
  } catch (err) {
    // The list keeps whatever it last showed, marked stale, rather than
    // blanking out and implying the producer has no fields.
    showLeadFieldError(err.message);
  } finally {
    if (loading) loading.hidden = true;
  }
}

function renderLeadFields() {
  const list = document.getElementById('lead-fields-list');
  const empty = document.getElementById('lead-fields-empty');
  const addBtn = document.getElementById('add-lead-field-btn');
  if (!list) return;

  const active = leadFields.filter(field => field.active);
  if (empty) empty.hidden = leadFields.length > 0;
  if (addBtn) addBtn.disabled = active.length >= MAX_LEAD_FIELDS;

  list.replaceChildren();
  leadFields.forEach((field) => {
    const row = document.createElement('li');
    row.dataset.leadFieldId = String(field.id);
    row.style.cssText = 'display:flex;gap:10px;align-items:center;flex-wrap:wrap';

    const label = document.createElement('input');
    label.type = 'text';
    label.className = 'form-input';
    label.style.flex = '1 1 220px';
    label.maxLength = 120;
    label.value = field.label;
    label.dataset.leadFieldAction = 'rename';

    // The same question in English, revealed by the EN switch.
    const labelEn = document.createElement('input');
    labelEn.type = 'text';
    labelEn.className = 'form-input';
    labelEn.style.flex = '1 1 220px';
    labelEn.maxLength = 120;
    labelEn.value = field.label_en || '';
    labelEn.placeholder = App.t('st_field_name_en');
    labelEn.dataset.field = 'label_en';
    labelEn.dataset.leadFieldAction = 'rename-en';
    labelEn.hidden = !englishNamesVisible;

    const type = document.createElement('select');
    type.className = 'form-select';
    type.dataset.leadFieldAction = 'retype';
    [['text', 'st_type_text'], ['number', 'st_type_number'], ['checkbox', 'st_type_yes_no']]
      .forEach(([value, key]) => {
        const option = document.createElement('option');
        option.value = value;
        option.textContent = App.t(key);
        if (field.field_type === value) option.selected = true;
        type.appendChild(option);
      });

    row.append(label, labelEn, type,
      toggleControl('required', App.t('st_required'), field.required),
      toggleControl('editable', App.t('st_partner_can_edit'), field.partner_editable),
      toggleControl('toggle', App.t('st_enabled'), field.active));
    list.appendChild(row);
  });

  function toggleControl(action, text, checked) {
    const wrap = document.createElement('label');
    wrap.style.cssText = 'display:flex;gap:6px;align-items:center';
    wrap.className = 'text-xs text-tertiary';
    const box = document.createElement('input');
    box.type = 'checkbox';
    box.checked = !!checked;
    box.dataset.leadFieldAction = action;
    wrap.append(box, document.createTextNode(text));
    return wrap;
  }
}

function setupLeadFields() {
  setupEnglishNameToggles();
  document.getElementById('add-lead-field-btn')?.addEventListener('click', async () => {
    try {
      await ApiClient.post('/api/lead-fields', { label: App.t('st_new_question'), field_type: 'text' });
      await loadLeadFields();
    } catch (err) {
      showLeadFieldError(err.message);
    }
  });

  const list = document.getElementById('lead-fields-list');
  if (!list) return;
  // Delegated, so rows rebuilt by a re-render never lose their handlers.
  list.addEventListener('change', async (event) => {
    const control = event.target.closest('[data-lead-field-action]');
    const row = event.target.closest('[data-lead-field-id]');
    if (!control || !row) return;
    const id = row.dataset.leadFieldId;
    const action = control.dataset.leadFieldAction;
    const patch = {
      toggle:   () => ({ active: control.checked }),
      required: () => ({ required: control.checked }),
      editable: () => ({ partner_editable: control.checked }),
      rename:   () => ({ label: control.value.trim() }),
      // Empty clears the translation; the field then reads in Polish.
      'rename-en': () => ({ label_en: control.value.trim() }),
      retype:   () => ({ field_type: control.value }),
    }[action];
    if (!patch) return;

    try {
      await ApiClient.patch(`/api/lead-fields/${id}`, patch());
      showLeadFieldError('');
      await loadLeadFields();
    } catch (err) {
      // Refused changes must not linger on screen as if they had applied --
      // re-reading is what keeps the switch honest about the server's answer.
      showLeadFieldError(err.message);
      await loadLeadFields();
    }
  });
}

// Everything below the static HTML is rebuilt from live state. Re-run those
// renderers after a language change so labels change without another API call.
function preserveSettingsDrafts(render) {
  const roots = ['stage-list', 'tiers-grid', 'bonus-percent-grid', 'bonus-cash-grid', 'reg-fields-list', 'lead-fields-list'];
  const drafts = [];
  roots.forEach(id => {
    document.getElementById(id)?.querySelectorAll('input, select, textarea').forEach(control => {
      const row = control.closest('[data-stage-id], [data-tier-id], [data-bonus-id], [data-reg-field-id], [data-lead-field-id]');
      if (!row) return;
      const rowAttr = [...row.attributes].find(attr => /^data-(stage|tier|bonus|reg-field|lead-field)-id$/.test(attr.name));
      const fieldAttr = [...control.attributes].find(attr => /^data-(field|reg-field-action|lead-field-action)$/.test(attr.name));
      if (rowAttr && fieldAttr) drafts.push({ id, rowAttr: rowAttr.name, rowValue: rowAttr.value, fieldAttr: fieldAttr.name, fieldValue: fieldAttr.value, value: control.value, checked: control.checked });
    });
  });
  // Where the caret was, not just what was typed, so a language switch in the
  // middle of writing an English bonus name keeps the producer in place.
  const active = document.activeElement;
  const focused = drafts.find(draft => {
    const row = active?.closest?.(`[${draft.rowAttr}="${draft.rowValue}"]`);
    return row && active.getAttribute?.(draft.fieldAttr) === draft.fieldValue
      && draft.id === row.closest('[id]')?.id;
  }) || null;
  const caret = focused && typeof active.selectionStart === 'number'
    ? { start: active.selectionStart, end: active.selectionEnd }
    : null;

  render();
  drafts.forEach(draft => {
    const rows = document.getElementById(draft.id)?.querySelectorAll(`[${draft.rowAttr}]`) || [];
    const row = [...rows].find(node => node.getAttribute(draft.rowAttr) === draft.rowValue);
    const control = [...(row?.querySelectorAll(`[${draft.fieldAttr}]`) || [])].find(node => node.getAttribute(draft.fieldAttr) === draft.fieldValue);
    if (!control) return;
    control.value = draft.value;
    control.checked = draft.checked;
    if (draft === focused) {
      control.focus();
      if (caret && typeof control.setSelectionRange === 'function') {
        control.setSelectionRange(caret.start, caret.end);
      }
    }
  });
  // The rows were rebuilt, so the switch has to say again which boxes show.
  applyEnglishNameVisibility();
}

App.onRerender(() => {
  const saveButton = document.getElementById('save-all-btn');
  if (saveButton && !saveButton.disabled) Icons.label(saveButton, 'check', App.t('st_save_settings'), { size: 15 });
  renderTabIcons();
  document.querySelectorAll('[data-queue-position] label').forEach(label => {
    label.textContent = App.t('st_nth_submission', { n: label.closest('[data-queue-position]').dataset.queuePosition });
  });
  updateQueueSummary();
  preserveSettingsDrafts(() => {
    renderStages();
    renderTiers();
    renderBonuses();
    renderRegistrationFields();
    renderLeadFields();
  });
  relabelActiveModal();
});

// Rebuild a rebuildable modal in the new language, carrying over every value
// the producer has already typed, plus where his caret was.
function relabelActiveModal() {
  const open = activeModal;
  if (!open || !document.getElementById('generic-modal-overlay')) { activeModal = null; return; }

  const typed = {};
  open.fieldIds.forEach(id => {
    const el = document.getElementById(id);
    if (el) typed[id] = el.value;
  });
  const focusedId = open.fieldIds.find(id => document.getElementById(id) === document.activeElement);
  const focusedEl = focusedId ? document.getElementById(focusedId) : null;
  const caret = focusedEl && typeof focusedEl.selectionStart === 'number'
    ? { start: focusedEl.selectionStart, end: focusedEl.selectionEnd }
    : null;

  openGenericModal(open.factory);

  Object.entries(typed).forEach(([id, value]) => {
    const el = document.getElementById(id);
    if (el) el.value = value;
  });
  if (focusedId) {
    const el = document.getElementById(focusedId);
    if (el) {
      el.focus();
      if (caret && typeof el.setSelectionRange === 'function') {
        el.setSelectionRange(caret.start, caret.end);
      }
    }
  }
}
