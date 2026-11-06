/* ============================================================
   PARTNERTYPE — MY REFERRAL LINK (partner's Affiliate page)
   The partner's personal link to the producer's shop, with what it
   brought: clicks, purchases, pending and approved commission.
   While the producer has the programme switched off, or the summary
   cannot be read, the card stays hidden and a notice says why.
   ============================================================ */

(function initPartnerAffiliate(global) {
  'use strict';

  const SUMMARY_URL = '/api/affiliate/me';
  const LINK_URL = '/api/affiliate/me/link';

  let summary = null;
  let rerenderBound = false;

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function locale() {
    return App.lang === 'en' ? 'en-GB' : 'pl-PL';
  }

  function money(amount) {
    const value = Number(amount);
    if (!Number.isFinite(value)) return '—';
    return new Intl.NumberFormat(locale(), {
      style: 'currency', currency: summary.currency || 'PLN',
    }).format(value);
  }

  async function copy(text, input) {
    if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
      try {
        await navigator.clipboard.writeText(text);
        showToast('success', App.t('aff_link_copied'), '');
        return;
      } catch {
        // Falls through to selecting the text for a manual copy.
      }
    }
    input.focus();
    input.select();
    showToast('warning', App.t('int_copy_failed'), '');
  }

  function stat(labelKey, value, id) {
    const box = el('div');
    box.appendChild(el('div', 'text-xs text-tertiary', App.t(labelKey)));
    const number = el('div', 'font-bold text-primary', value);
    number.id = id;
    box.appendChild(number);
    return box;
  }

  // The line shown in place of the card; no key clears it.
  function notice(key) {
    const node = document.getElementById('affiliate-off');
    if (!node) return;
    node.hidden = !key;
    node.textContent = key ? App.t(key) : '';
  }

  async function createLink(trigger) {
    trigger.disabled = true;
    try {
      summary = await ApiClient.post(LINK_URL, {});
    } catch (error) {
      showToast('error', App.t('aff_action_failed'), error?.message || '');
    }
    render();
  }

  function render() {
    const card = document.getElementById('affiliate-card');
    const host = document.getElementById('affiliate-card-body');
    if (!card || !host) return;
    if (!summary || !summary.enabled) {
      card.hidden = true;
      notice(summary ? 'aff_partner_off' : 'aff_partner_failed');
      return;
    }
    notice(null);
    card.hidden = false;
    host.textContent = '';
    if (!summary.link) {
      host.appendChild(el('p', 'text-sm text-secondary', App.t('aff_no_link_yet')));
      const create = el('button', 'btn btn-primary btn-sm', App.t('aff_create_link'));
      create.type = 'button';
      create.id = 'affiliate-create-link';
      create.addEventListener('click', () => createLink(create));
      host.appendChild(create);
      return;
    }
    host.appendChild(el('p', 'text-sm text-secondary', App.t('aff_link_hint')));
    const row = el('div');
    row.style.cssText = 'display:flex;gap:8px;align-items:center;margin-top:8px';
    const input = document.createElement('input');
    input.className = 'form-input';
    input.id = 'affiliate-link';
    input.readOnly = true;
    input.value = summary.link.url;
    input.setAttribute('aria-label', App.t('aff_my_link'));
    const button = el('button', 'btn btn-secondary btn-sm', App.t('int_copy'));
    button.type = 'button';
    button.id = 'affiliate-copy';
    button.addEventListener('click', () => copy(input.value, input));
    row.append(input, button);
    host.appendChild(row);

    const stats = el('div');
    stats.style.cssText = 'display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));'
      + 'gap:12px;margin-top:16px';
    stats.append(
      stat('aff_clicks', String(summary.clicks ?? 0), 'affiliate-clicks'),
      stat('aff_purchases', String(summary.purchases ?? 0), 'affiliate-purchases'),
      stat('aff_pending_commission', money(summary.pending_commission), 'affiliate-pending'),
      stat('aff_approved_commission', money(summary.approved_commission), 'affiliate-approved'),
    );
    host.appendChild(stats);
  }

  async function load() {
    if (!document.getElementById('affiliate-card')) return;
    try {
      summary = await ApiClient.get(SUMMARY_URL);
    } catch {
      summary = null;
    }
    render();
    const registerRerender = App.onRerender;
    if (!rerenderBound && typeof registerRerender === 'function') {
      rerenderBound = true;
      App.onRerender(render);
    }
  }

  global.PartnerAffiliate = { load, render };
  document.addEventListener('DOMContentLoaded', load);
})(window);
