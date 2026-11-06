/* ============================================================
   PARTNERTYPE — DROPDOWN
   ============================================================
   Replaces the operating system's native <select> popup with a list
   styled like the rest of the app. macOS renders the native popup as a
   floating system menu that looks nothing like the product, which is what
   this exists to avoid.

   Progressive enhancement: the real <select> stays in the DOM, keeps its
   name/value and still fires 'change', so every existing controller and
   form keeps working untouched. Only its rendering is replaced.

   Selects added later (modals, JS-built rows) are upgraded automatically by
   a MutationObserver, and a select whose <option>s are replaced at runtime
   rebuilds its menu on its own.

   Opt out with data-no-dropdown on the select.
   ============================================================ */

(function (global) {
  'use strict';

  var OPEN_CLASS = 'is-open';
  var instances = new WeakMap();

  function optionsOf(select) {
    return Array.prototype.slice.call(select.options);
  }

  function build(select) {
    if (instances.has(select) || select.hasAttribute('data-no-dropdown')) return null;
    if (select.multiple || select.size > 1) return null;

    var wrapper = document.createElement('div');
    wrapper.className = 'ph-select';
    // Carry sizing/spacing hooks over so page-level rules keep applying.
    if (select.className) wrapper.dataset.selectClass = select.className;
    if (select.style.width) wrapper.style.width = select.style.width;

    var toggle = document.createElement('button');
    toggle.type = 'button';
    toggle.className = 'ph-select-toggle';
    toggle.setAttribute('aria-haspopup', 'listbox');
    toggle.setAttribute('aria-expanded', 'false');
    if (select.getAttribute('aria-label')) toggle.setAttribute('aria-label', select.getAttribute('aria-label'));
    if (select.id) toggle.id = select.id + '-toggle';

    var label = document.createElement('span');
    label.className = 'ph-select-label';
    toggle.appendChild(label);

    var caret = document.createElement('span');
    caret.className = 'ph-select-caret';
    if (global.Icons) {
      var icon = global.Icons.el('chevron-down', { size: 16 });
      if (icon) caret.appendChild(icon);
    }
    toggle.appendChild(caret);

    var menu = document.createElement('div');
    menu.className = 'ph-select-menu';
    menu.setAttribute('role', 'listbox');
    menu.hidden = true;

    select.parentNode.insertBefore(wrapper, select);
    wrapper.appendChild(toggle);
    wrapper.appendChild(menu);
    wrapper.appendChild(select);
    select.classList.add('ph-select-native');
    if (select.disabled) wrapper.classList.add('is-disabled');

    var instance = { select: select, wrapper: wrapper, toggle: toggle, menu: menu, label: label };
    instances.set(select, instance);

    bind(instance);
    render(instance);

    // Controllers replace a select's <option>s at runtime (tier pickers,
    // product lists); rebuild the menu when that happens.
    var observer = new MutationObserver(function () { render(instance); });
    observer.observe(select, { childList: true, subtree: true, attributes: true, attributeFilter: ['disabled'] });
    instance.observer = observer;

    return instance;
  }

  function render(instance) {
    var select = instance.select;
    var current = optionsOf(select).filter(function (option) { return option.selected; })[0];
    instance.label.textContent = current ? current.textContent.trim() : '';
    instance.label.classList.toggle('is-placeholder', !!current && current.value === '');
    instance.wrapper.classList.toggle('is-disabled', select.disabled);
    instance.toggle.disabled = select.disabled;

    var items = optionsOf(select).map(function (option, index) {
      var item = document.createElement('button');
      item.type = 'button';
      item.className = 'ph-select-item';
      item.setAttribute('role', 'option');
      item.dataset.index = String(index);
      item.textContent = option.textContent.trim();
      if (option.disabled) item.disabled = true;
      if (option.selected) {
        item.classList.add('is-selected');
        item.setAttribute('aria-selected', 'true');
      } else {
        item.setAttribute('aria-selected', 'false');
      }
      return item;
    });
    instance.menu.replaceChildren.apply(instance.menu, items);
  }

  /**
   * Panels use `overflow: hidden` for their rounded corners, which clips an
   * absolutely positioned menu inside them. Park the menu on <body> with fixed
   * coordinates while it is open so it can never be cut off, and flip it above
   * the field when there is no room below.
   */
  function place(instance) {
    var rect = instance.toggle.getBoundingClientRect();
    var menu = instance.menu;
    menu.style.width = rect.width + 'px';
    menu.style.left = rect.left + 'px';
    menu.style.right = 'auto';

    var below = window.innerHeight - rect.bottom - 14;
    var above = rect.top - 14;
    var height = menu.scrollHeight;
    // Prefer opening downwards and just scrolling a long list; only flip above
    // when there is genuinely no usable room below, so the menu does not cover
    // the fields the user just filled in.
    if (below < 160 && above > below) {
      menu.style.top = Math.max(8, rect.top - Math.min(height, above) - 6) + 'px';
      menu.style.maxHeight = above + 'px';
    } else {
      menu.style.top = (rect.bottom + 6) + 'px';
      menu.style.maxHeight = Math.max(120, below) + 'px';
    }
  }

  function open(instance) {
    closeAll(instance);
    document.body.appendChild(instance.menu);
    instance.menu.classList.add('is-floating');
    instance.menu.hidden = false;
    instance.wrapper.classList.add(OPEN_CLASS);
    instance.toggle.setAttribute('aria-expanded', 'true');
    place(instance);
    instance.reposition = function () { place(instance); };
    window.addEventListener('scroll', instance.reposition, true);
    window.addEventListener('resize', instance.reposition);
    var selected = instance.menu.querySelector('.is-selected') || instance.menu.firstElementChild;
    if (selected) selected.focus();
  }

  function close(instance, refocus) {
    if (instance.menu.hidden) return;
    instance.menu.hidden = true;
    instance.menu.classList.remove('is-floating');
    instance.menu.removeAttribute('style');
    instance.wrapper.appendChild(instance.menu);
    if (instance.reposition) {
      window.removeEventListener('scroll', instance.reposition, true);
      window.removeEventListener('resize', instance.reposition);
      instance.reposition = null;
    }
    instance.wrapper.classList.remove(OPEN_CLASS);
    instance.toggle.setAttribute('aria-expanded', 'false');
    if (refocus) instance.toggle.focus();
  }

  var openInstances = [];

  function closeAll(except) {
    openInstances.forEach(function (instance) {
      if (instance !== except) close(instance, false);
    });
  }

  function choose(instance, index) {
    var option = instance.select.options[index];
    if (!option || option.disabled) return;
    if (instance.select.selectedIndex !== index) {
      instance.select.selectedIndex = index;
      // Controllers listen for 'change'; 'input' keeps parity with a real select.
      instance.select.dispatchEvent(new Event('input', { bubbles: true }));
      instance.select.dispatchEvent(new Event('change', { bubbles: true }));
    }
    render(instance);
    close(instance, true);
  }

  function moveFocus(instance, delta) {
    var items = Array.prototype.slice.call(instance.menu.querySelectorAll('.ph-select-item:not([disabled])'));
    if (!items.length) return;
    var index = items.indexOf(document.activeElement);
    var next = index === -1 ? 0 : (index + delta + items.length) % items.length;
    items[next].focus();
  }

  function bind(instance) {
    openInstances.push(instance);

    instance.toggle.addEventListener('click', function (event) {
      event.preventDefault();
      event.stopPropagation();
      if (instance.menu.hidden) open(instance); else close(instance, true);
    });

    instance.toggle.addEventListener('keydown', function (event) {
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault();
        open(instance);
      }
    });

    instance.menu.addEventListener('click', function (event) {
      var item = event.target.closest('.ph-select-item');
      if (!item) return;
      event.preventDefault();
      choose(instance, Number(item.dataset.index));
    });

    instance.menu.addEventListener('keydown', function (event) {
      if (event.key === 'Escape') { event.preventDefault(); close(instance, true); }
      else if (event.key === 'ArrowDown') { event.preventDefault(); moveFocus(instance, 1); }
      else if (event.key === 'ArrowUp') { event.preventDefault(); moveFocus(instance, -1); }
      else if (event.key === 'Tab') close(instance, false);
    });

    // Something else changed the value (a controller assigning select.value).
    instance.select.addEventListener('change', function () { render(instance); });
  }

  document.addEventListener('click', function (event) {
    if (!event.target.closest('.ph-select')) closeAll(null);
  });

  function upgradeAll(root) {
    var scope = root || document;
    Array.prototype.forEach.call(scope.querySelectorAll('select'), build);
  }

  function refresh(select) {
    var instance = instances.get(select);
    if (instance) render(instance);
  }

  document.addEventListener('DOMContentLoaded', function () {
    upgradeAll(document);
    // Modals and JS-built rows insert selects after load.
    new MutationObserver(function (records) {
      records.forEach(function (record) {
        Array.prototype.forEach.call(record.addedNodes, function (node) {
          if (node.nodeType !== 1) return;
          if (node.tagName === 'SELECT') build(node);
          else upgradeAll(node);
        });
      });
    }).observe(document.body, { childList: true, subtree: true });
  });

  global.Dropdown = { upgradeAll: upgradeAll, refresh: refresh, build: build };
})(typeof window !== 'undefined' ? window : globalThis);
