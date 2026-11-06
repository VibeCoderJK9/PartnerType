/* ============================================================
   PARTNERTYPE — COMMISSIONS PAGE JS
   Tiers, Bonus Definitions, Partner Bonuses, Bulk actions
   ============================================================ */

'use strict';

/* Amounts are grouped ("+2 500 PLN", not "+2500 PLN"). Delegates to app.js's
   formatAmount rather than redeclaring it: these are classic scripts sharing
   one global scope, so a second `function formatAmount` here would silently
   replace the shared one for every other screen. The fallback covers unit
   tests, which mount this module without app.js. */
// Grouping for figures that must keep their grosze exactly as the API sent
// them -- amountText() rounds to whole PLN, which is right for a bonus chip
// and wrong for a commission line.
function ledgerLocale() {
  return (typeof App !== 'undefined' && App.lang === 'en') ? 'en-GB' : 'pl-PL';
}

function amountText(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return '0';
  return typeof formatAmount === 'function'
    ? formatAmount(number)
    : new Intl.NumberFormat(App.lang === 'en' ? 'en-GB' : 'pl-PL', { maximumFractionDigits: 0 }).format(number);
}

// ─── STATE ───────────────────────────────────────────────────
let state = {
  tiers:       [],
  bonusDefs:   [],
  partners:    [],
  ledger:      [],
  selectedPartners: new Set(),
  openPartnerId: null,
  partnerDetail: null,
  settlement: [],
  payoutPartnerId: null,
  pendingWins: [],
  selectedWins: new Set(),
  evidence: null,
};
const commissionActionRoots = new WeakSet();

// ─── INIT ─────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  if (document.body.dataset.page === 'admin-commissions') initCommissions();
});

if (typeof App !== 'undefined' && App.onRerender) App.onRerender(() => {
  if (document.body.dataset.page !== 'admin-commissions') return;
  // Every tab on this screen renders from state already in memory, so the
  // render functions are enough -- no refetch needed to change language.
  renderSettlement();
  renderPendingWins();
  renderPartnerTable();
  updateBulkBar();
  renderEvidence();
  renderLedger(state.ledger);
});

async function initCommissions() {
  setupCommissionTabs();
  setupSlidePanel();
  setupPayoutPanel();
  setupWins();
  setupEvidence();
  setupBulkBar();
  setupPartnerTableRetry();
  setupLedgerRetry();
  await Promise.all([
    loadTiers(), loadBonusDefs(), loadPartners(), loadLedger(), loadSettlement(),
  ]);
  await Promise.all([loadPendingWins(), loadEvidence()]);
}

// ─── TABS ─────────────────────────────────────────────────────


// ═══════════════════════════════════════════════════════════════
//  TAB 1 — TIERS
// ═══════════════════════════════════════════════════════════════
async function loadTiers() {
  try {
    const data = await ApiClient.get('/api/tiers');
    state.tiers = data.tiers || [];
  } catch(e) { console.error('loadTiers', e); }
}









// ═══════════════════════════════════════════════════════════════
//  TAB 2 — BONUS DEFINITIONS
// ═══════════════════════════════════════════════════════════════
//
//  DELIBERATELY NOT WIRED: `bonus-percent-grid` and
//  `bonus-cash-grid` exist ONLY in pages/admin/settings.html, and that page
//  loads js/settings.js, not this file. renderBonusSection() and saveBonus()
//  below therefore return at their first `if (!grid) return;` on every screen
//  that actually loads this controller -- the one reachable definition editor
//  is the one in js/settings.js.
//
//  loadBonusDefs() itself IS load-bearing: state.bonusDefs feeds the bonus
//  toggles, the bulk-assign selects and the per-partner rate breakdown, all of
//  which do render here.
//
//  So the producer's English wording (name_en / description_en) is edited in
//  js/settings.js only. Adding it here too would build a translation box no
//  producer can ever open.
//  Before changing that, check whether a page has since grown those grids.
//
async function loadBonusDefs() {
  try {
    const data = await ApiClient.get('/api/bonus-defs');
    state.bonusDefs = data.bonuses || [];
    renderBonusDefs();
  } catch(e) { console.error('loadBonusDefs', e); }
}

function renderBonusDefs() {
  const percentBonuses = state.bonusDefs.filter(b => b.type === 'percent');
  const cashBonuses    = state.bonusDefs.filter(b => b.type === 'cash');

  renderBonusSection('bonus-percent-grid', percentBonuses, 'percent', 'add-percent-btn', 10);
  renderBonusSection('bonus-cash-grid',    cashBonuses,    'cash',    'add-cash-btn',    5);
}

function renderBonusSection(gridId, bonuses, type, addBtnId, max) {
  const grid = document.getElementById(gridId);
  if (!grid) return;

  const unit = type === 'percent' ? '%' : ' PLN';

  grid.innerHTML = bonuses.map(b => {
    const bonusId = safePositiveId(b.id);
    return `
    <div class="bonus-def-card" data-bonus-id="${bonusId ?? ''}">
      <span class="bonus-type-pill ${type}">${type === 'percent' ? '%' : 'PLN'}</span>
      <input class="bonus-def-name-input" value="${escHtml(b.name)}" data-field="name" placeholder="${escHtml(App.t('st_bonus_name'))}">
      <div class="bonus-value-row">
        <input class="bonus-value-input"
               type="number" min="0" step="${type === 'percent' ? '0.1' : '1'}"
               value="${escHtml(finiteValue(b.default_value))}"
               data-field="default_value">
        <span class="bonus-value-unit">${unit}</span>
      </div>
      <input class="bonus-desc-input"
             value="${escHtml(b.description)}"
             data-field="description"
             placeholder="${escHtml(App.t('st_bonus_description_optional'))}">
      <div class="bonus-def-footer">
        <button class="tier-save-btn" type="button" style="font-size:0.75rem" data-commission-action="save-bonus" data-bonus-id="${bonusId ?? ''}">${escHtml(App.t('st_save'))}</button>
        <button class="tier-delete-btn" type="button" data-commission-action="delete-bonus" data-bonus-id="${bonusId ?? ''}" title="${escHtml(App.t('st_delete_bonus'))}" aria-label="${escHtml(App.t('st_delete_bonus'))}">
          <svg width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14H6L5 6"/></svg>
        </button>
      </div>
    </div>
  `; }).join('');
  bindCommissionActions(grid);

  const addBtn = document.getElementById(addBtnId);
  if (addBtn) addBtn.disabled = bonuses.length >= max;
  const countEl = document.getElementById(addBtnId.replace('add-', 'count-'));
  if (countEl) countEl.textContent = `${bonuses.length}/${max}`;
}







// ═══════════════════════════════════════════════════════════════
//  TAB 3 — PARTNERS & BONUSES
// ═══════════════════════════════════════════════════════════════
async function loadPartners() {
  setPartnerTableState('loading');
  try {
    const data = await ApiClient.get('/api/partner-bonuses');
    state.partners = data.partners || [];
    setPartnerTableState('populated');
    renderPartnerTable();
  } catch(e) {
    console.error('loadPartners', e);
    setPartnerTableState('error');
  }
}

function setPartnerTableState(uiState) {
  const errorEl = document.getElementById('partner-comm-error-state');
  const table = document.querySelector('.partner-comm-table');
  if (errorEl) errorEl.hidden = uiState !== 'error';
  if (table) table.hidden = uiState === 'error';
}

function setupPartnerTableRetry() {
  const retry = document.getElementById('partner-comm-retry-btn');
  if (retry && !commissionActionRoots.has(retry)) {
    commissionActionRoots.add(retry);
    retry.addEventListener('click', () => void loadPartners());
  }
}

function renderPartnerTable() {
  const tbody = document.getElementById('partner-comm-tbody');
  if (!tbody) return;

  if (!state.partners.length) {
    tbody.innerHTML = `<tr><td colspan="7" class="text-center text-tertiary" style="padding:40px">${escHtml(App.t('no_data'))}</td></tr>`;
    return;
  }

  tbody.innerHTML = state.partners.map(p => {
    const partnerId = safePositiveId(p.id);
    const bonusPct  = finiteValue(p.bonus_percent);
    const totalPct  = finiteValue(p.total_percent);
    const totalCash = finiteValue(p.bonus_cash);
    const checked   = state.selectedPartners.has(partnerId) ? 'checked' : '';

    // Bonus chips
    const chips = [];
    if (bonusPct > 0) chips.push(`<span class="bonus-chip">+${bonusPct.toFixed(1)}%</span>`);
    if (totalCash > 0) chips.push(`<span class="bonus-chip cash">+${amountText(totalCash)} PLN</span>`);
    const bonusCount = p.active_bonus_count || 0;

    return `<tr data-partner-id="${partnerId ?? ''}" class="${state.selectedPartners.has(partnerId) ? 'table-row-selected' : ''}">
      <td><input type="checkbox" class="row-check partner-cb" data-id="${partnerId ?? ''}" ${checked}></td>
      <td>
        <div style="display:flex;align-items:center;gap:10px">
          <div class="avatar avatar-sm">${escHtml(getInitials(p.name))}</div>
          <div>
            <div class="cell-primary">${escHtml(p.name)}</div>
            <div class="text-xs text-tertiary">${escHtml(p.company || '')}</div>
          </div>
        </div>
      </td>
      <td>
        <div class="bonus-chips">
          ${chips.join('') || '<span class="text-xs text-tertiary">—</span>'}
          ${bonusCount > 0 ? `<span class="bonus-chip" style="background:rgba(255,255,255,0.06);color:var(--text-tertiary)">${escHtml(App.t('cm_bonus_count', { count: bonusCount, unit: App.plural(bonusCount, 'unit_bonus') }))}</span>` : ''}
        </div>
      </td>
      <td>
        <div class="total-rate-cell">${totalPct.toFixed(2)}%</div>
        ${totalCash > 0 ? `<div class="total-rate-sub">+${amountText(totalCash)} PLN</div>` : ''}
      </td>
      <td>
        <button type="button" class="btn btn-ghost btn-sm" data-commission-action="open-partner" data-partner-id="${partnerId ?? ''}" title="${escHtml(App.t('cm_edit_commissions'))}" aria-label="${escHtml(App.t('cm_edit_commissions'))}">
          <svg width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M11 4H4a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7"/><path d="M18.5 2.5l3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
        </button>
      </td>
    </tr>`;
  }).join('');
  bindCommissionActions(tbody);

  // Checkbox events
  tbody.querySelectorAll('.partner-cb').forEach(cb => {
    cb.addEventListener('change', e => {
      const pid = parseInt(e.target.dataset.id);
      if (e.target.checked) {
        state.selectedPartners.add(pid);
        e.target.closest('tr').classList.add('table-row-selected');
      } else {
        state.selectedPartners.delete(pid);
        e.target.closest('tr').classList.remove('table-row-selected');
      }
      updateBulkBar();
    });
  });

  // Select all header
  const allCb = document.getElementById('partner-select-all');
  if (allCb) {
    allCb.addEventListener('change', e => {
      tbody.querySelectorAll('.partner-cb').forEach(cb => {
        cb.checked = e.target.checked;
        const pid = parseInt(cb.dataset.id);
        if (e.target.checked) {
          state.selectedPartners.add(pid);
          cb.closest('tr').classList.add('table-row-selected');
        } else {
          state.selectedPartners.delete(pid);
          cb.closest('tr').classList.remove('table-row-selected');
        }
      });
      updateBulkBar();
    });
  }
}

// ═══════════════════════════════════════════════════════════════
//  PER-LEAD COMMISSION LEDGER (real stages, real total)
// ═══════════════════════════════════════════════════════════════
// The ledger badge is built from the fetched stage pipeline (the shared
// Ui.buildStagePipeline/Ui.stageBadge), fetched once alongside the lead list
// this screen already loads. No hardcoded status/class allowlist.
let ledgerStagePipeline = null;

/* ═════════════════════════════════════════════════════════════════
   SETTLEMENT — what is still owed, and recording that it was paid

   Every figure on this screen comes from api/payouts.py. Nothing is summed in
   the browser: these are amounts of money, and a second place that computes
   them is a second place that can disagree with the first.

   The cards used to read "To pay out this month" while summing every
   win in the current month, paid or not, so they overstated the debt from the
   first payout onwards.
   ═══════════════════════════════════════════════════════════════ */

function writeText(id, value) {
  const el = document.getElementById(id);
  if (el) el.textContent = value;
}

function setSettlementState(uiState) {
  const table = document.getElementById('settlement-table');
  const empty = document.getElementById('settlement-empty-state');
  const error = document.getElementById('settlement-error-state');
  if (table) table.hidden = uiState !== 'ready';
  if (empty) empty.hidden = uiState !== 'empty';
  if (error) error.hidden = uiState !== 'error';
}

async function loadSettlement() {
  try {
    const data = await ApiClient.get('/api/payouts/outstanding');
    state.settlement = Array.isArray(data.partners) ? data.partners : [];
    renderSettlement();
  } catch (e) {
    console.error('loadSettlement', e);
    setSettlementState('error');
    // The cards keep their em-dash rather than a zero, which would read as
    // "nothing to pay" on a screen about money.
  }
}

function renderSettlement() {
  const rows = state.settlement;
  const total = rows.reduce((sum, row) => sum + (row.amount || 0), 0);
  const leads = rows.reduce((sum, row) => sum + (row.lead_count || 0), 0);

  const bonusTotal = rows.reduce((sum, row) => sum + (row.bonus_amount || 0), 0);
  writeText('payout-total', formatCurrency(total));
  writeText('payout-total-deals',
    bonusTotal > 0
      ? App.t('cm_wins_plus_bonuses', { wins: App.pluralize(leads, 'unit_win'), amount: amountText(bonusTotal) })
      : App.pluralize(leads, 'unit_win'));
  writeText('payout-partners', String(rows.length));
  const oldest = rows.reduce(
    (acc, row) => (!acc || (row.oldest_won_at && row.oldest_won_at < acc) ? row.oldest_won_at : acc),
    null,
  );
  writeText('payout-oldest', oldest ? App.t('cm_oldest_sale', { date: formatDate(oldest) }) : '—');

  const tbody = document.getElementById('settlement-tbody');
  if (!tbody) return;
  tbody.replaceChildren();
  if (!rows.length) {
    setSettlementState('empty');
    return;
  }

  for (const row of rows) {
    const tr = document.createElement('tr');

    const who = document.createElement('td');
    const name = document.createElement('div');
    name.className = 'cell-primary';
    name.textContent = row.name || '—';
    const company = document.createElement('div');
    company.className = 'text-xs text-tertiary';
    company.textContent = row.company || '—';
    who.append(name, company);

    const amount = document.createElement('td');
    amount.className = 'cell-primary';
    amount.textContent = formatCurrency(row.amount);

    // "Za co": a producer settling a period has to know whether he is paying
    // commission on deals, a one-off bonus, or both -- the single total hides
    // exactly the thing he is least likely to remember.
    const count = document.createElement('td');
    const parts = [];
    const deals = row.lead_count || 0;
    const bonuses = row.bonus_count || 0;
    if (deals) parts.push(App.pluralize(deals, 'unit_win'));
    if (bonuses) {
      parts.push(App.pluralize(bonuses, 'unit_bonus') + ' '
        + `(${amountText(row.bonus_amount || 0)} PLN)`);
    }
    count.className = 'text-sm';
    count.textContent = parts.length ? parts.join(' + ') : '—';

    const oldestCell = document.createElement('td');
    oldestCell.className = 'text-tertiary text-sm';
    oldestCell.textContent = row.oldest_won_at ? formatDate(row.oldest_won_at) : '—';

    const actions = document.createElement('td');
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'btn btn-secondary btn-sm';
    button.textContent = App.t('cm_settle');
    button.addEventListener('click', () => void openPayoutPanel(row));
    actions.appendChild(button);

    tr.append(who, amount, count, oldestCell, actions);
    tbody.appendChild(tr);
  }
  setSettlementState('ready');
}

function setupPayoutPanel() {
  document.getElementById('payout-panel-close')?.addEventListener('click', closePayoutPanel);
  document.getElementById('settlement-retry-btn')?.addEventListener('click', () => void loadSettlement());
  document.getElementById('payout-panel-retry')?.addEventListener('click', () => {
    const row = state.settlement.find(r => r.partner_id === state.payoutPartnerId);
    if (row) void openPayoutPanel(row);
  });
}

function closePayoutPanel() {
  document.getElementById('slide-overlay')?.classList.remove('open');
  document.getElementById('payout-panel')?.classList.remove('open');
  state.payoutPartnerId = null;
}

async function openPayoutPanel(row) {
  state.payoutPartnerId = row.partner_id;
  writeText('payout-panel-name', row.name || '—');
  writeText('payout-panel-sub', row.company || '—');
  document.getElementById('slide-overlay')?.classList.add('open');
  document.getElementById('payout-panel')?.classList.add('open');
  const loading = document.getElementById('payout-panel-loading');
  const content = document.getElementById('payout-panel-content');
  const error = document.getElementById('payout-panel-error');
  if (loading) loading.hidden = false;
  if (content) content.hidden = true;
  if (error) error.hidden = true;

  try {
    const [owed, history] = await Promise.all([
      ApiClient.get(`/api/payouts/partner/${row.partner_id}`),
      ApiClient.get(`/api/payouts/partner/${row.partner_id}/history`),
    ]);
    renderPayoutPanel(row, owed, history);
    if (loading) loading.hidden = true;
    if (content) content.hidden = false;
  } catch (e) {
    console.error('openPayoutPanel', e);
    if (loading) loading.hidden = true;
    if (error) error.hidden = false;
  }
}

// Month names from Intl rather than a hardcoded Polish map: a translated map
// would have to be maintained twice and would still be wrong for a third
// language. Read per call, so a language switch relabels the periods.
function monthName(monthNumber) {
  const index = Number(monthNumber) - 1;
  if (!Number.isInteger(index) || index < 0 || index > 11) return String(monthNumber);
  const locale = (typeof App !== 'undefined' && App.lang === 'en') ? 'en-GB' : 'pl-PL';
  return new Intl.DateTimeFormat(locale, { month: 'long' })
    .format(new Date(2024, index, 1));
}

function periodLabel(entry) {
  const year = entry.period_key.slice(0, 4);
  if (entry.period_type === 'month') {
    return `${monthName(entry.period_key.slice(5))} ${year}`;
  }
  return `${entry.period_key.slice(5)} ${year}`;
}

function payoutRow(partner, entry) {
  const line = document.createElement('div');
  line.className = 'payout-line';

  const label = document.createElement('div');
  const title = document.createElement('div');
  title.className = 'payout-line-title';
  title.textContent = periodLabel(entry);
  const sub = document.createElement('div');
  sub.className = 'text-xs text-tertiary';
  sub.textContent = App.pluralize(entry.lead_count, 'unit_sale');
  label.append(title, sub);

  const amount = document.createElement('div');
  amount.className = 'payout-line-amount';
  amount.textContent = formatCurrency(entry.amount);

  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'btn btn-primary btn-sm';
  button.textContent = App.t('cm_paid');
  button.addEventListener('click', () => void markPaid(partner, entry, button));

  line.append(label, amount, button);
  return line;
}

function renderPayoutPanel(partner, owed, history) {
  const box = document.getElementById('payout-panel-total-box');
  if (box) {
    box.replaceChildren();
    const heading = document.createElement('div');
    heading.className = 'commission-summary-title';
    heading.textContent = App.t('cm_payable');
    const value = document.createElement('div');
    value.style.cssText = 'font-size:1.5rem;font-weight:700';
    value.textContent = formatCurrency(owed.total);
    const sub = document.createElement('div');
    sub.className = 'text-xs text-tertiary';
    sub.textContent = App.pluralize(owed.lead_count, 'unit_win');
    box.append(heading, value, sub);
  }

  const fill = (id, entries) => {
    const host = document.getElementById(id);
    if (!host) return;
    host.replaceChildren();
    if (!entries.length) {
      const none = document.createElement('p');
      none.className = 'text-xs text-tertiary';
      none.textContent = App.t('cm_nothing_unsettled');
      host.appendChild(none);
      return;
    }
    entries.forEach(entry => host.appendChild(payoutRow(partner, entry)));
  };
  fill('payout-months', owed.months || []);
  fill('payout-quarters', owed.quarters || []);

  const host = document.getElementById('payout-history');
  if (!host) return;
  host.replaceChildren();
  const years = history.years || [];
  const payouts = history.payouts || [];
  if (!payouts.length) {
    const none = document.createElement('p');
    none.className = 'text-xs text-tertiary';
    none.textContent = App.t('cm_nothing_paid_partner');
    host.appendChild(none);
    return;
  }
  for (const year of years) {
    const line = document.createElement('div');
    line.className = 'payout-line payout-line-year';
    const label = document.createElement('div');
    label.className = 'payout-line-title';
    label.textContent = App.t('cm_year', { year: year.year });
    const value = document.createElement('div');
    value.className = 'payout-line-amount';
    value.textContent = formatCurrency(year.amount);
    line.append(label, value);
    host.appendChild(line);
  }
  for (const payout of payouts) {
    const line = document.createElement('div');
    line.className = 'payout-line';
    const label = document.createElement('div');
    const title = document.createElement('div');
    title.className = 'payout-line-title';
    title.textContent = periodLabel(payout);
    if (payout.reversed_at) {
      title.style.textDecoration = 'line-through';
      title.style.color = 'var(--text-tertiary)';
    }
    const sub = document.createElement('div');
    sub.className = 'text-xs text-tertiary';
    sub.textContent = payout.reversed_at
      ? App.t('cm_reversed_on', { date: formatDate(payout.reversed_at) })
      : `${formatDate(payout.paid_at)}${payout.paid_by_name ? ` · ${payout.paid_by_name}` : ''}`;
    label.append(title, sub);

    const amount = document.createElement('div');
    amount.className = 'payout-line-amount';
    amount.textContent = formatCurrency(payout.amount);
    line.append(label, amount);

    if (!payout.reversed_at) {
      const undo = document.createElement('button');
      undo.type = 'button';
      undo.className = 'btn btn-ghost btn-sm';
      undo.textContent = App.t('cm_undo');
      undo.addEventListener('click', () => void reversePayout(partner, payout, undo));
      line.appendChild(undo);
    }
    host.appendChild(line);
  }
}

async function markPaid(partner, entry, button) {
  // Money leaving the company is worth one question. The period and the amount
  // are both named, because "Paid out" on the wrong row is not recoverable
  // without the producer noticing it happened.
  const ok = window.confirm(
    App.t('cm_mark_paid_q', {
      amount: formatCurrency(entry.amount),
      name: partner.name,
      period: periodLabel(entry),
    }),
  );
  if (!ok) return;
  button.disabled = true;
  try {
    await ApiClient.post('/api/payouts', {
      partner_id: partner.partner_id,
      period_type: entry.period_type,
      period_key: entry.period_key,
    });
    showToast('success', App.t('cm_payout_recorded'),
      `${formatCurrency(entry.amount)} — ${periodLabel(entry)}`);
    await loadSettlement();
    const fresh = state.settlement.find(r => r.partner_id === partner.partner_id);
    if (fresh) await openPayoutPanel(fresh);
    else closePayoutPanel();
  } catch (e) {
    button.disabled = false;
    showToast('error', App.t('cm_payout_not_recorded'), e.message || App.t('cm_try_again'));
  }
}

async function reversePayout(partner, payout, button) {
  const ok = window.confirm(
    App.t('cm_reverse_q', { amount: formatCurrency(payout.amount), period: periodLabel(payout) })
    + App.t('cm_reverse_hint'),
  );
  if (!ok) return;
  button.disabled = true;
  try {
    await ApiClient.post(`/api/payouts/${payout.id}/reverse`, {});
    showToast('success', App.t('cm_payout_reversed'), periodLabel(payout));
    await loadSettlement();
    const fresh = state.settlement.find(r => r.partner_id === partner.partner_id)
      || { partner_id: partner.partner_id, name: partner.name, company: partner.company };
    await openPayoutPanel(fresh);
  } catch (e) {
    button.disabled = false;
    showToast('error', App.t('cm_payout_not_reversed'), e.message || App.t('cm_try_again'));
  }
}

async function loadLedger() {
  setLedgerState('loading');
  try {
    const [leadsData, stagesData] = await Promise.all([
      ApiClient.get('/api/leads'),
      ApiClient.get('/api/lead-stages'),
    ]);
    ledgerStagePipeline = Ui.buildStagePipeline(stagesData.stages || []);
    const leads = (leadsData.leads || []).filter(l => l.commission != null);
    state.ledger = leads;
    renderLedger(leads);
    setLedgerState(leads.length ? 'populated' : 'empty');
  } catch (e) {
    console.error('loadLedger', e);
    setLedgerState('error');
  }
}

function setLedgerState(uiState) {
  const table = document.getElementById('ledger-table');
  const empty = document.getElementById('ledger-empty-state');
  const error = document.getElementById('ledger-error-state');
  if (table) table.hidden = uiState === 'error' || uiState === 'empty';
  if (empty) empty.hidden = uiState !== 'empty';
  if (error) error.hidden = uiState !== 'error';
}

function setupLedgerRetry() {
  const retry = document.getElementById('ledger-retry-btn');
  if (retry && !commissionActionRoots.has(retry)) {
    commissionActionRoots.add(retry);
    retry.addEventListener('click', () => void loadLedger());
  }
}

function renderLedger(leads) {
  const tbody = document.getElementById('ledger-tbody');
  const totalEl = document.getElementById('ledger-total');
  if (!tbody) return;

  let total = 0;
  tbody.innerHTML = leads.map(lead => {
    const commission = finiteValue(lead.commission);
    total += commission;
    const leadId = escHtml(String(lead.id ?? ''));
    return `<tr data-lead-id="${leadId}">
      <td>${leadId || '—'}</td>
      <td>${escHtml(lead.client_name || '—')}</td>
      <td>${escHtml(lead.partner_name || '—')}</td>
      <td>${finiteValue(lead.value).toLocaleString(ledgerLocale())} PLN</td>
      <td>${commission.toLocaleString(ledgerLocale())} PLN</td>
      <td class="ledger-status-cell"></td>
    </tr>`;
  }).join('');

  // The badge is DOM-appended, not string-interpolated: a producer-supplied
  // stage name can never enter this row's HTML string sink, so a markup
  // character in a stage name renders as literal text.
  const rows = tbody.querySelectorAll('tr[data-lead-id]');
  leads.forEach((lead, index) => {
    const cell = rows[index]?.querySelector('.ledger-status-cell');
    if (cell) cell.appendChild(Ui.stageBadge(Ui.stageFor(ledgerStagePipeline, lead.status)));
  });

  if (totalEl) totalEl.textContent = leads.length ? `${total.toLocaleString(ledgerLocale())} PLN` : '—';
}

// ═══════════════════════════════════════════════════════════════
//  SLIDE PANEL
// ═══════════════════════════════════════════════════════════════
function setupSlidePanel() {
  document.getElementById('slide-overlay')?.addEventListener('click', closeAnyPanel);
  document.getElementById('slide-panel-close')?.addEventListener('click', closePanel);
  // "Cancel" in the panel footer.
  document.getElementById('panel-cancel-btn')?.addEventListener('click', closePanel);
  document.getElementById('panel-save-btn')?.addEventListener('click', savePartnerPanel);
}

async function openPartnerPanel(partnerId) {
  state.openPartnerId = partnerId;
  try {
    const data = await ApiClient.get(`/api/partner-bonuses/${partnerId}`);
    state.partnerDetail = data.partner;
    renderSlidePanel(data.partner);
    document.getElementById('slide-overlay').classList.add('open');
    document.getElementById('slide-panel').classList.add('open');
  } catch(e) { showToast('error', App.t('cm_error'), e.message); }
}

// The overlay is shared by the tier/bonus panel and the payout panel, so it
// has to close whichever one is actually open.
function closeAnyPanel() {
  closePanel();
  closePayoutPanel();
}

function closePanel() {
  document.getElementById('slide-overlay').classList.remove('open');
  document.getElementById('slide-panel').classList.remove('open');
  state.openPartnerId = null;
}

function renderSlidePanel(partner) {
  const nameEl = document.getElementById('panel-partner-name');
  const subEl  = document.getElementById('panel-partner-sub');
  if (nameEl) nameEl.textContent = partner.name || '—';
  // The tier name follows the reader's language; the company is
  // the partner's own registered name and is never translated.
  if (subEl)  subEl.textContent  = `${partner.company || ''} · ${Ui.producerLabel(partner, 'tier_name') || '—'}`;

  // Commission summary
  renderCommissionSummary(partner);

  // Tier select
  const tierSel = document.getElementById('panel-tier-select');
  if (tierSel) {
    tierSel.replaceChildren(...state.tiers.map(t => {
      const option = document.createElement('option');
      option.value = String(safePositiveId(t.id) ?? '');
      option.textContent = `${t.name || '—'} (${finiteValue(t.base_rate)}%)`;
      option.selected = safePositiveId(t.id) === safePositiveId(partner.tier_id);
      return option;
    }));
    tierSel.onchange = () => renderCommissionSummaryFromPanel();
  }

  // Bonus toggles
  renderBonusToggles(partner);
}

function renderCommissionSummary(partner) {
  const box = document.getElementById('commission-summary-box');
  if (!box) return;

  const bonusPctItems = partner.bonus_percent || [];
  const bonusCashItems = partner.bonus_cash || [];
  const totalPct  = finiteValue(partner.total_percent);
  const totalCash = finiteValue(partner.total_cash);

  let rows = `<div class="commission-summary-row"><span>${escHtml(App.t('cm_base_rate', { tier: Ui.producerLabel(partner, 'tier_name') || '—' }))}</span><span class="val">${finiteValue(partner.base_rate)}%</span></div>`;
  bonusPctItems.forEach(b => {
    rows += `<div class="commission-summary-row"><span style="color:var(--text-secondary)">+ ${escHtml(Ui.producerLabel(b, 'name'))}</span><span class="val" style="color:var(--brand-400)">+${finiteValue(b.effective_value).toFixed(2)}%</span></div>`;
  });
  bonusCashItems.forEach(b => {
    rows += `<div class="commission-summary-row"><span style="color:var(--text-secondary)">+ ${escHtml(Ui.producerLabel(b, 'name'))}</span><span class="val" style="color:var(--success-400)">+${amountText(finiteValue(b.effective_value))} PLN</span></div>`;
  });
  rows += `<div class="commission-summary-row total"><span>${escHtml(App.t('cm_total'))}</span><div style="text-align:right"><span class="val">${totalPct.toFixed(2)}%</span>${totalCash > 0 ? `<div style="font-size:0.75rem;color:var(--success-400)">+${amountText(totalCash)} PLN</div>` : ''}</div></div>`;

  box.innerHTML = `<div class="commission-summary-title">${App.t("kpi_commissions")}</div>${rows}`;
}

function renderBonusToggles(partner) {
  const container = document.getElementById('bonus-toggles-container');
  if (!container) return;

  const activePercentIds = new Set((partner.bonus_percent || []).map(b => b.bonus_def_id));
  const activeCashIds    = new Set((partner.bonus_cash   || []).map(b => b.bonus_def_id));
  const activeMap = {};
  [...(partner.bonus_percent || []), ...(partner.bonus_cash || [])].forEach(b => {
    activeMap[b.bonus_def_id] = b;
  });

  const percentDefs = state.bonusDefs.filter(b => b.type === 'percent' && b.active);
  const cashDefs    = state.bonusDefs.filter(b => b.type === 'cash'    && b.active);

  const renderSection = (defs, activeIds, unit, sectionLabel) => {
    if (!defs.length) return '';
    return `<div class="bonus-section-title">${sectionLabel}</div>` +
      defs.map(b => {
        const bonusId = safePositiveId(b.id);
        const isActive    = activeIds.has(b.id);
        const assignment  = activeMap[b.id];
        const customVal   = assignment && assignment.custom_value != null ? assignment.custom_value : b.default_value;
        return `
          <label class="bonus-toggle-item">
            <input type="checkbox" class="bonus-toggle-check"
                   data-bonus-id="${bonusId ?? ''}"
                   data-type="${b.type === 'cash' ? 'cash' : 'percent'}"
                   ${isActive ? 'checked' : ''}
                   >
            <div class="bonus-toggle-info">
              <div class="bonus-toggle-name">${escHtml(Ui.producerLabel(b, 'name'))}</div>
              ${Ui.producerLabel(b, 'description') ? `<div class="bonus-toggle-desc">${escHtml(Ui.producerLabel(b, 'description'))}</div>` : ''}
            </div>
            <div class="bonus-toggle-value">
              <input type="number"
                     class="bonus-custom-input"
                     data-bonus-id="${bonusId ?? ''}"
                     data-default="${finiteValue(b.default_value)}"
                     value="${finiteValue(customVal)}"
                     min="0"
                     step="${b.type === 'percent' ? '0.1' : '1'}"
                     ${isActive ? '' : 'disabled'}>
              <span class="bonus-unit-label">${unit}</span>
            </div>
          </label>`;
      }).join('');
  };

  container.innerHTML =
    renderSection(percentDefs, activePercentIds, '%', App.t('cm_percentage_bonuses')) +
    renderSection(cashDefs,    activeCashIds,    'PLN', App.t('cm_cash_bonuses'));
  bindCommissionActions(container);
}

async function onBonusToggle(checkbox, partnerId) {
  const bonusDefId = parseInt(checkbox.dataset.bonusId);
  const valueInput = document.querySelector(`.bonus-custom-input[data-bonus-id="${bonusDefId}"]`);
  if (valueInput) valueInput.disabled = !checkbox.checked;

  try {
    if (checkbox.checked) {
      const customVal = valueInput ? parseFloat(valueInput.value) : null;
      const data = await ApiClient.post(`/api/partner-bonuses/${partnerId}/assign`, {
        bonus_def_id: bonusDefId,
        custom_value: customVal,
      });
      state.partnerDetail = data.partner;
    } else {
      const data = await ApiClient.delete(`/api/partner-bonuses/${partnerId}/bonus/${bonusDefId}`);
      state.partnerDetail = data.partner;
    }
    renderCommissionSummary(state.partnerDetail);
    await loadPartners();
  } catch(e) {
    showToast('error', App.t('cm_error'), e.message);
    checkbox.checked = !checkbox.checked;
    if (valueInput) valueInput.disabled = !checkbox.checked;
  }
}

function confirmRateChange({ tierName, oldRate, newRate, today }) {
  const overlay = document.getElementById('rate-confirm-overlay');
  if (!overlay) return Promise.resolve(true);

  // One sentence with placeholders, not a template with holes cut in it:
  // the clause order differs between the two languages.
  document.getElementById('rate-confirm-text').textContent = App.t('cm_rate_change_confirm', {
    tier: tierName, old: String(oldRate), new: String(newRate), date: today,
  });
  overlay.hidden = false;
  overlay.style.display = 'flex';

  return new Promise(resolve => {
    const confirmBtn = document.getElementById('rate-confirm-confirm');
    const cancelBtn = document.getElementById('rate-confirm-cancel');
    const finish = result => {
      overlay.hidden = true;
      overlay.style.display = 'none';
      confirmBtn.removeEventListener('click', onConfirm);
      cancelBtn.removeEventListener('click', onCancel);
      resolve(result);
    };
    const onConfirm = () => finish(true);
    const onCancel = () => finish(false);
    confirmBtn.addEventListener('click', onConfirm);
    cancelBtn.addEventListener('click', onCancel);
  });
}

async function savePartnerPanel() {
  if (!state.openPartnerId) return;
  // Save tier change
  const tierSel = document.getElementById('panel-tier-select');
  if (tierSel) {
    const tierId = parseInt(tierSel.value);
    const tierChanged = state.partnerDetail && safePositiveId(state.partnerDetail.tier_id) !== tierId;
    if (tierChanged) {
      const newTier = state.tiers.find(t => safePositiveId(t.id) === tierId);
      const oldRate = finiteValue(state.partnerDetail.base_rate);
      const newRate = newTier ? finiteValue(newTier.base_rate) : oldRate;
      const tierName = newTier ? newTier.name : (tierSel.selectedOptions[0]?.textContent || '—');
      const today = new Date().toISOString().slice(0, 10);
      const confirmed = await confirmRateChange({ tierName, oldRate, newRate, today });
      if (!confirmed) return;
    }
    try {
      const data = await ApiClient.post(`/api/partner-bonuses/${state.openPartnerId}/tier`, { tier_id: tierId });
      state.partnerDetail = data.partner;
      renderCommissionSummary(data.partner);
      showToast('success', App.t('st_saved'), App.t('cm_tier_updated'));
      await loadPartners();
    } catch(e) { showToast('error', App.t('cm_error'), e.message); return; }
  }

  // Save custom values for all checked bonuses
  const checkedBoxes = document.querySelectorAll('.bonus-toggle-check:checked');
  for (const cb of checkedBoxes) {
    const bonusDefId = parseInt(cb.dataset.bonusId);
    const valueInput = document.querySelector(`.bonus-custom-input[data-bonus-id="${bonusDefId}"]`);
    if (!valueInput) continue;
    const customVal = parseFloat(valueInput.value);
    const defaultVal = parseFloat(valueInput.dataset.default);
    if (Math.abs(customVal - defaultVal) > 0.001) {
      try {
        await ApiClient.post(`/api/partner-bonuses/${state.openPartnerId}/assign`, {
          bonus_def_id: bonusDefId,
          custom_value: customVal,
        });
      } catch(e) { /* non-fatal */ }
    }
  }

  showToast('success', App.t('st_saved'), App.t('cm_pay_updated'));
  closePanel();
  await loadPartners();
}

// ═══════════════════════════════════════════════════════════════
//  BULK BAR
// ═══════════════════════════════════════════════════════════════
// A granted cash bonus is money owed, which only the account owner creates;
// the server refuses it from any other role, so it is not offered.
function isAccountOwnerSession() {
  try {
    return JSON.parse(sessionStorage.getItem('ph_session') || '{}').role === 'admin';
  } catch (_) {
    return false;
  }
}

function setupBulkBar() {
  if (!isAccountOwnerSession()) {
    document.querySelector('#pbulk-action-select option[value="grant_bonus"]')?.remove();
  }
  document.getElementById('pbulk-action-select')?.addEventListener('change', updateBulkBar);
  document.getElementById('pbulk-apply')?.addEventListener('click', applyBulkAction);
  document.getElementById('pbulk-clear')?.addEventListener('click', () => {
    state.selectedPartners.clear();
    renderPartnerTable();
    updateBulkBar();
  });
}

function updateBulkBar() {
  const count  = state.selectedPartners.size;
  const bar    = document.getElementById('partner-bulk-bar');
  const badge  = document.getElementById('pbulk-count-badge');
  const label = document.getElementById('pbulk-count-label');
  if (!bar) return;
  if (badge) badge.textContent = count;
  if (label) label.textContent = App.t('cm_selected_partners_label');
  bar.classList.toggle('visible', count > 0);

  // Populate bonus selects
  // Assignment offers percent bonuses only -- the backend refuses a cash one
  // outright, and offering it here would be offering an action that fails.
  const bonusSel = document.getElementById('pbulk-bonus-select');
  if (bonusSel) {
    bonusSel.replaceChildren(commissionOption('', App.t('select_bonus')),
      ...state.bonusDefs.filter(b => b.active && b.type === 'percent').map(b =>
        commissionOption(safePositiveId(b.id), `${Ui.producerLabel(b, 'name')} (${finiteValue(b.default_value)}%)`)
      ));
  }

  const awardSel = document.getElementById('pbulk-award-select');
  if (awardSel) {
    awardSel.replaceChildren(commissionOption('', App.t('select_bonus')),
      ...state.bonusDefs.filter(b => b.active && b.type === 'cash').map(b =>
        commissionOption(safePositiveId(b.id), `${Ui.producerLabel(b, 'name')} (${amountText(b.default_value)} PLN)`)
      ));
  }

  const tierSel = document.getElementById('pbulk-tier-select');
  if (tierSel) {
    tierSel.replaceChildren(commissionOption('', App.t('select_tier')), ...state.tiers.filter(t => t.active).map(t =>
      commissionOption(safePositiveId(t.id), `${t.name} (${finiteValue(t.base_rate)}%)`)
    ));
  }

  // Only the select the chosen action actually uses. Filling all three and
  // showing all three would offer a bonus list to somebody setting a tier.
  const action = document.getElementById('pbulk-action-select')?.value;
  if (bonusSel) bonusSel.style.display = (action === 'assign_bonus' || action === 'remove_bonus') ? '' : 'none';
  if (awardSel) awardSel.style.display = action === 'grant_bonus' ? '' : 'none';
  if (tierSel)  tierSel.style.display  = action === 'set_tier' ? '' : 'none';
}

async function applyBulkAction() {
  const action  = document.getElementById('pbulk-action-select')?.value;
  const pids    = [...state.selectedPartners];
  if (!pids.length) return;
  if (!action) { showToast('warning', App.t('toast_notice'), App.t('cm_choose_action')); return; }

  const btn = document.getElementById('pbulk-apply');
  btn.disabled = true; btn.textContent = App.t('cm_processing');

  try {
    if (action === 'assign_bonus' || action === 'remove_bonus') {
      const bonusDefId = parseInt(document.getElementById('pbulk-bonus-select')?.value);
      if (!bonusDefId) { showToast('warning', App.t('toast_notice'), App.t('cm_choose_bonus')); return; }
      const data = await ApiClient.post('/api/partner-bonuses/bulk', {
        action,
        partner_ids: pids,
        bonus_def_id: bonusDefId,
      });
      showToast('success', App.t('toast_done'),
        App.t('cm_partners_updated', { count: data.processed })
        + (data.errors?.length ? App.t('cm_with_errors', { errors: data.errors.length }) : '')
      );
    } else if (action === 'grant_bonus') {
      const bonusDefId = parseInt(document.getElementById('pbulk-award-select')?.value);
      if (!bonusDefId) { showToast('warning', App.t('toast_notice'), App.t('cm_choose_bonus')); return; }
      const data = await ApiClient.post('/api/bonus-awards', {
        partner_ids: pids,
        bonus_def_id: bonusDefId,
      });
      const granted = data.awards?.length || 0;
      const sum = (data.awards || []).reduce((total, a) => total + (a.amount || 0), 0);
      showToast('success', App.t('toast_granted'),
        App.t('cm_bonuses_granted', { bonuses: App.pluralize(granted, 'unit_bonus'), amount: amountText(sum) }));
      await loadSettlement();
    } else if (action === 'set_tier') {
      const tierId = parseInt(document.getElementById('pbulk-tier-select')?.value);
      if (!tierId) { showToast('warning', App.t('toast_notice'), App.t('cm_choose_tier')); return; }
      const data = await ApiClient.post('/api/partner-bonuses/bulk', {
        action: 'set_tier',
        partner_ids: pids,
        tier_id: tierId,
      });
      showToast('success', App.t('toast_done'), App.t('cm_tier_updated_for', { count: data.processed }));
    }

    state.selectedPartners.clear();
    await loadPartners();
    updateBulkBar();
  } catch(e) {
    showToast('error', App.t('cm_error'), e.message);
  } finally {
    btn.disabled = false; btn.textContent = App.t('cm_apply');
  }
}

// ─── HELPERS ─────────────────────────────────────────────────
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

function commissionOption(value, label) {
  const option = document.createElement('option');
  option.value = value == null ? '' : String(value);
  option.textContent = String(label ?? '');
  return option;
}

function bindCommissionActions(root) {
  if (commissionActionRoots.has(root)) return;
  commissionActionRoots.add(root);
  root.addEventListener('click', event => {
    const control = event.target.closest('[data-commission-action]');
    if (!control || !root.contains(control)) return;
    const bonusId = safePositiveId(control.dataset.bonusId);
    const partnerId = safePositiveId(control.dataset.partnerId);
    if (control.dataset.commissionAction === 'save-bonus' && bonusId) saveBonus(bonusId);
    if (control.dataset.commissionAction === 'delete-bonus' && bonusId) deleteBonus(bonusId);
    if (control.dataset.commissionAction === 'open-partner' && partnerId) openPartnerPanel(partnerId);
  });
  root.addEventListener('change', event => {
    const control = event.target.closest('.bonus-toggle-check');
    if (control && root.contains(control) && state.openPartnerId) onBonusToggle(control, state.openPartnerId);
  });
}

async function saveBonus(bonusId) {
  const card = document.querySelector(`[data-bonus-id="${bonusId}"]`);
  const bonus = state.bonusDefs.find(item => safePositiveId(item.id) === bonusId);
  if (!card || !bonus) return;
  const name = card.querySelector('[data-field="name"]')?.value.trim() || '';
  const defaultValue = Number(card.querySelector('[data-field="default_value"]')?.value);
  const description = card.querySelector('[data-field="description"]')?.value.trim() || '';
  if (!name || !Number.isFinite(defaultValue)) return;
  try {
    await ApiClient.patch(`/api/bonus-defs/${bonusId}`, {
      name,
      default_value: defaultValue,
      description,
    });
    showToast('success', App.t('st_saved'), App.t('st_bonus_updated', { name }));
    await loadBonusDefs();
  } catch (error) {
    showToast('error', App.t('cm_error'), error.message);
  }
}

async function deleteBonus(bonusId) {
  const bonus = state.bonusDefs.find(item => safePositiveId(item.id) === bonusId);
  if (!bonus || !confirm(App.t('st_bonus_delete_confirm', { name: Ui.producerLabel(bonus, 'name') }))) return;
  try {
    await ApiClient.delete(`/api/bonus-defs/${bonusId}`);
    showToast('success', App.t('cm_removed'), App.t('st_bonus_removed', { name: Ui.producerLabel(bonus, 'name') }));
    await loadBonusDefs();
  } catch (error) {
    showToast('error', App.t('cm_error'), error.message);
  }
}

// ─── GENERIC MODAL ────────────────────────────────────────────
// Usage: openModal({ title, subtitle, fields: [{id, label, type, placeholder, value, step, min, max, required}], submitLabel, onSubmit })
// onSubmit(vals) must return true to close, or false to stay open (on validation error)
function openModal({ title, subtitle = '', fields = [], submitLabel = App.t('st_save'), onSubmit }) {
  // Remove existing modal if any
  document.getElementById('generic-modal-overlay')?.remove();

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

      <!-- Header -->
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

      <!-- Body -->
      <div style="padding:20px 24px">
        <form id="generic-modal-form" autocomplete="off">
          ${fieldsHtml}
        </form>
      </div>

      <!-- Footer -->
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

  // Color picker label sync
  fields.filter(f => f.type === 'color').forEach(f => {
    const inp = document.getElementById(f.id);
    const lbl = document.getElementById(`${f.id}-label`);
    if (inp && lbl) inp.addEventListener('input', () => { lbl.textContent = inp.value; });
  });

  const close = () => overlay.remove();

  document.getElementById('modal-close-btn').addEventListener('click', close);
  document.getElementById('modal-close-btn').addEventListener('mouseenter', event => { event.currentTarget.style.background = 'var(--bg-hover)'; });
  document.getElementById('modal-close-btn').addEventListener('mouseleave', event => { event.currentTarget.style.background = 'transparent'; });
  document.getElementById('modal-cancel-btn').addEventListener('click', close);
  overlay.addEventListener('click', e => { if (e.target === overlay) close(); });

  document.getElementById('modal-submit-btn').addEventListener('click', async () => {
    const btn = document.getElementById('modal-submit-btn');
    const form = document.getElementById('generic-modal-form');

    // HTML5 validation
    if (form && !form.checkValidity()) { form.reportValidity(); return; }

    // Collect values
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
      showToast('error', App.t('cm_error'), e.message);
    } finally {
      if (document.getElementById('modal-submit-btn')) {
        btn.disabled = false;
        btn.innerHTML = escHtml(submitLabel);
      }
    }
  });

  // Focus first input
  setTimeout(() => {
    const first = overlay.querySelector('input[type="text"], input[type="number"]');
    if (first) first.focus();
  }, 50);
}


/* ═══════════════════════════════════════════════════════════════
   TABS, CONFIRMATION AND EVIDENCE

   Same tile navigation as Settings, and the panes are in the order money
   moves: a partner claims a win, the producer agrees to it, it gets paid, the
   payment goes on record.
   ═══════════════════════════════════════════════════════════════ */

function setupCommissionTabs() {
  const tiles = [...document.querySelectorAll('.settings-tile')];
  tiles.forEach((tile) => {
    tile.setAttribute('role', 'tab');
    tile.setAttribute('aria-selected', String(tile.classList.contains('active')));
    tile.addEventListener('click', () => {
      tiles.forEach((other) => {
        other.classList.remove('active');
        other.setAttribute('aria-selected', 'false');
      });
      document.querySelectorAll('.tab-pane').forEach((pane) => pane.classList.remove('active'));
      tile.classList.add('active');
      tile.setAttribute('aria-selected', 'true');
      document.getElementById(tile.dataset.tab)?.classList.add('active');
      // The rails measure real table widths, and a pane that was hidden had
      // none until now.
      Ui.attachTableScroll(document);
    });
  });

  // Deep link: ?tab=evidence opens the payout register directly. The dashboard's
  // commission KPI links straight here, and landing on "Do zatwierdzenia" and
  // then hunting for the right tile is one click too many. Matched against the
  // tiles actually present, so an unknown or crafted value simply does nothing.
  const requested = new URLSearchParams(window.location.search).get('tab');
  if (requested) {
    const wanted = `tab-${requested}`;
    const target = tiles.find((tile) => tile.dataset.tab === wanted);
    if (target && !target.classList.contains('active')) target.click();
  }
}

// ─── WINS AWAITING CONFIRMATION ───────────────────────────────

async function loadPendingWins() {
  try {
    const data = await ApiClient.get('/api/payouts/pending-wins');
    state.pendingWins = Array.isArray(data.wins) ? data.wins : [];
    state.selectedWins.clear();
    renderPendingWins();
  } catch (error) {
    console.error('loadPendingWins', error);
    document.getElementById('wins-error-state')?.removeAttribute('hidden');
  }
}

function renderPendingWins() {
  const tbody = document.getElementById('wins-tbody');
  const empty = document.getElementById('wins-empty-state');
  const table = document.getElementById('wins-table');
  const sub = document.getElementById('wins-tile-sub');
  if (!tbody) return;

  const wins = state.pendingWins;
  // The tile carries the count: this queue is a partner waiting to be paid, and
  // it must be visible without opening the pane.
  if (sub) {
    sub.textContent = wins.length
      ? App.t('cm_wins_waiting', { wins: App.pluralize(wins.length, 'unit_win') })
      : App.t('cm_wins_claimed_sub');
  }

  tbody.replaceChildren();
  if (table) table.hidden = !wins.length;
  if (empty) empty.hidden = !!wins.length;

  for (const win of wins) {
    const row = document.createElement('tr');

    const pick = document.createElement('td');
    const box = document.createElement('input');
    box.type = 'checkbox';
    box.className = 'row-check';
    box.checked = state.selectedWins.has(win.id);
    box.setAttribute('aria-label', App.t('cm_confirm_win_for', { name: win.company || win.id }));
    box.addEventListener('change', () => {
      if (box.checked) state.selectedWins.add(win.id);
      else state.selectedWins.delete(win.id);
      updateWinsBar();
    });
    pick.appendChild(box);

    const client = document.createElement('td');
    const clientName = document.createElement('div');
    clientName.className = 'cell-primary';
    clientName.textContent = win.company || '—';
    const contact = document.createElement('div');
    contact.className = 'text-xs text-tertiary';
    contact.textContent = win.client_name || '';
    client.append(clientName, contact);

    const partner = document.createElement('td');
    partner.textContent = win.partner_company || win.partner_name || '—';

    const value = document.createElement('td');
    value.textContent = formatCurrency(win.value);

    const commission = document.createElement('td');
    commission.className = 'cell-primary';
    // A win with no recorded commission is said so plainly rather than shown as
    // 0 PLN, which would read as "this one is worth nothing".
    commission.textContent = win.commission == null
      ? App.t('cm_not_calculated') : formatCurrency(win.commission);

    const when = document.createElement('td');
    when.className = 'text-tertiary text-sm';
    when.textContent = win.won_at ? formatDate(win.won_at) : '—';

    row.append(pick, client, partner, value, commission, when);
    tbody.appendChild(row);
  }
  updateWinsBar();
  Ui.attachTableScroll(document);
}

function updateWinsBar() {
  const button = document.getElementById('confirm-wins-btn');
  const all = document.getElementById('wins-select-all');
  const count = state.selectedWins.size;
  if (button) {
    button.hidden = count === 0;
    button.textContent = App.t('cm_confirm_selected', { count });
  }
  if (all) {
    all.checked = count > 0 && count === state.pendingWins.length;
    all.indeterminate = count > 0 && count < state.pendingWins.length;
  }
}

function setupWins() {
  document.getElementById('wins-retry-btn')?.addEventListener('click', () => void loadPendingWins());
  document.getElementById('wins-select-all')?.addEventListener('change', (event) => {
    state.selectedWins.clear();
    if (event.target.checked) state.pendingWins.forEach((win) => state.selectedWins.add(win.id));
    renderPendingWins();
  });
  document.getElementById('confirm-wins-btn')?.addEventListener('click', () => void confirmSelectedWins());
}

async function confirmSelectedWins() {
  const ids = [...state.selectedWins];
  if (!ids.length) return;
  const total = state.pendingWins
    .filter((win) => state.selectedWins.has(win.id))
    .reduce((sum, win) => sum + (win.commission || 0), 0);
  // Confirming is what makes the money owed, so it is worth one question --
  // naming the amount, not just the count.
  const ok = window.confirm(
    App.t('cm_confirm_wins_q', { wins: App.pluralize(ids.length, 'unit_win_accusative') })
    + App.t('cm_confirm_wins_amount', { amount: formatCurrency(total) }),
  );
  if (!ok) return;
  const button = document.getElementById('confirm-wins-btn');
  if (button) button.disabled = true;
  try {
    const result = await ApiClient.post('/api/payouts/confirm-wins', { lead_ids: ids });
    showToast('success', App.t('toast_confirmed'), App.t('cm_wins_settled', { count: result.confirmed }));
    await Promise.all([loadPendingWins(), loadSettlement()]);
  } catch (error) {
    showToast('error', App.t('toast_not_confirmed'), error.message || App.t('cm_try_again'));
  } finally {
    if (button) button.disabled = false;
  }
}

// ─── EVIDENCE ─────────────────────────────────────────────────

async function loadEvidence(year) {
  try {
    const query = year ? `?year=${encodeURIComponent(year)}` : '';
    state.evidence = await ApiClient.get(`/api/payouts/evidence${query}`);
    renderEvidence();
  } catch (error) {
    console.error('loadEvidence', error);
  }
}

function setupEvidence() {
  document.getElementById('evidence-year')?.addEventListener('change', (event) => {
    void loadEvidence(event.target.value || null);
  });
}

function periodRow(entry) {
  const line = document.createElement('div');
  line.className = 'payout-line';
  const label = document.createElement('div');
  const title = document.createElement('div');
  title.className = 'payout-line-title';
  title.textContent = periodLabel(entry);
  const sub = document.createElement('div');
  sub.className = 'text-xs text-tertiary';
  // What the amount is for: how many deals, for how many partners.
  sub.textContent = App.pluralize(entry.lead_count, 'unit_sale')
    + ' · ' + App.pluralize(entry.partner_count, 'unit_partner');
  label.append(title, sub);
  const amount = document.createElement('div');
  amount.className = 'payout-line-amount';
  amount.textContent = formatCurrency(entry.amount);
  line.append(label, amount);
  return line;
}

function renderEvidence() {
  const data = state.evidence;
  if (!data) return;

  const yearSelect = document.getElementById('evidence-year');
  if (yearSelect && !yearSelect.dataset.filled) {
    yearSelect.replaceChildren();
    const all = document.createElement('option');
    all.value = '';
    all.textContent = App.t('cm_all_years');
    yearSelect.appendChild(all);
    (data.years || []).forEach((year) => {
      const option = document.createElement('option');
      option.value = year;
      option.textContent = year;
      yearSelect.appendChild(option);
    });
    yearSelect.dataset.filled = '1';
  }

  writeText('evidence-summary',
    App.t('cm_paid_in_entries', { amount: formatCurrency(data.total), count: data.entries.length }) + ' '
    + App.plural(data.entries.length, 'unit_position'));

  const fill = (id, rows, renderer, emptyText) => {
    const host = document.getElementById(id);
    if (!host) return;
    host.replaceChildren();
    if (!rows.length) {
      const none = document.createElement('p');
      none.className = 'text-xs text-tertiary';
      none.textContent = emptyText;
      host.appendChild(none);
      return;
    }
    rows.forEach((row) => host.appendChild(renderer(row)));
  };

  fill('evidence-months', data.months || [], periodRow, App.t('cm_nothing_paid'));
  fill('evidence-quarters', data.quarters || [], periodRow, App.t('cm_nothing_paid'));
  fill('evidence-partners', data.partners || [], (row) => {
    const line = document.createElement('div');
    line.className = 'payout-line';
    const label = document.createElement('div');
    const title = document.createElement('div');
    title.className = 'payout-line-title';
    title.textContent = row.company || row.name || '—';
    const sub = document.createElement('div');
    sub.className = 'text-xs text-tertiary';
    sub.textContent = App.pluralize(row.lead_count, 'unit_sale');
    label.append(title, sub);
    const amount = document.createElement('div');
    amount.className = 'payout-line-amount';
    amount.textContent = formatCurrency(row.amount);
    line.append(label, amount);
    return line;
  }, App.t('cm_nobody_paid'));

  const tbody = document.getElementById('evidence-tbody');
  const empty = document.getElementById('evidence-empty-state');
  const table = document.getElementById('evidence-table');
  if (!tbody) return;
  tbody.replaceChildren();
  const entries = data.entries || [];
  if (table) table.hidden = !entries.length;
  if (empty) empty.hidden = !!entries.length;

  for (const entry of entries) {
    const row = document.createElement('tr');
    const paid = document.createElement('td');
    paid.className = 'cell-primary';
    paid.textContent = formatDate(entry.paid_at);
    const partner = document.createElement('td');
    partner.textContent = entry.partner_company || entry.partner_name || '—';
    const deal = document.createElement('td');
    const dealName = document.createElement('div');
    // A bonus line's "company" cell is the producer's own bonus name,
    // snapshotted with an English column just like everywhere else he names
    // something (`company_en`). A commission line's is the CUSTOMER's
    // company name, which is never translated -- so only a bonus entry ever
    // reads through Ui.producerLabel here.
    dealName.textContent = entry.kind === 'bonus'
      ? (Ui.producerLabel(entry, 'company') || '—')
      : (entry.company || '—');
    const dealId = document.createElement('div');
    dealId.className = 'text-xs text-tertiary';
    dealId.textContent = entry.lead_id;
    deal.append(dealName, dealId);
    const value = document.createElement('td');
    value.textContent = formatCurrency(entry.value);
    const amount = document.createElement('td');
    amount.className = 'cell-primary';
    amount.textContent = formatCurrency(entry.amount);
    const won = document.createElement('td');
    won.className = 'text-tertiary text-sm';
    won.textContent = entry.won_at ? formatDate(entry.won_at) : '—';
    row.append(paid, partner, deal, value, amount, won);
    tbody.appendChild(row);
  }
  Ui.attachTableScroll(document);
}
