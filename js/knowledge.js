/* ============================================================
   PARTNERTYPE — KNOWLEDGE BASE
   One script for both screens. The producer's staff add, rename and remove
   documents (pages/admin/knowledge.html); partners and observers only list,
   search and download them (pages/partner/knowledge.html and the observer's
   read-only admin page). The server decides who may do what
   (api/knowledge.py); the buttons only follow its answer.
   Everything typed by people goes in through textContent.
   ============================================================ */

(function initKnowledgeBase(global) {
  'use strict';

  const KIND_LABELS = { pdf: 'PDF', docx: 'DOCX', xlsx: 'XLSX', pptx: 'PPTX', png: 'PNG', jpeg: 'JPG' };
  const EDITOR_ROLES = ['admin', 'manager'];
  const DEFAULT_MAX_FILE_BYTES = 10 * 1024 * 1024;
  const MAX_TITLE = 200;
  // A 10 MB file on a slow connection takes longer than an ordinary request.
  const UPLOAD_TIMEOUT_MS = 180000;

  const state = {
    documents: [],
    usage: null,
    canEdit: false,
    query: '',
    editingId: null,
    deletingId: null,
    busy: false,
    uploading: false,
  };

  function t(key, params) { return App.t(key, params); }
  const $ = (id) => document.getElementById(id);

  function formatSize(bytes) {
    const locale = activeLocale();
    if (bytes < 1024 * 1024) {
      return `${Math.max(1, Math.round(bytes / 1024)).toLocaleString(locale)} KB`;
    }
    const mb = bytes / (1024 * 1024);
    return `${mb.toLocaleString(locale, { maximumFractionDigits: mb < 10 ? 1 : 0 })} MB`;
  }

  function matches(doc, query) {
    if (!query) return true;
    const text = `${doc.title} ${doc.description} ${doc.filename}`.toLocaleLowerCase();
    return text.includes(query);
  }

  // ─── RENDER ─────────────────────────────────────────────

  function renderUsage() {
    const box = $('kb-usage');
    if (!box) return;
    if (!state.canEdit || !state.usage) { box.hidden = true; return; }
    box.hidden = false;
    const { used_bytes: used, quota_bytes: quota } = state.usage;
    const fill = $('kb-usage-fill');
    if (quota === null || quota === undefined) {
      $('kb-usage-text').textContent = t('kb_usage_invalid');
      fill.style.width = '100%';
      box.classList.add('kb-usage-full');
      return;
    }
    const share = quota > 0 ? Math.min(100, (used / quota) * 100) : 100;
    $('kb-usage-text').textContent = t('kb_usage', { used: formatSize(used), quota: formatSize(quota) });
    fill.style.width = `${share.toFixed(1)}%`;
    box.classList.toggle('kb-usage-full', share >= 90);
  }

  function actionButton(label, icon, className, handler) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = `btn btn-ghost btn-sm ${className}`;
    Icons.label(button, icon, label, { size: 14 });
    button.addEventListener('click', handler);
    return button;
  }

  function renderCard(doc) {
    const card = document.createElement('article');
    card.className = 'kb-card';
    card.dataset.id = String(doc.id);

    const badge = document.createElement('div');
    badge.className = `kb-kind kb-kind-${doc.kind}`;
    badge.textContent = KIND_LABELS[doc.kind] || String(doc.kind || '').toUpperCase();

    const body = document.createElement('div');
    body.className = 'kb-card-body';
    const titleRow = document.createElement('div');
    titleRow.className = 'kb-card-title-row';
    const title = document.createElement('h3');
    title.className = 'kb-card-title';
    title.textContent = doc.title;
    titleRow.appendChild(title);
    if (doc.is_new) {
      const fresh = document.createElement('span');
      fresh.className = 'badge badge-brand kb-new';
      fresh.textContent = t('kb_new');
      titleRow.appendChild(fresh);
    }
    body.appendChild(titleRow);
    if (doc.description) {
      const description = document.createElement('p');
      description.className = 'kb-card-description';
      description.textContent = doc.description;
      body.appendChild(description);
    }
    const meta = document.createElement('div');
    meta.className = 'kb-card-meta text-sm text-tertiary';
    const parts = [doc.filename, formatSize(doc.size_bytes), t('kb_added_on', { date: formatDate(doc.created_at) })];
    if (state.canEdit && doc.uploaded_by_name) parts.push(t('kb_added_by', { name: doc.uploaded_by_name }));
    meta.textContent = parts.join(' · ');
    body.appendChild(meta);

    const actions = document.createElement('div');
    actions.className = 'kb-card-actions';
    const download = document.createElement('a');
    download.className = 'btn btn-secondary btn-sm kb-download';
    download.href = `/api/knowledge/${encodeURIComponent(doc.id)}/file`;
    download.setAttribute('download', doc.filename);
    Icons.label(download, 'download', t('kb_download'), { size: 14 });
    actions.appendChild(download);
    if (state.canEdit) {
      actions.appendChild(actionButton(t('kb_edit'), 'pencil', 'kb-edit', () => openEditor(doc)));
      actions.appendChild(actionButton(t('kb_delete'), 'trash-2', 'kb-delete', () => openDelete(doc)));
    }

    card.append(badge, body, actions);
    return card;
  }

  function renderEmpty(text) {
    const empty = document.createElement('div');
    empty.className = 'kb-empty';
    const icon = Icons.el('book-open', { size: 32 });
    if (icon) empty.appendChild(icon);
    const message = document.createElement('p');
    message.textContent = text;
    empty.appendChild(message);
    return empty;
  }

  function render() {
    const list = $('kb-list');
    if (!list) return;
    list.replaceChildren();
    renderUsage();
    if (!state.documents.length) {
      list.appendChild(renderEmpty(t(state.canEdit ? 'kb_empty_admin' : 'kb_empty_partner')));
      return;
    }
    const visible = state.documents.filter((doc) => matches(doc, state.query));
    if (!visible.length) {
      list.appendChild(renderEmpty(t('kb_no_results')));
      return;
    }
    visible.forEach((doc) => list.appendChild(renderCard(doc)));
  }

  function renderError() {
    const list = $('kb-list');
    if (!list) return;
    list.replaceChildren();
    const box = renderEmpty(t('kb_load_failed'));
    const retry = document.createElement('button');
    retry.type = 'button';
    retry.className = 'btn btn-secondary btn-sm';
    retry.textContent = t('kb_retry');
    retry.addEventListener('click', () => { void load(); });
    box.appendChild(retry);
    list.appendChild(box);
  }

  async function load() {
    try {
      const data = await ApiClient.get('/api/knowledge');
      state.documents = Array.isArray(data.documents) ? data.documents : [];
      state.usage = data.usage || null;
      render();
    } catch {
      renderError();
    }
  }

  // ─── ADD / EDIT ─────────────────────────────────────────

  function showModalError(message) {
    const box = $('kb-modal-error');
    box.textContent = message || '';
    box.hidden = !message;
  }

  function maxFileBytes() {
    return (state.usage && state.usage.max_file_bytes) || DEFAULT_MAX_FILE_BYTES;
  }

  function maxFileMegabytes() {
    return Math.round(maxFileBytes() / (1024 * 1024));
  }

  // The dialog's title and button follow what it is doing (adding, editing,
  // uploading), so they are drawn here and not by data-i18n: a language
  // switch repaints them instead of resetting them to "Add".
  function paintEditor() {
    const title = $('kb-modal-title');
    const button = $('kb-save-btn');
    if (!title || !button) return;
    const editing = state.editingId !== null;
    title.textContent = t(editing ? 'kb_edit_title' : 'kb_add');
    if (state.uploading) button.textContent = t('kb_uploading');
    else button.textContent = t(editing ? 'kb_save_changes' : 'kb_save');
  }

  function openEditor(doc) {
    state.editingId = doc ? doc.id : null;
    paintEditor();
    $('kb-title').value = doc ? doc.title : '';
    $('kb-description').value = doc ? doc.description : '';
    $('kb-file').value = '';
    $('kb-file-group').hidden = Boolean(doc);
    $('kb-file-hint').textContent = t('kb_file_hint', { mb: maxFileMegabytes() });
    showModalError('');
    openModal('kb-modal');
    $('kb-title').focus();
  }

  function readAsDataUrl(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.addEventListener('load', () => resolve(String(reader.result || '')));
      reader.addEventListener('error', () => reject(reader.error || new Error('read')));
      reader.readAsDataURL(file);
    });
  }

  // A file chosen without a title gets its name as one, minus the extension.
  function onFileChosen() {
    const file = $('kb-file').files[0];
    if (file && !$('kb-title').value.trim()) {
      $('kb-title').value = file.name.replace(/\.[^.]+$/, '').slice(0, MAX_TITLE);
    }
  }

  async function save() {
    if (state.busy) return;
    const title = $('kb-title').value.trim();
    const description = $('kb-description').value.trim();
    if (!title) { showModalError(t('kb_title_required')); return; }
    const file = state.editingId ? null : $('kb-file').files[0];
    if (!state.editingId) {
      if (!file) { showModalError(t('kb_file_required')); return; }
      if (file.size > maxFileBytes()) {
        showModalError(t('kb_file_too_large', { mb: maxFileMegabytes() }));
        return;
      }
    }
    const button = $('kb-save-btn');
    state.busy = true;
    button.disabled = true;
    try {
      if (state.editingId) {
        await ApiClient.patch(`/api/knowledge/${encodeURIComponent(state.editingId)}`, { title, description });
        showToast('success', t('kb_saved'));
      } else {
        state.uploading = true;
        paintEditor();
        const data = await readAsDataUrl(file);
        await ApiClient.post('/api/knowledge', { title, description, filename: file.name, data },
          { timeout: UPLOAD_TIMEOUT_MS });
        showToast('success', t('kb_uploaded'));
      }
      closeModal('kb-modal');
      await load();
    } catch (err) {
      showModalError(err && err.message ? err.message : t('kb_save_failed'));
    } finally {
      state.busy = false;
      state.uploading = false;
      button.disabled = false;
      paintEditor();
    }
  }

  // ─── DELETE ─────────────────────────────────────────────

  function openDelete(doc) {
    state.deletingId = doc.id;
    $('kb-delete-text').textContent = t('kb_delete_confirm', { title: doc.title });
    openModal('kb-delete-modal');
  }

  async function confirmDelete() {
    if (state.busy || !state.deletingId) return;
    state.busy = true;
    const button = $('kb-delete-confirm');
    button.disabled = true;
    try {
      await ApiClient.delete(`/api/knowledge/${encodeURIComponent(state.deletingId)}`);
      closeModal('kb-delete-modal');
      showToast('success', t('kb_deleted'));
      await load();
    } catch (err) {
      showToast('error', t('kb_delete_failed'), err && err.message);
    } finally {
      state.busy = false;
      button.disabled = false;
      state.deletingId = null;
    }
  }

  // ─── WIRING ─────────────────────────────────────────────

  function bindCommon() {
    const search = $('kb-search');
    if (search) {
      search.addEventListener('input', () => {
        state.query = search.value.trim().toLocaleLowerCase();
        render();
      });
    }
    App.onRerender(render);
  }

  function bindEditor() {
    const addButton = $('kb-add-btn');
    if (!addButton) return;
    addButton.hidden = false;
    const icon = Icons.el('plus', { size: 16 });
    if (icon) addButton.prepend(icon);
    addButton.addEventListener('click', () => openEditor(null));
    $('kb-file').addEventListener('change', onFileChosen);
    $('kb-save-btn').addEventListener('click', () => { void save(); });
    App.onRerender(paintEditor);
    $('kb-delete-confirm').addEventListener('click', () => { void confirmDelete(); });
  }

  async function start(user) {
    state.canEdit = Boolean(user) && EDITOR_ROLES.includes(user.role);
    bindCommon();
    if (state.canEdit) bindEditor();
    await load();
  }

  // The admin shell calls init once it knows who is signed in.
  global.AdminKnowledge = Object.freeze({ init: start });

  document.addEventListener('DOMContentLoaded', async () => {
    if (document.body.dataset.page !== 'partner-knowledge') return;
    const session = await global.initPartnerPage();
    if (session) await start(session);
  });

  // Exposed for tests only.
  global.KnowledgeBase = Object.freeze({ formatSize, matches, state });
})(window);
