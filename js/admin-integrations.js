/* ============================================================
   PARTNERTYPE — ADMIN INTEGRATIONS CONTROLLER
   Settings › Integrations. Six sections: switches with the GDPR
   acknowledgement, Connect to Claude, connections, API keys,
   data scope and limits, log and usage.
   Admin only: the tile and the pane stay hidden until the
   admin-only GET /api/integrations/settings answers 200, so a
   manager never sees them (the server stays the authority).
   Every value is drawn with textContent and createElement —
   client names and key names are chosen by strangers.
   A freshly created raw key lives only in a closure variable
   until Close; it is never stored, never put in an attribute
   and cannot be fetched again.
   ============================================================ */

(function initAdminIntegrations(global) {
  'use strict';

  const SETTINGS_URL = '/api/integrations/settings';
  const OVERVIEW_URL = '/api/integrations/admin/connections';
  const LOG_URL = '/api/integrations/admin/log?limit=50';
  const USAGE_URL = '/api/integrations/admin/usage?days=30';
  const GROUPS = ['contact', 'notes', 'client_ids', 'custom', 'commissions'];
  const OUTCOMES = ['ok', 'error', 'denied', 'rate_limited'];
  const EXPIRY_DAYS = [30, 90, 365];

  let settings = null;
  let overview = null;   // { ok, data } once loaded
  let log = null;
  let usage = null;
  let euAck = false;
  let confirmingDisable = false;
  let confirmRevoke = null;   // "connection-3" / "key-7" awaiting its second click
  let freshKey = null;        // the raw key, shown once
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

  function panel(title) {
    const root = el('div', 'panel');
    const header = el('div', 'panel-header');
    header.appendChild(el('div', 'panel-title', title));
    const body = el('div', 'panel-body');
    root.append(header, body);
    return { root, body };
  }

  function box(warning) {
    const node = el('div', 'text-sm');
    node.style.cssText = 'margin-top:16px;padding:12px 14px;border-radius:var(--radius-md);'
      + `background:var(${warning ? '--warning-bg' : '--bg-elevated'});`
      + 'border:1px solid var(--border-subtle);color:var(--text-primary)';
    return node;
  }

  function toggleRow(id, labelText, checked, hintText) {
    const row = el('div');
    row.style.cssText = 'display:flex;align-items:center;justify-content:space-between;gap:16px;margin-top:16px';
    const text = el('div');
    const label = el('label', 'text-sm font-medium text-primary', labelText);
    label.htmlFor = id;
    text.appendChild(label);
    if (hintText) text.appendChild(el('div', 'form-hint', hintText));
    const sw = el('label', 'toggle-switch');
    sw.style.flexShrink = '0';
    const input = document.createElement('input');
    input.type = 'checkbox';
    input.id = id;
    input.checked = !!checked;
    sw.append(input, el('span', 'toggle-slider'));
    row.append(text, sw);
    return { row, input };
  }

  function locale() {
    return App.lang === 'en' ? 'en-GB' : 'pl-PL';
  }

  function parseDate(iso) {
    if (typeof Ui !== 'undefined' && Ui && typeof Ui.parseServerDate === 'function') {
      return Ui.parseServerDate(iso);
    }
    return new Date(iso);
  }

  function formatDateTime(iso) {
    if (!iso) return App.t('int_never');
    const date = parseDate(String(iso));
    if (Number.isNaN(date.getTime())) return String(iso);
    return new Intl.DateTimeFormat(locale(), {
      day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit',
    }).format(date);
  }

  function formatDay(day) {
    const date = new Date(`${day}T00:00:00Z`);
    if (Number.isNaN(date.getTime())) return String(day ?? '');
    return new Intl.DateTimeFormat(locale(), {
      day: '2-digit', month: '2-digit', year: 'numeric', timeZone: 'UTC',
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
    // Plain-http origins may have no Clipboard API: select it for a manual copy.
    if (input && typeof input.select === 'function') {
      input.focus();
      input.select();
    }
    showToast('warning', App.t('int_copy_failed'), '');
  }

  async function saveSettings(body) {
    try {
      const data = await ApiClient.patch(SETTINGS_URL, body);
      if (data && typeof data === 'object') settings = data;
      showToast('success', App.t('int_saved'), App.t('st_integrations'));
      return true;
    } catch (error) {
      showToast('error', App.t('int_save_failed'), error?.message || '');
      return false;
    }
  }

  // ─── SECTION 1: switches ────────────────────────────────────
  function renderSwitches() {
    const host = document.getElementById('int-switches');
    if (!host || !settings) return;
    host.textContent = '';
    const { root, body } = panel(App.t('int_switches_title'));

    const status = el('div', 'text-sm');
    status.appendChild(el('span', settings.enabled ? 'badge badge-success' : 'badge badge-neutral',
      App.t(settings.enabled ? 'int_status_on' : 'int_status_off')));
    body.appendChild(status);

    let ack = null;
    if (!settings.enabled) {
      const callout = box(true);
      callout.appendChild(el('p', '', App.t('int_eu_warning')));
      const ackLabel = el('label');
      ackLabel.style.cssText = 'display:flex;align-items:flex-start;gap:8px;margin-top:10px;font-weight:600;cursor:pointer';
      ack = document.createElement('input');
      ack.type = 'checkbox';
      ack.id = 'int-eu-ack';
      ack.checked = euAck;
      ack.style.cssText = 'margin-top:3px;accent-color:var(--brand-500)';
      ackLabel.append(ack, el('span', '', App.t('int_eu_ack')));
      callout.appendChild(ackLabel);
      body.appendChild(callout);
    }

    const main = toggleRow('int-enabled', App.t('int_switch_label'), settings.enabled);
    main.input.disabled = !settings.enabled && !euAck;
    body.appendChild(main.row);

    if (ack) {
      ack.addEventListener('change', () => {
        euAck = ack.checked;
        main.input.disabled = !euAck;
      });
    }

    main.input.addEventListener('change', async () => {
      if (main.input.checked) {
        if (!euAck) { main.input.checked = false; return; }
        main.input.disabled = true;
        const ok = await saveSettings({ enabled: true, acknowledge_eu_transfer: true });
        if (ok) euAck = false;
        renderSwitches();
        return;
      }
      // Switching off asks first, inline; the switch stays on until confirmed.
      main.input.checked = true;
      confirmingDisable = true;
      renderSwitches();
    });

    if (settings.enabled && confirmingDisable) {
      const confirm = box(false);
      confirm.style.marginTop = '12px';
      confirm.appendChild(el('p', '', App.t('int_confirm_disable')));
      const actions = el('div');
      actions.style.cssText = 'display:flex;gap:8px;flex-wrap:wrap;margin-top:10px';
      const yes = button('btn btn-danger btn-sm', App.t('int_disable_confirm'), 'int-disable-confirm');
      const no = button('btn btn-secondary btn-sm', App.t('int_cancel'), 'int-disable-cancel');
      actions.append(yes, no);
      confirm.appendChild(actions);
      body.appendChild(confirm);
      yes.addEventListener('click', async () => {
        yes.disabled = true;
        await saveSettings({ enabled: false });
        confirmingDisable = false;
        renderSwitches();
      });
      no.addEventListener('click', () => {
        confirmingDisable = false;
        renderSwitches();
      });
    }

    const partners = toggleRow('int-partners', App.t('int_partners_switch'),
      settings.partners_enabled, App.t('int_partners_hint'));
    body.appendChild(partners.row);
    partners.input.addEventListener('change', async () => {
      const wanted = partners.input.checked;
      partners.input.disabled = true;
      await saveSettings({ partners_enabled: wanted });
      renderSwitches();
    });

    host.appendChild(root);
  }

  // ─── SECTION 2: Connect to Claude ───────────────────────────
  function renderConnect() {
    const host = document.getElementById('int-connect');
    if (!host || !settings) return;
    host.textContent = '';
    const { root, body } = panel(App.t('int_connect_title'));
    const url = String(settings.mcp_url || '');

    const group = el('div', 'form-group');
    const label = el('label', 'form-label', App.t('int_connect_url_label'));
    const row = el('div');
    row.style.cssText = 'display:flex;gap:8px;align-items:center;flex-wrap:wrap';
    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'form-input';
    input.id = 'int-mcp-url';
    input.readOnly = true;
    input.value = url;
    input.style.cssText = 'flex:1 1 280px;font-family:var(--font-mono,monospace)';
    label.htmlFor = input.id;
    const copy = button('btn btn-secondary', App.t('int_copy'), 'int-copy-mcp');
    copy.addEventListener('click', () => copyText(url, input));
    row.append(input, copy);
    group.append(label, row);
    body.appendChild(group);

    body.appendChild(el('p', 'text-sm text-secondary', App.t('int_connect_claude_ai'))).style.marginTop = '16px';

    const cliTitle = el('p', 'text-sm text-secondary', App.t('int_connect_claude_code'));
    cliTitle.style.marginTop = '12px';
    body.appendChild(cliTitle);
    const command = `claude mcp add --transport http partnertype ${url}`;
    const cliRow = el('div');
    cliRow.style.cssText = 'display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:6px';
    const code = el('code', '', command);
    code.style.cssText = 'user-select:all;padding:6px 10px;border-radius:var(--radius-md);'
      + 'background:var(--bg-elevated);border:1px solid var(--border-subtle);overflow-wrap:anywhere';
    const copyCli = button('btn btn-secondary btn-sm', App.t('int_copy'), 'int-copy-cli');
    copyCli.addEventListener('click', () => copyText(command, null));
    cliRow.append(code, copyCli);
    body.appendChild(cliRow);

    const note = el('div', 'form-hint', App.t('int_connect_public_https'));
    note.style.marginTop = '12px';
    body.appendChild(note);
    host.appendChild(root);
  }

  // ─── Revoke: first click asks, second click revokes ─────────
  function revokeButton(kind, id) {
    const token = `${kind}-${id}`;
    const asking = confirmRevoke === token;
    const node = button(asking ? 'btn btn-danger btn-sm' : 'btn btn-secondary btn-sm',
      App.t(asking ? 'int_revoke_confirm' : 'int_revoke'));
    node.dataset.revoke = token;
    node.addEventListener('click', async () => {
      if (confirmRevoke !== token) {
        confirmRevoke = token;
        renderConnections();
        renderKeys();
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
      await loadOverview();
      renderConnections();
      renderKeys();
    });
    return node;
  }

  function active(items) {
    return (Array.isArray(items) ? items : []).filter((item) => item && !item.revoked_at);
  }

  function listFailed(body) {
    body.appendChild(el('div', 'form-hint', App.t('int_load_failed')));
  }

  // ─── SECTION 3: OAuth connections ───────────────────────────
  function renderConnections() {
    const host = document.getElementById('int-connections');
    if (!host || !settings) return;
    host.textContent = '';
    const { root, body } = panel(App.t('int_connections_title'));
    if (!overview || !overview.ok) {
      listFailed(body);
    } else {
      const items = active(overview.data.connections);
      if (!items.length) {
        body.appendChild(el('div', 'form-hint', App.t('int_connections_empty')));
      } else {
        body.appendChild(table(
          [App.t('int_col_client'), App.t('int_col_person'), App.t('int_col_created'),
            App.t('int_col_last_used'), ''],
          items.map((item) => {
            const client = el('div');
            client.appendChild(el('div', 'font-medium text-primary', String(item.client_name ?? '')));
            const hosts = Array.isArray(item.redirect_hosts) ? item.redirect_hosts.join(', ') : '';
            if (hosts) client.appendChild(el('div', 'text-xs text-tertiary', hosts));
            return [client, String(item.user_name ?? ''), formatDateTime(item.created_at),
              formatDateTime(item.last_used_at), revokeButton('connection', item.id)];
          }),
        ));
      }
    }
    host.appendChild(root);
  }

  // ─── SECTION 4: API keys ────────────────────────────────────
  function renderOnceBox(body) {
    if (!freshKey) return;
    const keyBox = box(true);
    keyBox.id = 'int-key-once';
    keyBox.appendChild(el('p', 'font-medium', App.t('int_key_once')));
    const code = el('code', '', freshKey);
    code.style.cssText = 'display:block;margin-top:8px;user-select:all;overflow-wrap:anywhere;'
      + 'font-family:var(--font-mono,monospace)';
    keyBox.appendChild(code);
    const actions = el('div');
    actions.style.cssText = 'display:flex;gap:8px;flex-wrap:wrap;margin-top:10px';
    const copy = button('btn btn-primary btn-sm', App.t('int_copy'), 'int-key-copy');
    const close = button('btn btn-secondary btn-sm', App.t('int_key_close'), 'int-key-close');
    copy.addEventListener('click', () => { if (freshKey) copyText(freshKey, null); });
    close.addEventListener('click', () => {
      freshKey = null;
      renderKeys();
    });
    actions.append(copy, close);
    keyBox.appendChild(actions);
    body.appendChild(keyBox);
  }

  function renderCreateForm(body) {
    const form = el('div');
    form.style.cssText = 'display:flex;gap:12px;align-items:flex-end;flex-wrap:wrap;margin-top:16px';
    const nameGroup = el('div', 'form-group');
    nameGroup.style.cssText = 'flex:1 1 220px;margin:0';
    const name = document.createElement('input');
    name.type = 'text';
    name.className = 'form-input';
    name.id = 'int-key-name';
    name.maxLength = 100;
    const nameLabel = el('label', 'form-label', App.t('int_key_name'));
    nameLabel.htmlFor = name.id;
    nameGroup.append(nameLabel, name);

    const expiryGroup = el('div', 'form-group');
    expiryGroup.style.margin = '0';
    const expiry = document.createElement('select');
    expiry.className = 'form-input';
    expiry.id = 'int-key-expiry';
    for (const days of EXPIRY_DAYS) {
      const option = el('option', '', App.t('int_key_days', { days }));
      option.value = String(days);
      if (days === 90) option.selected = true;
      expiry.appendChild(option);
    }
    const expiryLabel = el('label', 'form-label', App.t('int_key_expiry'));
    expiryLabel.htmlFor = expiry.id;
    expiryGroup.append(expiryLabel, expiry);

    const create = button('btn btn-primary', App.t('int_key_create'), 'int-key-create');
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
      await loadOverview();
      renderConnections();
      renderKeys();
    });
  }

  function renderKeys() {
    const host = document.getElementById('int-keys');
    if (!host || !settings) return;
    host.textContent = '';
    const { root, body } = panel(App.t('int_keys_title'));
    renderOnceBox(body);
    if (!overview || !overview.ok) {
      listFailed(body);
    } else {
      const items = active(overview.data.keys);
      if (!items.length) {
        body.appendChild(el('div', 'form-hint', App.t('int_keys_empty')));
      } else {
        body.appendChild(table(
          [App.t('int_col_key'), App.t('int_col_person'), App.t('int_col_created'),
            App.t('int_col_last_used'), App.t('int_col_expires'), ''],
          items.map((item) => {
            const key = el('div');
            key.appendChild(el('div', 'font-medium text-primary', String(item.name ?? '')));
            const prefix = el('code', 'text-xs text-tertiary', `${item.display_prefix ?? ''}…`);
            key.appendChild(prefix);
            return [key, String(item.user_name ?? ''), formatDateTime(item.created_at),
              formatDateTime(item.last_used_at), item.expires_at ? formatDateTime(item.expires_at) : '—',
              revokeButton('key', item.id)];
          }),
        ));
      }
    }
    renderCreateForm(body);
    host.appendChild(root);
  }

  // ─── SECTION 5: data scope and limits ───────────────────────
  function switchableGroups() {
    const catalogue = Array.isArray(settings.field_group_catalogue)
      ? settings.field_group_catalogue.map((entry) => entry && entry.key).filter((key) => GROUPS.includes(key))
      : [];
    return catalogue.length ? catalogue : GROUPS.slice();
  }

  function limitInput(id, labelText, value, cap) {
    const group = el('div', 'form-group');
    group.style.margin = '0';
    const input = document.createElement('input');
    input.type = 'number';
    input.className = 'form-input';
    input.id = id;
    input.min = '1';
    input.step = '1';
    if (Number.isFinite(Number(cap))) input.max = String(cap);
    input.value = String(value ?? '');
    input.style.maxWidth = '160px';
    const label = el('label', 'form-label', labelText);
    label.htmlFor = id;
    group.append(label, input, el('div', 'form-hint', App.t('int_limit_operator_cap', { cap })));
    return { group, input };
  }

  function renderFields() {
    const host = document.getElementById('int-fields');
    if (!host || !settings) return;
    host.textContent = '';
    const { root, body } = panel(App.t('int_fields_title'));
    body.appendChild(el('div', 'form-hint', App.t('int_fields_hint')));

    const enabled = new Set(Array.isArray(settings.field_groups) ? settings.field_groups : []);
    const boxes = {};
    const list = el('div');
    list.style.cssText = 'display:flex;flex-direction:column;gap:8px;margin-top:12px';
    for (const key of switchableGroups()) {
      const label = el('label', 'text-sm text-primary');
      label.style.cssText = 'display:flex;align-items:center;gap:8px;cursor:pointer';
      const input = document.createElement('input');
      input.type = 'checkbox';
      input.id = `int-group-${key}`;
      input.checked = enabled.has(key);
      input.style.accentColor = 'var(--brand-500)';
      label.append(input, el('span', '', App.t(`int_group_${key}`)));
      list.appendChild(label);
      boxes[key] = input;
    }
    body.appendChild(list);
    const saveFields = button('btn btn-primary', App.t('int_save'), 'int-fields-save');
    saveFields.style.marginTop = '12px';
    body.appendChild(saveFields);
    saveFields.addEventListener('click', async () => {
      const groups = Object.keys(boxes).filter((key) => boxes[key].checked);
      saveFields.disabled = true;
      if (await saveSettings({ field_groups: groups })) renderFields();
      else saveFields.disabled = false;
    });

    const limitsTitle = el('div', 'panel-title', App.t('int_limits_title'));
    limitsTitle.style.marginTop = '24px';
    body.appendChild(limitsTitle);
    const limits = settings.limits || {};
    const caps = settings.operator_caps || {};
    const row = el('div');
    row.style.cssText = 'display:flex;gap:16px;flex-wrap:wrap;margin-top:12px';
    const minute = limitInput('int-limit-minute', App.t('int_limit_per_minute'), limits.per_minute, caps.rate_per_minute);
    const day = limitInput('int-limit-day', App.t('int_limit_per_day'), limits.per_day, caps.rate_per_day);
    row.append(minute.group, day.group);
    body.appendChild(row);
    const message = el('div', 'text-sm text-error');
    message.setAttribute('aria-live', 'polite');
    message.style.marginTop = '8px';
    const saveLimits = button('btn btn-primary', App.t('int_save'), 'int-limits-save');
    saveLimits.style.marginTop = '12px';
    body.append(message, saveLimits);

    saveLimits.addEventListener('click', async () => {
      message.textContent = '';
      const values = {};
      for (const [field, input, cap] of [['rate_per_minute', minute.input, caps.rate_per_minute],
        ['rate_per_day', day.input, caps.rate_per_day]]) {
        const raw = String(input.value).trim();
        const value = Number(raw);
        if (!/^\d+$/.test(raw) || value < 1) {
          message.textContent = App.t('int_limit_invalid');
          input.focus();
          return;
        }
        if (Number.isFinite(Number(cap)) && value > Number(cap)) {
          message.textContent = App.t('int_limit_above_cap', { cap });
          input.focus();
          return;
        }
        values[field] = value;
      }
      saveLimits.disabled = true;
      if (await saveSettings(values)) renderFields();
      else saveLimits.disabled = false;
    });
    host.appendChild(root);
  }

  // ─── SECTION 6: log and usage ───────────────────────────────
  function connectionLabel(kind, id) {
    const data = overview && overview.ok ? overview.data : null;
    if (data) {
      const list = kind === 'key' ? data.keys : data.connections;
      const match = (Array.isArray(list) ? list : []).find((item) => item && item.id === id);
      const name = match ? (kind === 'key' ? match.name : match.client_name) : '';
      if (name) return String(name);
    }
    return App.t(kind === 'key' ? 'int_kind_key' : 'int_kind_oauth', { id: id ?? '' });
  }

  function outcomeLabel(outcome) {
    return OUTCOMES.includes(outcome) ? App.t(`int_outcome_${outcome}`) : String(outcome ?? '');
  }

  function renderLog() {
    const host = document.getElementById('int-log');
    if (!host || !settings) return;
    host.textContent = '';
    const { root, body } = panel(App.t('int_log_title'));
    if (!log || !log.ok) {
      listFailed(body);
    } else {
      const calls = Array.isArray(log.data.calls) ? log.data.calls : [];
      if (!calls.length) {
        body.appendChild(el('div', 'form-hint', App.t('int_log_empty')));
      } else {
        body.appendChild(table(
          [App.t('int_col_time'), App.t('int_col_person'), App.t('int_col_connection'),
            App.t('int_col_operation'), App.t('int_col_result'), App.t('int_col_records')],
          calls.map((call) => [formatDateTime(call.created_at), String(call.user_name ?? ''),
            connectionLabel(call.connection_kind, call.connection_id), String(call.operation ?? ''),
            outcomeLabel(call.outcome), String(call.records ?? 0)]),
        ));
      }
    }

    const usageTitle = el('div', 'panel-title', App.t('int_usage_title'));
    usageTitle.style.marginTop = '24px';
    body.appendChild(usageTitle);
    if (!usage || !usage.ok) {
      listFailed(body);
    } else {
      const rows = Array.isArray(usage.data.usage) ? usage.data.usage : [];
      if (!rows.length) {
        body.appendChild(el('div', 'form-hint', App.t('int_usage_empty')));
      } else {
        body.appendChild(table(
          [App.t('int_col_day'), App.t('int_col_connection'), App.t('int_col_calls'),
            App.t('int_col_denied'), App.t('int_col_records')],
          rows.map((row) => [formatDay(row.day),
            row.label ? String(row.label) : connectionLabel(row.connection_kind, row.connection_id),
            String(row.calls ?? 0), String(row.denied ?? 0), String(row.records ?? 0)]),
        ));
      }
    }
    host.appendChild(root);
  }

  // ─── Loading ────────────────────────────────────────────────
  function settled(result) {
    return result.status === 'fulfilled' && result.value && typeof result.value === 'object'
      ? { ok: true, data: result.value }
      : { ok: false, data: null };
  }

  async function loadOverview() {
    const [result] = await Promise.allSettled([ApiClient.get(OVERVIEW_URL)]);
    overview = settled(result);
  }

  async function loadLists() {
    const [o, l, u] = await Promise.allSettled([
      ApiClient.get(OVERVIEW_URL), ApiClient.get(LOG_URL), ApiClient.get(USAGE_URL),
    ]);
    overview = settled(o);
    log = settled(l);
    usage = settled(u);
  }

  function render() {
    if (!settings) return;
    renderSwitches();
    renderConnect();
    renderConnections();
    renderKeys();
    renderFields();
    renderLog();
  }

  async function load() {
    const tile = document.getElementById('integrations-tile');
    const pane = document.getElementById('tab-integrations');
    if (!tile || !pane) return;
    settings = null;
    overview = null;
    log = null;
    usage = null;
    euAck = false;
    confirmingDisable = false;
    confirmRevoke = null;
    freshKey = null;
    let data;
    try {
      data = await ApiClient.get(SETTINGS_URL);
    } catch {
      // 403 for a manager (or any other failure): nothing to show, nothing drawn.
      tile.hidden = true;
      pane.hidden = true;
      return;
    }
    settings = data && typeof data === 'object' ? data : {};
    tile.hidden = false;
    pane.hidden = false;
    await loadLists();
    render();
    // Bound once; read into a local first so the hook is checked, not assigned.
    const registerRerender = App.onRerender;
    if (!rerenderBound && typeof registerRerender === 'function') {
      rerenderBound = true;
      App.onRerender(render);
    }
  }

  global.AdminIntegrations = { load, render };
  document.addEventListener('DOMContentLoaded', load);
})(window);
