/* ============================================================
   PARTNERTYPE — AFFILIATE SETTINGS
   Affiliate › Settings: the programme switch, the shop
   address, cookie and auto-approval days, the signing secret (shown
   once), the snippet for the shop, a test event and the Stripe
   endpoint's signing secret.
   Admin only: drawn only when GET /api/affiliate/settings answers.
   Every value is drawn with textContent or as a form value; a freshly
   created secret lives in a closure variable until Close.
   ============================================================ */

(function initAffiliateSettings(global) {
  'use strict';

  const SETTINGS_URL = '/api/affiliate/settings';
  const SECRET_URL = '/api/affiliate/secret';
  const TEST_URL = '/api/affiliate/test-event';
  const STRIPE_URL = '/api/affiliate/stripe-secret';

  let settings = null;
  let freshSecret = null;
  let confirmingRotate = false;
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

  function field(id, labelText, input, hintText) {
    const group = el('div', 'form-group');
    group.style.marginTop = '16px';
    const label = el('label', 'form-label', labelText);
    label.htmlFor = id;
    input.id = id;
    group.append(label, input);
    if (hintText) group.appendChild(el('div', 'form-hint', hintText));
    return group;
  }

  function textInput(value, type) {
    const input = document.createElement('input');
    input.type = type || 'text';
    input.className = 'form-input';
    input.value = value ?? '';
    return input;
  }

  function readonlyRow(id, labelText, value) {
    const input = textInput(value);
    input.readOnly = true;
    const copy = button('btn btn-secondary btn-sm', App.t('int_copy'), `${id}-copy`);
    copy.addEventListener('click', () => copyText(input.value, input));
    const row = el('div');
    row.style.cssText = 'display:flex;gap:8px;align-items:center';
    row.append(input, copy);
    const group = field(id, labelText, input);
    group.appendChild(row);
    return group;
  }

  function toggle(id, labelText, checked, hintText) {
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

  function callout(warning) {
    const node = el('div', 'text-sm');
    node.style.cssText = 'margin-top:16px;padding:12px 14px;border-radius:var(--radius-md);'
      + `background:var(${warning ? '--warning-bg' : '--bg-elevated'});`
      + 'border:1px solid var(--border-subtle);color:var(--text-primary)';
    return node;
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

  async function save(body) {
    try {
      const data = await ApiClient.patch(SETTINGS_URL, body);
      if (data && typeof data === 'object') settings = data;
      showToast('success', App.t('int_saved'), App.t('aff_title'));
      return true;
    } catch (error) {
      showToast('error', App.t('int_save_failed'), error?.message || '');
      return false;
    }
  }

  function renderSecret(body) {
    const box = callout(!!freshSecret);
    box.id = 'aff-secret';
    if (freshSecret) {
      box.appendChild(el('p', 'font-medium', App.t('aff_secret_once')));
      const code = el('code', '', freshSecret);
      code.style.cssText = 'display:block;margin-top:8px;user-select:all;overflow-wrap:anywhere;'
        + 'font-family:var(--font-mono,monospace)';
      box.appendChild(code);
      const actions = el('div');
      actions.style.cssText = 'display:flex;gap:8px;flex-wrap:wrap;margin-top:10px';
      const copy = button('btn btn-primary btn-sm', App.t('int_copy'), 'aff-secret-copy');
      const close = button('btn btn-secondary btn-sm', App.t('int_key_close'), 'aff-secret-close');
      copy.addEventListener('click', () => { if (freshSecret) copyText(freshSecret, null); });
      close.addEventListener('click', () => { freshSecret = null; render(); });
      actions.append(copy, close);
      box.appendChild(actions);
      body.appendChild(box);
      return;
    }
    const state = settings.secret;
    box.appendChild(el('p', '', state
      ? App.t('aff_secret_state', { fingerprint: state.fingerprint })
      : App.t('aff_secret_none')));
    const actions = el('div');
    actions.style.cssText = 'display:flex;gap:8px;flex-wrap:wrap;margin-top:10px';
    if (state && confirmingRotate) {
      box.appendChild(el('p', 'text-xs text-tertiary', App.t('aff_rotate_warning')));
      const yes = button('btn btn-danger btn-sm', App.t('aff_rotate_confirm'), 'aff-rotate-confirm');
      const no = button('btn btn-secondary btn-sm', App.t('int_cancel'), 'aff-rotate-cancel');
      yes.addEventListener('click', () => createSecret(yes));
      no.addEventListener('click', () => { confirmingRotate = false; render(); });
      actions.append(yes, no);
    } else {
      const create = button('btn btn-secondary btn-sm',
        App.t(state ? 'aff_rotate' : 'aff_create_secret'), 'aff-secret-create');
      create.addEventListener('click', () => {
        if (state) { confirmingRotate = true; render(); return; }
        createSecret(create);
      });
      actions.appendChild(create);
    }
    box.appendChild(actions);
    body.appendChild(box);
  }

  async function createSecret(trigger) {
    trigger.disabled = true;
    try {
      const data = await ApiClient.post(SECRET_URL, {});
      freshSecret = data && typeof data.secret === 'string' ? data.secret : null;
      confirmingRotate = false;
      settings = await ApiClient.get(SETTINGS_URL);
    } catch (error) {
      showToast('error', App.t('int_save_failed'), error?.message || '');
    }
    render();
  }

  function renderStripe(body) {
    body.appendChild(el('div', 'panel-title', App.t('aff_stripe_title')));
    body.lastChild.style.marginTop = '24px';
    body.appendChild(el('div', 'form-hint', App.t('aff_stripe_hint')));
    body.appendChild(readonlyRow('aff-stripe-url', App.t('aff_webhook_url'),
      settings.stripe_webhook_url));
    const state = settings.stripe_secret;
    body.appendChild(el('p', 'text-sm', state
      ? App.t('aff_secret_state', { fingerprint: state.fingerprint })
      : App.t('aff_stripe_none')));
    const input = textInput('', 'password');
    input.autocomplete = 'off';
    input.placeholder = 'whsec_…';
    body.appendChild(field('aff-stripe-secret', App.t('aff_stripe_secret'), input));
    const saveStripe = button('btn btn-secondary btn-sm', App.t('aff_stripe_save'), 'aff-stripe-save');
    saveStripe.style.marginTop = '8px';
    saveStripe.addEventListener('click', async () => {
      if (!input.value.trim()) return;
      saveStripe.disabled = true;
      try {
        const data = await ApiClient.put(STRIPE_URL, { secret: input.value.trim() });
        if (data && typeof data === 'object') settings = data;
        showToast('success', App.t('int_saved'), App.t('aff_stripe_title'));
      } catch (error) {
        showToast('error', App.t('int_save_failed'), error?.message || '');
      }
      input.value = '';
      render();
    });
    body.appendChild(saveStripe);
  }

  async function sendTest(trigger) {
    trigger.disabled = true;
    try {
      const result = await ApiClient.post(TEST_URL, {});
      if (result && result.ok) {
        showToast('success', App.t('aff_test_ok'), '');
      } else {
        showToast('error', App.t('aff_test_failed'), result?.error || '');
      }
    } catch (error) {
      showToast('error', App.t('aff_test_failed'), error?.message || '');
    }
    trigger.disabled = false;
  }

  function render() {
    const host = document.getElementById('int-affiliate');
    if (!host || !settings) return;
    host.textContent = '';
    const root = el('div', 'panel');
    const header = el('div', 'panel-header');
    header.appendChild(el('div', 'panel-title', App.t('aff_settings_title')));
    const body = el('div', 'panel-body');
    root.append(header, body);

    body.appendChild(el('div', 'form-hint', App.t('aff_intro')));
    const enabled = toggle('aff-enabled', App.t('aff_switch'), settings.enabled);
    enabled.input.addEventListener('change', async () => {
      enabled.input.disabled = true;
      await save({ enabled: enabled.input.checked });
      render();
    });
    body.appendChild(enabled.row);

    const shop = textInput(settings.shop_url, 'url');
    shop.placeholder = 'https://shop.example.com/';
    body.appendChild(field('aff-shop-url', App.t('aff_shop_url'), shop, App.t('aff_shop_url_hint')));
    const cookieDays = textInput(String(settings.cookie_days), 'number');
    cookieDays.min = '1';
    cookieDays.max = '365';
    body.appendChild(field('aff-cookie-days', App.t('aff_cookie_days'), cookieDays,
      App.t('aff_cookie_days_hint')));

    const auto = toggle('aff-auto', App.t('aff_auto_switch'), settings.auto_approve,
      App.t('aff_auto_hint'));
    body.appendChild(auto.row);
    const autoDays = textInput(String(settings.auto_approve_days), 'number');
    autoDays.min = '1';
    autoDays.max = '365';
    body.appendChild(field('aff-auto-days', App.t('aff_auto_days'), autoDays));

    const saveButton = button('btn btn-primary btn-sm', App.t('aff_save'), 'aff-save');
    saveButton.style.marginTop = '16px';
    saveButton.addEventListener('click', async () => {
      saveButton.disabled = true;
      await save({
        shop_url: shop.value,
        cookie_days: Number.parseInt(cookieDays.value, 10),
        auto_approve: auto.input.checked,
        auto_approve_days: Number.parseInt(autoDays.value, 10),
      });
      render();
    });
    body.appendChild(saveButton);

    body.appendChild(el('div', 'panel-title', App.t('aff_shop_setup')));
    body.lastChild.style.marginTop = '24px';
    body.appendChild(readonlyRow('aff-webhook-url', App.t('aff_webhook_url'), settings.webhook_url));
    renderSecret(body);

    const snippet = document.createElement('textarea');
    snippet.className = 'form-input';
    snippet.readOnly = true;
    snippet.rows = 6;
    snippet.value = settings.snippet || '';
    snippet.style.fontFamily = 'var(--font-mono,monospace)';
    body.appendChild(field('aff-snippet', App.t('aff_snippet'), snippet, App.t('aff_snippet_hint')));
    const snippetCopy = button('btn btn-secondary btn-sm', App.t('int_copy'), 'aff-snippet-copy');
    snippetCopy.style.marginTop = '8px';
    snippetCopy.addEventListener('click', () => copyText(snippet.value, snippet));
    body.appendChild(snippetCopy);

    const test = button('btn btn-secondary btn-sm', App.t('aff_send_test'), 'aff-test');
    test.style.cssText = 'margin-top:16px;display:block';
    test.addEventListener('click', () => sendTest(test));
    body.appendChild(test);

    renderStripe(body);

    host.appendChild(root);
  }

  async function load() {
    const host = document.getElementById('int-affiliate');
    if (!host) return;
    settings = null;
    freshSecret = null;
    confirmingRotate = false;
    try {
      settings = await ApiClient.get(SETTINGS_URL);
    } catch {
      host.textContent = '';
      return;
    }
    render();
    const registerRerender = App.onRerender;
    if (!rerenderBound && typeof registerRerender === 'function') {
      rerenderBound = true;
      App.onRerender(render);
    }
  }

  global.AffiliateSettings = { load, render };
  document.addEventListener('DOMContentLoaded', load);
})(window);
