/* ============================================================
   PARTNERTYPE — SET PASSWORD 

   The producer never hands a partner a password: they send an e-mail
   invitation and the partner sets their own. This is the invited partner's
   entry point. A fresh token previews their
   name and e-mail and reveals the form; anything else — unknown, expired,
   already-accepted, or simply missing — falls back to one clear Polish
   sentence and a way back to the login page, never a blank screen and
   never a stack trace.

   Classic script, no module syntax, no build step, matching every other
   file in js/. Functions are exposed on a single frozen namespace (the
   way js/admin-partners.js and js/admin-shell.js do) so the test suite can
   drive each step directly instead of relying on top-level leakage.
   ============================================================ */

(function initSetPasswordNamespace(global) {
  'use strict';

  const MIN_PASSWORD_LENGTH = 8;

  // ─── TOKEN / VISIBILITY ─────────────────────────────────────────────────
  function readToken() {
    return new URLSearchParams(global.location.search).get('token');
  }

  // The same page serves two arrivals: an invited person activating an account
  // and an existing one recovering it. Only the flag decides, never the token,
  // so neither endpoint can be probed with the other's secret.
  function isReset() {
    return new URLSearchParams(global.location.search).get('reset') === '1';
  }

  function showInvalid() {
    const invalid = document.getElementById('sp-invalid');
    const form = document.getElementById('sp-form');
    if (invalid) invalid.hidden = false;
    if (form) form.hidden = true;
  }

  function showForm(invitation) {
    const invalid = document.getElementById('sp-invalid');
    const form = document.getElementById('sp-form');
    const invitee = document.getElementById('sp-invitee');
    if (invitee) invitee.textContent = `${invitation.name} (${invitation.email})`;
    if (form) form.hidden = false;
    if (invalid) invalid.hidden = true;
  }

  async function loadInvitation() {
    const token = readToken();
    if (!token) {
      showInvalid();
      return;
    }
    if (isReset()) {
      // A reset link is not previewed: answering "whose account is this?"
      // before the password is set would turn a stolen link into a way of
      // reading somebody's name and address.
      const invitee = document.getElementById('sp-invitee');
      if (invitee) invitee.textContent = App.t('setpw_reset_account');
      const form = document.getElementById('sp-form');
      const invalid = document.getElementById('sp-invalid');
      if (form) form.hidden = false;
      if (invalid) invalid.hidden = true;
      return;
    }
    try {
      const data = await ApiClient.get(`/api/invitations/${encodeURIComponent(token)}`);
      if (!data || !data.invitation) {
        showInvalid();
        return;
      }
      showForm(data.invitation);
    } catch {
      // Unknown, expired and already-accepted tokens all reach here
      // identically — none of the three is an
      // existence oracle, and this page must not tell them apart either.
      showInvalid();
    }
  }

  // ─── PASSWORD STRENGTH (mirrors register.html's meter, own ids) ────────
  function updateStrength() {
    const pwInput = document.getElementById('sp-password');
    const value = pwInput.value;
    const bars = [1, 2, 3, 4].map((n) => document.getElementById('sp-sb' + n));
    const label = document.getElementById('sp-strength-label');
    let score = 0;
    if (value.length >= 8) score++;
    if (/[A-Z]/.test(value)) score++;
    if (/[0-9]/.test(value)) score++;
    if (/[^A-Za-z0-9]/.test(value)) score++;

    bars.forEach((bar, index) => {
      bar.className = 'strength-bar';
      if (index < score) bar.classList.add(score <= 1 ? 'weak' : score <= 2 ? 'medium' : 'strong');
    });

    const labels = ['', App.t('setpw_weak'), App.t('setpw_medium'),
      App.t('setpw_good'), App.t('setpw_strong')];
    const colors = ['var(--text-tertiary)', 'var(--error-400)', 'var(--warning-400)', 'var(--success-400)', 'var(--success-400)'];
    label.textContent = labels[score] || App.t('setpw_enter_password');
    label.style.color = colors[score] || colors[0];
  }

  // ─── MATCH HINT ──────────────────────────────────────────────────────────
  // Built as elements, not an innerHTML string, so this adds no markup sink.
  function renderMatchHint(matches) {
    const hint = document.getElementById('sp-match-hint');
    const span = document.createElement('span');
    span.style.cssText = 'display:inline-flex;align-items:center;gap:6px;color:var(--' + (matches ? 'success' : 'error') + '-400)';
    const icon = Icons.el(matches ? 'check' : 'x', { size: 14 });
    if (icon) span.appendChild(icon);
    span.appendChild(document.createTextNode(matches ? App.t('setpw_match') : App.t('setpw_mismatch')));
    hint.replaceChildren(span);
  }

  function checkMatch() {
    const pwInput = document.getElementById('sp-password');
    const pw2Input = document.getElementById('sp-password2');
    if (!pw2Input.value) {
      document.getElementById('sp-match-hint').replaceChildren();
      return;
    }
    renderMatchHint(pw2Input.value === pwInput.value);
  }

  function togglePasswordVisibility() {
    const pwInput = document.getElementById('sp-password');
    pwInput.type = pwInput.type === 'text' ? 'password' : 'text';
  }

  // ─── SUBMIT ──────────────────────────────────────────────────────────────
  function showError(message) {
    const errEl = document.getElementById('sp-error');
    errEl.textContent = message;
    errEl.style.display = 'block';
  }

  function validateLocally(password, password2) {
    if (password.length < MIN_PASSWORD_LENGTH) return App.t('setpw_too_short');
    if (password !== password2) return App.t('setpw_mismatch_sentence');
    return null;
  }

  async function handleSubmit(event) {
    event.preventDefault();
    const errEl = document.getElementById('sp-error');
    errEl.style.display = 'none';

    const password = document.getElementById('sp-password').value;
    const password2 = document.getElementById('sp-password2').value;
    const localError = validateLocally(password, password2);
    if (localError) {
      if (password !== password2) renderMatchHint(false);
      showError(localError);
      return;
    }

    const token = readToken();
    const submitBtn = document.getElementById('sp-submit');
    submitBtn.disabled = true;
    try {
      await ApiClient.post(
        isReset()
          ? `/api/auth/password-reset/${encodeURIComponent(token)}`
          : `/api/invitations/${encodeURIComponent(token)}/accept`,
        { password },
      );
      // A query flag the login page can use later (e.g. a "your account is
      // ready" note) — set-password.html's own job ends at handing the
      // partner back to the login form able to sign in.
      global.location.href = 'index.html?activated=1';
    } catch (err) {
      showError(err.message);
    } finally {
      submitBtn.disabled = false;
    }
  }

  // ─── INIT ────────────────────────────────────────────────────────────────
  function init() {
    document.getElementById('sp-toggle-password')?.addEventListener('click', togglePasswordVisibility);
    document.getElementById('sp-password')?.addEventListener('input', updateStrength);
    document.getElementById('sp-password2')?.addEventListener('input', checkMatch);
    document.getElementById('sp-form')?.addEventListener('submit', handleSubmit);
    Icons.into(document.getElementById('sp-invalid-icon'), 'triangle-alert', { size: 36 });
    void loadInvitation();
  }

  document.addEventListener('DOMContentLoaded', init);

  global.SetPassword = Object.freeze({
    init,
    loadInvitation,
    handleSubmit,
    validateLocally,
  });
})(window);
