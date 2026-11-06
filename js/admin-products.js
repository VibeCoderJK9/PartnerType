/* ============================================================
   PARTNERTYPE — ADMIN PRODUCTS CONTROLLER
   The producer's catalogue. The "offered" switch is the one
   control that decides what a partner can pick on the lead
   form, so it acts immediately and reports failure rather
   than leaving the row showing a state the server rejected.
   Closure-owned record keys route every row action, so no
   database id is ever written into the DOM.
   ============================================================ */

(function initAdminProductsNamespace(global) {
  'use strict';

  // The STORED value, not display copy: a product's category is persisted as
  // this exact string and matched against the <select> options in
  // pages/admin/settings.html. Translating it made CATEGORIES.includes() miss
  // for every product filed in Polish, which silently saved an empty category.
  // Only the option LABEL is translated (data-i18n on the option), never this.
  const CATEGORIES = ['Wdrożenie', 'Licencja', 'Usługa', 'Wsparcie'];

  // The stored value is Polish and stays Polish. What the reader SEES is a
  // label: the <option> in settings.html is already translated this way, and
  // the table cell has to agree with it or an English screen shows the raw
  // database word next to a translated dropdown.
  const CATEGORY_KEYS = {
    'Wdrożenie': 'products_category_rollout',
    'Licencja': 'products_category_licence',
    'Usługa': 'products_category_service',
    'Wsparcie': 'products_category_support',
  };

  function categoryLabel(value) {
    const key = CATEGORY_KEYS[value];
    return key ? App.t(key) : (value || '—');
  }

  const productRecords = new Map();
  let productsState = [];
  let initialization = null;
  let actionsBound = false;
  let editingKey = null;
  let savingProduct = false;

  function textElement(tagName, className, value) {
    const element = document.createElement(tagName);
    if (className) element.className = className;
    element.textContent = String(value ?? '');
    return element;
  }

  function formatPrice(value) {
    const number = Number(value);
    if (!Number.isFinite(number)) return '—';
    // The fallback runs when app.js is absent (unit tests mount this module
    // alone), so it must not assume its helpers are there either.
    if (typeof global.formatCurrency === 'function') return global.formatCurrency(number);
    return typeof global.formatAmount === 'function'
      ? `${global.formatAmount(number)} PLN`
      : `${number} PLN`;
  }

  function recordKey(index) {
    return `product-${index}`;
  }

  // ─── STATES ───────────────────────────────────────────────
  function showOnly(which) {
    const map = {
      loading: 'products-loading-state',
      empty: 'products-empty-state',
      error: 'products-error-state',
      table: 'products-table-wrapper',
    };
    Object.entries(map).forEach(([name, id]) => {
      const el = document.getElementById(id);
      if (el) el.hidden = name !== which;
    });
  }

  // ─── RENDER ───────────────────────────────────────────────
  function renderKpis(products) {
    const total = products.length;
    const offered = products.filter((p) => Number(p.offered) === 1).length;
    const set = (id, value) => {
      const el = document.getElementById(id);
      if (el) el.textContent = String(value);
    };
    set('kpi-products-total', total);
    set('kpi-products-offered', offered);
    set('kpi-products-hidden', total - offered);
  }

  function offeredCell(product, key) {
    const cell = document.createElement('td');
    const wrap = document.createElement('div');
    wrap.style.display = 'flex';
    wrap.style.alignItems = 'center';
    wrap.style.gap = '10px';

    const toggle = document.createElement('button');
    toggle.type = 'button';
    toggle.className = 'btn btn-sm';
    toggle.dataset.productAction = 'offered';
    toggle.dataset.productKey = key;
    const on = Number(product.offered) === 1;
    toggle.textContent = App.t(on ? 'products_yes' : 'products_no');
    toggle.classList.add(on ? 'btn-success' : 'btn-secondary');
    toggle.setAttribute('aria-pressed', on ? 'true' : 'false');
    toggle.setAttribute(
      'aria-label',
      App.t(on ? 'products_hide_aria' : 'products_offer_aria', { name: product.name }),
    );
    wrap.appendChild(toggle);

    cell.appendChild(wrap);
    return cell;
  }

  function actionsCell(product, key) {
    const cell = document.createElement('td');
    const wrap = document.createElement('div');
    wrap.className = 'row-actions';
    wrap.style.opacity = '1';

    const edit = document.createElement('button');
    edit.type = 'button';
    edit.className = 'btn btn-ghost btn-sm';
    edit.dataset.productAction = 'edit';
    edit.dataset.productKey = key;
    edit.textContent = App.t('products_edit');
    edit.setAttribute('aria-label', App.t('products_edit_aria', { name: product.name }));

    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'btn btn-danger btn-sm';
    remove.dataset.productAction = 'archive';
    remove.dataset.productKey = key;
    remove.textContent = App.t('products_withdraw');
    remove.setAttribute('aria-label', App.t('products_withdraw_aria', { name: product.name }));

    wrap.append(edit, remove);
    cell.appendChild(wrap);
    return cell;
  }

  function buildRow(product, key) {
    const row = document.createElement('tr');
    row.dataset.recordKey = key;

    const nameCell = document.createElement('td');
    nameCell.appendChild(textElement('div', 'cell-primary', Ui.producerLabel(product, 'name')));
    if (Number(product.active) === 0) {
      const archived = textElement('div', 'text-xs text-tertiary', App.t('products_archived'));
      nameCell.appendChild(archived);
    }
    row.appendChild(nameCell);

    row.appendChild(textElement('td', 'cell-mono', product.code || '—'));

    const categoryCell = document.createElement('td');
    categoryCell.appendChild(textElement('span', 'badge badge-neutral', categoryLabel(product.category)));
    row.appendChild(categoryCell);

    row.appendChild(textElement('td', 'cell-primary', formatPrice(product.base_price)));
    row.appendChild(offeredCell(product, key));
    row.appendChild(actionsCell(product, key));
    return row;
  }

  function visibleProducts() {
    const searchEl = document.getElementById('products-search');
    const offeredEl = document.getElementById('products-filter-offered');
    const term = String(searchEl?.value || '').trim().toLowerCase();
    const offeredFilter = String(offeredEl?.value || '');

    return productsState.filter((product) => {
      const haystack = `${product.name || ''} ${product.code || ''} ${product.category || ''} ${categoryLabel(product.category)}`.toLowerCase();
      const matchesTerm = !term || haystack.includes(term);
      const matchesOffered = !offeredFilter || String(Number(product.offered)) === offeredFilter;
      return matchesTerm && matchesOffered;
    });
  }

  function renderProducts() {
    const tbody = document.getElementById('products-tbody');
    const label = document.getElementById('products-count-label');
    if (!tbody) return;

    productRecords.clear();
    renderKpis(productsState);

    if (!productsState.length) {
      tbody.replaceChildren();
      if (label) label.textContent = App.t('products_none');
      showOnly('empty');
      return;
    }

    const rows = visibleProducts();
    if (label) {
      label.textContent = rows.length === productsState.length
        ? App.pluralize(productsState.length, 'unit_product')
        : App.t('products_shown_of', { shown: rows.length, total: productsState.length });
    }

    tbody.replaceChildren(...rows.map((product, index) => {
      const key = recordKey(index);
      productRecords.set(key, product);
      return buildRow(product, key);
    }));
    showOnly('table');
  }

  // ─── LOAD ─────────────────────────────────────────────────
  async function loadProducts() {
    showOnly('loading');
    try {
      const data = await ApiClient.get('/api/products');
      productsState = (data && data.products) || [];
      renderProducts();
    } catch (err) {
      showOnly('error');
      handleApiError(err, 'loadProducts');
    }
  }

  App.onRerender(() => renderProducts());

  // ─── MODAL ────────────────────────────────────────────────
  function setModalError(message) {
    const el = document.getElementById('product-modal-error');
    if (!el) return;
    el.textContent = message || '';
    el.hidden = !message;
  }

  function fillModal(product) {
    const set = (id, value) => {
      const el = document.getElementById(id);
      if (el) el.value = value ?? '';
    };
    set('p-name', product ? product.name : '');
    set('p-name-en', product ? product.name_en : '');
    set('p-code', product ? product.code : '');
    set('p-category', product && CATEGORIES.includes(product.category) ? product.category : CATEGORIES[0]);
    set('p-price', product && product.base_price !== null && product.base_price !== undefined ? product.base_price : '');
    const offered = document.getElementById('p-offered');
    if (offered) offered.checked = product ? Number(product.offered) === 1 : true;
    setModalError('');
  }

  function openCreate() {
    editingKey = null;
    const title = document.getElementById('product-modal-title');
    if (title) { title.dataset.i18n = 'products_new'; title.textContent = App.t('products_new'); }
    fillModal(null);
    openModal('product-modal');
  }

  function openEdit(key) {
    const product = productRecords.get(key);
    if (!product) return;
    editingKey = key;
    const title = document.getElementById('product-modal-title');
    if (title) { title.dataset.i18n = 'products_edit_title'; title.textContent = App.t('products_edit_title'); }
    fillModal(product);
    openModal('product-modal');
  }

  function readModal() {
    const value = (id) => String(document.getElementById(id)?.value ?? '').trim();
    const price = value('p-price');
    const payload = {
      name: value('p-name'),
      name_en: value('p-name-en'),
      code: value('p-code'),
      category: value('p-category'),
      offered: document.getElementById('p-offered')?.checked ? 1 : 0,
    };
    // An empty price means "no hint", not zero -- send null so the server
    // stores the absence rather than a misleading 0 PLN on the partner form.
    payload.base_price = price === '' ? null : Number(price);
    return payload;
  }

  async function saveProduct() {
    if (savingProduct) return;
    const payload = readModal();
    if (!payload.name) {
      setModalError(App.t('products_name_required'));
      return;
    }
    if (payload.base_price !== null && !Number.isFinite(payload.base_price)) {
      setModalError(App.t('products_price_numeric'));
      return;
    }

    const button = document.getElementById('product-save-btn');
    savingProduct = true;
    if (button) button.disabled = true;
    try {
      if (editingKey) {
        const product = productRecords.get(editingKey);
        if (!product) return;
        await ApiClient.patch(`/api/products/${product.id}`, payload);
      } else {
        await ApiClient.post('/api/products', payload);
      }
      closeModal('product-modal');
      await loadProducts();
      showToast('success', editingKey ? App.t('products_updated') : App.t('products_added'), payload.name);
    } catch (err) {
      // Keep the modal open with the entered values: the server reason
      // (duplicate name, bad category) is the only way to fix the input.
      setModalError((err && err.message) || App.t('products_save_failed'));
    } finally {
      savingProduct = false;
      if (button) button.disabled = false;
    }
  }

  // ─── ROW ACTIONS ──────────────────────────────────────────
  async function toggleOffered(key, button) {
    const product = productRecords.get(key);
    if (!product) return;
    const next = Number(product.offered) === 1 ? 0 : 1;
    if (button) button.disabled = true;
    try {
      await ApiClient.patch(`/api/products/${product.id}`, { offered: next });
      await loadProducts();
    } catch (err) {
      // The row still shows the old state, which is the truth on the server.
      handleApiError(err, 'toggleOffered');
    } finally {
      if (button) button.disabled = false;
    }
  }

  async function archiveProduct(key) {
    const product = productRecords.get(key);
    if (!product) return;
    try {
      await ApiClient.delete(`/api/products/${product.id}`);
      await loadProducts();
      showToast('success', App.t('products_withdrawn'), App.t('products_withdrawn_detail', { name: product.name }));
    } catch (err) {
      handleApiError(err, 'archiveProduct');
    }
  }

  function bindActions() {
    if (actionsBound) return;
    actionsBound = true;

    document.getElementById('products-tbody')?.addEventListener('click', (event) => {
      const button = event.target.closest('[data-product-action][data-product-key]');
      if (!button || !productRecords.has(button.dataset.productKey)) return;
      const action = button.dataset.productAction;
      if (action === 'offered') void toggleOffered(button.dataset.productKey, button);
      if (action === 'edit') openEdit(button.dataset.productKey);
      if (action === 'archive') void archiveProduct(button.dataset.productKey);
    });

    document.getElementById('add-product-btn')?.addEventListener('click', openCreate);
    document.getElementById('product-save-btn')?.addEventListener('click', () => void saveProduct());
    document.getElementById('product-cancel-btn')?.addEventListener('click', () => closeModal('product-modal'));
    document.getElementById('product-modal-close')?.addEventListener('click', () => closeModal('product-modal'));
    document.getElementById('products-retry-btn')?.addEventListener('click', () => void loadProducts());
    document.getElementById('products-search')?.addEventListener('input', renderProducts);
    document.getElementById('products-filter-offered')?.addEventListener('change', renderProducts);
  }

  async function initialize() {
    bindActions();
    await loadProducts();
  }

  /**
   * Load and render the product catalogue once AdminShell has authorized.
   * @param {object} user authoritative public admin identity from AdminShell
   * @returns {Promise<void>}
   */
  function init(user) {
    void user;
    if (!initialization) initialization = initialize();
    return initialization;
  }

  global.AdminProducts = Object.freeze({ init });
})(window);
