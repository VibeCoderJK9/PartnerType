/* ============================================================
   PARTNERTYPE — AFFILIATE SALES
   Affiliate › Sales from referrals: orders reported by the shop, with
   filters, and the administrator's decisions (approve one or the
   selected, reject, assign an order without a partner, reverse the
   unpaid commission of an order refunded after approval).
   Staff read the list; only the administrator sees the decision
   buttons, and the server refuses them from anyone else anyway.
   Order ids and partner names are drawn with textContent only.
   ============================================================ */

(function initAffiliateSales(global) {
  'use strict';

  const LIST_URL = '/api/affiliate/conversions';
  const STATUSES = ['pending', 'unattributed', 'approved', 'rejected', 'refunded',
    'refund_after_approval', 'reversed'];
  const BADGES = {
    pending: 'badge-warning',
    unattributed: 'badge-neutral',
    approved: 'badge-success',
    rejected: 'badge-neutral',
    refunded: 'badge-neutral',
    refund_after_approval: 'badge-error',
    reversed: 'badge-neutral',
  };

  let data = null;
  let partners = null;
  let status = 'pending';
  let page = 1;
  let selected = new Set();
  let busy = false;
  let rerenderBound = false;

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function button(className, text) {
    const node = el('button', className, text);
    node.type = 'button';
    return node;
  }

  function option(text, value) {
    const node = el('option', '', text);
    node.value = value;
    return node;
  }

  function isAdmin() {
    try {
      return JSON.parse(sessionStorage.getItem('ph_session') || '{}').role === 'admin';
    } catch {
      return false;
    }
  }

  function locale() {
    return App.lang === 'en' ? 'en-GB' : 'pl-PL';
  }

  function money(amount, currency) {
    if (amount === null || amount === undefined) return '—';
    const value = Number(amount);
    if (!Number.isFinite(value)) return String(amount);
    return new Intl.NumberFormat(locale(), {
      style: 'currency', currency: currency || 'PLN',
    }).format(value);
  }

  function day(iso) {
    if (!iso) return '—';
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return String(iso);
    return new Intl.DateTimeFormat(locale(), {
      day: '2-digit', month: '2-digit', year: 'numeric',
    }).format(date);
  }

  // Results the server gives for an order it left as it was. It answers 200
  // for the whole request, so only these say what happened to each order.
  const NOT_DONE = {
    partner_inactive: 'aff_result_partner_inactive',
    skipped: 'aff_result_skipped',
  };

  /** Money decisions: say what the server actually did, order by order. */
  function report(body, success, done) {
    const results = Array.isArray(body?.results) ? body.results : [];
    const counts = new Map();
    for (const item of results) {
      let key = item?.result;
      // Anything unrecognised is reported as not done rather than as success.
      if (key !== done && !Object.hasOwn(NOT_DONE, key)) key = 'skipped';
      counts.set(key, (counts.get(key) || 0) + 1);
    }
    const changed = counts.get(done) || 0;
    if (changed) {
      showToast('success', App.t(success),
        results.length > 1 ? App.pluralize(changed, 'unit_order') : '');
    }
    for (const [result, key] of Object.entries(NOT_DONE)) {
      const count = counts.get(result);
      if (!count) continue;
      showToast('warning', App.t(key, { orders: App.pluralize(count, 'unit_order') }), '');
    }
  }

  async function act(request, success, done) {
    if (busy) return;
    busy = true;
    try {
      const body = await request();
      if (done) report(body, success, done);
      else showToast('success', App.t(success), '');
      selected = new Set();
      await refresh();
    } catch (error) {
      showToast('error', App.t('aff_action_failed'), error?.message || '');
    } finally {
      busy = false;
      render();
    }
  }

  function approve(ids) {
    return act(() => ApiClient.post(`${LIST_URL}/approve`, { ids }), 'aff_approved_toast',
      'approved');
  }

  function reject(ids) {
    return act(() => ApiClient.post(`${LIST_URL}/reject`, { ids }), 'aff_rejected_toast',
      'rejected');
  }

  function assign(id, partnerId) {
    return act(() => ApiClient.post(`${LIST_URL}/${id}/assign`, { partner_id: partnerId }),
      'aff_assigned_toast');
  }

  function reverse(item) {
    // Taking back a partner's commission is worth one question.
    if (!window.confirm(App.t('aff_reverse_q', { order: item.order_id }))) return undefined;
    return act(() => ApiClient.post(`${LIST_URL}/${item.id}/reverse`, {}), 'aff_reversed_toast');
  }

  function assignControl(item) {
    const wrap = el('div');
    wrap.style.cssText = 'display:flex;gap:6px;align-items:center';
    const choose = document.createElement('select');
    choose.className = 'form-input';
    choose.setAttribute('aria-label', App.t('aff_choose_partner'));
    choose.appendChild(option(App.t('aff_choose_partner'), ''));
    for (const partner of partners || []) {
      choose.appendChild(option(partner.name || partner.email || String(partner.id),
        String(partner.id)));
    }
    const go = button('btn btn-secondary btn-sm', App.t('aff_assign'));
    go.addEventListener('click', () => {
      const partnerId = Number.parseInt(choose.value, 10);
      if (partnerId > 0) assign(item.id, partnerId);
    });
    wrap.append(choose, go);
    return wrap;
  }

  /**
   * Why a pending order cannot be approved. Only a deactivated partner may be
   * reactivated: a deleted one is gone, and an erased account must never be
   * brought back to be paid.
   */
  function inactiveReason(item) {
    if (item.partner_id == null) return 'aff_partner_removed_info';
    if (item.partner_state === 'erased') return 'aff_partner_erased_info';
    return 'aff_partner_inactive_info';
  }

  function actions(item) {
    const cell = el('div');
    cell.style.cssText = 'display:flex;gap:6px;flex-wrap:wrap';
    if (item.status === 'refund_after_approval' && item.commission_paid) {
      cell.appendChild(el('span', 'text-xs text-tertiary', App.t('aff_paid_info')));
      return cell;
    }
    const partial = item.status === 'refund_after_approval'
      && Number(item.refunded_amount) < Number(item.amount);
    if (partial) {
      cell.appendChild(el('span', 'text-xs text-tertiary', App.t('aff_partial_info')));
      return cell;
    }
    if (!isAdmin()) return cell;
    if (item.status === 'pending' && item.partner_active === false) {
      // The server refuses to approve it; say why and leave only Reject.
      cell.appendChild(el('span', 'text-xs text-tertiary', App.t(inactiveReason(item))));
      const no = button('btn btn-secondary btn-sm', App.t('aff_reject'));
      no.addEventListener('click', () => reject([item.id]));
      cell.appendChild(no);
    } else if (item.status === 'pending') {
      const yes = button('btn btn-primary btn-sm', App.t('aff_approve'));
      yes.addEventListener('click', () => approve([item.id]));
      const no = button('btn btn-secondary btn-sm', App.t('aff_reject'));
      no.addEventListener('click', () => reject([item.id]));
      cell.append(yes, no);
    } else if (item.status === 'unattributed') {
      const no = button('btn btn-secondary btn-sm', App.t('aff_reject'));
      no.addEventListener('click', () => reject([item.id]));
      cell.append(assignControl(item), no);
    } else if (item.status === 'refund_after_approval') {
      const undo = button('btn btn-danger btn-sm', App.t('aff_reverse'));
      undo.addEventListener('click', () => reverse(item));
      cell.appendChild(undo);
    }
    return cell;
  }

  function checkbox(item) {
    if (!isAdmin() || item.status !== 'pending' || item.partner_active === false) {
      return el('span');
    }
    const box = document.createElement('input');
    box.type = 'checkbox';
    box.checked = selected.has(item.id);
    box.setAttribute('aria-label', App.t('aff_select_order', { order: item.order_id }));
    box.addEventListener('change', () => {
      if (box.checked) selected.add(item.id);
      else selected.delete(item.id);
      render();
    });
    return box;
  }

  function filters() {
    const bar = el('div');
    bar.style.cssText = 'display:flex;gap:8px;align-items:center;flex-wrap:wrap';
    const choose = document.createElement('select');
    choose.className = 'form-input';
    choose.id = 'aff-status-filter';
    choose.setAttribute('aria-label', App.t('aff_filter_status'));
    choose.appendChild(option(App.t('aff_status_all'), ''));
    for (const value of STATUSES) {
      choose.appendChild(option(App.t(`aff_status_${value}`), value));
    }
    choose.value = status;
    choose.addEventListener('change', async () => {
      status = choose.value;
      page = 1;
      selected = new Set();
      await refresh();
      render();
    });
    bar.appendChild(choose);
    if (isAdmin() && selected.size) {
      const bulk = button('btn btn-primary btn-sm',
        App.t('aff_approve_selected', { count: selected.size }));
      bulk.id = 'aff-approve-selected';
      bulk.addEventListener('click', () => approve([...selected]));
      bar.appendChild(bulk);
    }
    return bar;
  }

  function table(items) {
    const wrap = el('div', 'table-scroll');
    wrap.style.marginTop = '12px';
    const tbl = el('table', 'data-table');
    tbl.id = 'aff-table';
    const head = el('tr');
    for (const key of ['', 'aff_col_order', 'aff_col_date', 'aff_col_partner', 'aff_col_amount',
      'aff_col_commission', 'aff_col_status', '']) {
      head.appendChild(el('th', '', key ? App.t(key) : ''));
    }
    const thead = el('thead');
    thead.appendChild(head);
    const tbody = el('tbody');
    for (const item of items) {
      const row = el('tr');
      row.dataset.id = String(item.id);
      const amount = el('div', '', money(item.amount, item.currency));
      if (item.refunded_amount) {
        amount.appendChild(el('div', 'text-xs text-tertiary',
          App.t('aff_refunded_amount', { amount: money(item.refunded_amount, item.currency) })));
      }
      const badge = el('span', `badge ${BADGES[item.status] || 'badge-neutral'}`,
        App.t(`aff_status_${item.status}`));
      const cells = [
        checkbox(item),
        item.order_id,
        day(item.occurred_at),
        item.partner_name || App.t('aff_no_partner'),
        amount,
        money(item.commission, item.currency),
        badge,
        actions(item),
      ];
      for (const cell of cells) {
        const td = el('td');
        if (cell instanceof Node) td.appendChild(cell);
        else td.textContent = String(cell ?? '');
        row.appendChild(td);
      }
      tbody.appendChild(row);
    }
    tbl.append(thead, tbody);
    wrap.appendChild(tbl);
    return wrap;
  }

  function pager() {
    const bar = el('div');
    bar.style.cssText = 'display:flex;gap:8px;align-items:center;margin-top:12px';
    const pages = Math.max(1, Math.ceil((data.total || 0) / (data.per_page || 50)));
    const prev = button('btn btn-secondary btn-sm', App.t('aff_prev'));
    prev.disabled = page <= 1;
    // A selection only ever covers the orders on screen.
    prev.addEventListener('click', async () => {
      page -= 1;
      selected = new Set();
      await refresh();
      render();
    });
    const next = button('btn btn-secondary btn-sm', App.t('aff_next'));
    next.disabled = page >= pages;
    next.addEventListener('click', async () => {
      page += 1;
      selected = new Set();
      await refresh();
      render();
    });
    bar.append(prev, el('span', 'text-sm text-secondary',
      App.t('aff_page', { page, pages })), next);
    return bar;
  }

  function render() {
    const host = document.getElementById('affiliate-sales');
    if (!host) return;
    host.textContent = '';
    host.appendChild(filters());
    if (!data) {
      host.appendChild(el('p', 'text-sm text-tertiary', App.t('aff_load_failed')));
      return;
    }
    if (!data.items.length) {
      host.appendChild(el('p', 'text-sm text-tertiary', App.t('aff_empty')));
      return;
    }
    host.appendChild(table(data.items));
    host.appendChild(pager());
  }

  async function refresh() {
    const query = new URLSearchParams({ page: String(page) });
    if (status) query.set('status', status);
    try {
      const body = await ApiClient.get(`${LIST_URL}?${query}`);
      data = body && Array.isArray(body.items) ? body : null;
    } catch {
      data = null;
    }
  }

  async function load() {
    const host = document.getElementById('affiliate-sales');
    if (!host) return;
    // A notification may link straight to one status (?status=...); only a
    // known status is taken, anything else opens the default view.
    const requested = new URLSearchParams(window.location.search).get('status');
    status = STATUSES.includes(requested) ? requested : 'pending';
    page = 1;
    selected = new Set();
    const [, people] = await Promise.allSettled([refresh(), ApiClient.get('/api/partners')]);
    partners = people.status === 'fulfilled' && Array.isArray(people.value?.partners)
      ? people.value.partners : [];
    render();
    const registerRerender = App.onRerender;
    if (!rerenderBound && typeof registerRerender === 'function') {
      rerenderBound = true;
      App.onRerender(render);
    }
  }

  global.AffiliateSales = { load, render };
  document.addEventListener('DOMContentLoaded', load);
})(window);
