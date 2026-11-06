/* ============================================================
   PARTNERTYPE — ADMIN SHELL
   Server-backed identity, shared sidebar, and page dispatch.
   ============================================================ */

(function initAdminShellNamespace(global) {
  'use strict';

  const CONTROLLERS = {
    'admin-dashboard': 'AdminDashboard',
    'admin-leads': 'AdminLeads',
    'admin-partners': 'AdminPartners',
    'admin-knowledge': 'AdminKnowledge',
  };

  // Which staff roles may reach the admin shell at all. Mirrors
  // the staff roles the server lets in: an observer gets in, then finds
  // every surface but Leads closed.
  const SHELL_ROLES = ['admin', 'manager', 'observer'];

  // What each role sees in the left menu. Anything not listed renders
  // greyed out and unclickable rather than a menu that silently hides
  // where the app ends. This is a courtesy, never a control: api/authz.py refuses the
  // same requests regardless of what the browser drew.
  const NAV_ALLOWED = {
    admin: null,      // null = everything
    manager: null,    // full Settings, minus the two pages below
    // The knowledge base is read by every account; the page shows an observer
    // no editing, and api/knowledge.py refuses them any.
    observer: ['leads.html', 'profile.html', 'knowledge.html'],
  };

  // Pages a role must never even see linked, as opposed to greyed out.
  // A moderator's own profile stays reachable; the audit log and the user
  // roster are admin-only.
  const NAV_DENIED = {
    manager: ['audit.html', 'users.html'],
  };

  const ROLE_LABEL_KEYS = {
    admin: 'users_role_admin',
    manager: 'users_role_manager',
    observer: 'users_role_observer',
  };

  let initialization = null;

  function initials(name) {
    if (typeof global.getInitials === 'function') return global.getInitials(name);
    const parts = String(name || '').trim().split(/\s+/).filter(Boolean);
    if (!parts.length) return '??';
    return (parts.length > 1 ? parts[0][0] + parts[1][0] : parts[0].slice(0, 2)).toUpperCase();
  }

  function navigateToLogin() {
    Ui.safeNavigate('../../index.html');
  }

  function renderSidebarIdentity(user) {
    const nameEl = document.getElementById('sidebar-user-name');
    const roleEl = document.getElementById('sidebar-user-role');
    const sidebarAvatar = document.getElementById('sidebar-avatar');
    const headerAvatar = document.getElementById('header-avatar');

    if (nameEl) nameEl.textContent = user.name || '';
    // Role and company on one line under the name. Without it a moderator's
    // session looks exactly like an administrator's.
    if (roleEl) {
      roleEl.textContent = [App.t(ROLE_LABEL_KEYS[user.role] || 'users_role_observer'), user.company]
        .filter(Boolean).join(' \u00b7 ');
    }
    if (sidebarAvatar) sidebarAvatar.textContent = initials(user.name);
    if (headerAvatar) headerAvatar.textContent = initials(user.name);
  }

  function initSidebar(user) {
    renderSidebarIdentity(user);

    document.getElementById('mobile-menu-btn')?.addEventListener('click', () => {
      if (global.matchMedia?.('(max-width: 768px)')?.matches) {
        const sidebar = document.querySelector('.sidebar');
        const open = sidebar?.classList.toggle('open') || false;
        document.getElementById('mobile-menu-btn')?.setAttribute('aria-expanded', String(open));
        return;
      }
      document.getElementById('sidebar-toggle')?.click();
    });

    document.getElementById('logout-btn')?.addEventListener('click', async () => {
      try {
        await ApiClient.post('/api/auth/logout', {});
      } catch {
        // Local state still clears when the server session already expired.
      }
      ApiClient.clearToken();
      navigateToLogin();
    });

    // "settings.html#plugins" is its own entry (Plugins), so when the address
    // carries that hash it is the one lit, not Settings.
    const currentHref = global.location.pathname.split('/').pop();
    const withHash = currentHref + (global.location.hash || '');
    const items = [...document.querySelectorAll('.nav-item')];
    const activeHref = items.some((item) => item.getAttribute('href') === withHash) ? withHash : currentHref;
    items.forEach((item) => {
      item.classList.toggle('active', item.getAttribute('href') === activeHref);
    });

    applyNavScope(user);
  }

  /**
   * Grey out every nav entry outside the acting role's scope.
   * @param {{role: string}} user the authenticated staff identity
   */
  function applyNavScope(user) {
    const allowed = NAV_ALLOWED[user.role];
    const denied = NAV_DENIED[user.role] || [];
    if (allowed === null && !denied.length) return;

    document.querySelectorAll('.nav-item').forEach((item) => {
      const href = item.getAttribute('href');
      const permitted = (allowed === null || allowed.includes(href)) && !denied.includes(href);
      if (permitted) return;
      item.classList.add('nav-item-disabled');
      item.setAttribute('aria-disabled', 'true');
      item.setAttribute('tabindex', '-1');
      item.title = App.t('nav_section_forbidden');
      // removeAttribute('href') alone would leave a focusable, clickable
      // element in some browsers; the listener is what actually stops the
      // navigation, and capture beats any handler attached later.
      item.addEventListener('click', (event) => {
        event.preventDefault();
        event.stopPropagation();
      }, true);
    });
  }

  async function dispatchPage(user) {
    const controllerName = CONTROLLERS[document.body.dataset.page];
    const controller = controllerName ? global[controllerName] : null;
    if (controller && typeof controller.init === 'function') {
      await controller.init(user);
    }
  }

  async function initialize() {
    let user = null;
    try {
      user = await Ui.rehydrateSession(ApiClient);
    } catch {
      sessionStorage.removeItem('ph_session');
    }

    if (!user || !SHELL_ROLES.includes(user.role)) {
      navigateToLogin();
      return null;
    }

    initSidebar(user);
    App.onRerender(() => renderSidebarIdentity(user));
    App.applyTranslations();
    await dispatchPage(user);
    // After the controller has painted: the sticky scroll rails need the real
    // table width, which only exists once rows are in the DOM. The helper is
    // idempotent and re-measures on its own afterwards (ResizeObserver).
    Ui.attachTableScroll(document);
    return user;
  }

  /**
   * Rehydrate the authoritative server identity and initialize this admin page once.
   * Repeated calls share the same promise so listeners and controllers cannot duplicate.
   * @returns {Promise<object|null>} the authenticated public admin user, or null after redirect
   */
  function init() {
    if (!initialization) initialization = initialize();
    return initialization;
  }

  global.AdminShell = Object.freeze({ init });

  document.addEventListener('DOMContentLoaded', () => {
    void init();
  });
})(window);
