/* ============================================================
   PARTNERTYPE — ADMIN DASHBOARD CONTROLLER
   Closure-scoped dashboard state and safe DOM rendering.
   ============================================================ */

(function initAdminDashboardNamespace(global) {
  'use strict';

  const leadRecords = new Map();
  let initialization = null;
  let recordSequence = 0;
  let stagePipeline = null;

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = String(text ?? '');
    return node;
  }

  function appendTextCell(row, value, className) {
    const cell = element('td', className, value);
    row.appendChild(cell);
    return cell;
  }

  function formatMoney(value) {
    return typeof global.formatCurrency === 'function' ? global.formatCurrency(value) : String(value ?? '');
  }

  function formatDay(value) {
    return typeof global.formatDate === 'function' ? global.formatDate(value) : String(value ?? '');
  }

  function avatarClass(name) {
    return typeof global.getAvatarColorClass === 'function'
      ? global.getAvatarColorClass(String(name ?? ''))
      : 'bg-nebula-indigo';
  }

  function initials(name) {
    return typeof global.getInitials === 'function' ? global.getInitials(String(name ?? '')) : '??';
  }

  function canAct(lead) {
    return Ui.isLeadUnhandled(lead);
  }

  // Stage badge rendering is owned by js/utils.js (Ui.stageBadge) so this
  // controller and js/admin-leads.js share exactly one implementation.
  function statusBadge(lead) {
    // The dashboard answers "what has been decided?", so it shows the
    // registration badge; the sales stage belongs on the leads screen.
    return Ui.registrationBadge(lead && lead.registration_status);
  }

  function setKpi(id, value) {
    const target = document.getElementById(id);
    if (target) target.textContent = String(value ?? '');
  }

  function renderKpis(stats) {
    // Each counting KPI card is a link to leads.html filtered by its own
    // data-kpi-status, so its number must be the count for exactly that
    // status. Reading both from the card keeps them from drifting apart.
    //
    // stats.new_leads / pending_leads / accepted_leads are aggregates by stage
    // KIND (pending_leads covers 'pending' and 'in_progress' together), so they
    // cannot answer "how many leads will the filtered list show".
    //
    // registration_dist, not status_dist. A lead carries two independent
    // answers, and these cards link to leads.html filtered by
    // ?registration_status=, so they must count the decision axis; status_dist
    // groups by the SALES STAGE column.
    const distribution = stats.registration_dist || {};
    for (const card of document.querySelectorAll('.kpi-card[data-kpi-status]')) {
      const valueElement = card.querySelector('.kpi-value');
      if (!valueElement || card.dataset.kpiMetric === 'commissions') continue;
      valueElement.textContent = String(distribution[card.dataset.kpiStatus] ?? 0);
    }

    // "Po terminie" is not a status: it is every lead still awaiting a decision
    // past the producer's own lead_action_days deadline. The backend counts it
    // (api/stats.py::count_overdue_leads) so this card and the leads filter
    // apply exactly the same rule.
    const overdueCard = document.querySelector('.kpi-card[data-kpi-metric="overdue"] .kpi-value');
    if (overdueCard) overdueCard.textContent = String(stats.overdue_leads ?? 0);

    const commissions = stats.total_commissions ?? 0;
    setKpi('kpi-commissions', Number(commissions) === 0 ? 0 : formatMoney(commissions));

    // What the channel sold, not what it cost: won_value is the deal value,
    // commission_recorded is the producer's share of it. Read from
    // stats.channel so this card and the Analytics channel panel can never
    // disagree about the same number.
    const channelRevenue = stats.channel?.won_value ?? 0;
    setKpi('kpi-channel-revenue', Number(channelRevenue) === 0 ? 0 : formatMoney(channelRevenue));
    setKpi('kpi-partners', stats.total_partners ?? 0);
    setKpi('kpi-total-leads', stats.total_leads ?? 0);
    setKpi('new-leads-badge', distribution.new ?? 0);
  }

  // Same four square controls as the leads list (js/admin-leads.js): the
  // producer should not have to learn a second vocabulary for the same
  // decision because the widget happens to sit on the dashboard.
  const ROW_ACTION_ICONS = { accept: 'check', reject: 'x', request_info: 'message-square' };

  function actionButton(action, label) {
    const button = element('button', `btn-row btn-row-${action.replace('_', '-')}`, '');
    if (global.Icons) Icons.into(button, ROW_ACTION_ICONS[action], { size: 15 });
    button.type = 'button';
    button.dataset.action = action;
    button.title = label;
    button.setAttribute('aria-label', label);
    return button;
  }

  function renderRecentLeads(leads) {
    const tbody = document.getElementById('recent-leads-tbody');
    if (!tbody) return;
    tbody.replaceChildren();
    leadRecords.clear();
    recordSequence = 0;
    const empty = document.getElementById('recent-leads-empty');

    if (!leads.length) {
      tbody.hidden = true;
      if (empty) empty.hidden = false;
      return;
    }

    tbody.hidden = false;
    if (empty) empty.hidden = true;

    for (const lead of leads) {
      const recordKey = `lead-${recordSequence += 1}`;
      leadRecords.set(recordKey, lead);
      const row = element('tr');
      row.dataset.recordKey = recordKey;

      const idCell = element('td');
      const id = element('span', '', lead.id);
      id.style.cssText = 'font-family:monospace;font-size:0.8rem;color:var(--text-tertiary)';
      idCell.appendChild(id);
      row.appendChild(idCell);

      const clientCell = element('td');
      clientCell.appendChild(element('div', 'cell-primary', lead.client_name));
      const company = element('div', 'text-xs text-tertiary', lead.company);
      company.style.marginTop = '2px';
      clientCell.appendChild(company);
      row.appendChild(clientCell);

      const partnerCell = element('td');
      const partnerWrap = element('div');
      partnerWrap.style.cssText = 'display:flex;align-items:center;gap:8px';
      partnerWrap.appendChild(element('div', `avatar avatar-sm ${avatarClass(lead.partner_name || '?')}`, initials(lead.partner_name || '?')));
      partnerWrap.appendChild(element('span', '', lead.partner_name || '—'));
      partnerCell.appendChild(partnerWrap);
      row.appendChild(partnerCell);

      appendTextCell(row, formatMoney(lead.value), 'cell-primary');
      const statusCell = element('td');
      statusCell.appendChild(statusBadge(lead));
      row.appendChild(statusCell);
      appendTextCell(row, formatDay(lead.created_at), 'text-tertiary');

      const actionCell = element('td');
      const actions = element('div', 'row-actions');
      if (canAct(lead)) {
        actions.appendChild(actionButton('accept', App.t('action_accept')));
        actions.appendChild(actionButton('reject', App.t('action_reject')));
        // Only while nobody has asked yet -- mirrors renderDecisionControls on
        // the leads screen.
        if (lead.registration_status === 'pending') {
          actions.appendChild(actionButton('request_info', App.t('action_request_info')));
        }
      }
      actionCell.appendChild(actions);
      row.appendChild(actionCell);
      tbody.appendChild(row);
    }
  }

  // A stage's kind (not its literal key) picks the glyph; its own colour
  // (not a fixed CSS var per hardcoded word) tints the icon, so a stage
  // added after this code was written still gets a sensible treatment.
  const ACTIVITY_ICON_BY_KIND = { intake: 'plus', progress: 'more-horizontal', win: 'check', lose: 'x' };

  function activityIcon(stage) {
    const color = stage && /^#[0-9a-fA-F]{6}$/.test(String(stage.color || '')) ? stage.color : '#94a3b8';
    const icon = (stage && ACTIVITY_ICON_BY_KIND[stage.kind]) || 'arrow-right';
    return { background: `${color}22`, color, icon };
  }

  function renderActivityFeed(leads) {
    const list = document.getElementById('activity-list');
    if (!list) return;
    list.replaceChildren();
    for (const lead of leads.slice(0, 5)) {
      const stage = Ui.stageFor(stagePipeline, lead.status);
      const config = activityIcon(stage);
      const item = element('div', 'activity-item animate-fadeIn');
      const icon = element('div', 'activity-icon');
      icon.style.background = config.background;
      icon.style.color = config.color;
      Icons.into(icon, config.icon, { size: 14 });
      const body = element('div', 'activity-body');
      // No stage yet (waiting, or rejected): name the decision, never a bare dash.
      const label = stage ? Ui.stageLabel(stage)
        : (lead.status ? String(lead.status) : (Ui.registrationLabel(lead.registration_status) || ''));
      body.appendChild(element('div', 'activity-text', `Lead ${lead.id} (${lead.client_name}) — ${label}`));
      body.appendChild(element('div', 'activity-time', `${formatDay(lead.created_at)} · ${lead.partner_name || ''}`));
      item.append(icon, body);
      list.appendChild(item);
    }
  }

  function renderTopPartners(partners) {
    const list = document.getElementById('top-partners');
    if (!list) return;
    list.replaceChildren();
    for (const partner of partners) {
      // Identify partners by company here too, matching the analytics ranking.
      const company = partner.company || partner.name || '—';
      const card = element('div', 'partner-card');
      if (partner.id != null) {
        card.dataset.partnerId = String(partner.id);
        card.style.cursor = 'pointer';
        card.title = App.t('open_partner', { company });
      }
      const info = element('div', 'partner-card-info');
      const companyName = element('div', 'partner-card-name', company);
      companyName.title = company;
      info.append(companyName);
      const stats = element('div', 'partner-card-stats');
      const accepted = element('div', 'partner-stat');
      accepted.append(element('div', 'partner-stat-value', partner.accepted_leads || 0), element('div', 'partner-stat-label', App.t('kpi_accepted')));
      const earnings = element('div', 'partner-stat');
      const earningsValue = element('div', 'partner-stat-value', formatMoney(partner.total_earnings || 0));
      earningsValue.style.color = 'var(--success-400)';
      earnings.append(earningsValue, element('div', 'partner-stat-label', App.t('kpi_commissions')));
      stats.append(accepted, earnings);
      card.append(info, stats);
      list.appendChild(card);
    }
  }

  // `action` is a UI verb ('accept'/'reject'/'request_info'), not a stage word.
  //
  // Refusing a partner's deal owes them a reason: Ui.openRowDecisionForm
  // splices the reason in under the row, so all three decisions complete here
  // and the producer keeps their place on the dashboard.
  const DECISION_TOASTS = {
    accept: ['success', 'Lead zaakceptowany'],
    reject: ['error', 'Lead odrzucony'],
    request_info: ['success', App.t('toast_info_request_sent')],
  };

  async function updateLead(recordKey, action, message) {
    const lead = leadRecords.get(recordKey);
    if (!lead) return;

    const revision = Number(lead.revision ?? 0);
    const body = {
      action,
      expected_revision: revision,
      // Derived, not random: a double-click is the same decision.
      operation_id: `${action}-${lead.id}-${revision}`,
    };
    if (message) body.message = message;

    try {
      const data = await ApiClient.post(
        `/api/leads/${encodeURIComponent(lead.id)}/registration`, body,
      );
      const updated = data.lead;
      leadRecords.set(recordKey, updated);
      renderRecentLeads([...leadRecords.values()].slice(0, 5));
      const [tone, title] = DECISION_TOASTS[action] || DECISION_TOASTS.accept;
      global.showToast?.(tone, title, `${updated.client_name} — ${updated.company}`);
    } catch (error) {
      if (error && error.status === 409) {
        global.showToast?.(
          'error', App.t('toast_lead_changed_title'),
          App.t('toast_lead_changed_dashboard'),
        );
        return;
      }
      global.handleApiError(error, `${action}Lead`);
    }
  }

  function bindLeadActions() {
    document.getElementById('recent-leads-tbody')?.addEventListener('click', (event) => {
      const row = event.target.closest('tr[data-record-key]');
      if (!row) return;
      const recordKey = row.dataset.recordKey;
      const action = event.target.closest('[data-action]')?.dataset.action;
      if (action === 'accept') {
        Ui.closeRowDecisionForm(row.parentElement);
        void updateLead(recordKey, action);
        return;
      }
      if (action === 'reject' || action === 'request_info') {
        Ui.openRowDecisionForm(row, action, (text) => updateLead(recordKey, action, text));
        return;
      }
      // A click anywhere else in the row is still a request to see the whole
      // lead, which only the leads screen can show.
      const lead = leadRecords.get(recordKey);
      if (lead) Ui.safeNavigate(`leads.html?lead=${encodeURIComponent(lead.id)}`);
    });
  }

  function bindDashboardActions(user) {
    bindLeadActions();
    document.addEventListener('click', (event) => {
      const card = event.target.closest('[data-kpi-status]');
      if (!card) return;
      const status = card.dataset.kpiStatus;
      if (!Ui.REGISTRATION_STATUSES.includes(status)) return;
      event.preventDefault();
      Ui.safeNavigate(`leads.html?registration_status=${status}`);
    });
    document.getElementById('top-partners')?.addEventListener('click', (event) => {
      const card = event.target.closest('[data-partner-id]');
      if (!card) return;
      Ui.safeNavigate(`partners.html?partner=${encodeURIComponent(card.dataset.partnerId)}`);
    });
    document.getElementById('dashboard-retry')?.addEventListener('click', () => {
      void loadDashboard(user);
    });
  }

  function renderCharts(stats) {
    const monthly = Array.isArray(stats.monthly) ? stats.monthly : [];
    const chart = document.getElementById('leads-chart');
    const chartEmpty = document.getElementById('leads-chart-empty');
    if (!monthly.length) {
      if (chart) chart.hidden = true;
      if (chartEmpty) chartEmpty.hidden = false;
    } else if (typeof Charts !== 'undefined') {
      if (chart) chart.hidden = false;
      if (chartEmpty) chartEmpty.hidden = true;
      global.setTimeout(() => {
        Charts.drawLine('leads-chart', monthly, { showCommission: false });
        if (stats.top_partners?.length) {
          // One measure across five partners is one series: the bars share the
          // brand colour and the name under each bar says which partner it is.
          Charts.drawBars('partners-chart', stats.top_partners.map((partner) => ({
            label: String(partner.name || '').split(' ')[0],
            title: String(partner.name || ''),
            value: partner.accepted,
          })));
        }
        if (stats.status_dist) Charts.drawDonut('status-donut', stats.status_dist, 'status-legend', stagePipeline?.list);
      }, 50);
    }
    if (stats.trends && typeof Charts !== 'undefined') {
      Charts.renderTrend('trend-leads', stats.trends.leads);
      Charts.renderTrend('trend-accepted', stats.trends.accepted);
      Charts.renderTrend('trend-commissions', stats.trends.commissions);
    }
  }

  async function loadDashboard(user) {
    const errorState = document.getElementById('dashboard-error');
    if (errorState) errorState.hidden = true;
    try {
      const [stats, leadsData, partnersData, stagesData] = await Promise.all([
        ApiClient.get('/api/stats'),
        // Five newest, which is exactly what the two panels below show. The KPI figures come from /api/stats, which counts in the
        // database and never needed the list.
        ApiClient.get('/api/leads?page=1&per_page=5'),
        ApiClient.get('/api/partners'),
        ApiClient.get('/api/lead-stages'),
      ]);
      stagePipeline = Ui.buildStagePipeline(stagesData.stages || []);
      const leads = Array.isArray(leadsData.leads) ? leadsData.leads : [];
      renderKpis(stats);
      renderRecentLeads(leads.slice(0, 5));
      renderActivityFeed(leads);
      renderTopPartners((partnersData.partners || []).slice(0, 3));
      renderCharts(stats);
    } catch (error) {
      if (errorState) errorState.hidden = false;
      global.handleApiError(error, 'AdminDashboard.load');
    }
  }

  async function initialize(user) {
    const name = document.getElementById('welcome-name');
    if (name) name.textContent = String(user.name || '').split(' ')[0];
    bindDashboardActions(user);
    await loadDashboard(user);
  }

  /**
   * Initialize dashboard data, rendering and delegated actions exactly once.
   * @param {object} user authoritative public admin identity from AdminShell
   * @returns {Promise<void>}
   */
  function init(user) {
    if (!initialization) {
      initialization = initialize(user);
      // The KPI cards, the recent-lead rows and the charts are all built in
      // code, so only drawing them again follows a language or theme switch.
      App.onRerender(() => { void loadDashboard(user); });
    }
    return initialization;
  }

  global.AdminDashboard = Object.freeze({ init });
})(window);
