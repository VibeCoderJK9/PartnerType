/* ============================================================
   PARTNERTYPE — DATE PICKER
   ============================================================
   Replaces the browser's native date field with a calendar styled like the
   rest of the app. The macOS segmented "dd.mm.rrrr" field is fiddly to type
   into and looks like a system control rather than part of the product.

   Same progressive enhancement as js/dropdown.js: the real
   <input type="date"> stays in the DOM, keeps its id/name/value and still
   fires 'input' and 'change', so existing filter code is untouched.

   Opt out with data-no-datepicker on the input.
   ============================================================ */

(function (global) {
  'use strict';

  // Month and weekday names come from Intl, not from a hardcoded Polish list:
  // a translated list would have to be maintained twice and would still be
  // wrong for any third language. `locale()` is read per render so the picker
  // follows a language switch.
  function locale() {
    return (typeof App !== 'undefined' && App.lang === 'en') ? 'en-GB' : 'pl-PL';
  }

  function monthName(month, year) {
    var formatted = new Intl.DateTimeFormat(locale(), { month: 'long' })
      .format(new Date(year, month, 1));
    return formatted.charAt(0).toUpperCase() + formatted.slice(1);
  }

  function weekdayNames() {
    // 2024-01-01 was a Monday, which is where this calendar's week starts.
    var format = new Intl.DateTimeFormat(locale(), { weekday: 'short' });
    var names = [];
    for (var i = 0; i < 7; i += 1) {
      var name = format.format(new Date(2024, 0, 1 + i));
      names.push(name.charAt(0).toUpperCase() + name.slice(1).replace(/\.$/, ''));
    }
    return names;
  }

  var instances = new WeakMap();
  var openInstances = [];

  function pad(value) { return String(value).padStart(2, '0'); }
  function toIso(date) { return date.getFullYear() + '-' + pad(date.getMonth() + 1) + '-' + pad(date.getDate()); }

  function parseIso(value) {
    var match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(value || ''));
    if (!match) return null;
    var date = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
    return isNaN(date.getTime()) ? null : date;
  }

  function display(date) {
    return date ? pad(date.getDate()) + '.' + pad(date.getMonth() + 1) + '.' + date.getFullYear() : '';
  }

  /** Monday-first offset for the 1st of the shown month. */
  function leadingBlanks(year, month) {
    var weekday = new Date(year, month, 1).getDay();
    return (weekday + 6) % 7;
  }

  function build(input) {
    if (instances.has(input) || input.hasAttribute('data-no-datepicker')) return null;

    var wrapper = document.createElement('div');
    wrapper.className = 'datepicker';

    var field = document.createElement('button');
    field.type = 'button';
    field.className = 'datepicker-field';
    field.setAttribute('aria-haspopup', 'dialog');
    field.setAttribute('aria-expanded', 'false');
    if (input.getAttribute('aria-label')) field.setAttribute('aria-label', input.getAttribute('aria-label'));

    var label = document.createElement('span');
    label.className = 'datepicker-label';
    field.appendChild(label);

    var glyph = document.createElement('span');
    glyph.className = 'datepicker-glyph';
    if (global.Icons) {
      var icon = global.Icons.el('calendar', { size: 16 });
      if (icon) glyph.appendChild(icon);
    }
    field.appendChild(glyph);

    var popover = document.createElement('div');
    popover.className = 'datepicker-popover';
    popover.setAttribute('role', 'dialog');
    popover.hidden = true;

    input.parentNode.insertBefore(wrapper, input);
    wrapper.appendChild(field);
    wrapper.appendChild(popover);
    wrapper.appendChild(input);
    input.classList.add('datepicker-native');

    var selected = parseIso(input.value);
    var instance = {
      input: input, wrapper: wrapper, field: field, label: label, popover: popover,
      view: selected ? new Date(selected.getFullYear(), selected.getMonth(), 1) : new Date(),
    };
    instances.set(input, instance);
    openInstances.push(instance);

    bind(instance);
    renderField(instance);
    return instance;
  }

  function renderField(instance) {
    var date = parseIso(instance.input.value);
    instance.label.textContent = date ? display(date) : (locale() === 'en-GB' ? 'dd.mm.yyyy' : 'dd.mm.rrrr');
    instance.label.classList.toggle('is-empty', !date);
    instance.field.disabled = instance.input.disabled;
  }

  function renderCalendar(instance) {
    var view = instance.view;
    var year = view.getFullYear();
    var month = view.getMonth();
    var selected = parseIso(instance.input.value);
    var today = new Date();
    var todayIso = toIso(today);

    var head = document.createElement('div');
    head.className = 'datepicker-head';

    var prev = navButton('chevron-left', App.t('datepicker_prev_month'));
    prev.addEventListener('click', function () {
      instance.view = new Date(year, month - 1, 1);
      renderCalendar(instance);
    });

    var title = document.createElement('div');
    title.className = 'datepicker-title';
    title.textContent = monthName(month, year) + ' ' + year;

    var next = navButton('chevron-right', App.t('datepicker_next_month'));
    next.addEventListener('click', function () {
      instance.view = new Date(year, month + 1, 1);
      renderCalendar(instance);
    });

    head.append(prev, title, next);

    var grid = document.createElement('div');
    grid.className = 'datepicker-grid';
    weekdayNames().forEach(function (name) {
      var cell = document.createElement('div');
      cell.className = 'datepicker-weekday';
      cell.textContent = name;
      grid.appendChild(cell);
    });

    var blanks = leadingBlanks(year, month);
    for (var i = 0; i < blanks; i++) grid.appendChild(document.createElement('div'));

    var daysInMonth = new Date(year, month + 1, 0).getDate();
    for (var day = 1; day <= daysInMonth; day++) {
      var date = new Date(year, month, day);
      var iso = toIso(date);
      var cell = document.createElement('button');
      cell.type = 'button';
      cell.className = 'datepicker-day';
      cell.textContent = String(day);
      cell.dataset.iso = iso;
      if (iso === todayIso) cell.classList.add('is-today');
      if (selected && iso === toIso(selected)) {
        cell.classList.add('is-selected');
        cell.setAttribute('aria-current', 'date');
      }
      grid.appendChild(cell);
    }

    var footer = document.createElement('div');
    footer.className = 'datepicker-footer';
    var todayBtn = textButton(App.t('datepicker_today'));
    todayBtn.addEventListener('click', function () { commit(instance, toIso(new Date())); });
    var clearBtn = textButton(App.t('datepicker_clear'));
    clearBtn.addEventListener('click', function () { commit(instance, ''); });
    footer.append(todayBtn, clearBtn);

    instance.popover.replaceChildren(head, grid, footer);
  }

  function navButton(iconName, label) {
    var button = document.createElement('button');
    button.type = 'button';
    button.className = 'datepicker-nav';
    button.title = label;
    button.setAttribute('aria-label', label);
    if (global.Icons) {
      var icon = global.Icons.el(iconName, { size: 16 });
      if (icon) button.appendChild(icon);
    }
    return button;
  }

  function textButton(text) {
    var button = document.createElement('button');
    button.type = 'button';
    button.className = 'datepicker-action';
    button.textContent = text;
    return button;
  }

  function commit(instance, iso) {
    if (instance.input.value !== iso) {
      instance.input.value = iso;
      instance.input.dispatchEvent(new Event('input', { bubbles: true }));
      instance.input.dispatchEvent(new Event('change', { bubbles: true }));
    }
    renderField(instance);
    close(instance, true);
  }

  /**
   * Panels clip their contents (`overflow: hidden` for rounded corners), so the
   * calendar lives on <body> with fixed coordinates while open, flipping above
   * the field when there is no room below.
   */
  function place(instance) {
    var rect = instance.field.getBoundingClientRect();
    var popover = instance.popover;
    popover.style.left = Math.min(rect.left, window.innerWidth - popover.offsetWidth - 8) + 'px';
    var below = window.innerHeight - rect.bottom - 14;
    var height = popover.offsetHeight;
    // The calendar is a fixed size, so it does flip when it would not fit.
    popover.style.top = (below < height && rect.top - 14 > below)
      ? Math.max(8, rect.top - height - 6) + 'px'
      : (rect.bottom + 6) + 'px';
  }

  function open(instance) {
    closeAll(instance);
    var selected = parseIso(instance.input.value);
    if (selected) instance.view = new Date(selected.getFullYear(), selected.getMonth(), 1);
    renderCalendar(instance);
    document.body.appendChild(instance.popover);
    instance.popover.classList.add('is-floating');
    instance.popover.hidden = false;
    instance.wrapper.classList.add('is-open');
    instance.field.setAttribute('aria-expanded', 'true');
    place(instance);
    instance.reposition = function () { place(instance); };
    window.addEventListener('scroll', instance.reposition, true);
    window.addEventListener('resize', instance.reposition);
  }

  function close(instance, refocus) {
    if (instance.popover.hidden) return;
    instance.popover.hidden = true;
    instance.popover.classList.remove('is-floating');
    instance.popover.removeAttribute('style');
    instance.wrapper.appendChild(instance.popover);
    if (instance.reposition) {
      window.removeEventListener('scroll', instance.reposition, true);
      window.removeEventListener('resize', instance.reposition);
      instance.reposition = null;
    }
    instance.wrapper.classList.remove('is-open');
    instance.field.setAttribute('aria-expanded', 'false');
    if (refocus) instance.field.focus();
  }

  function closeAll(except) {
    openInstances.forEach(function (instance) {
      if (instance !== except) close(instance, false);
    });
  }

  function bind(instance) {
    instance.field.addEventListener('click', function (event) {
      event.preventDefault();
      event.stopPropagation();
      if (instance.popover.hidden) open(instance); else close(instance, true);
    });

    instance.popover.addEventListener('click', function (event) {
      var day = event.target.closest('.datepicker-day');
      if (!day) return;
      event.preventDefault();
      commit(instance, day.dataset.iso);
    });

    instance.popover.addEventListener('keydown', function (event) {
      if (event.key === 'Escape') { event.preventDefault(); close(instance, true); }
    });

    // Something else cleared or set the filter (e.g. "Clear filters").
    instance.input.addEventListener('change', function () { renderField(instance); });
  }

  document.addEventListener('click', function (event) {
    if (!event.target.closest('.datepicker')) closeAll(null);
  });

  function upgradeAll(root) {
    var scope = root || document;
    Array.prototype.forEach.call(scope.querySelectorAll('input[type="date"]'), build);
  }

  document.addEventListener('DOMContentLoaded', function () {
    upgradeAll(document);
    new MutationObserver(function (records) {
      records.forEach(function (record) {
        Array.prototype.forEach.call(record.addedNodes, function (node) {
          if (node.nodeType !== 1) return;
          if (node.tagName === 'INPUT' && node.type === 'date') build(node);
          else upgradeAll(node);
        });
      });
    }).observe(document.body, { childList: true, subtree: true });
  });

  global.DatePicker = { upgradeAll: upgradeAll, build: build };
  if (typeof App !== 'undefined' && App.onRerender) App.onRerender(function () {
    openInstances = openInstances.filter(function (instance) { return instance.input.isConnected; });
    openInstances.forEach(function (instance) {
      renderField(instance);
      if (!instance.popover.hidden) renderCalendar(instance);
    });
  });
})(typeof window !== 'undefined' ? window : globalThis);
