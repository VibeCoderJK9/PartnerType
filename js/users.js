/* ============================================================
   PARTNERTYPE — USERS PAGE LOGIC
   The producer's own team: roster, invitation, emergency manual add.

   A staff-only roster, in the Konto block under the profile. Partner
   accounts — invitation, details, acceptance, suspension, rejection —
   belong entirely to js/admin-partners.js and never appear here.
   ============================================================ */

let teamMembers = [];
let currentUserId = null;

// Identifiers stay English, the producer-facing label stays Polish.
// Mirrors api/authz.py::can_manage_users, which is admin-only. The server
// refuses either way; this only decides whether a control that would always
// fail is drawn at all.
let canManageTeam = false;

// Read when a row is drawn, never built at load time: a frozen object would
// hold whatever language was active when the file was parsed, and a language
// switch could never reach it.
const TEAM_ROLES = ['admin', 'manager', 'observer'];

// App.t returns the KEY when a string is missing, which is truthy -- so an
// `|| role` fallback after it can never fire. The membership check is what
// makes an unrecognised value fall back to itself instead of rendering
// "users_role_x" on screen.
const TEAM_STATUSES = ['active', 'pending', 'suspended', 'rejected'];

function teamRoleLabel(role) {
  return TEAM_ROLES.includes(role) ? App.t('users_role_' + role) : role;
}

// One line per role saying what it can actually do, shown under the role
// name. A roster that lists roles without saying what they mean makes the
// producer guess at exactly the moment they are handing out access.
function teamRoleHint(role) {
  return App.t('users_hint_' + role);
}

function teamStatusLabel(status) {
  return TEAM_STATUSES.includes(status) ? App.t('users_status_' + status) : status;
}

document.addEventListener('DOMContentLoaded', () => {
  if (document.body.dataset.page === 'admin-users') {
    initUsersPage();
  }
});

async function initUsersPage() {
  const session = await Ui.rehydrateSession(ApiClient).catch(() => null);
  currentUserId = session ? session.id : null;
  canManageTeam = !!session && session.role === 'admin';
  await loadTeam();
  setupTeamInviteModal();
}

// ─── ROSTER ───────────────────────────────────────────────────────────────────

async function loadTeam() {
  showTeamLoading();
  try {
    const data = await ApiClient.get('/api/users?scope=team');
    teamMembers = data.users || [];
    renderTeam();
  } catch (err) {
    showTeamError();
  }
}

function showTeamLoading() {
  const loadingEl = document.getElementById('team-loading-state');
  const emptyEl = document.getElementById('team-empty-state');
  const errorEl = document.getElementById('team-error-state');
  const wrapper = document.getElementById('team-table-wrapper');
  if (loadingEl) loadingEl.hidden = false;
  if (emptyEl) emptyEl.hidden = true;
  if (errorEl) errorEl.hidden = true;
  if (wrapper) wrapper.hidden = true;
}

function showTeamError() {
  const loadingEl = document.getElementById('team-loading-state');
  const emptyEl = document.getElementById('team-empty-state');
  const errorEl = document.getElementById('team-error-state');
  const wrapper = document.getElementById('team-table-wrapper');
  if (loadingEl) loadingEl.hidden = true;
  if (emptyEl) emptyEl.hidden = true;
  if (errorEl) errorEl.hidden = false;
  if (wrapper) wrapper.hidden = true;
}

// Built with DOM nodes and textContent only, never innerHTML/markup
// assembly and never a stored name interpolated into an attribute or a
// handler (see security-sink-allowlist.json).
function createTeamRow(member) {
  const row = document.createElement('tr');

  const nameCell = document.createElement('td');
  const nameWrap = document.createElement('div');
  nameWrap.style.cssText = 'display:flex;align-items:center;gap:8px';
  const nameSpan = document.createElement('span');
  nameSpan.className = 'cell-primary';
  nameSpan.textContent = member.name;
  nameWrap.appendChild(nameSpan);
  // The reader's own row is marked as theirs — a list of who has access
  // should never hide the reader themselves.
  if (member.id === currentUserId) {
    const youBadge = document.createElement('span');
    youBadge.className = 'badge badge-success';
    youBadge.textContent = App.t('users_you');
    nameWrap.appendChild(youBadge);
  }
  const emailLine = document.createElement('div');
  emailLine.className = 'text-xs text-tertiary';
  emailLine.textContent = member.email || '';
  nameCell.appendChild(nameWrap);
  nameCell.appendChild(emailLine);
  row.appendChild(nameCell);

  // The role is editable in place (owner: "Brakuje funkcji usuwania i zmiany
  // uprawnien takiego usera"). Your own row keeps a plain label: demoting
  // yourself out of admin would take user management with it, and the backend
  // would have to refuse it anyway.
  const roleCell = document.createElement('td');
  const editable = canManageTeam && member.id !== currentUserId;
  if (editable) {
    const select = document.createElement('select');
    select.className = 'form-select form-select-sm';
    select.setAttribute('aria-label', `${App.t('label_role')}: ${member.name}`);
    for (const value of TEAM_ROLES) {
      const label = teamRoleLabel(value);
      const option = document.createElement('option');
      option.value = value;
      option.textContent = label;
      option.selected = value === member.role;
      select.appendChild(option);
    }
    select.addEventListener('change', () => void changeRole(member, select));
    roleCell.appendChild(select);
  } else {
    const roleName = document.createElement('div');
    roleName.textContent = teamRoleLabel(member.role);
    roleCell.appendChild(roleName);
  }
  const roleHint = teamRoleHint(member.role);
  if (roleHint) {
    const hintLine = document.createElement('div');
    hintLine.className = 'text-xs text-tertiary';
    hintLine.textContent = roleHint;
    roleCell.appendChild(hintLine);
  }
  row.appendChild(roleCell);

  const statusCell = document.createElement('td');
  statusCell.textContent = teamStatusLabel(member.account_status);
  row.appendChild(statusCell);

  const dateCell = document.createElement('td');
  dateCell.textContent = formatDate(member.created_at);
  row.appendChild(dateCell);

  const actionsCell = document.createElement('td');
  if (canManageTeam && member.id !== currentUserId) {
    // Somebody who lost the phone AND the printed codes cannot reopen their own
    // account, so an administrator can reset the factor. Never offered for your own row: a session in the wrong hands
    // would otherwise strip the factor with one click.
    const resetMfa = document.createElement('button');
    resetMfa.type = 'button';
    resetMfa.className = 'btn btn-ghost btn-sm';
    resetMfa.textContent = App.t('users_reset_mfa');
    resetMfa.addEventListener('click', () => void resetMemberMfa(member, resetMfa));
    actionsCell.appendChild(resetMfa);

    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'btn btn-ghost btn-sm';
    remove.textContent = App.t('users_delete');
    remove.addEventListener('click', () => void removeMember(member, remove));
    actionsCell.appendChild(remove);
  }
  row.appendChild(actionsCell);

  return row;
}

// ─── ROLE AND REMOVAL ─────────────────────────────────────────────────────────

async function resetMemberMfa(member, button) {
  if (!window.confirm(App.t('users_reset_mfa_q', { name: member.name }))) return;
  button.disabled = true;
  try {
    await ApiClient.post(`/api/users/${member.id}/mfa/reset`, {});
    showToast('success', App.t('users_reset_mfa_done'), member.name);
    await loadTeam();
  } catch (err) {
    handleApiError(err, 'resetMemberMfa');
  } finally {
    button.disabled = false;
  }
}

async function changeRole(member, select) {
  const next = select.value;
  if (next === member.role) return;
  const label = teamRoleLabel(next);
  if (!window.confirm(App.t('users_change_role_q', { name: member.name, role: label }))) {
    select.value = member.role;
    return;
  }
  select.disabled = true;
  try {
    await ApiClient.patch(`/api/users/${member.id}`, { role: next });
    showToast('success', App.t('users_role_changed'), `${member.name} — ${label}`);
    await loadTeam();
  } catch (e) {
    select.value = member.role;
    select.disabled = false;
    showToast('error', App.t('users_role_not_changed'), e.message || App.t('users_try_again'));
  }
}

async function removeMember(member, button) {
  // Named, and stated as permanent. The backend refuses an account that
  // carries financial or decision history and says why, so the producer is
  // told to deactivate instead rather than seeing a database error.
  const ok = window.confirm(
    App.t('users_delete_account_q', { name: member.name, email: member.email }),
  );
  if (!ok) return;
  button.disabled = true;
  try {
    await ApiClient.delete(`/api/users/${member.id}`);
    showToast('success', App.t('users_account_deleted'), member.name);
    await loadTeam();
  } catch (e) {
    button.disabled = false;
    showToast('error', App.t('users_account_not_deleted'), e.message || App.t('users_try_again'));
  }
}

// Renders in the order the server returned (administrators, then moderators,
// then observers, each by name then id, per api/users.py::list_team()) —
// re-sorting on the client would introduce a second, drifting ordering rule.
function renderTeam() {
  const tbody = document.getElementById('team-tbody');
  const wrapper = document.getElementById('team-table-wrapper');
  const emptyEl = document.getElementById('team-empty-state');
  const errorEl = document.getElementById('team-error-state');
  const loadingEl = document.getElementById('team-loading-state');
  const countEl = document.getElementById('team-count-label');
  if (!tbody) return;

  if (loadingEl) loadingEl.hidden = true;
  if (errorEl) errorEl.hidden = true;
  tbody.textContent = '';

  const team = teamMembers || [];
  if (countEl) countEl.textContent = App.pluralize(team.length, 'unit_person');

  // A company always contains the person reading the screen — a
  // single-account response renders one row, never the empty state.
  if (team.length === 0) {
    if (wrapper) wrapper.hidden = true;
    if (emptyEl) emptyEl.hidden = false;
    return;
  }
  if (emptyEl) emptyEl.hidden = true;
  if (wrapper) wrapper.hidden = false;

  team.forEach(member => tbody.appendChild(createTeamRow(member)));
}

// ─── INVITE MODAL ─────────────────────────────────────────────────────────────

// Icons.label writes the icon AND the word, so these two buttons cannot carry
// data-i18n -- applyTranslations sets textContent and would delete the icon.
// They are relabelled from here instead, on load and on a language switch.
function renderTeamButtonLabels() {
  const inviteBtn = document.getElementById('team-invite-btn');
  if (inviteBtn) Icons.label(inviteBtn, 'send', App.t('users_invite_to_team'), { size: 15 });
  Icons.label(document.getElementById('team-invite-copy-btn'), 'clipboard-list', App.t('action_copy'), { size: 14 });
}

function setupTeamInviteModal() {
  document.getElementById('team-retry-btn')?.addEventListener('click', loadTeam);

  renderTeamButtonLabels();
  Icons.into(document.getElementById('team-invite-modal-close'), 'x', { size: 18 });

  document.getElementById('team-invite-btn')?.addEventListener('click', openTeamInviteModal);
  document.getElementById('team-invite-modal-close')?.addEventListener('click', closeTeamInviteModal);
  document.getElementById('team-invite-cancel')?.addEventListener('click', closeTeamInviteModal);
  document.getElementById('team-invite-send-btn')?.addEventListener('click', sendTeamInvite);
  document.getElementById('team-invite-copy-btn')?.addEventListener('click', copyTeamInviteLink);
}

// Dedicated open/close pair rather than js/app.js's page-level
// openModal(id), which the settings page's generic modal builder can shadow
// by load order.
function openTeamInviteModal() {
  document.getElementById('team-invite-name').value = '';
  document.getElementById('team-invite-email').value = '';
  const roleEl = document.getElementById('team-invite-role');
  if (roleEl) roleEl.value = 'manager';
  document.getElementById('team-invite-error').style.display = 'none';
  document.getElementById('team-invite-result').hidden = true;
  document.getElementById('team-invite-link').value = '';
  document.getElementById('team-invite-mail-status').textContent = '';
  const overlay = document.getElementById('team-invite-modal');
  if (overlay) { overlay.classList.add('show'); document.body.style.overflow = 'hidden'; }
}

function closeTeamInviteModal() {
  const overlay = document.getElementById('team-invite-modal');
  if (overlay) { overlay.classList.remove('show'); document.body.style.overflow = ''; }
}

async function sendTeamInvite() {
  const errEl = document.getElementById('team-invite-error');
  errEl.style.display = 'none';

  // Name, address and the requested team role. No company (derived
  // server-side from the acting administrator), no level, no bio — a team
  // member is not a partner. The server validates the role against
  // users.TEAM_ROLES and refuses anything else, so this select widens what
  // can be asked for without widening what can be granted.
  const roleEl = document.getElementById('team-invite-role');
  const body = {
    name: document.getElementById('team-invite-name').value.trim(),
    email: document.getElementById('team-invite-email').value.trim(),
    role: roleEl ? roleEl.value : 'manager',
  };

  if (!body.name || !body.email) {
    errEl.textContent = App.t('users_required_fields');
    errEl.style.display = 'block';
    return;
  }

  try {
    const data = await ApiClient.post('/api/users/team-invite', body);

    document.getElementById('team-invite-link').value = data.invite_url;
    // Mail outcome is stated plainly either way — a failed send is never
    // presented as a failed invitation, since a fresh install's SMTP starts
    // disabled and the link must still be usable.
    document.getElementById('team-invite-mail-status').textContent = data.sent
      ? App.t('users_invite_sent', { email: data.user.email })
      : App.t('users_smtp_missing');
    document.getElementById('team-invite-result').hidden = false;
    await loadTeam();
  } catch (err) {
    errEl.textContent = err.message;
    errEl.style.display = 'block';
  }
}

async function copyTeamInviteLink() {
  const input = document.getElementById('team-invite-link');
  const link = input.value;
  if (!link) return;

  if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
    try {
      await navigator.clipboard.writeText(link);
      showToast('success', App.t('toast_copied'), App.t('toast_invite_link_clipboard'));
      return;
    } catch {
      // Falls through to the keyboard fallback below.
    }
  }

  // The Clipboard API is unavailable on plain http origins in some browsers —
  // exactly the local, unencrypted deployment this product runs on today.
  // Select the text so the producer can still copy it by hand.
  input.removeAttribute('readonly');
  input.focus();
  input.select();
  input.setAttribute('readonly', '');
  showToast('warning', App.t('toast_link_selected'), App.t('users_copy_manually'));
}

// Every row, both selects and the count label are built here, so a language
// switch has to draw them again -- applyTranslations only reaches the markup
// that lives in users.html.
if (typeof App !== 'undefined' && App.onRerender) {
  App.onRerender(() => {
    renderTeamButtonLabels();
    renderTeam();
  });
}
