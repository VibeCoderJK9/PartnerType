/* ============================================================
   PARTNERTYPE — AUTH LOGIC
   Login via /api/auth/login, authenticated by an HttpOnly cookie.
   ============================================================ */

// There are no demo accounts and no demo credentials on the login screen.
//
// Which door the form is pointed at — "producent" or "partner". The server
// maps it to the roles that door admits (api/auth.py::LOGIN_DOORS); the form
// never asks the user to name their own staff role.
let selectedRole = 'producent';

// Everyone who is not a partner works inside the admin shell — the producer,
// their moderators and their observers alike. Listing partner as the one
// exception means a staff role added later lands in the shell by default
// rather than being bounced to the partner dashboard by omission.
function isPartnerRole(role) {
  return role === 'partner';
}

// The consent page (pages/oauth/consent.html) sends a signed-out user here as
// index.html?oauth=<request id>, so they can come back to the same consent
// screen after signing in. Only an id of the shape the server issues is
// accepted, and the target path is fixed here: the parameter can never name a
// URL, so it cannot become an open redirect.
const OAUTH_RE = /^[A-Za-z0-9_-]{32,64}$/;
const OAUTH_CONSENT_PAGE = '/pages/oauth/consent.html';

function pendingOAuthRequest() {
  const id = new URLSearchParams(window.location.search).get('oauth');
  return id && OAUTH_RE.test(id) ? id : null;
}

// Where a successful sign-in goes: back to a pending consent, or the role's home.
function afterLoginTarget(role) {
  const id = pendingOAuthRequest();
  if (id) return OAUTH_CONSENT_PAGE + '?req=' + id;
  return isPartnerRole(role)
    ? 'pages/partner/dashboard.html'
    : 'pages/admin/dashboard.html';
}

// ─── INIT ─────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  // Already logged in? → redirect
  const user = storedSessionUser();
  if (user) {
    redirectAfterLogin(user.role);
    return;
  }

  setupRoleTabs();
  setupForm();
  App.applyTranslations();
});

// ─── SESSION HELPERS ──────────────────────────────────────
// The cached public profile, or null. A value that does not parse to an object
// with a role is dropped, so a corrupted cache cannot stop the login form from
// being wired.
function storedSessionUser() {
  const raw = sessionStorage.getItem('ph_session');
  if (!raw) return null;
  try {
    const user = JSON.parse(raw);
    if (user && typeof user === 'object' && typeof user.role === 'string') return user;
  } catch { /* fall through and clear it */ }
  sessionStorage.removeItem('ph_session');
  return null;
}

function redirectAfterLogin(role) {
  window.location.href = afterLoginTarget(role);
}

// ─── ROLE TABS ────────────────────────────────────────────
function setupRoleTabs() {
  document.querySelectorAll('.role-tab').forEach(tab => {
    tab.addEventListener('click', () => {
      selectedRole = tab.dataset.role;
      document.querySelectorAll('.role-tab').forEach(t => t.classList.remove('active'));
      tab.classList.add('active');
      // A hint at the shape of the address, not at a real account's.
      const emailInput = document.getElementById('auth-email');
      if (emailInput) {
        emailInput.placeholder = App.t(selectedRole === 'producent' ? 'auth_email_example_vendor' : 'auth_email_example_partner');
      }
    });
  });
}

// ─── FORM ─────────────────────────────────────────────────
function setupForm() {
  const form = document.getElementById('auth-form');
  if (!form) return;

  // Toggle password visibility
  document.getElementById('toggle-password')?.addEventListener('click', function() {
    const passInput = document.getElementById('auth-password');
    const isText    = passInput.type === 'text';
    passInput.type  = isText ? 'password' : 'text';
    this.innerHTML  = isText
      ? `<svg width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>`
      : `<svg width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M17.94 17.94A10.07 10.07 0 0112 20c-7 0-11-8-11-8a18.45 18.45 0 015.06-5.94"/><path d="M9.9 4.24A9.12 9.12 0 0112 4c7 0 11 8 11 8a18.5 18.5 0 01-2.16 3.19"/><line x1="1" y1="1" x2="23" y2="23"/></svg>`;
  });

  form.addEventListener('submit', handleLogin);

  document.querySelector('.forgot-link')?.addEventListener('click', handleForgotPassword);
}

async function handleForgotPassword(e) {
  e.preventDefault();

  const emailInput = document.getElementById('auth-email');
  const email = emailInput.value.trim();
  if (!email) {
    showError(App.t('forgot_needs_email'));
    emailInput.focus();
    return;
  }

  const link = e.currentTarget;
  const originalText = link.textContent;
  link.textContent = App.t('forgot_sending');

  try {
    await ApiClient.post('/api/auth/password-reset', { email });
  } catch (err) {
    // Deliberately ignored: the answer is the same either way, and a visible
    // failure here would tell a stranger which addresses have accounts.
  }

  // Same wording whether or not that address has an account here.
  showNotice(App.t('forgot_sent'));
  link.textContent = originalText;
}

function showNotice(msg) {
  const errorEl = document.getElementById('auth-error');
  const spanEl  = errorEl.querySelector('[data-i18n]');
  if (spanEl) spanEl.textContent = msg;
  else errorEl.textContent = msg;
  errorEl.classList.add('show');
  document.getElementById('auth-email').classList.remove('error');
  document.getElementById('auth-password').classList.remove('error');
}

async function handleLogin(e) {
  e.preventDefault();

  const emailInput  = document.getElementById('auth-email');
  const passInput   = document.getElementById('auth-password');
  const errorEl     = document.getElementById('auth-error');
  const btn         = document.getElementById('login-btn');
  const errorTextEl = errorEl.querySelector('span[data-i18n]') || errorEl;

  const email    = emailInput.value.trim();
  const password = passInput.value;

  // Reset UI
  errorEl.classList.remove('show');
  emailInput.classList.remove('error');
  passInput.classList.remove('error');

  // Validate locally first
  if (!email || !password) {
    showError(App.t('err_fill_all_fields'));
    return;
  }

  // Loading state
  const originalBtnHtml = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = `<span class="spinner" style="width:18px;height:18px;border-width:2px"></span>`;

  try {
    const data = await ApiClient.post('/api/auth/login', {
      email,
      password,
      role: selectedRole,
    });

    // The password was right but the login is unfinished: no session exists
    // until the six digits are accepted.
    if (data.mfa_required) {
      btn.disabled = false;
      btn.innerHTML = originalBtnHtml;
      showCodeStep(data.challenge);
      return;
    }

    // Cache public display state only; the session secret is HttpOnly.
    sessionStorage.setItem('ph_session', JSON.stringify(data.user));

    redirectAfterLogin(data.user.role);

  } catch (err) {
    btn.disabled  = false;
    btn.innerHTML = originalBtnHtml;
    showError(err.message || App.t('login_error'));
  }
}

function showCodeStep(challenge) {
  const form = document.getElementById('auth-form');
  let step = document.getElementById('mfa-step');
  if (!step) {
    step = document.createElement('div');
    step.id = 'mfa-step';
    // Built element by element rather than as a blob of markup: nothing here
    // needs innerHTML, and the sink audit should stay honest.
    const label = document.createElement('label');
    label.className = 'form-label';
    label.setAttribute('for', 'mfa-login-code');
    label.textContent = App.t('mfa_code_label');

    const input = document.createElement('input');
    Object.assign(input, { type: 'text', id: 'mfa-login-code', className: 'form-input',
      inputMode: 'numeric', autocomplete: 'one-time-code', maxLength: 9, placeholder: '000000' });

    const button = document.createElement('button');
    Object.assign(button, { type: 'button', id: 'mfa-login-btn',
      className: 'btn btn-primary btn-full btn-lg', textContent: App.t('mfa_login_btn') });
    button.style.marginTop = '12px';

    const error = document.createElement('div');
    Object.assign(error, { id: 'mfa-login-error', className: 'text-sm' });
    error.style.cssText = 'color:var(--error-400);display:none;margin-top:10px';

    const hint = document.createElement('p');
    Object.assign(hint, { className: 'text-xs', textContent: App.t('mfa_backup_hint') });
    hint.style.cssText = 'color:var(--text-tertiary);margin-top:8px';

    step.append(label, input, button, error, hint);
    // The form is hidden rather than replaced: replacing it also removed the
    // element the error message is written into, so a mistyped code failed
    // silently.
    form.hidden = true;
    form.parentNode.insertBefore(step, form.nextSibling);
  }
  const input = document.getElementById('mfa-login-code');
  input.focus();

  document.getElementById('mfa-login-btn').addEventListener('click', async () => {
    try {
      const data = await ApiClient.post('/api/auth/mfa/login', {
        challenge,
        code: input.value,
      });
      sessionStorage.setItem('ph_session', JSON.stringify(data.user));
      redirectAfterLogin(data.user.role);
    } catch (err) {
      const errorEl = document.getElementById('mfa-login-error');
      errorEl.textContent = err.message || App.t('login_error');
      errorEl.style.display = 'block';
      input.value = '';
      input.focus();
    }
  });
}

function showError(msg) {
  const errorEl  = document.getElementById('auth-error');
  const emailInput = document.getElementById('auth-email');
  const passInput  = document.getElementById('auth-password');
  const spanEl   = errorEl.querySelector('[data-i18n]');

  if (spanEl) spanEl.textContent = msg;
  else errorEl.textContent = msg;

  errorEl.classList.add('show');
  emailInput.classList.add('error');
  passInput.classList.add('error');

  // Shake
  const container = document.querySelector('.auth-form-container');
  container.style.animation = 'none';
  container.offsetHeight;
  container.style.animation = 'shake 0.4s ease';
}

// The `shake` keyframes live in css/auth.css: a <style> element built here
// would be refused by `style-src-elem 'self'`. This file only starts the
// animation.
