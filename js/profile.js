/* ============================================================
   PARTNERTYPE — PROFILE PAGE LOGIC
   Profile edit + password change for both admin and partner.
   ============================================================ */

document.addEventListener('DOMContentLoaded', () => {
  const page = document.body.dataset.page;
  if (page === 'admin-profile' || page === 'partner-profile') {
    initProfilePage();
  }
});

async function initProfilePage() {
  try {
    const page = document.body.dataset.page;
    const session = page === 'partner-profile' && typeof initPartnerPage === 'function'
      ? await initPartnerPage()
      : await Ui.rehydrateSession(ApiClient);
    // Every staff role owns a profile, not just the administrator. The
    // previous single-role check bounced a Moderator off their own "My
    // profile" page even though AdminShell had already admitted them to the
    // shell — and would have done the same to an Observer.
    const allowedRoles = page === 'admin-profile'
      ? ['admin', 'manager', 'observer']
      : page === 'partner-profile'
        ? ['partner']
        : null;
    if (!session || (allowedRoles && !allowedRoles.includes(session.role))) {
      Ui.safeNavigate('../../index.html');
      return;
    }
    const data = await ApiClient.get(`/api/users/${session.id}`);
    const user = data.user;
    cachePublicProfile({ ...session, ...user });

    renderProfileCard(user);
    populateForm(user);
    setupProfileForm(user);
    setupPasswordForm(user);
    setupTwoFactor(user);
    await renderTierList(user);

    // The card, the role word and the whole tier ladder are drawn in code, so
    // a language or theme switch has to run them again.
    App.onRerender(() => {
      renderProfileCard(user);
      void renderTierList(user);
    });
  } catch (err) {
    handleApiError(err, 'initProfilePage');
  }
}

function cachePublicProfile(user) {
  // The commission tier, not the legacy users.level word: `tier_name` is what
  // every screen shows and `tier_id` is what marks the partner's own rung on
  // the ladder below. `tier_name_en` is the producer's own English wording of
  // that same tier, read through Ui.producerLabel so a language
  // switch never needs a fresh /api/users/{id} round trip.
  const publicFields = ['id', 'name', 'email', 'company', 'phone', 'bio', 'role',
    'tier_id', 'tier_name', 'tier_color', 'tier_name_en', 'account_status', 'created_at'];
  const displayProfile = {};
  publicFields.forEach(field => {
    if (Object.hasOwn(user || {}, field)) displayProfile[field] = user[field];
  });
  sessionStorage.setItem('ph_session', JSON.stringify(displayProfile));
  return displayProfile;
}

// ─── CARD ─────────────────────────────────────────────────────────────────────

function renderProfileCard(user) {
  const nameEl    = document.getElementById('profile-name');
  const companyEl = document.getElementById('profile-company');
  const emailEl   = document.getElementById('profile-email');
  const phoneEl   = document.getElementById('profile-phone');
  const joinedEl  = document.getElementById('profile-joined');
  const levelEl   = document.getElementById('profile-level');
  const roleEl    = document.getElementById('profile-role');

  if (nameEl)    nameEl.textContent    = user.name;
  if (companyEl) companyEl.textContent = user.company || '—';
  if (emailEl)   emailEl.textContent   = user.email;
  if (phoneEl)   phoneEl.textContent   = user.phone || '—';
  if (joinedEl)  joinedEl.textContent  = App.t('profile_since', { date: formatDate(user.created_at) });
  // This panel shipped the literal "Administrator", so a Moderator opening
  // their own profile was told they were an admin.
  if (roleEl) {
    // Resolved here, never at module load: a frozen object cannot follow a
    // language switch (the trap listed in js/app.js's i18n notes).
    const roleLabels = {
      admin: App.t('users_role_admin'),
      manager: App.t('users_role_manager'),
      observer: App.t('users_role_observer'),
      partner: App.t('role_partner'),
    };
    roleEl.textContent = roleLabels[user.role] || user.role || '—';
  }
  // The partner's commission tier, named and coloured by the producer in
  // Settings, never the legacy users.level word.
  if (levelEl && user.role === 'partner') {
    levelEl.replaceChildren();
    const badge = document.createElement('span');
    // The tier badge itself follows the reader's language --
    // `Ui.producerLabel` falls back to the Polish original when the producer
    // has not translated this tier yet, exactly like every other producer
    // label in the app.
    const tierName = Ui.producerLabel(user, 'tier_name');
    badge.className = `level-badge${tierName ? '' : ' none'}`;
    if (tierName && /^#[0-9a-fA-F]{6}$/.test(String(user.tier_color || ''))) {
      // The producer picks a tier colour with nothing to stop it disappearing
      // on the light theme's paper card; Ui.readableInk keeps the hue and
      // only clamps the lightness.
      const ink = Ui.readableInk(user.tier_color);
      badge.style.color = ink;
      badge.style.border = `1px solid ${ink}`;
    }
    Icons.label(badge, 'star', tierName || App.t('ap_no_tier'), { size: 14 });
    levelEl.appendChild(badge);
  }
}

// ─── PARTNER TIERS ────────────────────────────────────────────────────────────

/**
 * Render the tier ladder from /api/tiers, marking the partner's own tier.
 * Rates are backend-owned (api/leads.py pays value x total_percent x queue
 * share); this panel never hardcodes them.
 */
async function renderTierList(user) {
  const list = document.getElementById('tier-list');
  if (!list) return;

  let tiers;
  try {
    const data = await ApiClient.get('/api/tiers');
    tiers = Array.isArray(data.tiers) ? data.tiers : [];
  } catch (err) {
    list.replaceChildren(tierRow(App.t('profile_tiers_load_failed'), '', false));
    handleApiError(err, 'renderTierList');
    return;
  }

  // Which rung is the partner's own -- by tier id, the only identifier the
  // producer cannot accidentally change by renaming a level.
  const mine = user.tier_id != null ? Number(user.tier_id) : null;
  const active = tiers
    .filter(tier => tier.active !== 0)
    .sort((left, right) => (left.sort_order ?? 0) - (right.sort_order ?? 0));

  if (!active.length) {
    list.replaceChildren(tierRow(App.t('profile_no_tiers'), '', false));
    return;
  }

  list.replaceChildren(...active.map(tier => tierRow(
    Ui.producerLabel(tier, 'name'),
    App.t('pt_tier_rate', { rate: formatRate(tier.base_rate) }),
    mine != null && Number(tier.id) === mine,
  )));
}

function formatRate(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return '—';
  return Number.isInteger(number) ? String(number) : String(number).replace('.', ',');
}

function tierRow(name, rate, isMine) {
  const row = document.createElement('div');
  row.className = 'stat-row';
  if (isMine) row.dataset.currentTier = 'true';

  const label = document.createElement('span');
  label.className = 'stat-row-label';
  if (isMine) {
    // The partner asked to see which rung is theirs, not just the whole ladder.
    label.style.cssText = 'display:inline-flex;align-items:center;gap:6px;font-weight:700;color:var(--brand-700)';
    const star = Icons.el('star', { size: 14, title: App.t('profile_your_tier') });
    if (star) label.appendChild(star);
    label.appendChild(document.createTextNode(name));
  } else {
    label.textContent = name;
  }

  const value = document.createElement('span');
  value.className = 'stat-row-value';
  value.textContent = rate;
  if (isMine) value.style.cssText = 'font-weight:700;color:var(--brand-700)';
  else value.style.color = 'var(--text-tertiary)';

  row.append(label, value);
  return row;
}

// ─── FORM POPULATION ──────────────────────────────────────────────────────────

function populateForm(user) {
  setVal('pf-name',    user.name);
  setVal('pf-company', user.company);
  setVal('pf-phone',   user.phone);
  setVal('pf-bio',     user.bio);
  setVal('pf-email',   user.email);  // read-only display only
}

function setVal(id, v) {
  const el = document.getElementById(id);
  if (el) el.value = v || '';
}

// ─── PROFILE FORM ─────────────────────────────────────────────────────────────

function setupProfileForm(user) {
  const form = document.getElementById('profile-form');
  if (!form) return;

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const btn = document.getElementById('save-profile-btn');
    btn.disabled = true;
    const spinner = document.createElement('span');
    spinner.className = 'spinner';
    spinner.style.cssText = 'width:16px;height:16px;border-width:2px';
    btn.replaceChildren(spinner, document.createTextNode(' ' + App.t('pt_saving')));

    const body = {
      name:    document.getElementById('pf-name').value.trim(),
      company: document.getElementById('pf-company').value.trim(),
      phone:   document.getElementById('pf-phone').value.trim(),
    };
    // Only the admin profile screen has #pf-bio: when the element is
    // absent, bio is simply never sent.
    const bioEl = document.getElementById('pf-bio');
    if (bioEl) body.bio = bioEl.value.trim();

    try {
      const data = await ApiClient.patch(`/api/users/${user.id}`, body);
      cachePublicProfile(data.user);

      renderProfileCard(data.user);
      showToast('success', App.t('toast_profile_updated'), App.t('profile_saved'));
    } catch (err) {
      handleApiError(err, 'saveProfile');
    } finally {
      btn.disabled = false;
      btn.innerHTML = '<svg width="15" height="15" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><polyline points="20 6 9 17 4 12"/></svg>';
      btn.append(` ${Ui.escapeHtml(App.t('zapisz_zmiany'))}`);
    }
  });
}

// ─── PASSWORD FORM ────────────────────────────────────────────────────────────

function setupPasswordForm(user) {
  const form = document.getElementById('password-form');
  if (!form) return;

  // Strength meter
  const newPw = document.getElementById('pw-new');
  if (newPw) {
    newPw.addEventListener('input', () => {
      const v = newPw.value;
      let score = 0;
      if (v.length >= 8)           score++;
      if (/[A-Z]/.test(v))         score++;
      if (/[0-9]/.test(v))         score++;
      if (/[^A-Za-z0-9]/.test(v)) score++;

      [1,2,3,4].forEach(n => {
        const bar = document.getElementById('psb' + n);
        if (!bar) return;
        bar.className = 'strength-bar';
        if (n <= score) bar.classList.add(score <= 1 ? 'weak' : score <= 2 ? 'medium' : 'strong');
      });

      const lbl = document.getElementById('pw-strength-label');
      if (lbl) {
        const labels = ['', App.t('profile_pw_weak'), App.t('profile_pw_medium'),
          App.t('profile_pw_good'), App.t('profile_pw_strong')];
        const colors = ['var(--text-tertiary)','var(--error-400)','var(--warning-400)','var(--success-400)','var(--success-400)'];
        lbl.textContent = labels[score] || '';
        lbl.style.color = colors[score] || colors[0];
      }
    });
  }

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const btn       = document.getElementById('save-password-btn');
    const errEl     = document.getElementById('pw-error');
    const currentPw = document.getElementById('pw-current').value;
    const newPwVal  = document.getElementById('pw-new').value;
    const newPw2    = document.getElementById('pw-new2').value;

    errEl.style.display = 'none';

    if (newPwVal.length < 8) {
      errEl.textContent = App.t('profile_pw_too_short'); errEl.style.display = 'block'; return;
    }
    if (newPwVal !== newPw2) {
      errEl.textContent = App.t('profile_pw_mismatch'); errEl.style.display = 'block'; return;
    }

    btn.disabled = true;
    btn.innerHTML = '<span class="spinner" style="width:16px;height:16px;border-width:2px"></span>';

    try {
      await ApiClient.post(`/api/users/${user.id}/password`, {
        current_password: currentPw,
        new_password:     newPwVal,
      });
      form.reset();
      showToast('success', App.t('profile_pw_changed'), App.t('profile_signing_out'));
      setTimeout(async () => {
        try { await ApiClient.post('/api/auth/logout', {}); } catch {}
        ApiClient.clearToken();
        Ui.safeNavigate('../../index.html');
      }, 2000);
    } catch (err) {
      errEl.textContent = err.message;
      errEl.style.display = 'block';
    } finally {
      btn.disabled = false;
      btn.textContent = App.t('profile_change_password');
    }
  });
}


// ─── TWO-FACTOR ──────────────────────────────────────────────────────────────

async function setupTwoFactor() {
  const state = document.getElementById('mfa-state');
  if (!state) return;

  const setup = document.getElementById('mfa-setup');
  const codesBox = document.getElementById('mfa-codes');
  const errEl = document.getElementById('mfa-error');
  const startBtn = document.getElementById('mfa-start-btn');
  const disableBtn = document.getElementById('mfa-disable-btn');
  const disableForm = document.getElementById('mfa-disable-form');
  const newCodesBtn = document.getElementById('mfa-new-codes-btn');
  const newCodesForm = document.getElementById('mfa-new-codes-form');

  const showError = (message) => {
    errEl.textContent = message;
    errEl.style.display = 'block';
  };

  // The one moment these are readable. Built as list items rather than an
  // innerHTML string -- they are secrets, and this panel should not be a markup
  // sink on the way to showing them.
  function renderCodes(codes) {
    const list = document.getElementById('mfa-codes-list');
    if (!list) return;
    list.replaceChildren(...(codes || []).map((backupCode) => {
      const item = document.createElement('li');
      item.textContent = backupCode;
      return item;
    }));
    codesBox.hidden = false;
  }

  async function refresh() {
    errEl.style.display = 'none';
    let status;
    try {
      status = await ApiClient.get('/api/auth/mfa');
    } catch (err) {
      showError(err.message);
      return;
    }
    if (disableForm) disableForm.hidden = true;
    if (newCodesForm) newCodesForm.hidden = true;
    // Offered only while the factor is actually on -- there is nothing to
    // re-issue for an account that holds none.
    if (newCodesBtn) newCodesBtn.hidden = !status.enabled;
    if (status.enabled) {
      state.textContent = App.t('mfa_on').replace('{n}', status.backup_codes_left);
      setup.hidden = true;
      startBtn.hidden = true;
      // A role that must hold the factor has no button to give it up.
      disableBtn.hidden = status.required;
    } else {
      state.textContent = status.required ? App.t('mfa_off_required') : App.t('mfa_off');
      startBtn.hidden = false;
      disableBtn.hidden = true;
    }
  }

  startBtn?.addEventListener('click', async () => {
    errEl.style.display = 'none';
    try {
      const started = await ApiClient.post('/api/auth/mfa/begin', {});
      // The QR arrives drawn: the secret never passes through a JS library.
      const qr = new DOMParser().parseFromString(started.qr_svg, 'image/svg+xml').documentElement;
      document.getElementById('mfa-qr').replaceChildren(document.importNode(qr, true));
      document.getElementById('mfa-secret').textContent = started.secret;
      setup.hidden = false;
      startBtn.hidden = true;
      document.getElementById('mfa-code').focus();
    } catch (err) {
      showError(err.message);
    }
  });

  document.getElementById('mfa-confirm-btn')?.addEventListener('click', async () => {
    errEl.style.display = 'none';
    const code = document.getElementById('mfa-code').value;
    try {
      const result = await ApiClient.post('/api/auth/mfa/confirm', { code });
      renderCodes(result.backup_codes);
      setup.hidden = true;
      await refresh();
    } catch (err) {
      showError(err.message);
    }
  });

  // "Turn off" only opens the form; the code is typed in the page, like every
  // other field. window.prompt was the one raw browser dialog left here, and
  // it cannot be styled, translated on rerender or reached from a keyboard the
  // same way the rest of the panel can.
  disableBtn?.addEventListener('click', () => {
    errEl.style.display = 'none';
    if (disableForm) disableForm.hidden = false;
    document.getElementById('mfa-disable-code')?.focus();
  });

  newCodesBtn?.addEventListener('click', () => {
    errEl.style.display = 'none';
    if (newCodesForm) newCodesForm.hidden = false;
    document.getElementById('mfa-new-codes-code')?.focus();
  });

  document.getElementById('mfa-new-codes-confirm-btn')?.addEventListener('click', async () => {
    errEl.style.display = 'none';
    const field = document.getElementById('mfa-new-codes-code');
    const code = field?.value.trim();
    if (!code) return;
    try {
      const issued = await ApiClient.post('/api/auth/mfa/backup-codes', { code });
      if (field) field.value = '';
      // refresh() hides the form and the old list; the new codes are painted
      // after it, because this is the only time they are readable.
      await refresh();
      renderCodes(issued.backup_codes);
    } catch (err) {
      showError(err.message);
    }
  });

  document.getElementById('mfa-disable-confirm-btn')?.addEventListener('click', async () => {
    errEl.style.display = 'none';
    const field = document.getElementById('mfa-disable-code');
    const code = field?.value.trim();
    if (!code) return;
    try {
      await ApiClient.post('/api/auth/mfa/disable', { code });
      if (field) field.value = '';
      if (disableForm) disableForm.hidden = true;
      codesBox.hidden = true;
      await refresh();
    } catch (err) {
      showError(err.message);
    }
  });

  await refresh();
}
