/* ============================================================
   PARTNERTYPE — REGISTRATION FORM
   ============================================================

   The public sign-up screen: password strength and match, the producer's own
   extra questions, and the submission itself.

   Kept in a file, not an inline <script>: this is the one page anybody on the
   internet can open, so `script-src` must not allow `unsafe-inline` here.
   ============================================================ */
document.addEventListener('DOMContentLoaded', () => {
  const form     = document.getElementById('register-form');
  const pwInput  = document.getElementById('r-password');
  const pw2Input = document.getElementById('r-password2');
  const errorEl  = document.getElementById('register-error');
  const btn      = document.getElementById('register-btn');

  // Password toggle
  document.getElementById('toggle-r-password')?.addEventListener('click', () => {
    const t = pwInput.type === 'text' ? 'password' : 'text';
    pwInput.type = t;
  });

  // Password strength
  pwInput.addEventListener('input', () => {
    const v = pwInput.value;
    const bars = [1,2,3,4].map(n => document.getElementById('sb' + n));
    const lbl  = document.getElementById('strength-label');
    let score  = 0;
    if (v.length >= 8)             score++;
    if (/[A-Z]/.test(v))           score++;
    if (/[0-9]/.test(v))           score++;
    if (/[^A-Za-z0-9]/.test(v))   score++;

    bars.forEach((b, i) => {
      b.className = 'strength-bar';
      if (i < score) {
        b.classList.add(score <= 1 ? 'weak' : score <= 2 ? 'medium' : 'strong');
      }
    });

    const labels = ['', App.t('setpw_weak'), App.t('setpw_medium'), App.t('setpw_good'), App.t('setpw_strong')];
    const colors = ['var(--text-tertiary)', 'var(--error-400)', 'var(--warning-400)', 'var(--success-400)', 'var(--success-400)'];
    lbl.textContent = labels[score] || App.t('setpw_enter_password');
    lbl.style.color = colors[score] || colors[0];
  });

  // Password match
  pw2Input.addEventListener('input', checkMatch);
  function checkMatch() {
    const hint = document.getElementById('pw-match-hint');
    if (!pw2Input.value) { hint.replaceChildren(); return; }
    // Built as elements rather than an innerHTML string so the icon adds no
    // markup sink here.
    const ok = pw2Input.value === pwInput.value;
    const span = document.createElement('span');
    span.style.cssText = 'display:inline-flex;align-items:center;gap:6px;color:var(--' + (ok ? 'success' : 'error') + '-400)';
    const icon = Icons.el(ok ? 'check' : 'x', { size: 14 });
    if (icon) span.appendChild(icon);
    span.appendChild(document.createTextNode(App.t(ok ? 'setpw_match' : 'setpw_mismatch')));
    hint.replaceChildren(span);
  }

  // Built with DOM nodes and textContent, never an interpolated markup
  // string: the labels are producer-authored text arriving over HTTP, and
  // this page is reachable by anyone.
  async function renderCustomFields() {
    const block = document.getElementById('custom-fields-block');
    if (!block) return;
    let fields = [];
    try {
      const data = await ApiClient.get('/api/registration-fields/public');
      fields = Array.isArray(data.fields) ? data.fields : [];
    } catch {
      // A producer's extra questions must never stand between somebody and
      // registering: if they cannot be fetched, the form works without them.
      return;
    }
    if (!fields.length) return;

    fields.forEach((field) => {
      const group = document.createElement('div');
      group.className = 'form-group';
      const label = document.createElement('label');
      label.className = 'form-label';
      label.htmlFor = `r-custom-${field.id}`;
      label.textContent = (typeof Ui !== 'undefined' && Ui.fieldLabel)
        ? Ui.fieldLabel(field) : field.label;
      const input = document.createElement('input');
      input.type = 'text';
      input.id = `r-custom-${field.id}`;
      input.className = 'form-input';
      input.maxLength = field.max_length || 200;
      input.dataset.registrationField = String(field.id);
      group.append(label, input);
      block.appendChild(group);
    });
    block.hidden = false;
  }
  void renderCustomFields();

  // Submit
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    errorEl.style.display = 'none';

    // Validate
    const name    = document.getElementById('r-name').value.trim();
    const company = document.getElementById('r-company').value.trim();
    const email   = document.getElementById('r-email').value.trim();
    const phone   = document.getElementById('r-phone').value.trim();
    const pw      = pwInput.value;
    const pw2     = pw2Input.value;
    const terms   = document.getElementById('r-terms').checked;

    if (!name || !company || !email || !phone || !pw) {
      return showError(App.t('reg_fill_required'));
    }
    if (pw.length < 8) return showError(App.t('reg_password_min'));
    if (pw !== pw2)    return showError(App.t('setpw_mismatch_sentence'));
    if (!terms)        return showError(App.t('reg_accept_terms'));

    btn.disabled = true;
    // Built from elements, like the match hint above. The interpolated value is
    // dictionary text rather than anything typed in, so the old innerHTML was
    // not a hole -- but it was a markup sink on the one page anybody on the
    // internet can open, and this page should not have to be reasoned about.
    const spinner = document.createElement('span');
    spinner.className = 'spinner';
    spinner.style.cssText = 'width:18px;height:18px;border-width:2px';
    btn.replaceChildren(spinner, document.createTextNode(' ' + App.t('reg_registering')));

    try {
      const customFields = {};
      document.querySelectorAll('[data-registration-field]').forEach((input) => {
        const value = input.value.trim();
        if (value) customFields[input.dataset.registrationField] = value;
      });

      await ApiClient.post('/api/auth/register', {
        name, company, email, phone,
        bio:   document.getElementById('r-bio').value.trim(),
        password: pw,
        custom_fields: customFields,
      });

      form.style.display = 'none';
      document.getElementById('success-screen').style.display = 'flex';

    } catch (err) {
      btn.disabled  = false;
      Icons.label(btn, 'user-plus', App.t('reg_create_account'), { size: 16 });
      showError(err.message);
    }
  });

  function showError(msg) {
    errorEl.textContent  = msg;
    errorEl.style.display = 'block';
  }

  App.onRerender(() => {
    pwInput.dispatchEvent(new Event('input'));
    checkMatch();
  });
});
