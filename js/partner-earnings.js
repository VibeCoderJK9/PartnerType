/* ============================================================
   PARTNERTYPE — PARTNER EARNINGS PAGE JS
   Shows the partner's own tier-plus-bonus percentage, the one-time
   cash bonuses, the per-lead commissions, and a single grand total
   for the period. Every write below lands on a DOM text sink -- no
   markup-string assignment exists anywhere in this file.

   This file stays self-contained (it does not call into partner.js)
   so its unit test module -- which loads only api.js and utils.js --
   can exercise it in isolation. The queue-badge text shape below
   mirrors the one in partner.js (new lead, my leads), reimplemented
   locally rather than shared, to keep that isolation possible.
   ============================================================ */

'use strict';

/* Amounts are grouped ("+2 500 PLN", not "+2500 PLN"). Delegates to app.js's
   formatAmount rather than redeclaring it: these are classic scripts sharing
   one global scope, so a second `function formatAmount` here would silently
   replace the shared one for every other screen. The fallback covers unit
   tests, which mount this module without app.js. */
function amountText(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return '0';
  return typeof formatAmount === 'function'
    ? formatAmount(number)
    : new Intl.NumberFormat(App.lang === 'en' ? 'en-GB' : 'pl-PL', { maximumFractionDigits: 0 }).format(number);
}

document.addEventListener('DOMContentLoaded', () => {
  if (document.body.dataset.page === 'partner-earnings') initEarnings();
});

// Resolved where it is used, never at load: App.setLang only re-runs
// applyTranslations over [data-i18n] nodes, so a constant captured here would
// keep whatever language was active when this file was parsed.
const earningsLoadErrorCopy = () => App.t('earn_load_failed');

async function initEarnings() {
  const session = typeof initPartnerPage === 'function'
    ? await initPartnerPage()
    : await Ui.rehydrateSession(ApiClient);
  const partnerId = session && session.id;
  if (!partnerId || session.role !== 'partner') { Ui.safeNavigate('../../index.html'); return; }

  const content = document.getElementById('earnings-content');
  try {
    const [bonusData, leadsData, settingsData, earningsData] = await Promise.all([
      ApiClient.get(`/api/partner-bonuses/${partnerId}`),
      ApiClient.get('/api/leads'),
      ApiClient.get('/api/settings'),
      // Deliberately not fatal: a ledger that fails to load must not blank a
      // screen whose other half loaded fine.
      ApiClient.get('/api/my-earnings').catch(() => null),
    ]);
    const queueShares = (settingsData && settingsData.settings && settingsData.settings.lead_queue_shares) || '100';
    const leads = (leadsData && leadsData.leads) || [];
    const summary = earningsData && earningsData.totals ? earningsData : null;
    // Module scope, not the closure: initEarnings runs again on the retry
    // button, and the hook below is bound once -- capturing this run's
    // variables would leave it redrawing the first attempt's data forever.
    earningsLastRender = { partner: bonusData.partner, session, leads, queueShares };
    renderEarnings(bonusData.partner, session, leads, queueShares, summary);

    // Panels, tables and badges are all built in code here, so a language or
    // theme switch has to draw them again.
    if (!earningsRerenderBound) {
      earningsRerenderBound = true;
      App.onRerender(() => {
        const last = earningsLastRender;
        if (!last) return;
        renderEarnings(last.partner, last.session, last.leads, last.queueShares,
                       earningsLedgerSummary);
      });
    }
  } catch (e) {
    renderEarningsError(content, e);
  }
}

let earningsRerenderBound = false;
let earningsLastRender = null;

function renderEarningsError(content, e) {
  const wrap = document.createElement('div');
  wrap.className = 'empty-state';

  const detail = document.createElement('p');
  detail.className = 'text-tertiary';
  detail.textContent = App.t('earn_load_error', { message: e.message || String(e) });
  wrap.appendChild(detail);

  const shared = document.createElement('p');
  shared.className = 'text-tertiary';
  shared.textContent = earningsLoadErrorCopy();
  wrap.appendChild(shared);

  const retry = document.createElement('button');
  retry.type = 'button';
  retry.id = 'earnings-retry-btn';
  retry.className = 'btn btn-secondary btn-sm';
  retry.textContent = App.t('earn_try_again');
  retry.addEventListener('click', () => { void initEarnings(); });
  wrap.appendChild(retry);

  content.replaceChildren(wrap);
}

// ─── local helpers (deliberately not shared with partner.js -- see header) ─
function finiteNumber(value) {
  const num = Number(value);
  return Number.isFinite(num) ? num : 0;
}

function formatEarningsMoney(value) {
  return new Intl.NumberFormat(App.lang === 'en' ? 'en-GB' : 'pl-PL', { style: 'currency', currency: 'PLN', maximumFractionDigits: 0 })
    .format(finiteNumber(value));
}

// Mirrors the queue-share formula partner.js's computeQueueShare established:
// index = min(max(position, 1), shares.length) - 1.
function earningsQueueShare(position, sharesCsv) {
  const shares = String(sharesCsv ?? '100')
    .split(',')
    .map(part => parseFloat(part.trim()))
    .filter(Number.isFinite);
  if (!shares.length) return 0;
  const index = Math.min(Math.max(position, 1), shares.length) - 1;
  return shares[index];
}

// Mirrors the queue-badge copy shape partner.js's renderQueueBadge established:
// "submission {position} — {share}% of the commission".
function earningsQueueBadge(position, share) {
  const badge = document.createElement('span');
  badge.className = 'badge badge-warning';
  const num = Number(share);
  const shareLabel = Number.isFinite(num) ? String(Math.round(num * 100) / 100) : '0';
  badge.textContent = App.t('earn_queue_position', { position, share: shareLabel });
  return badge;
}

/* On a money screen the sales stage is the wrong question anyway. What a
   partner wants to know about a row here is where his money is, and that has
   exactly four answers, all of them read from the same columns api/payouts.py
   settles against. */
const EARNINGS_STATE_CLASSES = {
  paid: 'badge-success', due: 'badge-info', awaiting: 'badge-warning',
  pipeline: 'badge-neutral', rejected: 'badge-error',
};

function earningsPayoutState(lead) {
  if (!lead) return 'pipeline';
  if (lead.commission_paid_at) return 'paid';
  if (lead.registration_status === 'rejected') return 'rejected';
  // A win the producer has not confirmed is a claim, not a debt.
  if (lead.won_at && !lead.win_confirmed_at) return 'awaiting';
  if (lead.commission != null) return 'due';
  return 'pipeline';
}

function earningsStateBadge(lead) {
  const state = earningsPayoutState(lead);
  const badge = document.createElement('span');
  badge.className = `badge ${EARNINGS_STATE_CLASSES[state] || 'badge-neutral'}`;
  badge.textContent = App.t('earn_state_' + state);
  return badge;
}

function buildSummaryRow(label, value, labelStyle, valueStyle) {
  const row = document.createElement('div');
  row.className = 'commission-summary-row';
  const labelEl = document.createElement('span');
  if (labelStyle) labelEl.style.cssText = labelStyle;
  labelEl.textContent = label;
  const valueEl = document.createElement('span');
  valueEl.className = 'val';
  if (valueStyle) valueEl.style.cssText = valueStyle;
  valueEl.textContent = value;
  row.append(labelEl, valueEl);
  return row;
}

function buildTotalRow(label, value, valueId) {
  const row = document.createElement('div');
  row.className = 'commission-summary-row total';
  const labelEl = document.createElement('span');
  labelEl.textContent = label;
  const valWrap = document.createElement('div');
  valWrap.style.textAlign = 'right';
  const valEl = document.createElement('div');
  valEl.className = 'val';
  valEl.id = valueId;
  valEl.style.fontSize = '1.4rem';
  valEl.textContent = value;
  valWrap.appendChild(valEl);
  row.append(labelEl, valWrap);
  return row;
}

// ─── page sections ──────────────────────────────────────────────────────
function buildEarningsPageHeader() {
  const header = document.createElement('div');
  header.className = 'page-header';
  const inner = document.createElement('div');
  const h1 = document.createElement('h1');
  h1.className = 'page-heading';
  h1.textContent = App.t('earn_heading');
  const subtitle = document.createElement('p');
  subtitle.className = 'page-subheading';
  subtitle.textContent = App.t('earn_subtitle');
  inner.append(h1, subtitle);
  header.appendChild(inner);
  return header;
}

// Percentage side: single source of truth -- the tier base rate plus
// each active percent bonus, summing to the one percentage total the API
// already computed. This total is rendered exactly once on the whole page.
function buildTierCard(tierName, baseRate, bonusPct, totalPct) {
  const panel = document.createElement('div');
  panel.className = 'panel';

  const panelHeader = document.createElement('div');
  panelHeader.className = 'panel-header';
  const panelTitle = document.createElement('div');
  panelTitle.className = 'panel-title';
  panelTitle.textContent = App.t('earn_rate_card_title');
  panelHeader.appendChild(panelTitle);
  panel.appendChild(panelHeader);

  const body = document.createElement('div');
  body.className = 'panel-body';
  body.style.padding = '24px';

  const top = document.createElement('div');
  top.style.cssText = 'display:flex;align-items:center;gap:16px';
  const icon = document.createElement('div');
  icon.id = 'earnings-tier-icon';
  icon.style.cssText = 'width:56px;height:56px;border-radius:50%;display:flex;align-items:center;justify-content:center';
  Icons.into(icon, 'star', { size: 28 });
  const nameWrap = document.createElement('div');
  const nameEl = document.createElement('div');
  nameEl.id = 'earnings-tier-name';
  nameEl.style.cssText = 'font-size:1.5rem;font-weight:800';
  nameEl.textContent = tierName;
  const baseLine = document.createElement('div');
  baseLine.style.cssText = 'font-size:0.85rem;color:var(--text-tertiary)';
  baseLine.append(`${App.t('st_base_rate')} `);
  const baseStrong = document.createElement('strong');
  baseStrong.textContent = `${baseRate}%`;
  baseLine.appendChild(baseStrong);
  nameWrap.append(nameEl, baseLine);
  top.append(icon, nameWrap);
  body.appendChild(top);

  const summary = document.createElement('div');
  summary.className = 'commission-summary';
  summary.style.marginTop = '20px';
  const summaryTitle = document.createElement('div');
  summaryTitle.className = 'commission-summary-title';
  summaryTitle.textContent = App.t('earn_rate_parts');
  summary.appendChild(summaryTitle);

  summary.appendChild(buildSummaryRow(App.t('cm_base_rate', { tier: tierName }), `${baseRate}%`));
  bonusPct.forEach(b => {
    const val = finiteNumber(b.effective_value != null ? b.effective_value : b.default_value);
    // His name, in the reader's language. Never b.name directly: the English
    // column is a second field he fills in, and an empty one falls back here.
    summary.appendChild(buildSummaryRow(`+ ${Ui.producerLabel(b, 'name')}`, `+${val.toFixed(2)}%`, 'color:var(--text-secondary)', 'color:var(--brand-400)'));
  });
  summary.appendChild(buildTotalRow(App.t('earn_total_rate'), `${totalPct.toFixed(2)}%`, 'earnings-percent-total'));

  body.appendChild(summary);
  panel.appendChild(body);
  return panel;
}

// Cash side: each one-time cash bonus as its own labelled row summing to a
// separate cash total that is never added into the percentage total above.
// The grand total for the period equals the sum of the displayed per-lead
// commissions (locked leads only) plus this cash total, and nothing else.
function buildTotalsCard(bonusCash, totalCash, paidTotal, grandTotal, provisionalCount) {
  const panel = document.createElement('div');
  panel.className = 'panel';

  const panelHeader = document.createElement('div');
  panelHeader.className = 'panel-header';
  const panelTitle = document.createElement('div');
  panelTitle.className = 'panel-title';
  panelTitle.textContent = App.t('earn_cash_and_total');
  panelHeader.appendChild(panelTitle);
  panel.appendChild(panelHeader);

  const body = document.createElement('div');
  body.className = 'panel-body';

  const summary = document.createElement('div');
  summary.className = 'commission-summary';
  summary.style.marginBottom = '0';
  const summaryTitle = document.createElement('div');
  summaryTitle.className = 'commission-summary-title';
  summaryTitle.textContent = App.t('earn_one_off_bonuses');
  summary.appendChild(summaryTitle);

  if (!bonusCash.length) {
    const none = document.createElement('div');
    none.className = 'text-xs text-tertiary';
    none.textContent = App.t('earn_no_cash_bonuses');
    summary.appendChild(none);
  }
  bonusCash.forEach(b => {
    const val = finiteNumber(b.effective_value != null ? b.effective_value : b.default_value);
    summary.appendChild(buildSummaryRow(`+ ${Ui.producerLabel(b, 'name')}`, `+${formatEarningsMoney(val)}`, 'color:var(--text-secondary)', 'color:var(--success-400)'));
  });
  const cashTotalRow = buildSummaryRow(App.t('earn_cash_total'), formatEarningsMoney(totalCash), null, 'color:var(--success-400)');
  cashTotalRow.querySelector('.val').id = 'earnings-cash-total';
  summary.appendChild(cashTotalRow);

  const paidRow = buildSummaryRow(App.t('earn_commission_from_accepted'), formatEarningsMoney(paidTotal));
  paidRow.querySelector('.val').id = 'earnings-paid-total';
  summary.appendChild(paidRow);

  summary.appendChild(buildTotalRow(App.t('earn_period_total'), formatEarningsMoney(grandTotal), 'earnings-grand-total'));

  if (provisionalCount > 0) {
    const note = document.createElement('div');
    note.id = 'earnings-provisional-note';
    note.className = 'text-xs text-tertiary';
    note.style.marginTop = '8px';
    // One string with the count in it, so the plural rule is the dictionary's
    // job rather than a Polish-shaped if/else frozen into the controller.
    note.textContent = App.t('earn_provisional', {
      leads: App.pluralize(provisionalCount, 'unit_lead'),
    });
    summary.appendChild(note);
  }

  body.appendChild(summary);
  panel.appendChild(body);
  return panel;
}

function buildBonusChipsPanel(bonusPct, bonusCash) {
  const panel = document.createElement('div');
  panel.className = 'panel';

  const header = document.createElement('div');
  header.className = 'panel-header';
  const title = document.createElement('div');
  title.className = 'panel-title';
  title.textContent = App.t('earn_active_bonuses');
  const allBonuses = [...bonusPct, ...bonusCash];
  const count = document.createElement('div');
  count.className = 'text-xs text-tertiary';
  count.textContent = App.t('earn_active_count', { count: allBonuses.length });
  header.append(title, count);
  panel.appendChild(header);

  const body = document.createElement('div');
  body.className = 'panel-body';

  if (!allBonuses.length) {
    const empty = document.createElement('div');
    empty.className = 'empty-state';
    empty.style.padding = '24px';
    const icon = document.createElement('div');
    icon.style.cssText = 'margin-bottom:8px;color:var(--text-tertiary)';
    Icons.into(icon, 'banknote', { size: 32 });
    const text = document.createElement('p');
    text.className = 'text-tertiary';
    text.append(
      App.t('earn_no_extra_bonuses'),
      document.createElement('br'),
      App.t('earn_contact_admin'),
    );
    empty.append(icon, text);
    body.appendChild(empty);
  } else {
    const list = document.createElement('div');
    list.style.cssText = 'display:flex;flex-direction:column;gap:10px';
    allBonuses.forEach(b => {
      const isPct = b.type === 'percent';
      const val = finiteNumber(b.effective_value != null ? b.effective_value : b.default_value);
      const chip = document.createElement('div');
      chip.className = `bonus-chip${isPct ? '' : ' cash'}`;
      chip.style.cssText = 'padding:6px 12px;font-size:0.8rem;gap:6px';

      const iconSpan = document.createElement('span');
      iconSpan.style.display = 'inline-flex';
      Icons.into(iconSpan, isPct ? 'percent' : 'banknote', { size: 14 });

      const textWrap = document.createElement('div');
      const nameEl = document.createElement('div');
      nameEl.style.fontWeight = '700';
      nameEl.textContent = Ui.producerLabel(b, 'name');
      const descEl = document.createElement('div');
      descEl.style.cssText = 'font-size:0.68rem;opacity:.7';
      // Read separately from the name: he may have translated one and not the
      // other, and each falls back to its own Polish original.
      descEl.textContent = Ui.producerLabel(b, 'description');
      textWrap.append(nameEl, descEl);

      const valSpan = document.createElement('span');
      valSpan.style.cssText = 'margin-left:auto;font-size:1rem;font-weight:800';
      valSpan.textContent = isPct ? `+${val.toFixed(2)}%` : `+${formatEarningsMoney(val)}`;

      chip.append(iconSpan, textWrap, valSpan);
      list.appendChild(chip);
    });
    body.appendChild(list);
  }

  panel.appendChild(body);
  return panel;
}

// Shows the leads the API returned, exactly as returned -- no rate is ever
// multiplied by a value in this file. A not-yet-locked commission gets an
// explicit "Przewidywana" label instead of a different number.
function buildLeadsPanel(leadList, queueSharesCsv) {
  const panel = document.createElement('div');
  panel.className = 'panel';

  const header = document.createElement('div');
  header.className = 'panel-header';
  header.style.cssText = 'display:flex;align-items:center;justify-content:space-between';
  const titleWrap = document.createElement('div');
  const title = document.createElement('div');
  title.className = 'panel-title';
  title.textContent = App.t('earn_lead_commissions');
  const subtitle = document.createElement('div');
  subtitle.className = 'text-xs text-tertiary';
  subtitle.textContent = App.t('earn_lead_list_subtitle');
  titleWrap.append(title, subtitle);

  const exportLink = document.createElement('a');
  exportLink.href = '/api/export/commissions';
  exportLink.id = 'earnings-export-link';
  exportLink.className = 'btn-export';
  exportLink.setAttribute('download', '');
  exportLink.setAttribute('aria-label', App.t('earn_export_csv_aria'));
  const exportText = document.createElement('span');
  exportText.textContent = App.t('earn_export_csv');
  exportLink.appendChild(exportText);

  header.append(titleWrap, exportLink);
  panel.appendChild(header);

  const body = document.createElement('div');
  body.className = 'panel-body';
  body.style.padding = '0';

  if (!leadList.length) {
    const empty = document.createElement('div');
    empty.id = 'earnings-leads-empty';
    empty.className = 'empty-state';
    empty.style.padding = '24px';
    const heading = document.createElement('h3');
    heading.className = 'text-sm font-medium text-primary';
    heading.textContent = App.t('earn_none_this_period');
    const text = document.createElement('p');
    text.className = 'text-sm text-secondary';
    text.textContent = App.t('earn_submit_to_see');
    empty.append(heading, text);
    body.appendChild(empty);
  } else {
    const tableWrap = document.createElement('div');
    tableWrap.style.overflowX = 'auto';
    const table = document.createElement('table');
    table.className = 'data-table';
    const thead = document.createElement('thead');
    const headRow = document.createElement('tr');
    [App.t('klient_firma'), App.t('earn_value'), App.t('status'), App.t('earn_commission')].forEach(text => {
      const th = document.createElement('th');
      th.textContent = text;
      headRow.appendChild(th);
    });
    thead.appendChild(headRow);

    const tbody = document.createElement('tbody');
    tbody.id = 'earnings-leads-tbody';

    leadList.forEach(lead => {
      const row = document.createElement('tr');

      const clientCell = document.createElement('td');
      const nameLine = document.createElement('div');
      nameLine.className = 'cell-primary';
      nameLine.textContent = lead.client_name || lead.company
        || App.t('earn_lead_fallback', { id: lead.id });
      clientCell.appendChild(nameLine);
      const position = Number(lead.queue_position || 1);
      if (position > 1) {
        const share = earningsQueueShare(position, queueSharesCsv);
        const badgeWrap = document.createElement('div');
        badgeWrap.style.marginTop = '4px';
        badgeWrap.appendChild(earningsQueueBadge(position, share));
        clientCell.appendChild(badgeWrap);
      }

      const valueCell = document.createElement('td');
      valueCell.className = 'cell-primary';
      valueCell.textContent = formatEarningsMoney(lead.value);

      const statusCell = document.createElement('td');
      statusCell.appendChild(earningsStateBadge(lead));

      const commissionCell = document.createElement('td');
      if (lead.commission == null) {
        const badge = document.createElement('span');
        badge.className = 'badge badge-neutral';
        badge.textContent = App.t('pt_projected');
        commissionCell.appendChild(badge);
      } else {
        const val = document.createElement('span');
        val.style.cssText = 'color:var(--success-400);font-weight:600';
        val.textContent = formatEarningsMoney(lead.commission);
        commissionCell.appendChild(val);
      }

      row.append(clientCell, valueCell, statusCell, commissionCell);
      tbody.appendChild(row);
    });

    table.append(thead, tbody);
    tableWrap.appendChild(table);
    body.appendChild(tableWrap);
  }

  panel.appendChild(body);
  return panel;
}

// ─── SETTLEMENTS OVER TIME ────────────────────────────────────────────────
//
// For a chosen month, quarter, year or in total: what was paid, what waits
// for payout, what the producer marked as paid, and what is still queued.
//
// Every figure comes from GET /api/my-earnings, which derives them from the
// same columns the producer's own payout screens settle against -- so the two
// sides of one deal can never disagree about what is owed. Nothing on this
// side adds money up from leads.
//
// The four columns are deliberately NOT merged:
//   Paid out          handed over, counted by the date of the payout
//   Awaiting payout   confirmed win, not paid, counted by the date of the win
//   Do potwierdzenia  a win the partner reported and the producer has not
//                     agreed to yet -- a claim, not a debt
//   W lejku           the forecast only; it has no period, so it is a total
//
// Bucketing by two different dates in one row is a deliberate choice, not an
// oversight: "what did I receive in September" and "what did September's deals
// earn me" are different questions, and the column hints say which is which.

const EARNINGS_PERIODS = ['month', 'quarter', 'year', 'all'];
let earningsLedgerPeriod = 'month';
let earningsLedgerSummary = null;

function ledgerRowTotal(row) {
  return finiteNumber(row.paid) + finiteNumber(row.due) + finiteNumber(row.awaiting);
}

// "Total" is one row built from the totals rather than a sum of the period
// rows: a lead with no usable date is counted in the totals and in no period,
// so adding the rows up would quietly under-report it.
function ledgerRows(summary) {
  if (!summary) return [];
  if (earningsLedgerPeriod === 'all') {
    const totals = summary.totals || {};
    return [{ period_key: App.t('earn_row_all'), paid: totals.paid, due: totals.due,
              awaiting: totals.awaiting }];
  }
  return (summary.periods && summary.periods[earningsLedgerPeriod]) || [];
}

function buildLedgerKpi(labelKey, value, hintKey, tone) {
  const tile = document.createElement('div');
  tile.className = 'earn-kpi';
  const label = document.createElement('div');
  label.className = 'earn-kpi-label';
  label.textContent = App.t(labelKey);
  // A native title, not a tooltip widget: the hint explains which date the
  // figure is counted by, which matters only when somebody asks.
  tile.title = App.t(hintKey);
  const amount = document.createElement('div');
  amount.className = `earn-kpi-value${tone ? ' ' + tone : ''}`;
  amount.textContent = formatEarningsMoney(value);
  tile.append(label, amount);
  return tile;
}

function buildLedgerTable(summary) {
  const rows = ledgerRows(summary);
  const wrap = document.createElement('div');
  wrap.id = 'earnings-ledger-table';
  wrap.style.overflowX = 'auto';

  if (!rows.length) {
    const empty = document.createElement('p');
    empty.className = 'text-sm text-tertiary';
    empty.style.padding = '16px';
    empty.textContent = App.t('earn_ledger_empty');
    wrap.appendChild(empty);
    return wrap;
  }

  const table = document.createElement('table');
  table.className = 'data-table';
  const thead = document.createElement('tr');
  [['earn_col_period', null], ['earn_col_paid', 'earn_paid_hint'],
   ['earn_col_due', 'earn_due_hint'], ['earn_col_awaiting', 'earn_awaiting_hint'],
   ['earn_col_total', null]].forEach(([key, hint]) => {
    const th = document.createElement('th');
    th.textContent = App.t(key);
    if (hint) th.title = App.t(hint);
    thead.appendChild(th);
  });

  const tbody = document.createElement('tbody');
  tbody.id = 'earnings-ledger-tbody';
  rows.forEach(row => {
    const tr = document.createElement('tr');
    const period = document.createElement('td');
    period.className = 'cell-primary';
    period.textContent = row.period_key;
    tr.appendChild(period);
    [['paid', 'var(--success-400)'], ['due', null], ['awaiting', 'var(--text-tertiary)']]
      .forEach(([field, colour]) => {
        const cell = document.createElement('td');
        cell.textContent = formatEarningsMoney(row[field]);
        if (colour && finiteNumber(row[field]) > 0) cell.style.color = colour;
        tr.appendChild(cell);
      });
    const total = document.createElement('td');
    total.className = 'cell-primary';
    total.textContent = formatEarningsMoney(ledgerRowTotal(row));
    tr.appendChild(total);
    tbody.appendChild(tr);
  });

  const head = document.createElement('thead');
  head.appendChild(thead);
  table.append(head, tbody);
  wrap.appendChild(table);
  return wrap;
}

function buildPeriodPicker(panel, summary) {
  const group = document.createElement('div');
  group.className = 'view-toggle';
  group.setAttribute('role', 'group');
  group.setAttribute('aria-label', App.t('earn_period_picker'));
  EARNINGS_PERIODS.forEach(period => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'view-toggle-btn';
    button.dataset.earningsPeriod = period;
    button.textContent = App.t('earn_period_' + period);
    button.setAttribute('aria-pressed', String(period === earningsLedgerPeriod));
    button.addEventListener('click', () => {
      earningsLedgerPeriod = period;
      group.querySelectorAll('[data-earnings-period]').forEach(other => {
        other.setAttribute('aria-pressed', String(other.dataset.earningsPeriod === period));
      });
      // Only the table is rebuilt: rebuilding the panel would move focus off
      // the button that was just pressed.
      panel.querySelector('#earnings-ledger-table')?.replaceWith(buildLedgerTable(summary));
    });
    group.appendChild(button);
  });
  return group;
}

function buildLedgerPanel(summary) {
  const panel = document.createElement('div');
  panel.className = 'panel';
  panel.id = 'earnings-ledger';

  const header = document.createElement('div');
  header.className = 'panel-header';
  header.style.cssText = 'display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap';
  const titleWrap = document.createElement('div');
  const title = document.createElement('div');
  title.className = 'panel-title';
  title.textContent = App.t('earn_ledger_title');
  const subtitle = document.createElement('div');
  subtitle.className = 'text-xs text-tertiary';
  subtitle.textContent = App.t('earn_ledger_subtitle');
  titleWrap.append(title, subtitle);
  header.append(titleWrap, buildPeriodPicker(panel, summary));
  panel.appendChild(header);

  const body = document.createElement('div');
  body.className = 'panel-body';
  body.style.padding = '0';

  const totals = (summary && summary.totals) || {};
  const kpis = document.createElement('div');
  kpis.className = 'earn-kpi-grid';
  kpis.append(
    buildLedgerKpi('earn_kpi_paid', totals.paid, 'earn_paid_hint', 'is-paid'),
    buildLedgerKpi('earn_kpi_due', totals.due, 'earn_due_hint', 'is-due'),
    buildLedgerKpi('earn_kpi_awaiting', totals.awaiting, 'earn_awaiting_hint'),
    buildLedgerKpi('earn_kpi_pipeline', totals.pipeline, 'earn_pipeline_hint'),
  );
  body.appendChild(kpis);
  body.appendChild(buildLedgerTable(summary));
  panel.appendChild(body);
  return panel;
}

// The producer's own receipts, read-only. A partner asking "did he actually
// mark September as paid?" gets the answer here rather than having to infer it
// from a total that moved.
function buildPayoutsPanel(summary) {
  const panel = document.createElement('div');
  panel.className = 'panel';

  const header = document.createElement('div');
  header.className = 'panel-header';
  const title = document.createElement('div');
  title.className = 'panel-title';
  title.textContent = App.t('earn_payouts_title');
  header.appendChild(title);
  panel.appendChild(header);

  const body = document.createElement('div');
  body.className = 'panel-body';
  const payouts = (summary && summary.payouts) || [];

  if (!payouts.length) {
    const empty = document.createElement('p');
    empty.className = 'text-sm text-tertiary';
    empty.textContent = App.t('earn_payouts_empty');
    body.appendChild(empty);
  } else {
    const list = document.createElement('div');
    list.style.cssText = 'display:flex;flex-direction:column;gap:10px';
    payouts.forEach(payout => {
      const row = document.createElement('div');
      row.className = 'commission-summary-row';
      const label = document.createElement('span');
      const periodKey = payout.period_type === 'quarter' ? 'earn_payout_quarter' : 'earn_payout_month';
      label.textContent = `${App.t(periodKey, { key: payout.period_key })} · ${formatEarningsDate(payout.paid_at)}`;
      const value = document.createElement('span');
      value.className = 'val';
      value.style.color = 'var(--success-400)';
      value.textContent = formatEarningsMoney(payout.amount);
      row.append(label, value);
      list.appendChild(row);
    });
    body.appendChild(list);
  }

  panel.appendChild(body);
  return panel;
}

// Intl, never a hardcoded month list -- the same rule datepicker.js follows.
function formatEarningsDate(iso) {
  if (!iso) return '—';
  const date = new Date(/(?:Z|[+-]\d{2}:?\d{2})$/.test(String(iso)) ? iso : `${iso}Z`);
  if (Number.isNaN(date.getTime())) return '—';
  return new Intl.DateTimeFormat(App.lang === 'en' ? 'en-GB' : 'pl-PL',
    { day: '2-digit', month: 'short', year: 'numeric' }).format(date);
}

function renderEarnings(p, session, leads = [], queueSharesCsv = '100', summary = null) {
  earningsLedgerSummary = summary;
  const tierColor = /^#[0-9a-f]{6}$/i.test(p.color || '') ? p.color : '#6366f1';
  // Follows the reader's language, same rule as the bonus names
  // on this screen (Ui.producerLabel(b, 'name') below).
  const tierName  = Ui.producerLabel(p, 'tier_name') || App.t('ap_no_tier');
  const baseRate  = finiteNumber(p.base_rate);
  const totalPct  = finiteNumber(p.total_percent);
  const totalCash = finiteNumber(p.total_cash);
  const bonusPct  = Array.isArray(p.bonus_percent) ? p.bonus_percent : [];
  const bonusCash = Array.isArray(p.bonus_cash) ? p.bonus_cash : [];
  const leadList  = Array.isArray(leads) ? leads : [];

  const lockedLeads = leadList.filter(l => l && l.commission != null);
  const provisionalLeads = leadList.filter(l => l && l.commission == null);
  // With the server's summary the card states the same money the ledger
  // below it does: everything earned so far -- paid, due, and wins awaiting
  // confirmation -- split into commission and cash bonuses. Summing the leads
  // here instead dropped every cash bonus already paid and disagreed with the
  // ledger on the same screen.
  // The lead sum stays only as the fallback when the summary cannot be read.
  const t = summary && summary.totals;
  const cashEarned = t ? finiteNumber(t.paid_bonus) + finiteNumber(t.due_bonus) : totalCash;
  const paidTotal = t
    ? finiteNumber(t.paid) + finiteNumber(t.due) + finiteNumber(t.awaiting) - cashEarned
    : lockedLeads.reduce((sum, l) => sum + finiteNumber(l.commission), 0);
  const grandTotal = paidTotal + cashEarned;

  const root = document.createElement('div');
  root.appendChild(buildEarningsPageHeader());

  const grid = document.createElement('div');
  grid.style.cssText = 'display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-bottom:24px';
  const tierCard = buildTierCard(tierName, baseRate, bonusPct, totalPct);
  const totalsCard = buildTotalsCard(bonusCash, cashEarned, paidTotal, grandTotal, provisionalLeads.length);
  grid.append(tierCard, totalsCard);
  root.appendChild(grid);

  // The ledger sits directly under the two cards: it answers "how much is
  // paid, how much is waiting" before the per-lead detail does.
  if (summary) {
    const ledger = buildLedgerPanel(summary);
    ledger.style.marginBottom = '24px';
    root.appendChild(ledger);
    const payouts = buildPayoutsPanel(summary);
    payouts.style.marginBottom = '24px';
    root.appendChild(payouts);
  }

  root.appendChild(buildBonusChipsPanel(bonusPct, bonusCash));
  root.appendChild(buildLeadsPanel(leadList, queueSharesCsv));

  const content = document.getElementById('earnings-content');
  content.replaceChildren(root);

  const tierIconEl = tierCard.querySelector('#earnings-tier-icon');
  const tierNameEl = tierCard.querySelector('#earnings-tier-name');
  if (tierIconEl) {
    tierIconEl.style.backgroundColor = `${tierColor}22`;
    tierIconEl.style.border = `2px solid ${tierColor}44`;
  }
  // The icon keeps the producer's raw colour (it is a fill), the NAME gets the
  // readable form of it, so the tier name stays legible on the light theme.
  if (tierNameEl) tierNameEl.style.color = Ui.readableInk(tierColor);
}
