/* ============================================================
   PARTNERTYPE — OAUTH CONSENT 

   The screen where a person decides whether an application may read their
   CRM through "Connect to PartnerType" (claude.ai, Claude Desktop, Claude
   Code). /oauth/authorize parks the request and sends the browser here as
   pages/oauth/consent.html?req=<id>.

   What keeps the decision safe:
   - The application chose its own name, so it is written with textContent
     only and labelled as declared by the application. The redirect HOST --
     the one thing that cannot be faked into pointing back at the real
     application -- gets its own prominent element, and a client that only
     ever returns to this computer is called out.
   - Nothing happens without a click on Allow / Deny, and every authorize
     shows this screen again; consent is never remembered.
   - The decision is a same-origin JSON POST, not a <form>: CSP form-action
     'self' would block the final redirect. The page then goes
     to the redirect_to the SERVER built, and only if it is http(s).
   - It talks to the server with its own fetch, not ApiClient: ApiClient
     answers any 401 by sending the browser to the login page without the
     request id. Here a 401 first drops the cached ph_session entry (the
     login page trusts it and would bounce straight back -- a loop) and then
     goes to index.html?oauth=<id>, which js/auth.js turns back into this
     page after the login and the second factor.

   Classic script, no build step, like every other file in js/.
   ============================================================ */

(function initOAuthConsent(global) {
  'use strict';

  const REQ_RE = /^[A-Za-z0-9_-]{32,64}$/;
  const LOGIN_PAGE = '/index.html';

  // Server refusal codes -> the sentence this page shows for them.
  const REFUSALS = {
    mfa_setup_required: 'oauth_mfa_required',
    integrations_disabled: 'oauth_integrations_off',
    partners_disabled: 'oauth_partners_off',
    role_not_allowed: 'oauth_role_not_allowed',
    request_expired: 'oauth_expired',
  };

  // Role names already translated elsewhere in the dictionary.
  const ROLE_KEYS = {
    admin: 'users_role_admin',
    manager: 'users_role_manager',
    observer: 'users_role_observer',
    partner: 'role_partner',
  };

  // view: 'idle' | 'ready' | 'pending' | 'leaving' | 'error'
  const state = { id: null, payload: null, view: 'idle', messageKey: '', profile: false };

  const el = (id) => global.document.getElementById(id);

  // App is app.js's top-level const: a global binding, not a window property.
  function t(key, params) {
    return typeof App !== 'undefined' && typeof App.t === 'function' ? App.t(key, params) : key;
  }

  function readRequestId() {
    const id = new URLSearchParams(global.location.search).get('req');
    return id && REQ_RE.test(id) ? id : null;
  }

  async function request(method, path, body) {
    const init = {
      method,
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    };
    if (typeof App !== 'undefined' && App.lang) init.headers['Accept-Language'] = App.lang;
    if (body !== undefined) init.body = JSON.stringify(body);
    const response = await global.fetch(path, init);
    let data = {};
    try {
      data = await response.json();
    } catch {
      data = {};
    }
    return { status: response.status, data: data && typeof data === 'object' ? data : {} };
  }

  function forgetCachedSession() {
    try {
      global.sessionStorage.removeItem('ph_session');
    } catch {
      // Storage disabled: nothing cached, nothing to forget.
    }
  }

  function toLogin() {
    // Order matters: the login page redirects anyone it finds in storage.
    forgetCachedSession();
    global.location.assign(LOGIN_PAGE + '?oauth=' + encodeURIComponent(state.id));
  }

  function cachedRole() {
    try {
      const user = JSON.parse(global.sessionStorage.getItem('ph_session') || 'null');
      return user && typeof user.role === 'string' ? user.role : '';
    } catch {
      return '';
    }
  }

  // Only an absolute http(s) address the server returned; anything else -- a
  // number, a relative path, another scheme -- is an error, not a destination.
  function safeRedirect(value) {
    if (typeof value !== 'string' || !value) return null;
    let url;
    try {
      url = new URL(value);
    } catch {
      return null;
    }
    return url.protocol === 'https:' || url.protocol === 'http:' ? value : null;
  }

  function fail(response) {
    const code = response && response.data && response.data.code;
    if (response && response.status === 404) state.messageKey = 'oauth_expired';
    else if (response && response.status === 403 && REFUSALS[code]) state.messageKey = REFUSALS[code];
    else state.messageKey = 'oauth_error_generic';
    state.profile = state.messageKey === 'oauth_mfa_required';
    state.view = 'error';
    render();
  }

  function setText(id, text) {
    const node = el(id);
    if (node) node.textContent = text;
  }

  function setHidden(id, hidden) {
    const node = el(id);
    if (node) node.hidden = hidden;
  }

  /** Draw the current state. Re-run on a language or theme switch. */
  function render() {
    const payload = state.payload;
    const showDetails = Boolean(payload) && state.view !== 'error';

    setHidden('oauth-details', !showDetails);
    if (payload) {
      setText('oauth-client-name', String(payload.client_name || ''));
      setText('oauth-redirect-host', String(payload.redirect_host || ''));
      // Driven by the address this request will actually return to.
      setHidden('oauth-loopback-warning', payload.redirect_is_loopback !== true);
      setHidden('oauth-punycode-warning', payload.redirect_host_punycode !== true);
      const user = payload.user || {};
      const role = String(user.role || '');
      setText('oauth-signed-in', t('oauth_signed_in_as', {
        name: String(user.name || ''),
        role: ROLE_KEYS[role] ? t(ROLE_KEYS[role]) : role,
      }));
    }

    const actionable = showDetails && (state.view === 'ready' || state.view === 'pending');
    ['oauth-allow', 'oauth-deny'].forEach((id) => {
      const button = el(id);
      if (!button) return;
      button.hidden = !actionable;
      button.disabled = state.view !== 'ready';
    });

    let message = '';
    if (state.view === 'pending') message = t('oauth_working');
    else if (state.view === 'leaving') message = t('oauth_redirecting');
    else if (state.view === 'error') message = t(state.messageKey || 'oauth_error_generic');
    setText('oauth-status', message);

    const link = el('oauth-profile-link');
    if (link) {
      link.hidden = !(state.view === 'error' && state.profile);
      link.setAttribute('href', cachedRole() === 'partner'
        ? '/pages/partner/profile.html'
        : '/pages/admin/profile.html');
    }
  }

  /** Read the pending request and show it. */
  async function load() {
    state.id = readRequestId();
    state.payload = null;
    state.profile = false;
    if (!state.id) {
      state.view = 'error';
      state.messageKey = 'oauth_invalid_request';
      render();
      return;
    }
    state.view = 'idle';
    render();

    let response;
    try {
      response = await request('GET', '/api/oauth/requests/' + encodeURIComponent(state.id));
    } catch {
      fail(null);
      return;
    }
    if (response.status === 401) {
      toLogin();
      return;
    }
    if (response.status !== 200) {
      fail(response);
      return;
    }
    state.payload = response.data;
    state.view = 'ready';
    render();
  }

  /** Answer the request once: true = Allow, false = Deny. */
  async function decide(approve) {
    if (state.view !== 'ready' || !state.id) return;
    state.view = 'pending';
    render();

    let response;
    try {
      response = await request('POST',
        '/api/oauth/requests/' + encodeURIComponent(state.id) + '/decision',
        { approve: approve === true });
    } catch {
      fail(null);
      return;
    }
    if (response.status === 401) {
      toLogin();
      return;
    }
    if (response.status !== 200) {
      fail(response);
      return;
    }
    const target = safeRedirect(response.data.redirect_to);
    if (!target) {
      fail(null);
      return;
    }
    state.view = 'leaving';
    render();
    global.location.assign(target);
  }

  // Delegated, so it works whatever rebuilt the page's markup.
  global.document.addEventListener('click', (event) => {
    const target = event.target && event.target.closest
      ? event.target.closest('#oauth-allow, #oauth-deny')
      : null;
    if (!target) return;
    event.preventDefault();
    decide(target.id === 'oauth-allow');
  });

  // A language or theme switch redraws from the last payload, no refetch.
  if (typeof App !== 'undefined' && App && 'onRerender' in App) App.onRerender(render);

  global.document.addEventListener('DOMContentLoaded', () => {
    // Only on the consent page itself; the script is inert anywhere else.
    if (el('oauth-consent')) load();
  });

  global.OAuthConsent = { load, render };
})(window);
