/* ============================================================
   PARTNERTYPE — ADMIN PARTNERS CONTROLLER
   Tier, activity and volume per partner; tier changes and
   deactivation act through the single tier/commission source
  . Closure-owned record keys route every row action.
   ============================================================ */

(function initAdminPartnersNamespace(global) {
  'use strict';

  // The Stan column's four labels -- computed server-side by
  // get_all_partners_roster() from account_status plus an unexpired,
  // unaccepted invitation row. Replaces the old single
  // account-status badge inside the identity cell: the screen now states
  // the account's state exactly once.
  // Built per render, not frozen at load: a constant captured here would keep
  // whatever language was active when this file was parsed, and App.setLang
  // only re-runs applyTranslations over [data-i18n] nodes.
  const PARTNER_STATE_CLASSES = {
    invited_pending: 'badge-warning',
    self_registered: 'badge-warning',
    active: 'badge-success',
    suspended: 'badge-error',
    rejected: 'badge-error',
  };

  function partnerStateLabel(state) {
    if (!PARTNER_STATE_CLASSES[state]) return null;
    return { label: App.t('ap_state_' + state), className: PARTNER_STATE_CLASSES[state] };
  }

  const partnerRecords = new Map();
  let partnersState = [];
  let tiersState = [];
  // The producer's configured pipeline, needed so the hub can say where a
  // partner's leads are standing using the stage names and colours HE set.
  let stagePipeline = null;
  let leadsState = [];
  let pendingDeactivateKey = null;
  let initialization = null;
  let actionsBound = false;
  // Mutating row actions (accept/restore/deactivate) render for the roles
  // the server actually admits. Partner account state is in the
  // moderator's remit -- api/users.py::update_user accepts a manager acting on a role='partner' target -- so hiding
  // these from a moderator would withhold a button that works.
  let canManagePartners = false;

  function textElement(tagName, className, value) {
    const element = document.createElement(tagName);
    if (className) element.className = className;
    element.textContent = String(value ?? '');
    return element;
  }

  function safeAmount(value) {
    const number = Number(value);
    return Number.isFinite(number) ? number : 0;
  }

  function safeHexColor(value) {
    return /^#[0-9a-f]{6}$/i.test(String(value || '')) ? String(value) : '#6246ea';
  }

  function formatVolume(amount) {
    if (!amount) return App.t('ap_zero_amount');
    return formatCurrency(amount);
  }

  function leadsForPartner(partnerId) {
    return leadsState.filter((lead) => Number(lead.partner_id) === Number(partnerId));
  }

  function mostRecentSubmission(leads) {
    return leads.reduce((latest, lead) => {
      const created = lead.created_at;
      if (!created) return latest;
      if (!latest || created > latest) return created;
      return latest;
    }, '');
  }

  // ─── ROW RENDERING ───────────────────────────────────────────
  function createIdentityCell(partner) {
    const cell = document.createElement('td');
    const identity = document.createElement('div');
    identity.style.cssText = 'display:flex;align-items:center;gap:12px';

    const details = document.createElement('div');
    const nameRow = document.createElement('div');
    nameRow.style.cssText = 'display:flex;align-items:center;gap:8px';
    nameRow.appendChild(textElement('span', 'cell-primary', partner.name));
    details.append(
      nameRow,
      textElement('div', 'text-xs text-tertiary', partner.company || partner.email || ''),
    );

    identity.append(
      textElement('div', `avatar ${getAvatarColorClass(partner.name)}`, getInitials(partner.name)),
      details,
    );
    cell.appendChild(identity);
    return cell;
  }

  const REJECTED_RETENTION_DAYS = 7;

  function createStateCell(partner) {
    const cell = document.createElement('td');
    const info = partnerStateLabel(partner.state);
    // The producer's decision about admitting this partner, so it takes the
    // same emphatic filled form as a lead's registration decision (see
    // js/utils.js::Ui.registrationBadge), not the quiet tint that
    // descriptive labels use.
    if (info) {
      cell.appendChild(textElement('span', `badge badge-solid ${info.className}`, info.label));
    }
    if (partner.state === 'rejected' && partner.rejected_at) {
      const deadline = Ui.parseServerDate(partner.rejected_at).getTime()
        + REJECTED_RETENTION_DAYS * 24 * 60 * 60 * 1000;
      const daysLeft = Math.max(0, Math.ceil((deadline - Date.now()) / (24 * 60 * 60 * 1000)));
      cell.appendChild(textElement(
        'div', 'text-xs text-tertiary',
        daysLeft > 0
          ? App.t('ap_disappears_in', { days: App.pluralize(daysLeft, 'unit_day') })
          : App.t('ap_disappears_soon'),
      ));
    }
    return cell;
  }

  function createTierCell(partner, recordKey) {
    const cell = document.createElement('td');
    const wrap = document.createElement('div');
    wrap.style.cssText = 'display:flex;flex-direction:column;gap:6px;align-items:flex-start';

    const tierColor = safeHexColor(partner.color);

    const select = document.createElement('select');
    select.className = 'form-select';
    // 40px of the control is reserved for the chevron; without a floor the
    // cell squeezes it until tier names truncate ("Bronze" -> "Bror").
    select.style.minWidth = '150px';
    select.dataset.partnerAction = 'tier';
    select.dataset.partnerKey = recordKey;
    select.setAttribute('aria-label', App.t('ap_change_tier_for', { name: partner.name }));
    // A partner with no tier assigned must not silently read as the first
    // option — that made the control contradict the badge beside it.
    const placeholder = document.createElement('option');
    placeholder.value = '';
    placeholder.textContent = App.t('ap_no_tier');
    placeholder.selected = partner.tier_id == null;
    placeholder.disabled = true;
    select.replaceChildren(
      placeholder,
      ...tiersState.map((tier) => {
        const option = document.createElement('option');
        option.value = String(tier.id);
        option.textContent = tier.name;
        option.selected = Number(tier.id) === Number(partner.tier_id);
        return option;
      }),
    );

    wrap.append(select);
    cell.appendChild(wrap);
    return cell;
  }

  function createActivityCell(leads) {
    const cell = document.createElement('td');
    const wrap = document.createElement('div');
    const count = leads.length;
    const recent = mostRecentSubmission(leads);
    wrap.appendChild(textElement('div', 'cell-primary', App.pluralize(count, 'unit_lead')));
    wrap.appendChild(textElement(
      'div',
      'text-xs text-tertiary',
      recent ? App.t('ap_last_submission', { date: formatDate(recent) }) : App.t('ap_no_submissions'),
    ));
    cell.appendChild(wrap);
    return cell;
  }

  function createVolumeCell(leads) {
    const volume = leads.reduce((sum, lead) => sum + safeAmount(lead.value), 0);
    return textElement('td', 'text-lg font-semibold', formatVolume(volume));
  }

  function createPercentCell(partner) {
    return textElement('td', 'font-medium', `${safeAmount(partner.total_percent).toFixed(2)}%`);
  }

  function createActionsCell(partner, recordKey) {
    const cell = document.createElement('td');
    const actions = document.createElement('div');
    actions.className = 'row-actions';

    const detailsButton = textElement('button', 'btn btn-secondary btn-sm', App.t('ap_details'));
    detailsButton.type = 'button';
    detailsButton.dataset.partnerAction = 'details';
    detailsButton.dataset.partnerKey = recordKey;
    actions.appendChild(detailsButton);

    // Mutating actions are state-appropriate and admin-only:
    // self_registered can be accepted, suspended can be restored, active can
    // be deactivated. invited_pending offers none -- the invited person has
    // not set a password yet, so activating them would produce an account
    // nobody can log into.
    if (canManagePartners) {
      if (partner.state === 'self_registered') {
        const acceptButton = textElement('button', 'btn btn-primary btn-sm', App.t('ap_accept'));
        acceptButton.type = 'button';
        acceptButton.dataset.partnerAction = 'accept';
        acceptButton.dataset.partnerKey = recordKey;
        actions.appendChild(acceptButton);
        // Refusing an application is its own outcome, distinct from
        // suspending an account that was once active.
        const rejectButton = textElement('button', 'btn btn-danger btn-sm', App.t('ap_reject'));
        rejectButton.type = 'button';
        rejectButton.dataset.partnerAction = 'reject';
        rejectButton.dataset.partnerKey = recordKey;
        actions.appendChild(rejectButton);
      } else if (partner.state === 'rejected') {
        // Restorable for REJECTED_RETENTION_DAYS (api/db.py), after which
        // the account is deleted --- so this button is also the only way to
        // undo a misclick before that window closes.
        const restoreButton = textElement('button', 'btn btn-secondary btn-sm', App.t('ap_restore'));
        restoreButton.type = 'button';
        restoreButton.dataset.partnerAction = 'restore';
        restoreButton.dataset.partnerKey = recordKey;
        actions.appendChild(restoreButton);
      } else if (partner.state === 'suspended') {
        const restoreButton = textElement('button', 'btn btn-primary btn-sm', App.t('ap_restore'));
        restoreButton.type = 'button';
        restoreButton.dataset.partnerAction = 'restore';
        restoreButton.dataset.partnerKey = recordKey;
        actions.appendChild(restoreButton);
      } else if (partner.state === 'active') {
        const deactivateButton = textElement('button', 'btn btn-danger btn-sm', App.t('ap_deactivate'));
        deactivateButton.type = 'button';
        deactivateButton.dataset.partnerAction = 'deactivate';
        deactivateButton.dataset.partnerKey = recordKey;
        actions.appendChild(deactivateButton);
      }
    }

    cell.appendChild(actions);
    return cell;
  }

  function createPartnerRow(partner, recordKey) {
    const row = document.createElement('tr');
    if (partner.id != null) row.dataset.partnerId = String(partner.id);
    const leads = leadsForPartner(partner.id);
    row.append(
      createIdentityCell(partner),
      createStateCell(partner),
      createTierCell(partner, recordKey),
      createActivityCell(leads),
      createVolumeCell(leads),
      createPercentCell(partner),
      createActionsCell(partner, recordKey),
    );
    return row;
  }

  function updateCountLabel(count) {
    const label = document.getElementById('partners-count-label');
    if (!label) return;
    label.textContent = App.t('ap_total_partners', { partners: App.pluralize(count, 'unit_partner') });
  }

  function renderPartners(partners) {
    const tbody = document.getElementById('partners-tbody');
    if (!tbody) return;
    partnerRecords.clear();
    const rows = partners.map((partner, index) => {
      const recordKey = `partner-${index + 1}`;
      partnerRecords.set(recordKey, partner);
      return createPartnerRow(partner, recordKey);
    });
    tbody.replaceChildren(...rows);
    updateCountLabel(partners.length);
    focusRequestedPartner(tbody);
  }

  /**
   * The partner ranking on the analytics page links here as
   * partners.html?partner=<id>. Bring that row into view and mark it, so the
   * click lands on the partner instead of the top of an unfiltered list.
   */
  function focusRequestedPartner(tbody) {
    const requested = new URLSearchParams(window.location.search).get('partner');
    if (!requested) return;
    const row = tbody.querySelector(`tr[data-partner-id="${CSS.escape(requested)}"]`);
    if (!row) return;
    row.classList.add('row-highlight');
    // Wait for layout: scrolling in the same frame as replaceChildren lands on
    // stale geometry and leaves the row below the fold.
    requestAnimationFrame(() => row.scrollIntoView({ block: 'center' }));
  }

  // ─── KPI CARDS ───────────────────────────────────────────────
  // Same shape as js/admin-dashboard.js::renderKpis -- read counts off the
  // stats endpoint and write with textContent, so this screen and the
  // dashboard agree instead of one of them inventing a number.
  const KPI_IDS = Object.freeze([
    'kpi-partners-active',
    'kpi-partners-leads',
    'kpi-partners-accepted',
    // Total earnings are not on this screen. The figure lives on Commissions, where it is the subject
    // rather than a fourth number beside a partner count.
  ]);

  function setKpi(id, value) {
    const target = document.getElementById(id);
    if (target) target.textContent = String(value ?? '');
  }

  function renderKpis(stats) {
    setKpi('kpi-partners-active', stats.total_partners ?? 0);
    setKpi('kpi-partners-leads', stats.total_leads ?? 0);
    setKpi('kpi-partners-accepted', stats.accepted_leads ?? 0);
  }

  function clearKpis() {
    for (const id of KPI_IDS) setKpi(id, '—');
  }

  // ─── STATE ───────────────────────────────────────────────────
  function setUiState(uiState) {
    const loading = document.getElementById('partners-loading-state');
    const empty = document.getElementById('partners-empty-state');
    const error = document.getElementById('partners-error-state');
    if (loading) loading.hidden = uiState !== 'loading';
    if (empty) empty.hidden = uiState !== 'empty';
    if (error) error.hidden = uiState !== 'error';
    if (uiState !== 'populated') {
      partnerRecords.clear();
      const tbody = document.getElementById('partners-tbody');
      if (tbody) tbody.replaceChildren();
    }
  }

  async function loadPartners() {
    setUiState('loading');
    clearKpis();

    // The KPI cards are decoration on this screen; the roster is the screen.
    // They are fetched separately so a failing /api/stats leaves three cards
    // reading "—" instead of blanking the partner list behind an error state.
    const statsPromise = ApiClient.get('/api/stats').catch((error) => {
      global.handleApiError(error, 'AdminPartners.loadKpis');
      return null;
    });

    try {
      // roster scope: every partner from both entry routes, any state.
      //No KPI is recomputed from this response.
      const [partnersResp, tiersResp, leadsResp] = await Promise.all([
        ApiClient.get('/api/partner-bonuses?scope=roster'),
        ApiClient.get('/api/tiers'),
        ApiClient.get('/api/leads'),
      ]);
      partnersState = Array.isArray(partnersResp.partners) ? partnersResp.partners : [];
      tiersState = Array.isArray(tiersResp.tiers) ? tiersResp.tiers : [];
      leadsState = Array.isArray(leadsResp.leads) ? leadsResp.leads : [];
      renderPartners(partnersState);
      setUiState(partnersState.length ? 'populated' : 'empty');
    } catch (error) {
      clearKpis();
      setUiState('error');
      global.handleApiError(error, 'AdminPartners.loadPartners');
      return;
    }

    // Separate from the three fetches above, and allowed to fail. Those three
    // ARE the roster; this one only enriches the hub modal with the producer's
    // stage names. Folding it into that Promise.all is the shape that once
    // blanked this entire screen when /api/stats died -- which is why the KPI
    // fetch sits outside it too.
    try {
      const stagesResp = await ApiClient.get('/api/lead-stages');
      stagePipeline = Ui.buildStagePipeline(stagesResp && stagesResp.stages);
    } catch (error) {
      stagePipeline = null;
    }

    const statsResp = await statsPromise;
    if (statsResp) renderKpis(statsResp);
  }

  // ─── TIER CHANGE ─────────────────────────────────────────────
  async function changeTier(recordKey, tierIdValue, selectEl) {
    const partner = partnerRecords.get(recordKey);
    if (!partner) return;
    const tierId = Number(tierIdValue);
    if (!Number.isInteger(tierId) || tierId <= 0) return;
    try {
      const data = await ApiClient.post(`/api/partner-bonuses/${partner.id}/tier`, { tier_id: tierId });
      if (data && data.partner) {
        const index = partnersState.findIndex((entry) => Number(entry.id) === Number(partner.id));
        if (index !== -1) partnersState[index] = data.partner;
      }
      renderPartners(partnersState);
    } catch (error) {
      global.handleApiError(error, 'AdminPartners.changeTier');
      if (selectEl) selectEl.value = String(partner.tier_id ?? '');
    }
  }

  // ─── DEACTIVATION ────────────────────────────────────────────
  function showDeactivateModal() {
    document.getElementById('deactivate-partner-modal')?.classList.add('show');
  }

  function hideDeactivateModal() {
    document.getElementById('deactivate-partner-modal')?.classList.remove('show');
  }

  function openDeactivateModal(recordKey) {
    const partner = partnerRecords.get(recordKey);
    if (!partner) return;
    pendingDeactivateKey = recordKey;
    const copy = document.getElementById('deactivate-partner-copy');
    if (copy) {
      copy.textContent = App.t('confirm_delete_partner', { name: partner.name });
    }
    showDeactivateModal();
  }

  function cancelDeactivate() {
    pendingDeactivateKey = null;
    hideDeactivateModal();
  }

  async function confirmDeactivate() {
    const recordKey = pendingDeactivateKey;
    if (!recordKey) return;
    const partner = partnerRecords.get(recordKey);
    if (!partner) return;
    const confirmButton = document.getElementById('deactivate-partner-confirm');
    if (confirmButton) confirmButton.disabled = true;
    try {
      const data = await ApiClient.patch(`/api/users/${partner.id}`, { account_status: 'suspended' });
      const updatedStatus = data && data.user && data.user.account_status ? data.user.account_status : 'suspended';
      const index = partnersState.findIndex((entry) => Number(entry.id) === Number(partner.id));
      if (index !== -1) {
        partnersState[index] = {
          ...partnersState[index],
          account_status: updatedStatus,
          state: updatedStatus === 'suspended' ? 'suspended' : partnersState[index].state,
        };
      }
      renderPartners(partnersState);
      pendingDeactivateKey = null;
      hideDeactivateModal();
    } catch (error) {
      global.handleApiError(error, 'AdminPartners.deactivate');
    } finally {
      if (confirmButton) confirmButton.disabled = false;
    }
  }

  // ─── ACCEPT / RESTORE ────────────────────────────────────────
  // Both transitions land the account in the 'active' state -- accept moves
  // a self-registered pending account forward, restore reverses a
  // suspension. Both patch through the same admin-only user endpoint the
  // deactivate action already uses, and both refresh the row from the
  // response rather than assuming success.
  async function setAccountActive(recordKey, button) {
    const partner = partnerRecords.get(recordKey);
    if (!partner) return;
    if (button) button.disabled = true;
    try {
      const data = await ApiClient.patch(`/api/users/${partner.id}`, { account_status: 'active' });
      const updatedStatus = data && data.user && data.user.account_status
        ? data.user.account_status
        : 'active';
      const index = partnersState.findIndex((entry) => Number(entry.id) === Number(partner.id));
      if (index !== -1) {
        partnersState[index] = {
          ...partnersState[index],
          account_status: updatedStatus,
          state: updatedStatus === 'active' ? 'active' : partnersState[index].state,
        };
      }
      renderPartners(partnersState);
    } catch (error) {
      global.handleApiError(error, 'AdminPartners.setAccountActive');
    } finally {
      if (button) button.disabled = false;
    }
  }

  // A refused application stays on the list, restorable, until the
  // retention sweep in api/db.py deletes it. Deleting it on the spot would
  // make a misclick unrecoverable; keeping it forever would hoard personal
  // data past its purpose (RODO art. 17). Seven days is the compromise.
  async function rejectPartner(recordKey, button) {
    const partner = partnerRecords.get(recordKey);
    if (!partner) return;
    if (button) button.disabled = true;
    try {
      await ApiClient.patch(`/api/users/${partner.id}`, { account_status: 'rejected' });
      global.showToast(
        'warning',
        App.t('ap_application_rejected'),
        App.t('ap_rejected_grace', { name: partner.name }),
      );
      await loadPartners();
    } catch (error) {
      global.handleApiError(error, 'AdminPartners.rejectPartner');
    } finally {
      if (button) button.disabled = false;
    }
  }

  // ─── PARTNER DETAILS, IN PLACE ───────────────────
  let detailsRecordKey = null;

  function openPartnerDetails(recordKey) {
    const partner = partnerRecords.get(recordKey);
    if (!partner) return;
    detailsRecordKey = recordKey;

    const set = (id, value) => {
      const el = document.getElementById(id);
      if (el) el.value = value ?? '';
    };
    const text = (id, value) => {
      const el = document.getElementById(id);
      if (el) el.textContent = value;
    };

    set('pd-name', partner.name);
    set('pd-company', partner.company);
    set('pd-email', partner.email);
    set('pd-phone', partner.phone);
    set('pd-bio', partner.bio);

    const stateInfo = partnerStateLabel(partner.state);
    text('partner-details-state', stateInfo ? stateInfo.label : (partner.state || ''));
    // Follows the reader's language, matching every other
    // producer label on this screen (the bonus names below already do).
    text('pd-tier', Ui.producerLabel(partner, 'tier_name') || App.t('ap_no_tier'));
    text('pd-rate', Number.isFinite(Number(partner.total_percent))
      ? `${Number(partner.total_percent)}%`
      : '—');
    text('pd-joined', partner.created_at ? formatDate(partner.created_at) : '—');

    const errEl = document.getElementById('partner-details-error');
    if (errEl) errEl.style.display = 'none';

    // Only a role the server admits gets a live form; everyone else reads.
    const saveBtn = document.getElementById('partner-details-save');
    if (saveBtn) saveBtn.hidden = !canManagePartners;
    ['pd-name', 'pd-company', 'pd-phone', 'pd-bio'].forEach((id) => {
      const el = document.getElementById(id);
      if (el) el.disabled = !canManagePartners;
    });

    renderHub(partner);

    const overlay = document.getElementById('partner-details-modal');
    if (overlay) { overlay.classList.add('show'); document.body.style.overflow = 'hidden'; }
  }

  /* ─── THE HUB HALF ─────────────────────────────────
     A hub for each partner: change their commission, add something, contact
     them, and see at once how their sales go -- conversion and how many deals
     sit at each stage.

     Everything is computed from the leads this screen already loaded, so the
     modal can never disagree with the table behind it. Bonus names are the one
     exception -- the roster carries only their totals, so the list is fetched
     when the modal opens.

     Deliberately NOT shown: a salesperson count. The model has no such thing
     yet and inventing a number here would be worse than
     leaving it out. */

  function figure(value, label) {
    const cell = document.createElement('div');
    cell.className = 'hub-figure';
    const v = document.createElement('div');
    v.className = 'hub-figure-value';
    v.textContent = value;
    const l = document.createElement('div');
    l.className = 'hub-figure-label';
    l.textContent = label;
    cell.append(v, l);
    return cell;
  }

  function renderHubFigures(partner, leads) {
    const host = document.getElementById('pd-figures');
    if (!host) return;

    const winKey = stagePipeline && stagePipeline.winTerminal
      ? stagePipeline.winTerminal.key : null;
    const accepted = leads.filter((lead) => lead.registration_status === 'accepted');
    const won = winKey ? accepted.filter((lead) => lead.status === winKey) : [];
    const wonValue = won.reduce((sum, lead) => sum + safeAmount(lead.value), 0);
    const earned = leads.reduce(
      (sum, lead) => sum + (lead.commission == null ? 0 : safeAmount(lead.commission)), 0,
    );
    const owed = leads.reduce((sum, lead) => (
      lead.commission != null && lead.won_at && !lead.commission_paid_at
        ? sum + safeAmount(lead.commission)
        : sum
    ), 0);

    // Accepted deals are the honest denominator: a lead the producer refused
    // never got the chance to be sold, so counting it against the partner would
    // measure the producer's own decisions.
    const conversion = accepted.length
      ? `${Math.round((won.length / accepted.length) * 100)}%`
      : '—';

    host.replaceChildren(
      figure(String(leads.length), App.t('ap_fig_submitted')),
      figure(String(accepted.length), App.t('ap_fig_accepted')),
      figure(String(won.length), App.t('ap_fig_won')),
      figure(conversion, App.t('ap_fig_conversion')),
      figure(formatCurrency(wonValue), App.t('ap_fig_won_revenue')),
      figure(formatCurrency(earned), App.t('ap_fig_commission_earned')),
      figure(formatCurrency(owed), App.t('ap_fig_payable')),
    );
  }

  function renderHubFunnel(leads) {
    const host = document.getElementById('pd-funnel');
    if (!host) return;
    host.replaceChildren();

    const stages = stagePipeline && stagePipeline.list ? stagePipeline.list : [];
    if (!stages.length) {
      host.appendChild(textElement('p', 'text-xs text-tertiary',
        App.t('ap_no_stages')));
      return;
    }

    const counts = new Map(stages.map((stage) => [stage.key, 0]));
    let waiting = 0;
    for (const lead of leads) {
      if (!lead.status) { waiting += 1; continue; }
      if (counts.has(lead.status)) counts.set(lead.status, counts.get(lead.status) + 1);
    }
    const peak = Math.max(waiting, ...counts.values(), 1);

    const row = (name, count, color) => {
      const line = document.createElement('div');
      line.className = 'hub-funnel-row';
      line.appendChild(textElement('span', 'hub-funnel-name', name));
      const bar = document.createElement('div');
      bar.className = 'hub-funnel-bar';
      const fill = document.createElement('div');
      fill.className = 'hub-funnel-fill';
      fill.style.width = `${Math.round((count / peak) * 100)}%`;
      fill.style.background = color;
      bar.appendChild(fill);
      line.appendChild(bar);
      line.appendChild(textElement('span', 'hub-funnel-count', String(count)));
      return line;
    };

    // Leads still awaiting the producer's own decision sit outside the funnel
    // and are counted separately, or they would look like a stage he forgot.
    if (waiting) {
      host.appendChild(row(App.t('ap_awaiting_your_decision'), waiting, 'var(--border-strong)'));
    }
    for (const stage of stages) {
      host.appendChild(row(Ui.stageLabel(stage), counts.get(stage.key) || 0,
        safeHexColor(stage.color) || 'var(--brand-500)'));
    }
  }

  // The invite form offers the producer's OWN levels, from /api/tiers. It used
  // to offer a hardcoded Bronze/Silver/Gold/Platinum list -- the legacy
  // users.level words -- so a producer whose levels are named differently
  // invited every partner onto a level that exists nowhere in their settings.
  function renderInviteTierSelect() {
    const select = document.getElementById('i-tier');
    if (!select) return;
    const placeholder = document.createElement('option');
    placeholder.value = '';
    placeholder.textContent = App.t('ap_no_tier');
    placeholder.selected = true;
    select.replaceChildren(
      placeholder,
      ...tiersState.map((tier) => {
        const option = document.createElement('option');
        option.value = String(tier.id);
        option.textContent = `${tier.name} — ${Number(tier.base_rate ?? 0)}%`;
        return option;
      }),
    );
  }

  function renderHubTierSelect(partner) {
    const select = document.getElementById('pd-tier-select');
    if (!select) return;
    const placeholder = document.createElement('option');
    placeholder.value = '';
    placeholder.textContent = App.t('ap_no_tier');
    placeholder.selected = partner.tier_id == null;
    placeholder.disabled = true;
    select.replaceChildren(
      placeholder,
      ...tiersState.map((tier) => {
        const option = document.createElement('option');
        option.value = String(tier.id);
        option.textContent = `${tier.name} — ${Number(tier.base_rate ?? 0)}%`;
        option.selected = Number(tier.id) === Number(partner.tier_id);
        return option;
      }),
    );
    select.disabled = !canManagePartners;
    select.dataset.partnerAction = 'hub-tier';
    select.dataset.partnerKey = detailsRecordKey || '';
  }

  async function renderHubBonuses(partner) {
    const host = document.getElementById('pd-bonuses');
    if (!host) return;
    host.replaceChildren(textElement('p', 'text-xs text-tertiary', App.t('ap_loading_bonuses')));
    try {
      const data = await ApiClient.get(`/api/partner-bonuses/${partner.id}`);
      const summary = data.partner || {};
      // Two arrays, not one: api/partner_bonuses.py::get_partner_summary splits
      // them because a percentage bonus adds to the rate while a cash one is
      // paid alongside it and is never folded into a percent.
      const percent = Array.isArray(summary.bonus_percent) ? summary.bonus_percent : [];
      const cash = Array.isArray(summary.bonus_cash) ? summary.bonus_cash : [];
      host.replaceChildren();

      const base = document.createElement('div');
      base.className = 'hub-bonus-row';
      // The tier name here follows the reader's language, same
      // as the bonus names below it.
      const summaryTierName = Ui.producerLabel(summary, 'tier_name');
      base.appendChild(textElement('span', '',
        `${App.t('leads_tier_rate')}${summaryTierName ? ` (${summaryTierName})` : ''}`));
      base.appendChild(textElement('span', '', `${Number(summary.base_rate ?? 0)}%`));
      host.appendChild(base);

      // His own name for the bonus, in the language the reader has open.
      for (const bonus of percent) {
        const line = document.createElement('div');
        line.className = 'hub-bonus-row';
        line.appendChild(textElement('span', '', Ui.producerLabel(bonus, 'name') || '—'));
        line.appendChild(textElement('span', '',
          `+${Number(bonus.effective_value ?? 0)}%`));
        host.appendChild(line);
      }
      for (const bonus of cash) {
        const line = document.createElement('div');
        line.className = 'hub-bonus-row';
        line.appendChild(textElement('span', '', Ui.producerLabel(bonus, 'name') || '—'));
        line.appendChild(textElement('span', '',
          formatCurrency(Number(bonus.effective_value ?? 0))));
        host.appendChild(line);
      }

      const total = document.createElement('div');
      total.className = 'hub-bonus-row';
      total.appendChild(textElement('span', '', App.t('ap_total_rate')));
      total.appendChild(textElement('span', '', `${Number(summary.total_percent ?? 0)}%`));
      host.appendChild(total);

      if (!percent.length && !cash.length) {
        host.appendChild(textElement('p', 'text-xs text-tertiary',
          App.t('ap_no_extra_bonuses')));
      }
    } catch (error) {
      host.replaceChildren(textElement('p', 'text-xs text-tertiary',
        App.t('ap_bonuses_failed')));
    }
  }

  function renderHubContact(partner) {
    const mail = document.getElementById('pd-mailto');
    if (mail) {
      mail.href = partner.email ? `mailto:${encodeURIComponent(partner.email)}` : '#';
      mail.hidden = !partner.email;
    }
    const tel = document.getElementById('pd-tel');
    if (tel) {
      // A phone number that is not there is not a dead button; it is no button.
      tel.href = partner.phone ? `tel:${String(partner.phone).replace(/[^+0-9]/g, '')}` : '#';
      tel.hidden = !partner.phone;
    }
  }

  function renderHub(partner) {
    const leads = leadsForPartner(partner.id);
    renderHubFigures(partner, leads);
    renderHubFunnel(leads);
    renderHubTierSelect(partner);
    renderHubContact(partner);
    void renderHubBonuses(partner);
  }

  function closePartnerDetails() {
    detailsRecordKey = null;
    const overlay = document.getElementById('partner-details-modal');
    if (overlay) { overlay.classList.remove('show'); document.body.style.overflow = ''; }
  }

  async function savePartnerDetails() {
    const partner = detailsRecordKey ? partnerRecords.get(detailsRecordKey) : null;
    if (!partner) return;
    const errEl = document.getElementById('partner-details-error');
    if (errEl) errEl.style.display = 'none';

    const body = {
      name: document.getElementById('pd-name').value.trim(),
      company: document.getElementById('pd-company').value.trim(),
      phone: document.getElementById('pd-phone').value.trim(),
      bio: document.getElementById('pd-bio').value.trim(),
    };

    if (!body.name || !body.company) {
      if (errEl) {
        errEl.textContent = App.t('ap_name_company_required');
        errEl.style.display = 'block';
      }
      return;
    }

    try {
      await ApiClient.patch(`/api/users/${partner.id}`, body);
      global.showToast('success', App.t('st_saved'), App.t('ap_partner_updated'));
      closePartnerDetails();
      await loadPartners();
    } catch (error) {
      if (errEl) {
        errEl.textContent = error.message;
        errEl.style.display = 'block';
      }
    }
  }

  // ─── PARTNER INVITE ──
  function openInviteModal() {
    ['i-name', 'i-company', 'i-email', 'i-phone', 'i-bio'].forEach((id) => {
      const el = document.getElementById(id);
      if (el) el.value = '';
    });
    renderInviteTierSelect();
    document.getElementById('invite-error').style.display = 'none';
    document.getElementById('invite-result').hidden = true;
    document.getElementById('invite-link').value = '';
    document.getElementById('invite-mail-status').textContent = '';
    openModal('invite-modal');
  }

  async function sendInvite() {
    const errEl = document.getElementById('invite-error');
    errEl.style.display = 'none';

    const body = {
      name:    document.getElementById('i-name').value.trim(),
      company: document.getElementById('i-company').value.trim(),
      email:   document.getElementById('i-email').value.trim(),
      phone:   document.getElementById('i-phone').value.trim(),
      bio:     document.getElementById('i-bio').value.trim(),
    };

    // The tier is optional: an invitation with no tier picked creates a
    // partner the producer assigns a level to later, on this same screen.
    const tierValue = document.getElementById('i-tier')?.value || '';
    if (tierValue) body.tier_id = Number(tierValue);

    if (!body.name || !body.company || !body.email) {
      errEl.textContent = App.t('ap_invite_required');
      errEl.style.display = 'block';
      return;
    }

    try {
      const data = await ApiClient.post('/api/users/invite', body);
      document.getElementById('invite-link').value = data.invite_url;
      // Mail outcome is stated plainly either way — a failed send is never
      // presented as a failed invitation, since a fresh install's SMTP
      // starts disabled and the link must still be the day-one path.
      document.getElementById('invite-mail-status').textContent = data.sent
        ? App.t('ap_invite_sent', { email: data.user.email })
        : App.t('ap_smtp_missing');
      document.getElementById('invite-result').hidden = false;
      await loadPartners();
    } catch (err) {
      errEl.textContent = err.message;
      errEl.style.display = 'block';
    }
  }

  async function copyInviteLink() {
    const input = document.getElementById('invite-link');
    const link = input.value;
    if (!link) return;

    if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
      try {
        await navigator.clipboard.writeText(link);
        global.showToast('success', App.t('toast_copied'), App.t('toast_invite_link_clipboard'));
        return;
      } catch {
        // Falls through to the keyboard fallback below.
      }
    }

    // The Clipboard API is unavailable on plain http origins in some
    // browsers — exactly the local, unencrypted deployment this product
    // runs on today. Select the text so it can still be copied by hand.
    input.removeAttribute('readonly');
    input.focus();
    input.select();
    input.setAttribute('readonly', '');
    global.showToast('warning', App.t('toast_link_selected'), App.t('ap_copy_manually'));
  }

  // ─── ACTIONS ─────────────────────────────────────────────────
  function bindActions() {
    if (actionsBound) return;
    actionsBound = true;

    document.getElementById('partners-tbody')?.addEventListener('click', (event) => {
      const button = event.target.closest('[data-partner-action][data-partner-key]');
      if (!button || !partnerRecords.has(button.dataset.partnerKey)) return;
      const action = button.dataset.partnerAction;
      if (action === 'details') openPartnerDetails(button.dataset.partnerKey);
      if (action === 'deactivate') openDeactivateModal(button.dataset.partnerKey);
      if (action === 'accept' || action === 'restore') {
        void setAccountActive(button.dataset.partnerKey, button);
      }
      if (action === 'reject') void rejectPartner(button.dataset.partnerKey, button);
    });

    document.getElementById('partners-tbody')?.addEventListener('change', (event) => {
      const select = event.target.closest('select[data-partner-action="tier"][data-partner-key]');
      if (!select || !partnerRecords.has(select.dataset.partnerKey)) return;
      void changeTier(select.dataset.partnerKey, select.value, select);
    });

    // The hub's own level control, writing through the same changeTier the row
    // uses so there is one writer and one audit entry shape for a tier change.
    document.getElementById('pd-tier-select')?.addEventListener('change', (event) => {
      const select = event.target;
      const key = select.dataset.partnerKey;
      if (!key || !partnerRecords.has(key)) return;
      void changeTier(key, select.value, select);
    });

    // This screen owns the partner invite; users.html holds the producer's
    // own team.
    document.getElementById('add-partner-btn')?.addEventListener('click', openInviteModal);
    document.getElementById('invite-modal-close')?.addEventListener('click', () => closeModal('invite-modal'));
    document.getElementById('invite-modal-cancel')?.addEventListener('click', () => closeModal('invite-modal'));
    document.getElementById('send-invite-btn')?.addEventListener('click', () => void sendInvite());
    document.getElementById('copy-invite-btn')?.addEventListener('click', () => void copyInviteLink());

    document.getElementById('partner-details-close')?.addEventListener('click', closePartnerDetails);
    document.getElementById('partner-details-cancel')?.addEventListener('click', closePartnerDetails);
    document.getElementById('partner-details-save')?.addEventListener('click', () => void savePartnerDetails());

    document.getElementById('partners-retry-btn')?.addEventListener('click', () => void loadPartners());
    document.getElementById('deactivate-partner-close')?.addEventListener('click', cancelDeactivate);
    document.getElementById('deactivate-partner-cancel')?.addEventListener('click', cancelDeactivate);
    document.getElementById('deactivate-partner-confirm')?.addEventListener('click', () => void confirmDeactivate());
  }

  async function initialize() {
    bindActions();
    await loadPartners();
    // The table rows, the tier chips and the count label are built here, so a
    // language or theme switch has to draw them again. bindActions stays out
    // of it -- the listeners are already attached.
    App.onRerender(() => { void loadPartners(); });
  }

  /**
   * Load and render the partner list exactly once after AdminShell authorization.
   * @param {object} user authoritative public admin identity from AdminShell
   * @returns {Promise<void>}
   */
  function init(user) {
    canManagePartners = !!user && (user.role === 'admin' || user.role === 'manager');
    if (!initialization) initialization = initialize();
    return initialization;
  }

  global.AdminPartners = Object.freeze({ init });
})(window);
