/* ============================================================
   PARTNERTYPE — LEAD COMMENTS
   Thread of comments rendered in lead modal / detail page.
   ============================================================ */

const Comments = (() => {
  'use strict';

  // Each container remembers which lead it was last asked to show. Responses
  // can arrive out of order when leads are opened in quick succession, so a
  // response is drawn only if it belongs to the most recent request for that
  // container, and a comment is always posted to the lead whose thread is on
  // screen.
  let loadSeq = 0;
  const containerState = new WeakMap();
  const commentRecords = new Map();
  const boundContainers = new WeakSet();

  // ─── PUBLIC API ──────────────────────────────────────────

  async function load(leadId, containerSelector, user) {
    const container = typeof containerSelector === 'string'
      ? document.querySelector(containerSelector)
      : containerSelector;
    if (!container) return;
    loadSeq += 1;
    const seq = loadSeq;
    containerState.set(container, { leadId, user, seq });
    const isCurrent = () => containerState.get(container)?.seq === seq;

    const loading = commentElement('div', 'comments-loading');
    const spinner = commentElement('span', 'spinner');
    Object.assign(spinner.style, { width: '20px', height: '20px' });
    loading.append(spinner);
    container.replaceChildren(loading);
    bindActions(container);

    try {
      const data = await ApiClient.get(`/api/leads/${encodeURIComponent(leadId)}/comments`);
      if (!isCurrent()) return;
      render(container, data.comments, user, leadId);
    } catch (err) {
      if (!isCurrent()) return;
      container.replaceChildren(commentElement('div', 'comments-error text-sm text-tertiary', App.t('comments_load_failed')));
    }
  }

  function render(container, comments, user, leadId) {
    const isAdmin = user?.role === 'admin';

    commentRecords.clear();
    const list = commentElement('div', 'comments-list');
    list.id = 'comments-thread';
    comments.forEach((c, index) => {
      const isOwn = c.user_id === user?.id;
      const color = c.author_role === 'admin' ? 'var(--brand-500)' : 'var(--success-600)';
      const recordKey = String(index);
      commentRecords.set(recordKey, c);
      const item = commentElement('div', `comment-item${c.is_internal ? ' comment-internal' : ''}`);
      item.dataset.commentKey = recordKey;
      const avatar = commentElement('div', 'comment-avatar', getInitials(c.author_name || '?'));
      avatar.style.background = color;
      const body = commentElement('div', 'comment-body');
      const header = commentElement('div', 'comment-header');
      header.append(commentElement('span', 'comment-author', c.author_name));
      // The company under the name: on a lead touched by
      // several partners and several people on his own side, the name alone does
      // not say who is speaking.
      if (c.author_company) {
        header.append(commentElement('span', 'comment-company', c.author_company));
      }
      if (c.author_role === 'admin') header.append(commentBadge('badge-brand', 'Admin'));
      if (c.is_internal) header.append(commentBadge('badge-warning', App.t('comments_internal')));
      header.append(commentElement('span', 'comment-time', timeAgo(c.created_at)));
      if (isOwn || isAdmin) {
        const button = commentElement('button', 'comment-delete-btn', '×');
        button.type = 'button';
        button.dataset.commentAction = 'delete';
        button.dataset.commentKey = recordKey;
        button.title = App.t('comments_delete');
        header.append(button);
      }
      body.append(header, commentElement('div', 'comment-text', c.body));
      item.append(avatar, body);
      list.append(item);
    });
    if (!comments.length) list.append(commentElement('div', 'comments-empty', App.t('no_data')));

    const form = commentElement('div', 'comment-form');
    form.dataset.leadId = String(leadId);
    const textarea = commentElement('textarea', 'form-textarea');
    textarea.id = 'comment-input';
    textarea.rows = 2;
    textarea.placeholder = isAdmin ? App.t('comments_placeholder_staff') : App.t('comments_placeholder_partner');
    textarea.style.fontSize = '0.8125rem';
    form.append(textarea);
    if (isAdmin) {
      const label = commentElement('label', 'comment-internal-label');
      Object.assign(label.style, { display: 'flex', alignItems: 'center', gap: '6px', fontSize: '0.75rem', color: 'var(--text-tertiary)', marginTop: '6px' });
      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.id = 'comment-internal';
      checkbox.style.accentColor = 'var(--warning-400)';
      label.append(checkbox, document.createTextNode(App.t('comments_internal_toggle')));
      form.append(label);
    }
    const actions = commentElement('div');
    Object.assign(actions.style, { display: 'flex', justifyContent: 'flex-end', marginTop: '8px' });
    const submitButton = commentElement('button', 'btn btn-primary btn-sm comment-submit-btn');
    Icons.label(submitButton, 'send', App.t('comments_send'), { size: 14 });
    submitButton.type = 'button';
    submitButton.dataset.commentAction = 'submit';
    actions.append(submitButton);
    form.append(actions);
    container.replaceChildren(list, form);
  }

  // ─── ACTIONS ─────────────────────────────────────────────

  async function submit(container) {
    const host = container || document.querySelector('.comment-form')?.parentElement;
    const form = host?.querySelector('.comment-form');
    const state = host && containerState.get(host);
    if (!form || !state) return;
    const leadId = form.dataset.leadId;
    const input = form.querySelector('#comment-input');
    const text  = input?.value.trim();
    if (!text || !leadId) return;

    const internal = form.querySelector('#comment-internal');
    const isInternal = internal?.checked || false;
    const btn = form.querySelector('.btn-primary');
    if (btn) { btn.disabled = true; btn.textContent = '...'; }

    try {
      await ApiClient.post(`/api/leads/${encodeURIComponent(leadId)}/comments`, {
        body: text, is_internal: isInternal
      });
      input.value = '';
      if (internal) internal.checked = false;
      // Reload the thread unless another lead has been opened meanwhile.
      if (containerState.get(host)?.seq === state.seq) await load(state.leadId, host, state.user);
    } catch (err) {
      showToast('error', App.t('comments_error'), err.message);
    } finally {
      if (btn) { btn.disabled = false; Icons.label(btn, 'send', App.t('comments_send'), { size: 14 }); }
    }
  }

  async function deleteComment(id) {
    try {
      await ApiClient.delete(`/api/comments/${encodeURIComponent(id)}`);
      const record = [...commentRecords.entries()].find(([, value]) => value.id === id);
      if (record) document.querySelector(`.comment-item[data-comment-key="${record[0]}"]`)?.remove();
      const thread = document.getElementById('comments-thread');
      if (thread && !thread.querySelector('.comment-item')) {
        thread.replaceChildren(commentElement('div', 'comments-empty', App.t('no_data')));
      }
    } catch (err) {
      showToast('error', App.t('comments_error'), err.message);
    }
  }

  // ─── UTILS ───────────────────────────────────────────────

  function bindActions(container) {
    if (boundContainers.has(container)) return;
    boundContainers.add(container);
    container.addEventListener('click', event => {
      const control = event.target.closest('[data-comment-action]');
      if (!control || !container.contains(control)) return;
      if (control.dataset.commentAction === 'submit') submit(container);
      if (control.dataset.commentAction === 'delete') {
        const record = commentRecords.get(control.dataset.commentKey);
        if (record) deleteComment(record.id);
      }
    });
  }

  function commentElement(tag, className = '', text = '') {
    const element = document.createElement(tag);
    if (className) element.className = className;
    element.textContent = String(text ?? '');
    return element;
  }

  function commentBadge(className, text) {
    const badge = commentElement('span', `badge ${className}`, text);
    Object.assign(badge.style, { fontSize: '0.6rem', padding: '1px 5px' });
    return badge;
  }

  function timeAgo(isoStr) {
    if (!isoStr) return '';
    const diff = (Date.now() - new Date(isoStr + 'Z').getTime()) / 1000;
    if (diff < 60)    return App.t('comments_just_now');
    if (diff < 3600)  return App.pluralize(Math.floor(diff / 60), 'unit_minute_short');
    if (diff < 86400) return App.pluralize(Math.floor(diff / 3600), 'unit_hour_short');
    return new Date(isoStr + 'Z').toLocaleDateString(App.lang === 'en' ? 'en-GB' : 'pl-PL', { day:'numeric', month:'short' });
  }

  return { load, submit, delete: deleteComment };
})();
