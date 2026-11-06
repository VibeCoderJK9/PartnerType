/* ============================================================
   PARTNERTYPE — ANALYTICS PAGE
   ============================================================ */

document.addEventListener('DOMContentLoaded', () => {
  if (document.body.dataset.page !== 'admin-analytics') return;
  void initAnalytics();
  // Charts and the channel-metric cards are built in code, so a language or
  // theme switch only reaches them by drawing the screen again.
  App.onRerender(() => { void initAnalytics(); });
});

async function initAnalytics() {
  try {
    // Only /api/stats now: the donut gets its stage names and colours from
    // stats.pipeline_dist, and renderFunnel (the one consumer of the stage
    // pipeline) went with the Lejek konwersji panel. Keeping the second request
    // in a Promise.all meant a failure or a permission change on
    // /api/lead-stages took the whole screen down for nothing.
    const stats = await ApiClient.get('/api/stats');
    renderAnalyticsKPIs(stats);
    renderChannelMetrics(stats.channel);
    renderAnalyticsCharts(stats);
    renderPartnersTable(stats.partner_ranking || []);
    setupPartnerRanking();
  } catch (err) { handleApiError(err, 'initAnalytics'); }
}

/* ─── CHANNEL HEALTH ─────────────────────────────────
   The standard partner-channel measures, each shown with the definition it is
   computed from. The definition is not decoration: "16% aktywacji" reads as a
   failure until the reader knows it counts every signed partner who has not
   submitted anything yet, and a win rate means nothing until you know whether
   open deals are in the denominator (they are not).

   Anything the backend cannot honestly compute comes back null and is shown as
   an em-dash with the reason, never as a zero -- a zero here would be a claim.
   ──────────────────────────────────────────────────────────── */
/* Grouped amount, delegating to app.js's formatAmount. Declared under its own
   name rather than redeclaring formatAmount: these are classic scripts sharing
   one global scope. The fallback covers unit tests, which mount this module
   without app.js. */
function amountText(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return '0';
  return typeof formatAmount === 'function'
    ? formatAmount(number)
    : new Intl.NumberFormat('pl-PL', { maximumFractionDigits: 0 }).format(number);
}

function channelMetrics() {
  return [
    {
      key: 'activation_rate',
      name: App.t('an_activation'),
      format: (channel) => percent(channel.activation_rate),
      note: (channel) => App.t('an_activation_count', {
        active: channel.partners_activated,
        signed: channel.partners_signed,
      }),
      empty: App.t('an_activation_empty'),
    },
    {
      key: 'approval_rate',
      name: App.t('an_approval'),
      format: (channel) => percent(channel.approval_rate),
      note: (channel) => App.t('an_approval_note', { decided: channel.registrations_decided }),
      empty: App.t('an_approval_empty'),
    },
    {
      key: 'win_rate',
      name: App.t('an_win_rate'),
      format: (channel) => percent(channel.win_rate),
      note: (channel) => App.t('an_win_rate_note',
        { won: channel.deals_won, closed: channel.deals_closed }),
      empty: App.t('an_win_rate_empty'),
    },
    {
      key: 'avg_deal_value',
      name: App.t('an_avg_deal'),
      format: (channel) => formatCurrency(channel.avg_deal_value),
      note: () => App.t('an_avg_deal_note')
        + App.t('an_avg_deal_note2'),
      empty: App.t('an_avg_deal_empty'),
    },
    {
      key: 'revenue_per_active_partner',
      name: App.t('an_rev_per_partner'),
      format: (channel) => formatCurrency(channel.revenue_per_active_partner),
      note: () => App.t('an_rev_per_partner_note')
        + App.t('an_rev_per_partner_note2'),
      empty: App.t('an_rev_per_partner_empty'),
    },
    {
      key: 'channel_cost_rate',
      name: App.t('an_channel_cost'),
      format: (channel) => percent(channel.channel_cost_rate),
      note: (channel) => App.t('an_channel_cost_note', {
        commission: formatCurrency(channel.commission_recorded),
        revenue: formatCurrency(channel.won_value),
      }),
      empty: App.t('an_channel_cost_empty'),
    },
    {
      key: 'median_days_to_first_lead',
      name: App.t('an_time_to_first'),
      format: (channel) => {
        const days = channel.median_days_to_first_lead;
        return App.pluralize(days, 'unit_day');
      },
      note: () => App.t('an_time_to_first_note')
        + App.t('an_time_to_first_note2'),
      empty: App.t('an_time_to_first_empty'),
    },
    {
      key: 'open_pipeline_value',
      name: App.t('an_open_pipeline'),
      format: (channel) => formatCurrency(channel.open_pipeline_value),
      note: (channel) => App.t('an_pipeline_note', {
        deals: App.pluralize(channel.open_pipeline_deals, 'unit_deal'),
      }),
      empty: App.t('an_pipeline_empty'),
    },
  ];
}

function percent(value) {
  return value == null ? null : `${new Intl.NumberFormat(App.lang === 'en' ? 'en-GB' : 'pl-PL', { maximumFractionDigits: 1 }).format(value)}%`;
}

function renderChannelMetrics(channel) {
  const host = document.getElementById('channel-metrics');
  if (!host) return;
  host.replaceChildren();
  if (!channel) {
    host.appendChild(textNode('p', 'channel-metric-note',
      App.t('an_metrics_forbidden')));
    return;
  }

  for (const metric of channelMetrics()) {
    const cell = document.createElement('div');
    const raw = channel[metric.key];
    const value = document.createElement('div');
    value.className = 'channel-metric-value';

    if (raw == null) {
      value.classList.add('is-empty');
      value.textContent = '—';
      cell.append(value, textNode('div', 'channel-metric-name', metric.name),
        textNode('div', 'channel-metric-note', metric.empty));
    } else {
      value.textContent = metric.format(channel);
      cell.append(value, textNode('div', 'channel-metric-name', metric.name),
        textNode('div', 'channel-metric-note', metric.note(channel)));
    }
    host.appendChild(cell);
  }
}

function textNode(tag, className, text) {
  const el = document.createElement(tag);
  el.className = className;
  el.textContent = text;
  return el;
}

/** Trim a company name down to something that fits under a chart bar. */
function shortCompanyLabel(company) {
  const trimmed = String(company)
    .replace(/\s*(sp\.\s*z\s*o\.?\s*o\.?|s\.a\.|sp\.\s*k\.|sp\.\s*j\.)\s*$/i, '')
    .trim();
  const base = trimmed || String(company).trim();
  return base.length > 16 ? `${base.slice(0, 15)}…` : base;
}

let partnerRankingBound = false;

/** Delegated so it survives every re-render of the ranking body. */
function setupPartnerRanking() {
  if (partnerRankingBound) return;
  const tbody = document.getElementById('an-partners-tbody');
  if (!tbody) return;
  partnerRankingBound = true;
  tbody.addEventListener('click', (event) => {
    const row = event.target.closest('tr[data-partner-id]');
    if (!row) return;
    Ui.safeNavigate(`partners.html?partner=${encodeURIComponent(row.dataset.partnerId)}`);
  });
}

function renderAnalyticsKPIs(stats) {
  const grid = document.getElementById('analytics-kpis');
  if (!grid) return;
  const total    = finiteNumber(stats.total_leads);
  const accepted = finiteNumber(stats.accepted_leads);
  const rate     = total > 0 ? Math.round((accepted / total) * 100) : 0;
  const comm     = finiteNumber(stats.total_commissions);
  const partners = finiteNumber(stats.total_partners);
  const cards = [
    { label: App.t('an_leads_total'),    value: total,                                   icon: 'bar-chart-3', sub: App.t('an_all_time'),            color: 'purple' },
    { label: App.t('an_conversion'), value: rate + '%', icon: 'circle-check', sub: App.t('an_accepted_count', { count: accepted }), color: 'green' },
    { label: App.t('an_commissions_total'), value: amountText(comm) + ' PLN', icon: 'banknote', sub: App.t('an_accrued_commissions'), color: 'orange' },
    { label: App.t('an_active_partners'), value: partners, icon: 'handshake', sub: App.t('an_in_system'), color: 'blue' },
  ];
  grid.replaceChildren(...cards.map(k => {
    const card = makeElement('div', `kpi-card ${k.color}`);
    const header = makeElement('div', 'kpi-header');
    const kpiIcon = makeElement('div', 'kpi-icon');
    Icons.into(kpiIcon, k.icon, { size: 20 });
    header.append(kpiIcon);
    const sub = makeElement('div', 'text-xs text-tertiary', k.sub);
    sub.style.marginTop = '4px';
    card.append(header, makeElement('div', 'kpi-value', k.value), makeElement('div', 'kpi-label', k.label), sub);
    return card;
  }));
}

/* Show a chart, or the panel's own "no data yet" line in its place.

   The three <p class="empty-state"> paragraphs have sat in analytics.html
   since the screen was built, `hidden`, with nothing ever unhiding them. On a
   fresh programme that left three panels showing an empty grey ring, a flat
   line at zero and -- for the partner bars -- plain white space, with no word
   of explanation. An empty chart looks exactly like a broken one. */
function toggleChartEmptyState(canvasId, hasData) {
  const canvas = document.getElementById(canvasId);
  const message = document.getElementById(`${canvasId}-empty`);
  if (canvas) canvas.hidden = !hasData;
  if (message) message.hidden = hasData;
  return hasData;
}

/* Takes `stats` and nothing else: the donut gets its stage names and colours
   from stats.pipeline_dist. */
function renderAnalyticsCharts(stats) {
  setTimeout(() => {
    if (typeof Charts === 'undefined') return;
    // A six-month series of zeros is "no leads yet", not data: the backend
    // always returns six buckets, so the array being non-empty proves nothing.
    const monthly = Array.isArray(stats.monthly) ? stats.monthly : [];
    if (toggleChartEmptyState('an-line', monthly.some(month => finiteNumber(month.leads) > 0))) {
      Charts.drawLine('an-line', monthly);
    }
    // One picture of every lead the producer has: the three pre-funnel
    // registration buckets, then the producer's own stages in their own order,
    // ending on win and lose. The slices partition the whole set, so the
    // number in the middle of the donut is the lead count and nothing is
    // counted twice. Built by api/stats.py::get_pipeline_distribution.
    const buckets = Array.isArray(stats.pipeline_dist) ? stats.pipeline_dist : [];
    const counts = {};
    buckets.forEach((bucket) => { counts[bucket.key] = finiteNumber(bucket.count); });
    document.getElementById('an-donut-legend')?.replaceChildren();
    if (toggleChartEmptyState('an-donut', Object.values(counts).some(n => n > 0))) {
      Charts.drawDonut('an-donut', counts, 'an-donut-legend', donutLookup(buckets));
    }

    // Top 5 by how many leads they actually brought in. The ranking table
    // below answers the money question; this answers the activity one.
    //
    // Partners on zero are SHOWN, not filtered out: "top 5" means five
    // partners, and a chart that silently drops the quiet ones turns "four of
    // your five partners have submitted nothing" into a blank space. The panel
    // only goes empty when there are no partners at all.
    const byLeads = [...(stats.partner_ranking || [])]
      .sort((a, b) => finiteNumber(b.total) - finiteNumber(a.total))
      .slice(0, 5);
    if (toggleChartEmptyState('an-bars', byLeads.length > 0)) {
      Charts.drawBars('an-bars', byLeads.map(p => ({
        // Company, not the contact's first name -- same identity the ranking
        // below uses. Bar labels are narrow, so drop the legal-form suffix and
        // cap the length rather than letting names collide.
        label: shortCompanyLabel(p.company || p.name || '—'),
        title: p.company || p.name || '—',
        value: finiteNumber(p.total),
      })));
    }
  }, 60);
}

/* drawDonut resolves a slice's colour and label from the stage list it is
   handed, preferring a real hex on the record. The pre-funnel buckets are not
   stages and carry a colour ROLE instead, so that a theme switch recolours
   them; resolving it here keeps that translation in one place and lets the
   chart code stay unchanged. */
function donutLookup(buckets) {
  const palette = typeof Charts !== 'undefined' ? Charts.getColors() : {};
  // Names come from where the producer configured them, never from a second
  // copy: a funnel stage brings its own name and hex from lead_stages, and an
  // intake bucket is named by Ui.REGISTRATION_LABELS -- the same list the
  // Lead stages screen renders its fixed badges from. Renaming a stage in
  // Settings renames it here, and the two screens cannot drift apart.
  const intakeLabels = (typeof Ui !== 'undefined' && Ui.registrationLabels)
    ? Ui.registrationLabels() : {};
  return buckets.map((bucket) => ({
    key: bucket.key,
    kind: bucket.kind || ({ won: 'win', lost: 'lose' }[bucket.key]),
    is_terminal: bucket.is_terminal ?? (bucket.key === 'won' || bucket.key === 'lost'),
    name: intakeLabels[bucket.key] || bucket.name,
    name_en: bucket.name_en,
    color: bucket.color || palette[bucket.color_role] || undefined,
  }));
}

function renderPartnersTable(partners) {
  const tbody = document.getElementById('an-partners-tbody');
  if (!tbody) return;

  tbody.replaceChildren(...partners.map((p, i) => {
    const total = finiteNumber(p.total);
    const accepted = finiteNumber(p.accepted);
    const rate = total > 0 ? Math.round((accepted / total) * 100) : 0;
    const row = document.createElement('tr');
    row.append(makeElement('td', 'text-sm text-tertiary', i + 1));
    // The ranking identifies partners by company, not by the contact person:
    // the programme is sold to firms, and that is the name that belongs on a
    // slide. The contact stays available on the Partners page.
    const identityCell = document.createElement('td');
    const identity = makeElement('div');
    Object.assign(identity.style, { display: 'flex', alignItems: 'center', gap: '10px' });
    const company = p.company || p.name || '—';
    identity.append(makeElement('div', 'cell-primary', company));
    identityCell.append(identity);
    row.append(identityCell);

    // Clicking a row opens that partner on the Partners page.
    if (p.id != null) {
      row.dataset.partnerId = String(p.id);
      row.style.cursor = 'pointer';
      row.title = App.t('open_partner', { company });
    }
    // The producer's OWN tier, from commission_tiers -- name and colour both,
    // never the legacy users.level word. The name follows the reader's language: the producer writes
    // both, and an untranslated tier falls back to his Polish original.
    const levelCell = document.createElement('td');
    const tierName = Ui.producerLabel(p, 'tier_name');
    const tierBadge = makeElement('span', 'badge', tierName || App.t('an_no_tier'));
    if (tierName && /^#[0-9a-fA-F]{6}$/.test(String(p.tier_color || ''))) {
      tierBadge.style.color = p.tier_color;
      tierBadge.style.borderColor = p.tier_color;
      tierBadge.style.background = 'transparent';
    } else {
      tierBadge.classList.add('badge-neutral');
    }
    if (p.tier_rate != null) tierBadge.title = App.t('an_tier_rate', { rate: Number(p.tier_rate) });
    levelCell.append(tierBadge);
    row.append(levelCell, makeElement('td', 'text-sm', total), makeElement('td', 'text-sm', accepted));
    const rateCell = document.createElement('td');
    const rateRow = makeElement('div');
    Object.assign(rateRow.style, { display: 'flex', alignItems: 'center', gap: '8px' });
    const rateTrack = makeElement('div');
    Object.assign(rateTrack.style, { flex: '1', height: '6px', background: 'var(--bg-elevated)', borderRadius: '99px', overflow: 'hidden', minWidth: '60px' });
    const rateFill = makeElement('div');
    Object.assign(rateFill.style, { height: '100%', width: `${rate}%`, background: 'var(--success-400)', borderRadius: '99px' });
    rateTrack.append(rateFill);
    const rateLabel = makeElement('span', 'text-xs font-semibold', `${rate}%`);
    Object.assign(rateLabel.style, { color: 'var(--success-400)', minWidth: '32px', textAlign: 'right' });
    rateRow.append(rateTrack, rateLabel);
    rateCell.append(rateRow);
    // Two different figures, and keeping them apart is the point: `revenue` is
    // what this partner's won deals sold for -- the producer's own top line --
    // and `commission` is what the producer owes them out of it. The ranking
    // is ordered by the first (api/stats.py::get_partner_ranking), so it reads
    // down the Sales column.
    const revenueCell = makeElement('td', 'text-sm font-semibold',
      `${amountText(finiteNumber(p.revenue))} PLN`);
    revenueCell.style.color = 'var(--text-primary)';
    const commissionCell = makeElement('td', 'text-sm font-semibold',
      `${amountText(finiteNumber(p.commission))} PLN`);
    commissionCell.style.color = 'var(--brand-400)';
    row.append(rateCell, revenueCell, commissionCell);
    return row;
  }));
}

function finiteNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : 0;
}

function makeElement(tag, className = '', text = '') {
  const element = document.createElement(tag);
  if (className) element.className = className;
  element.textContent = String(text ?? '');
  return element;
}
