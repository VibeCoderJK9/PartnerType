/* ============================================================
   PARTNERTYPE — Notifications
   Bell dropdown with unread count, mark-read, and safe navigation.
   ============================================================ */

(function initNotifications(global) {
  'use strict';

  const POLL_INTERVAL = 30_000;
  let pollTimer = null;
  let currentUnread = 0;
  let latestNotifications = [];
  const notificationsById = new Map();

  const TYPE_ICONS = {
    success: `<svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><polyline points="20 6 9 17 4 12"/></svg>`,
    error: `<svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>`,
    warning: `<svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/></svg>`,
    info: `<svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>`,
  };

  function injectBell() {
    const headerRight = document.querySelector('.header-right');
    if (!headerRight || document.getElementById('notif-bell')) return;

    const bell = document.createElement('div');
    bell.id = 'notif-bell';
    bell.className = 'notif-bell';
    bell.innerHTML = `
      <button class="notif-btn" id="notif-btn" aria-label="${App.t('notif_title')}">
        <svg width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
          <path d="M18 8A6 6 0 006 8c0 7-3 9-3 9h18s-3-2-3-9"/>
          <path d="M13.73 21a2 2 0 01-3.46 0"/>
        </svg>
        <span class="notif-badge" id="notif-badge" style="display:none">0</span>
      </button>
      <div class="notif-dropdown" id="notif-dropdown">
        <div class="notif-header">
          <span class="notif-title">${App.t('notif_title')}</span>
          <button class="notif-mark-all" id="notif-mark-all">${App.t('notif_mark_all')}</button>
        </div>
        <div class="notif-list" id="notif-list">
          <div class="notif-empty">${App.t('notif_empty')}</div>
        </div>
      </div>
    `;

    const avatar = headerRight.querySelector('.avatar');
    if (avatar) headerRight.insertBefore(bell, avatar);
    else headerRight.appendChild(bell);

    document.getElementById('notif-btn').addEventListener('click', (event) => {
      event.stopPropagation();
      const dropdown = document.getElementById('notif-dropdown');
      const wasOpen = dropdown.classList.contains('open');
      dropdown.classList.toggle('open');
      if (!wasOpen) void loadNotifications();
    });

    document.addEventListener('click', () => {
      document.getElementById('notif-dropdown')?.classList.remove('open');
    });
    document.getElementById('notif-dropdown').addEventListener('click', (event) => event.stopPropagation());

    document.getElementById('notif-list').addEventListener('click', (event) => {
      const item = event.target.closest('.notif-item[data-id]');
      if (!item) return;
      const notification = notificationsById.get(item.dataset.id);
      if (notification) void handleNotificationClick(notification, item);
    });

    document.getElementById('notif-mark-all').addEventListener('click', async () => {
      try {
        const data = await ApiClient.post('/api/notifications/read', {});
        updateBadge(data.unread);
        renderNotifications([]);
      } catch {}
    });
  }

  async function loadNotifications() {
    try {
      const data = await ApiClient.get('/api/notifications');
      updateBadge(data.unread);
      renderNotifications(data.notifications || []);
    } catch {}
  }

  async function pollUnread() {
    try {
      const data = await ApiClient.get('/api/notifications?unread=1');
      updateBadge(data.unread);
      if (data.unread > currentUnread && currentUnread >= 0) {
        document.getElementById('notif-btn')?.classList.add('notif-pulse');
        setTimeout(() => document.getElementById('notif-btn')?.classList.remove('notif-pulse'), 1200);
      }
      currentUnread = data.unread;
    } catch {}
  }

  function appendText(parent, className, value) {
    const element = document.createElement('div');
    element.className = className;
    element.textContent = value ?? '';
    parent.appendChild(element);
    return element;
  }

  function renderNotifications(notifications) {
    const list = document.getElementById('notif-list');
    if (!list) return;

    latestNotifications = Array.isArray(notifications) ? notifications : [];
    notificationsById.clear();
    list.replaceChildren();

    if (!latestNotifications.length) {
      appendText(list, 'notif-empty', App.t('notif_empty'));
      return;
    }

    for (const notification of latestNotifications.slice(0, 20)) {
      const id = String(notification.id);
      notificationsById.set(id, notification);

      const item = document.createElement('div');
      item.className = `notif-item${notification.is_read ? '' : ' unread'}`;
      item.dataset.id = id;

      const type = Object.hasOwn(TYPE_ICONS, notification.type) ? notification.type : 'info';
      const icon = document.createElement('div');
      icon.className = `notif-icon notif-icon-${type}`;
      icon.innerHTML = TYPE_ICONS[type];

      const body = document.createElement('div');
      body.className = 'notif-item-body';
      const bodyText = notificationText(notification, 'body');
      appendText(body, 'notif-item-title', notificationText(notification, 'title'));
      if (bodyText) appendText(body, 'notif-item-body-text', bodyText);
      appendText(body, 'notif-item-time', timeAgo(notification.created_at));

      item.append(icon, body);
      if (!notification.is_read) {
        const dot = document.createElement('div');
        dot.className = 'notif-dot';
        item.appendChild(dot);
      }
      list.appendChild(item);
    }
  }

  async function handleNotificationClick(notification, item) {
    try {
      await ApiClient.post('/api/notifications/read', { id: notification.id });
      item.classList.remove('unread');
      item.querySelector('.notif-dot')?.remove();
      currentUnread = Math.max(0, currentUnread - 1);
      updateBadge(currentUnread);
      if (notification.link) {
        setTimeout(() => { Ui.safeNavigate(notification.link); }, 150);
      }
    } catch {}
  }

  function updateBadge(count) {
    currentUnread = Number.isFinite(Number(count)) ? Number(count) : 0;
    const badge = document.getElementById('notif-badge');
    if (!badge) return;
    if (currentUnread > 0) {
      badge.textContent = currentUnread > 99 ? '99+' : String(currentUnread);
      badge.style.display = 'flex';
    } else {
      badge.style.display = 'none';
    }
  }

  function timeAgo(isoString) {
    if (!isoString) return '';
    const timestamp = /(?:Z|[+-]\d\d:\d\d)$/.test(isoString) ? isoString : `${isoString}Z`;
    const diff = (Date.now() - new Date(timestamp).getTime()) / 1000;
    if (!Number.isFinite(diff) || diff < 60) return App.t('notif_just_now');
    if (diff < 3600) return App.pluralize(Math.floor(diff / 60), 'unit_minute_short');
    if (diff < 86400) return App.pluralize(Math.floor(diff / 3600), 'unit_hour_short');
    return App.pluralize(Math.floor(diff / 86400), 'unit_day_short');
  }

  /**
   * One notification sentence, in the reader's language.
   *
   * The row carries both halves: the Polish text as written at the moment the
   * event happened, and a dictionary key with its values. The key wins when
   * the dictionary knows it. Anything written before notifications became
   * translatable -- and anything whose key the dictionary has since dropped --
   * still reads as the stored sentence rather than as a raw key.
   */
  function notificationText(notification, field) {
    const key = notification[`${field}_key`];
    const stored = notification[field] || '';
    if (!key || App.t(key) === key) return stored;
    const params = notification.params;
    if (!params || typeof params !== 'object') return App.t(key);
    // The bonus name is the one param that is itself a producer-written
    // label with its own English snapshot column (bonus/bonus_en) -- read
    // it the same way every other producer label is read. Every other
    // param (a customer's name, a lead id, an amount) substitutes literally;
    // App.t(key, params) has no way to know which of ITS params are data and
    // which are somebody's own words, so that choice is made here.
    const substituted = key === 'ntf_bonus_granted_title'
      ? { ...params, bonus: Ui.producerLabel(params, 'bonus') }
      : { ...params };
    // Amounts are stored pre-formatted the English way ("2,520"), which read
    // as "Prowizja: 2,520 PLN" in the Polish panel. Re-format them in the reader's language; older rows included.
    for (const name of ['amount', 'value']) {
      const raw = substituted[name];
      if (typeof raw === 'string' && /^-?[\d,]+(\.\d+)?$/.test(raw)) {
        const number = Number(raw.replace(/,/g, ''));
        if (Number.isFinite(number)) {
          substituted[name] = typeof formatAmount === 'function'
            ? formatAmount(number)
            : new Intl.NumberFormat(App.lang === 'en' ? 'en-GB' : 'pl-PL',
              { maximumFractionDigits: 0 }).format(number);
        }
      }
    }
    return App.t(key, substituted);
  }

  async function init() {
    stop();
    await Ui.rehydrateSession(ApiClient);
    injectBell();
    App.onRerender(() => {
      const button = document.getElementById('notif-btn');
      const title = document.querySelector('.notif-title');
      const markAll = document.getElementById('notif-mark-all');
      if (button) button.setAttribute('aria-label', App.t('notif_title'));
      if (title) title.textContent = App.t('notif_title');
      if (markAll) markAll.textContent = App.t('notif_mark_all');
      renderNotifications(latestNotifications);
    });
    await loadNotifications();
    await pollUnread();
    pollTimer = setInterval(pollUnread, POLL_INTERVAL);
  }

  function stop() {
    if (pollTimer !== null) clearInterval(pollTimer);
    pollTimer = null;
  }

  global.Notifications = { init, stop, loadNotifications, renderNotifications };

  document.addEventListener('DOMContentLoaded', () => {
    void init().catch(() => {
      // ApiClient owns the established 401 redirect/session-clear contract.
    });
  });
})(window);
