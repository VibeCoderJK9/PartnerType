/* ============================================================
   PARTNERTYPE — BULK SELECT + ADVANCED FILTER
   Injected into admin/leads page via leads6.js
   ============================================================ */

(function() {
  'use strict';

  const selectedIds = new Set();
  const operationKeys = new Map();
  let bulkBusy = false;
  let allLeads = [];

  // ─── INIT ────────────────────────────────────────────────

  document.addEventListener('DOMContentLoaded', () => {
    if (document.body.dataset.page !== 'admin-leads') return;
    injectFilterPanel();
    injectBulkBar();
    // Hook into table renders (wait for admin.js to paint)
    observeTable();
  });

  // ─── FILTER PANEL ────────────────────────────────────────

  function injectFilterPanel() {
    const existingPanel = document.getElementById('filter-panel');
    if (existingPanel) {
      document.getElementById('filter-clear-btn')?.addEventListener('click', clearFilters);
      return;
    }
    const area = document.querySelector('.page-header-actions') ||
                 document.querySelector('.page-header');
    if (!area) return;

    // Toggle button
    const toggleBtn = document.createElement('button');
    toggleBtn.className = 'btn btn-secondary btn-sm';
    toggleBtn.id = 'filter-toggle-btn';
    toggleBtn.innerHTML = `
      <svg width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
        <polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3"/>
      </svg>
      <span data-i18n="filters_toggle">${App.t('filters_toggle')}</span>
    `;

    const actionsDiv = document.querySelector('.page-header-actions');
    if (actionsDiv) actionsDiv.prepend(toggleBtn);

    // Build panel
    const panel = document.createElement('div');
    panel.id = 'filter-panel';
    panel.className = 'filter-panel';
    panel.innerHTML = `
      <div class="filter-group">
        <div class="filter-label" data-i18n="data_od">${App.t('data_od')}</div>
        <input type="date" id="filter-date-from" class="form-input" style="width:150px;font-size:0.8rem">
      </div>
      <div class="filter-group">
        <div class="filter-label" data-i18n="data_do">${App.t('data_do')}</div>
        <input type="date" id="filter-date-to" class="form-input" style="width:150px;font-size:0.8rem">
      </div>
      <div class="filter-group" style="align-self:flex-end">
        <button class="btn btn-primary btn-sm" id="filter-apply-btn" data-i18n="cm_apply">${App.t('cm_apply')}</button>
      </div>
      <div class="filter-group" style="align-self:flex-end">
        <button class="btn btn-ghost btn-sm" id="filter-clear-btn" data-i18n="wyczysc">${App.t('wyczysc')}</button>
      </div>
    `;

    // Insert after page-header
    const pageHeader = document.querySelector('.page-header');
    if (pageHeader?.parentNode) pageHeader.parentNode.insertBefore(panel, pageHeader.nextSibling);

    // Events
    toggleBtn.addEventListener('click', () => {
      panel.classList.toggle('open');
      toggleBtn.classList.toggle('active');
    });

    document.getElementById('filter-apply-btn')?.addEventListener('click', applyFilters);
    document.getElementById('filter-clear-btn')?.addEventListener('click', clearFilters);
  }

  function applyFilters() {
    if (window.applyLocalFilters) window.applyLocalFilters();
  }

  function clearFilters() {
    ['filter-date-from', 'filter-date-to', 'filter-value', 'leads-search'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.value = '';
    });
    const statusEl = document.getElementById('filter-status');
    const statusTextEl = document.getElementById('status-search-input');
    if (statusEl) statusEl.value = '';
    if (statusTextEl) statusTextEl.value = '';
    
    const partnerEl = document.getElementById('filter-partner');
    const partnerTextEl = document.getElementById('partner-search-input');
    if (partnerEl) partnerEl.value = '';
    if (partnerTextEl) partnerTextEl.value = '';

    const overdueEl = document.getElementById('filter-overdue');
    if (overdueEl) overdueEl.checked = false;
    
    if (window.applyLocalFilters) window.applyLocalFilters();
  }

  // ─── BULK ACTION BAR ─────────────────────────────────────

  function injectBulkBar() {
    const mainArea = document.querySelector('.main-area');
    if (!mainArea) return;

    const bar = document.createElement('div');
    bar.id = 'bulk-bar';
    bar.className = 'bulk-bar';
    bar.style.flexWrap = 'wrap';
    bar.innerHTML = `
      <div class="bulk-bar-count"><span id="bulk-count">0</span> <span id="bulk-count-label"></span></div>
      <button class="bulk-btn bulk-btn-accept" id="bulk-accept-btn">
        <svg width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><polyline points="20 6 9 17 4 12"/></svg>
        <span data-i18n="ap_accept">${App.t('ap_accept')}</span>
      </button>
      <button class="bulk-btn bulk-btn-reject" id="bulk-reject-btn">
        <svg width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
        <span data-i18n="ap_reject">${App.t('ap_reject')}</span>
      </button>
      <button class="bulk-btn bulk-btn-clear" id="bulk-clear-btn" data-i18n="assign_cancel_btn">${App.t('assign_cancel_btn')}</button>
      <div id="bulk-reject-form" hidden>
        <label for="bulk-reject-reason" data-i18n="bulk_reject_reason">${App.t('bulk_reject_reason')}</label>
        <textarea id="bulk-reject-reason" class="form-input" rows="2" style="max-width:100%"></textarea>
        <button class="bulk-btn bulk-btn-reject" id="bulk-reject-confirm-btn" data-i18n="bulk_reject_selected">${App.t('bulk_reject_selected')}</button>
      </div>
    `;
    mainArea.appendChild(bar);
    updateBulkBar();
    const result = document.createElement('div');
    result.id = 'bulk-result';
    result.setAttribute('role', 'status');
    result.style.cssText = 'white-space:pre-wrap;overflow-wrap:anywhere';
    result.hidden = true;
    mainArea.appendChild(result);

    document.getElementById('bulk-accept-btn').addEventListener('click', () => executeBulk('accept'));
    document.getElementById('bulk-reject-btn').addEventListener('click', () => {
      document.getElementById('bulk-reject-form').hidden = false;
      document.getElementById('bulk-reject-reason').focus();
    });
    document.getElementById('bulk-reject-confirm-btn').addEventListener('click', () => executeBulk('reject'));
    document.getElementById('bulk-clear-btn').addEventListener('click', clearSelection);
  }

  function updateBulkBar() {
    const count = selectedIds.size;
    const bar   = document.getElementById('bulk-bar');
    const badge = document.getElementById('bulk-count');
    if (!bar) return;
    if (badge) badge.textContent = String(count);
    const label = document.getElementById('bulk-count-label');
    if (label) label.textContent = App.plural(count, 'bulk_selected_noun');
    bar.classList.toggle('visible', count > 0);
  }

  function clearSelection() {
    if (bulkBusy) return;
    selectedIds.clear();
    const rejectForm = document.getElementById('bulk-reject-form');
    const rejectReason = document.getElementById('bulk-reject-reason');
    if (rejectForm) rejectForm.hidden = true;
    if (rejectReason) rejectReason.value = '';
    document.querySelectorAll('.row-check').forEach(cb => {
      cb.checked = false;
      cb.closest('tr')?.classList.remove('table-row-selected');
    });
    document.getElementById('select-all-cb')?.checked && (document.getElementById('select-all-cb').checked = false);
    updateBulkBar();
  }

  async function executeBulk(action) {
    if (bulkBusy || !selectedIds.size) return;
    const message = document.getElementById('bulk-reject-reason').value.trim();
    if (action === 'reject' && (!message || Array.from(message).length > 4000)) {
      showToast('error', App.t('bulk_reason_required'), App.t('bulk_reason_length'));
      return;
    }
    const rows = new Map([...document.querySelectorAll('.data-table tbody tr[data-id]')]
      .map(row => [row.dataset.id, row]));
    const items = [];
    for (const id of selectedIds) {
      const raw = rows.get(id)?.dataset.revision;
      const revision = Number(raw);
      if (raw == null || !/^\d+$/.test(raw) || !Number.isSafeInteger(revision)) continue;
      const key = JSON.stringify([action, id, revision, action === 'reject' ? message : null]);
      if (!operationKeys.has(key)) {
        const randomKey = Array.from(crypto.getRandomValues(new Uint8Array(16)),
          byte => byte.toString(16).padStart(2, '0')).join('');
        operationKeys.set(key, randomKey);
      }
      items.push({ id, expected_revision: revision, operation_id: operationKeys.get(key),
        ...(action === 'reject' ? { message } : {}) });
    }
    const skipped = selectedIds.size - items.length;
    if (skipped) showToast('warning', App.t('bulk_some_skipped'),
      App.t('bulk_skipped_detail', { ids: skipped }));
    if (!items.length) return;
    bulkBusy = true;
    const controls = [...document.querySelectorAll('#bulk-bar button, #bulk-reject-reason, .row-check')];
    controls.forEach(control => { control.disabled = true; });

    try {
      const d = await ApiClient.post('/api/leads/bulk', { action, items });
      if (!Array.isArray(d.ok) || !Array.isArray(d.error)) throw new Error(App.t('bulk_bad_response'));
      const result = document.getElementById('bulk-result');
      result.hidden = false;
      const summary = App.t('bulk_summary', { saved: d.ok.length, failed: d.error.length, skipped });
      result.textContent = summary;
      d.error.forEach(item => {
        result.textContent += `\n${item.id}: ${item.reason || App.t('bulk_save_error')}`;
      });
      showToast(d.error.length ? 'warning' : 'success',
        d.error.length ? App.t('bulk_partial_save') : App.t('toast_done'), summary);
      bulkBusy = false;
      clearSelection();
      bulkBusy = true;
      operationKeys.clear();
      document.getElementById('bulk-reject-form').hidden = true;
      document.getElementById('bulk-reject-reason').value = '';
      try {
        if (window.AdminLeads?.reload) await window.AdminLeads.reload();
        else location.reload();
      } catch (_) {
        showToast('warning', App.t('toast_result_saved'), App.t('bulk_refresh_failed'));
      }
    } catch(err) {
      showToast('error', App.t('bulk_error'), err.message);
    } finally {
      bulkBusy = false;
      controls.forEach(control => { control.disabled = false; });
      document.querySelectorAll('.row-check').forEach(control => { control.disabled = false; });
    }
  }

  // ─── CHECKBOXES ──────────────────────────────────────────

  function addCheckboxes() {
    const table = document.querySelector('.data-table');
    if (!table) return;

    // Header checkbox
    const thead = table.querySelector('thead tr');
    if (thead && !thead.querySelector('.select-all-th')) {
      const th = document.createElement('th');
      th.className = 'select-all-th';
      th.style.width = '36px';
      th.innerHTML = '<input type="checkbox" id="select-all-cb" class="row-check">';
      thead.prepend(th);
      document.getElementById('select-all-cb')?.addEventListener('change', e => {
        document.querySelectorAll('.row-check:not(#select-all-cb)').forEach(cb => {
          cb.checked = e.target.checked;
          const id = cb.dataset.id;
          if (id) e.target.checked ? selectedIds.add(id) : selectedIds.delete(id);
          cb.closest('tr')?.classList.toggle('table-row-selected', e.target.checked);
        });
        updateBulkBar();
      });
    }

    // Row checkboxes
    table.querySelectorAll('tbody tr').forEach(row => {
      if (row.querySelector('.row-check')) return;
      const leadId = row.dataset.id;
      if (!leadId) return;
      const td = document.createElement('td');
      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.className = 'row-check';
      checkbox.disabled = bulkBusy;
      checkbox.dataset.id = leadId;
      checkbox.checked = selectedIds.has(leadId);
      td.appendChild(checkbox);
      row.prepend(td);
      if (selectedIds.has(leadId)) row.classList.add('table-row-selected');
      td.querySelector('input').addEventListener('change', e => {
        if (e.target.checked) { selectedIds.add(leadId); row.classList.add('table-row-selected'); }
        else                  { selectedIds.delete(leadId); row.classList.remove('table-row-selected'); }
        updateBulkBar();
      });
    });
  }

  // ─── OBSERVE TABLE RENDERS ───────────────────────────────

  function observeTable() {
    const target = document.getElementById('leads-tbody') || document.querySelector('.data-table tbody');
    if (!target) { setTimeout(observeTable, 300); return; }
    const obs = new MutationObserver(() => { addCheckboxes(); });
    obs.observe(target, { childList: true });
    addCheckboxes();
  }

  App.onRerender(updateBulkBar);
})();
