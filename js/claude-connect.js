/* ============================================================
   PARTNERTYPE — PROFILE › "CONNECT TO CLAUDE"
   Each user's own API keys and
   OAuth connections, on both profile pages.
   Staff always see the section (with a message when they may
   not connect). A partner sees it only when partners may
   connect, or when old keys/connections are still there to be
   revoked. Every value is drawn with textContent; a new raw key
   lives only in a closure variable until Close.
   Deliberately self-contained: no global shared with
   js/admin-integrations.js.
   ============================================================ */

(function initClaudeConnect(global) {
  'use strict';

  const ME_URL = '/api/integrations/me';
  const EXPIRY_DAYS = [30, 90, 365];
  const REFUSAL_KEYS = {
    integrations_disabled: 'connect_off_admin',
    partners_disabled: 'connect_partner_off',
    mfa_setup_required: 'connect_mfa_required',
    role_not_allowed: 'connect_role_off',
  };

  let me = null;
  let confirmRevoke = null;
  let freshKey = null;
  // True when the last refresh failed and the lists show the previous state.
  let listStale = false;
  let rerenderBound = false;

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function button(className, text, id) {
    const node = el('button', className, text);
    node.type = 'button';
    if (id) node.id = id;
    return node;
  }

  function box(warning) {
    const node = el('div', 'text-sm');
    node.style.cssText = 'margin-top:16px;padding:12px 14px;border-radius:var(--radius-md);'
      + `background:var(${warning ? '--warning-bg' : '--bg-elevated'});`
      + 'border:1px solid var(--border-subtle);color:var(--text-primary)';
    return node;
  }

  function subtitle(text) {
    const node = el('div', 'panel-title', text);
    node.style.marginTop = '24px';
    return node;
  }

  function page() {
    return document.body ? document.body.dataset.page : '';
  }

  function formatDateTime(iso) {
    if (!iso) return App.t('int_never');
    const date = (typeof Ui !== 'undefined' && Ui && typeof Ui.parseServerDate === 'function')
      ? Ui.parseServerDate(String(iso)) : new Date(String(iso));
    if (Number.isNaN(date.getTime())) return String(iso);
    return new Intl.DateTimeFormat(App.lang === 'en' ? 'en-GB' : 'pl-PL', {
      day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit',
    }).format(date);
  }

  function table(headers, rows) {
    const wrap = el('div', 'table-scroll');
    wrap.style.marginTop = '12px';
    const tbl = el('table', 'data-table');
    const head = el('thead');
    const headRow = el('tr');
    for (const text of headers) headRow.appendChild(el('th', '', text));
    head.appendChild(headRow);
    const body = el('tbody');
    for (const cells of rows) {
      const tr = el('tr');
      for (const cell of cells) {
        const td = el('td');
        if (cell instanceof Node) td.appendChild(cell);
        else td.textContent = String(cell ?? '');
        tr.appendChild(td);
      }
      body.appendChild(tr);
    }
    tbl.append(head, body);
    wrap.appendChild(tbl);
    return wrap;
  }

  async function copyText(text, input) {
    if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
      try {
        await navigator.clipboard.writeText(text);
        showToast('success', App.t('int_copied'), '');
        return;
      } catch {
        // Falls through to the manual fallback below.
      }
    }
    if (input && typeof input.select === 'function') {
      input.focus();
      input.select();
    }
    showToast('warning', App.t('int_copy_failed'), '');
  }

  function active(items) {
    return (Array.isArray(items) ? items : []).filter((item) => item && !item.revoked_at);
  }

  function visible() {
    if (!me) return false;
    if (me.can_connect) return true;
    if (page() !== 'partner-profile') return true;
    // A partner without permission sees the section only to revoke what is left.
    return active(me.keys).length > 0 || active(me.connections).length > 0;
  }

  function renderConnect(body) {
    const url = String(me.mcp_url || '');
    const group = el('div', 'form-group');
    group.style.marginTop = '16px';
    const label = el('label', 'form-label', App.t('int_connect_url_label'));
    const row = el('div');
    row.style.cssText = 'display:flex;gap:8px;align-items:center;flex-wrap:wrap';
    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'form-input';
    input.id = 'connect-mcp-url';
    input.readOnly = true;
    input.value = url;
    input.style.cssText = 'flex:1 1 260px;font-family:var(--font-mono,monospace)';
    label.htmlFor = input.id;
    const copy = button('btn btn-secondary', App.t('int_copy'), 'connect-copy-mcp');
    copy.addEventListener('click', () => copyText(url, input));
    row.append(input, copy);
    group.append(label, row);
    body.appendChild(group);

    const claudeAi = el('p', 'text-sm text-secondary', App.t('int_connect_claude_ai'));
    claudeAi.style.marginTop = '12px';
    body.appendChild(claudeAi);
    const cliTitle = el('p', 'text-sm text-secondary', App.t('int_connect_claude_code'));
    cliTitle.style.marginTop = '12px';
    body.appendChild(cliTitle);
    const command = `claude mcp add --transport http partnertype ${url}`;
    const cliRow = el('div');
    cliRow.style.cssText = 'display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:6px';
    const code = el('code', '', command);
    code.style.cssText = 'user-select:all;padding:6px 10px;border-radius:var(--radius-md);'
      + 'background:var(--bg-elevated);border:1px solid var(--border-subtle);overflow-wrap:anywhere';
    const copyCli = button('btn btn-secondary btn-sm', App.t('int_copy'), 'connect-copy-cli');
    copyCli.addEventListener('click', () => copyText(command, null));
    cliRow.append(code, copyCli);
    body.appendChild(cliRow);
    const note = el('div', 'form-hint', App.t('int_connect_public_https'));
    note.style.marginTop = '12px';
    body.appendChild(note);
  }

  function revokeButton(kind, id) {
    const token = `${kind}-${id}`;
    const asking = confirmRevoke === token;
    const node = button(asking ? 'btn btn-danger btn-sm' : 'btn btn-secondary btn-sm',
      App.t(asking ? 'int_revoke_confirm' : 'int_revoke'));
    node.dataset.revoke = token;
    node.addEventListener('click', async () => {
      if (confirmRevoke !== token) {
        confirmRevoke = token;
        render();
        return;
      }
      node.disabled = true;
      confirmRevoke = null;
      const path = kind === 'key' ? 'keys' : 'connections';
      try {
        await ApiClient.delete(`/api/integrations/${path}/${encodeURIComponent(id)}`);
        showToast('success', App.t('int_revoked'), '');
      } catch (error) {
        showToast('error', App.t('int_save_failed'), error?.message || '');
      }
      await refresh();
      render();
    });
    return node;
  }

  function renderKeyBox(body) {
    if (!freshKey) return;
    const keyBox = box(true);
    keyBox.id = 'connect-key-once';
    keyBox.appendChild(el('p', 'font-medium', App.t('int_key_once')));
    const code = el('code', '', freshKey);
    code.style.cssText = 'display:block;margin-top:8px;user-select:all;overflow-wrap:anywhere;'
      + 'font-family:var(--font-mono,monospace)';
    keyBox.appendChild(code);
    const actions = el('div');
    actions.style.cssText = 'display:flex;gap:8px;flex-wrap:wrap;margin-top:10px';
    const copy = button('btn btn-primary btn-sm', App.t('int_copy'), 'connect-key-copy');
    const close = button('btn btn-secondary btn-sm', App.t('int_key_close'), 'connect-key-close');
    copy.addEventListener('click', () => { if (freshKey) copyText(freshKey, null); });
    close.addEventListener('click', () => {
      freshKey = null;
      render();
    });
    actions.append(copy, close);
    keyBox.appendChild(actions);
    body.appendChild(keyBox);
  }

  function renderCreateForm(body) {
    const form = el('div');
    form.style.cssText = 'display:flex;gap:12px;align-items:flex-end;flex-wrap:wrap;margin-top:16px';
    const nameGroup = el('div', 'form-group');
    nameGroup.style.cssText = 'flex:1 1 200px;margin:0';
    const name = document.createElement('input');
    name.type = 'text';
    name.className = 'form-input';
    name.id = 'connect-key-name';
    name.maxLength = 100;
    const nameLabel = el('label', 'form-label', App.t('int_key_name'));
    nameLabel.htmlFor = name.id;
    nameGroup.append(nameLabel, name);
    const expiryGroup = el('div', 'form-group');
    expiryGroup.style.margin = '0';
    const expiry = document.createElement('select');
    expiry.className = 'form-input';
    expiry.id = 'connect-key-expiry';
    for (const days of EXPIRY_DAYS) {
      const option = el('option', '', App.t('int_key_days', { days }));
      option.value = String(days);
      if (days === 90) option.selected = true;
      expiry.appendChild(option);
    }
    const expiryLabel = el('label', 'form-label', App.t('int_key_expiry'));
    expiryLabel.htmlFor = expiry.id;
    expiryGroup.append(expiryLabel, expiry);
    const create = button('btn btn-primary', App.t('int_key_create'), 'connect-key-create');
    form.append(nameGroup, expiryGroup, create);
    body.appendChild(form);

    create.addEventListener('click', async () => {
      const keyName = name.value.trim();
      if (!keyName) {
        showToast('error', App.t('int_key_name_required'), '');
        name.focus();
        return;
      }
      create.disabled = true;
      try {
        const data = await ApiClient.post('/api/integrations/keys',
          { name: keyName, expires_in_days: Number.parseInt(expiry.value, 10) });
        freshKey = typeof data?.key === 'string' ? data.key : null;
        showToast('success', App.t('int_key_created'), keyName);
      } catch (error) {
        showToast('error', App.t('int_save_failed'), error?.message || '');
        create.disabled = false;
        return;
      }
      await refresh();
      render();
    });
  }

  function render() {
    const section = document.getElementById('claude-connect');
    const body = document.getElementById('claude-connect-body');
    if (!section || !body) return;
    body.textContent = '';
    if (!visible()) {
      // A just-created key is shown only once, so it is drawn even when the
      // rest of the section cannot be.
      section.hidden = !freshKey;
      renderKeyBox(body);
      return;
    }
    section.hidden = false;
    body.appendChild(el('div', 'text-sm text-secondary', App.t('connect_intro')));
    if (me.can_connect) {
      renderConnect(body);
    } else {
      const message = box(true);
      message.textContent = App.t(REFUSAL_KEYS[me.refusal_code] || 'connect_role_off');
      body.appendChild(message);
    }
    renderKeyBox(body);

    if (listStale) body.appendChild(el('div', 'form-hint', App.t('connect_list_stale')));
    body.appendChild(subtitle(App.t('connect_keys_title')));
    const keys = active(me.keys);
    if (!keys.length) {
      body.appendChild(el('div', 'form-hint', App.t('int_keys_empty')));
    } else {
      body.appendChild(table(
        [App.t('int_col_key'), App.t('int_col_created'), App.t('int_col_last_used'), App.t('int_col_expires'), ''],
        keys.map((item) => {
          const key = el('div');
          key.appendChild(el('div', 'font-medium text-primary', String(item.name ?? '')));
          key.appendChild(el('code', 'text-xs text-tertiary', `${item.display_prefix ?? ''}…`));
          return [key, formatDateTime(item.created_at), formatDateTime(item.last_used_at),
            item.expires_at ? formatDateTime(item.expires_at) : '—', revokeButton('key', item.id)];
        }),
      ));
    }
    if (me.can_connect) renderCreateForm(body);

    body.appendChild(subtitle(App.t('connect_connections_title')));
    const connections = active(me.connections);
    if (!connections.length) {
      body.appendChild(el('div', 'form-hint', App.t('int_connections_empty')));
    } else {
      body.appendChild(table(
        [App.t('int_col_client'), App.t('int_col_created'), App.t('int_col_last_used'), ''],
        connections.map((item) => {
          const client = el('div');
          client.appendChild(el('div', 'font-medium text-primary', String(item.client_name ?? '')));
          if (item.redirect_host) client.appendChild(el('div', 'text-xs text-tertiary', String(item.redirect_host)));
          return [client, formatDateTime(item.created_at), formatDateTime(item.last_used_at),
            revokeButton('connection', item.id)];
        }),
      ));
    }
  }

  // A failed refresh keeps the last good state instead of hiding the section,
  // so a key created a moment ago is never lost from view.
  async function refresh() {
    try {
      const data = await ApiClient.get(ME_URL);
      if (data && typeof data === 'object') {
        me = data;
        listStale = false;
        return;
      }
    } catch { /* fall through */ }
    listStale = me !== null;
  }

  async function load() {
    const current = page();
    if (current !== 'admin-profile' && current !== 'partner-profile') return;
    me = null;
    confirmRevoke = null;
    freshKey = null;
    listStale = false;
    await refresh();
    render();
    // Bound once; read into a local first so the hook is checked, not assigned.
    const registerRerender = App.onRerender;
    if (!rerenderBound && typeof registerRerender === 'function') {
      rerenderBound = true;
      App.onRerender(render);
    }
  }

  global.ClaudeConnect = { load, render };
  document.addEventListener('DOMContentLoaded', load);
})(window);
