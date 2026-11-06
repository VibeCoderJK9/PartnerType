/* ============================================================
   PARTNERTYPE — AUDIT LOG PAGE
   ============================================================ */

document.addEventListener('DOMContentLoaded', () => {
  if (document.body.dataset.page === 'admin-audit') initAuditPage();
});

// Icon names come from js/icons.js (Lucide). Add a name to MANIFEST in
// scripts/build-icons.mjs and regenerate before using it here.
const ACTION_ICONS = {
  'lead.create':        { icon: 'clipboard-list', cls: 'brand'    },
  'lead.accepted':      { icon: 'circle-check',   cls: 'success'  },
  'lead.accept':        { icon: 'circle-check',   cls: 'success'  },
  'lead.rejected':      { icon: 'circle-x',       cls: 'error'    },
  'lead.reject':        { icon: 'circle-x',       cls: 'error'    },
  'lead.bulk_accept':   { icon: 'check-check',    cls: 'success'  },
  'lead.bulk_reject':   { icon: 'circle-x',       cls: 'error'    },
  'lead.update':        { icon: 'pencil',         cls: 'info'     },
  'lead.commission':    { icon: 'banknote',       cls: 'warning'  },
  'lead.commission_rate': { icon: 'banknote',     cls: 'warning'  },
  // Reopening a closed lead, and the settlement review that unblocks it.
  'lead.reopen':            { icon: 'rotate-ccw', cls: 'warning'  },
  'lead.reopen_eligibility': { icon: 'eye',        cls: 'info'     },
  // The producer creating a lead for a chosen partner -- distinct from lead.create, which is the partner's own
  // submission, because the actor and the owner are different people here.
  'lead.assign':        { icon: 'user-plus',     cls: 'brand'    },
  'comment.create':     { icon: 'message-square', cls: 'brand'    },
  'comment.delete':     { icon: 'trash-2',        cls: 'error'    },
  'lead.assign': { icon: 'user-plus', cls: 'brand' },
  'lead.reopen': { icon: 'rotate-ccw', cls: 'warning' },
  'lead.reopen_eligibility': { icon: 'circle-check', cls: 'info' },
  'tier.create': { icon: 'plus', cls: 'success' },
  'tier.update': { icon: 'pencil', cls: 'info' },
  'tier.delete': { icon: 'trash-2', cls: 'error' },
  'partner.bulk_tier': { icon: 'pencil', cls: 'warning' },
  'bonus.create': { icon: 'plus', cls: 'success' },
  'bonus.update': { icon: 'pencil', cls: 'info' },
  'bonus.delete': { icon: 'trash-2', cls: 'error' },
  'bonus.grant': { icon: 'banknote', cls: 'success' },
  'bonus.revoke': { icon: 'rotate-ccw', cls: 'warning' },
  'bonus.remove': { icon: 'trash-2', cls: 'warning' },
  'bonus.bulk_assign': { icon: 'banknote', cls: 'success' },
  'bonus.bulk_remove': { icon: 'trash-2', cls: 'warning' },
  'stage.create': { icon: 'plus', cls: 'success' },
  'stage.delete': { icon: 'trash-2', cls: 'error' },
  'reg_field.create': { icon: 'plus', cls: 'success' },
  'reg_field.update': { icon: 'pencil', cls: 'info' },
  'reg_field.delete': { icon: 'trash-2', cls: 'error' },
  'agent.action_confirm': { icon: 'circle-check', cls: 'success' },
  'agent.action_cancel': { icon: 'x', cls: 'tertiary' },
  'agent.tool_denied': { icon: 'shield-alert', cls: 'error' },
  'data.person_exported': { icon: 'download', cls: 'info' },
  'data.person_erased': { icon: 'trash-2', cls: 'error' },
  'data.retention_applied': { icon: 'calendar', cls: 'warning' },
  'user.login':         { icon: 'key',            cls: 'info'     },
  'user.logout':        { icon: 'log-out',        cls: 'tertiary' },
  // A refused sign-in is the one a producer scans for, so it carries the
  // warning colour rather than the neutral one a successful one gets.
  'user.login_failed':  { icon: 'shield-alert',   cls: 'error'    },
  'user.login_mfa_pending': { icon: 'key',        cls: 'tertiary' },
  'user.logout_all':    { icon: 'log-out',        cls: 'warning'  },
  'user.mfa_enabled':   { icon: 'circle-check',   cls: 'success'  },
  'user.mfa_codes_regenerated': { icon: 'rotate-ccw', cls: 'info'   },
  // The producer taking a factor off somebody else's account. Rare, and worth
  // seeing at a glance in a column somebody scans for trouble.
  'user.mfa_reset':     { icon: 'shield-alert',   cls: 'warning'  },
  'user.mfa_disabled':  { icon: 'shield-alert',   cls: 'warning'  },
  'user.password_reset_requested': { icon: 'send', cls: 'info'    },
  'user.password_reset': { icon: 'lock',          cls: 'warning'  },
  'user.password_reset_throttled': { icon: 'shield-alert', cls: 'warning' },
  'user.create':        { icon: 'user-plus',      cls: 'success'  },
  'user.update':        { icon: 'pencil',         cls: 'info'     },
  'user.password':      { icon: 'lock',           cls: 'warning'  },
  'user.delete':        { icon: 'trash-2',        cls: 'error'    },
  'user.invitation_accepted': { icon: 'user-plus', cls: 'success' },
  // Money leaving the company, and money clawed back.
  'payout.confirm_win': { icon: 'circle-check',   cls: 'success'  },
  'payout.record':      { icon: 'banknote',       cls: 'success'  },
  'payout.reverse':     { icon: 'rotate-ccw',     cls: 'warning'  },
  // The affiliate programme: its settings and the producer's decisions on shop orders.
  'affiliate.secret': { icon: 'key', cls: 'warning' },
  'affiliate.approve': { icon: 'circle-check', cls: 'success' },
  'affiliate.reject': { icon: 'circle-x', cls: 'warning' },
  'affiliate.assign': { icon: 'user-plus', cls: 'brand' },
  'affiliate.reverse': { icon: 'rotate-ccw', cls: 'warning' },
  'affiliate.settings': { icon: 'settings', cls: 'warning' },
  'affiliate.stripe_secret': { icon: 'key', cls: 'warning' },
  'settings.update':    { icon: 'settings',       cls: 'warning'  },
  'stage.update':       { icon: 'shuffle',        cls: 'warning'  },
  'partner.tier':       { icon: 'star',           cls: 'brand'    },
  'partner.bulk_tier':  { icon: 'star',           cls: 'brand'    },
  'bonus.assign':       { icon: 'gift',           cls: 'success'  },
  'bonus.bulk_assign':  { icon: 'gift',           cls: 'success'  },
  'bonus.remove':       { icon: 'circle-minus',   cls: 'error'    },
  'bonus.bulk_remove':  { icon: 'circle-minus',   cls: 'error'    },
  'bonus.create':       { icon: 'plus',           cls: 'success'  },
  'bonus.update':       { icon: 'pencil',         cls: 'info'     },
  'bonus.delete':       { icon: 'trash-2',        cls: 'error'    },
  'product.create':     { icon: 'package-plus',   cls: 'success'  },
  'product.update':     { icon: 'package',        cls: 'info'     },
  'product.delete':     { icon: 'trash-2',        cls: 'error'    },
  'knowledge.upload':   { icon: 'book-open',      cls: 'success'  },
  'knowledge.update':   { icon: 'pencil',         cls: 'info'     },
  'knowledge.delete':   { icon: 'trash-2',        cls: 'error'    },
  'stage.create':       { icon: 'plus',           cls: 'success'  },
  'stage.delete':       { icon: 'trash-2',        cls: 'error'    },
  'tier.create':        { icon: 'plus',           cls: 'success'  },
  'tier.update':        { icon: 'pencil',         cls: 'info'     },
  'tier.delete':        { icon: 'trash-2',        cls: 'error'    },
  'agent.chat':         { icon: 'bot',            cls: 'brand'    },
  'agent.action_confirm': { icon: 'circle-check', cls: 'success'  },
  'agent.action_cancel':  { icon: 'circle-x',     cls: 'tertiary' },
  'agent.tool_denied':    { icon: 'shield-alert', cls: 'warning'  },
  'export.leads':       { icon: 'download',       cls: 'tertiary' },
  'export.commissions': { icon: 'download',       cls: 'tertiary' },
};

/**
 * Values for one audit sentence, with the nested labels resolved.
 *
 * A value named `*_key` holds a dictionary key rather than a finished word --
 * that is how a decision like "needs_info" travels without being frozen into
 * Polish at the moment it was recorded. It is translated here and offered
 * under the name without the suffix, so the sentence reads `{status}`.
 */
function resolvedDetailParams(params) {
  if (!params || typeof params !== 'object') return undefined;
  const resolved = {};
  for (const [name, value] of Object.entries(params)) {
    if (name.endsWith('_key') && typeof value === 'string' && value) {
      resolved[name.slice(0, -4)] = App.t(value);
    } else {
      resolved[name] = value;
    }
  }
  return resolved;
}

const ACTION_LABEL_KEYS = {
  'lead.create': 'audit_action_lead_create',
  'lead.update': 'audit_action_lead_update',
  'lead.accept': 'audit_action_lead_accept',
  'lead.accepted': 'audit_action_lead_accept',
  'lead.reject': 'audit_action_lead_reject',
  'lead.rejected': 'audit_action_lead_reject',
  'lead.bulk_accept': 'audit_action_bulk_accept',
  'lead.bulk_reject': 'audit_action_bulk_reject',
  'lead.commission': 'audit_action_commission',
  'lead.commission_rate': 'audit_action_commission_rate',
  'comment.create': 'audit_action_comment_create',
  'comment.delete': 'audit_action_comment_delete',
  // Half the log had no name at all and rendered its raw key --
  // `bonus.create`, `lead.assign`, `tier.delete` -- on the one screen whose
  // whole job is answering "who did what".
  'lead.assign': 'audit_action_lead_assign',
  'lead.reopen': 'audit_action_lead_reopen',
  'lead.reopen_eligibility': 'audit_action_lead_reopen_eligibility',
  'tier.create': 'audit_action_tier_create',
  'tier.update': 'audit_action_tier_update',
  'tier.delete': 'audit_action_tier_delete',
  'partner.bulk_tier': 'audit_action_partner_bulk_tier',
  'bonus.create': 'audit_action_bonus_create',
  'bonus.update': 'audit_action_bonus_update',
  'bonus.delete': 'audit_action_bonus_delete',
  'bonus.grant': 'audit_action_bonus_grant',
  'bonus.revoke': 'audit_action_bonus_revoke',
  'bonus.remove': 'audit_action_bonus_remove',
  'bonus.bulk_assign': 'audit_action_bonus_bulk_assign',
  'bonus.bulk_remove': 'audit_action_bonus_bulk_remove',
  'stage.create': 'audit_action_stage_create',
  'stage.delete': 'audit_action_stage_delete',
  'reg_field.create': 'audit_action_reg_field_create',
  'reg_field.update': 'audit_action_reg_field_update',
  'reg_field.delete': 'audit_action_reg_field_delete',
  'agent.action_confirm': 'audit_action_agent_confirm',
  'agent.action_cancel': 'audit_action_agent_cancel',
  'agent.tool_denied': 'audit_action_agent_denied',
  'data.person_exported': 'audit_action_data_exported',
  'data.person_erased': 'audit_action_data_erased',
  'data.retention_applied': 'audit_action_data_retention',
  'user.login': 'audit_action_login',
  'user.logout': 'audit_action_logout',
  // The sign-in and account-recovery events. Without a name here the screen
  // would fall back to the raw key.
  'user.login_failed': 'audit_action_login_failed',
  'user.login_mfa_pending': 'audit_action_login_mfa_pending',
  'user.logout_all': 'audit_action_logout_all',
  'user.mfa_enabled': 'audit_action_mfa_enabled',
  'user.mfa_codes_regenerated': 'audit_action_mfa_codes_regenerated',
  'user.mfa_reset': 'audit_action_mfa_reset',
  'user.mfa_disabled': 'audit_action_mfa_disabled',
  'user.password_reset_requested': 'audit_action_password_reset_requested',
  'user.password_reset': 'audit_action_password_reset',
  'user.password_reset_throttled': 'audit_action_password_reset_throttled',
  'user.create': 'audit_action_user_create',
  'user.update': 'audit_action_user_update',
  'user.password': 'audit_action_user_password',
  'user.delete': 'audit_action_user_delete',
  'user.invitation_accepted': 'audit_action_user_invitation_accepted',
  'payout.confirm_win': 'audit_action_confirm_win',
  'payout.record': 'audit_action_payout_record',
  'payout.reverse': 'audit_action_payout_reverse',
  'affiliate.secret': 'audit_action_affiliate_secret',
  'affiliate.approve': 'audit_action_affiliate_approve',
  'affiliate.reject': 'audit_action_affiliate_reject',
  'affiliate.assign': 'audit_action_affiliate_assign',
  'affiliate.reverse': 'audit_action_affiliate_reverse',
  'affiliate.settings': 'audit_action_affiliate_settings',
  'affiliate.stripe_secret': 'audit_action_affiliate_stripe_secret',
  'settings.update': 'audit_action_settings_update',
  'product.create': 'audit_action_product_create',
  'product.update': 'audit_action_product_update',
  'product.delete': 'audit_action_product_delete',
  'knowledge.upload': 'audit_action_knowledge_upload',
  'knowledge.update': 'audit_action_knowledge_update',
  'knowledge.delete': 'audit_action_knowledge_delete',
  'stage.update': 'audit_action_stage_update',
  'partner.tier': 'audit_action_partner_tier',
  'bonus.assign': 'audit_action_bonus_assign',
  'export.leads': 'audit_action_export_leads',
  'export.commissions': 'audit_action_export_commissions',
  'agent.chat': 'audit_action_agent_chat',
  // Bramka API i MCP: written in api/gateway/, scanned by audit-labels.
  'integration.enabled': 'audit_action_integration_enabled',
  'integration.disabled': 'audit_action_integration_disabled',
  'integration.partners_enabled': 'audit_action_integration_partners_enabled',
  'integration.partners_disabled': 'audit_action_integration_partners_disabled',
  'integration.fields_changed': 'audit_action_integration_fields_changed',
  'integration.limits_changed': 'audit_action_integration_limits_changed',
  'integration.key_created': 'audit_action_integration_key_created',
  'integration.key_revoked': 'audit_action_integration_key_revoked',
  'integration.oauth_consent': 'audit_action_integration_oauth_consent',
  'integration.connection_revoked': 'audit_action_integration_connection_revoked',
  'integration.credentials_revoked': 'audit_action_integration_credentials_revoked',
};

let currentPage = 1;
const PAGE_SIZE  = 50;
let totalEntries = 0;
let labels = {};

async function initAuditPage() {
  setupFilters();
  await loadAudit();
}

function setupFilters() {
  document.getElementById('audit-apply-btn')?.addEventListener('click', () => {
    currentPage = 1; loadAudit();
  });
  document.getElementById('audit-clear-btn')?.addEventListener('click', () => {
    ['audit-action-filter','audit-user-filter','audit-date-from','audit-date-to']
      .forEach(id => { const el = document.getElementById(id); if (el) el.value = ''; });
    currentPage = 1; loadAudit();
  });
  document.getElementById('audit-prev-btn')?.addEventListener('click', () => {
    if (currentPage > 1) { currentPage--; loadAudit(); }
  });
  document.getElementById('audit-next-btn')?.addEventListener('click', () => {
    if (currentPage * PAGE_SIZE < totalEntries) { currentPage++; loadAudit(); }
  });
}

if (typeof App !== 'undefined' && App.onRerender) {
  App.onRerender(() => {
    if (document.body.dataset.page === 'admin-audit') void loadAudit();
  });
}

async function loadAudit() {
  const tbody = document.getElementById('audit-tbody');
  if (tbody) showAuditStatus(tbody, '', true);

  const params = new URLSearchParams({
    limit:  PAGE_SIZE,
    offset: (currentPage - 1) * PAGE_SIZE,
  });

  const action = document.getElementById('audit-action-filter')?.value?.trim();
  const usr    = document.getElementById('audit-user-filter')?.value?.trim();
  const dfrom  = document.getElementById('audit-date-from')?.value;
  const dto    = document.getElementById('audit-date-to')?.value;
  // "@all" and "@sessions" pick a scope rather than one action; an empty choice
  // leaves the server's default, the actions taken on the data.
  if (action === '@all' || action === '@sessions') params.set('scope', action.slice(1));
  else if (action) params.set('action', action);
  if (usr)    params.set('user', usr);
  if (dfrom)  params.set('from', dfrom);
  if (dto)    params.set('to', dto);

  try {
    const data = await ApiClient.get(`/api/audit?${params.toString()}`);
    totalEntries = data.total;
    labels = data.labels || {};
    renderTable(data.logs);
    updatePager();
  } catch(e) {
    if (tbody) showAuditStatus(tbody, App.t('audit_load_error', { message: e.message || '' }));
  }
}

function renderTable(logs) {
  const tbody = document.getElementById('audit-tbody');
  if (!tbody) return;

  if (!logs.length) {
    showAuditStatus(tbody, App.t('no_data'), false, 'text-center text-tertiary');
    return;
  }

  tbody.replaceChildren(...logs.map(log => {
    const meta   = ACTION_ICONS[log.action] || { icon: 'more-horizontal', cls: 'info' };
    const labelKey = ACTION_LABEL_KEYS[log.action];
    const label = labelKey ? App.t(labelKey) : (labels[log.action] || log.action);
    const time   = formatDateTime(log.created_at);
    const roleClr = log.user_role === 'admin' ? 'var(--brand-400)' : 'var(--success-400)';
    const row = document.createElement('tr');
    const timeCell = auditElement('td', 'text-xs text-tertiary', time);
    timeCell.style.whiteSpace = 'nowrap';
    row.append(timeCell);
    const userCell = document.createElement('td');
    const user = auditElement('div');
    Object.assign(user.style, { display: 'flex', alignItems: 'center', gap: '8px' });
    const avatar = auditElement('div', 'avatar avatar-sm', getInitials(log.user_name || '?'));
    Object.assign(avatar.style, { background: 'linear-gradient(135deg,var(--brand-600),var(--brand-500))', fontSize: '0.6rem' });
    const identity = auditElement('div');
    const role = auditElement('div', 'text-xs', log.user_role || '');
    role.style.color = roleClr;
    identity.append(auditElement('div', 'cell-primary', log.user_name || '—'), role);
    user.append(avatar, identity);
    userCell.append(user);
    row.append(userCell);
    const actionCell = document.createElement('td');
    const badge = auditElement('span', 'audit-action-badge');
    // The badge is inline-flex with a gap (css/bulk.css), so the icon and the
    // label sit side by side without extra styling here.
    Icons.label(badge, meta.icon, label || '—', { size: 14 });
    // Tint the background rather than filling it with the same colour as the
    // label — a solid fill made the text invisible against its own background,
    // leaving only the icon visible.
    const badgeClr = badgeColor(meta.cls);
    Object.assign(badge.style, { background: `color-mix(in srgb, ${badgeClr} 15%, transparent)`, color: badgeClr });
    actionCell.append(badge);
    row.append(actionCell, auditElement('td', 'text-xs text-secondary', log.entity || '—'), auditElement('td', 'text-xs text-secondary', log.entity_id || '—'));
    const detailCell = document.createElement('td');
    // A detail that is a SENTENCE was stored as a dictionary key plus its
    // values, so it reads in the language the viewer has the app in. One made
    // of names and figures was stored as-is and needs nothing: it reads the
    // same either way. Anything older, or any key the dictionary no longer
    // has, falls back to the text as it was written.
    const detailText = (log.detail_key && App.t(log.detail_key) !== log.detail_key)
      ? App.t(log.detail_key, resolvedDetailParams(log.detail_params))
      : (log.detail || '');
    const detail = auditElement('div', 'audit-detail', detailText || '—');
    detail.title = String(detailText || '');
    detailCell.append(detail);
    row.append(detailCell, auditElement('td', 'text-xs text-tertiary', log.ip || '—'));
    return row;
  }));
}

function badgeColor(cls) {
  const map = {
    brand: 'var(--brand-400)',
    success: 'var(--success-400)',
    error: 'var(--error-400)',
    warning: 'var(--warning-400)',
    info: 'var(--info-400)',
    tertiary: 'var(--text-tertiary)',
  };
  return map[cls] || 'var(--text-secondary)';
}

function updatePager() {
  const pages    = Math.max(1, Math.ceil(totalEntries / PAGE_SIZE));
  const pagerEl  = document.getElementById('audit-pager-info');
  const prevBtn  = document.getElementById('audit-prev-btn');
  const nextBtn  = document.getElementById('audit-next-btn');
  const totalEl  = document.getElementById('audit-total');
  if (pagerEl)  pagerEl.textContent  = App.t('audit_page', { page: currentPage, pages });
  if (totalEl)  totalEl.textContent  = App.pluralize(totalEntries, 'unit_entry');
  if (prevBtn)  prevBtn.disabled = currentPage <= 1;
  if (nextBtn)  nextBtn.disabled = currentPage >= pages;
}

function formatDateTime(iso) {
  if (!iso) return '—';
  try {
    const d = new Date(iso + (iso.endsWith('Z') ? '' : 'Z'));
    return d.toLocaleString(activeLocale(), { day:'2-digit',month:'2-digit',year:'numeric', hour:'2-digit',minute:'2-digit' });
  } catch { return iso; }
}

function showAuditStatus(tbody, message, loading = false, className = '') {
  const row = document.createElement('tr');
  const cell = auditElement('td', className, '');
  cell.colSpan = 7;
  Object.assign(cell.style, { textAlign: 'center', padding: loading ? '32px' : '40px' });
  if (loading) {
    cell.append(auditElement('span', 'spinner'));
  } else if (message.startsWith(App.t('err_load_prefix'))) {
    cell.append(auditElement('p', 'text-sm text-secondary', App.t('audit_retry_copy')));
    cell.append(auditElement('p', 'text-xs text-tertiary', message));
    const retry = auditElement('button', 'btn btn-secondary btn-sm', App.t('retry'));
    retry.type = 'button';
    retry.style.marginTop = '12px';
    retry.addEventListener('click', () => loadAudit());
    cell.append(retry);
  } else {
    cell.textContent = App.t('audit_empty');
  }
  row.append(cell);
  tbody.replaceChildren(row);
}

function auditElement(tag, className = '', text = '') {
  const element = document.createElement(tag);
  if (className) element.className = className;
  element.textContent = String(text ?? '');
  return element;
}
