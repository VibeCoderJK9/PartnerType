/* ============================================================
   PARTNERTYPE — ADMIN BLOCKS CONTROLLER
   Settings › Blocks. Each block is a card: what it does, an
   on/off switch, its numbers, Save, and — for a scheduled
   block — "Check now", so the producer sees it work
   without waiting for the nightly run.
   Every value is drawn with textContent. The block list comes
   from the server; nothing here knows a block by name.
   ============================================================ */

(function initAdminBlocks(global) {
  'use strict';

  let blocksState = [];
  let rerenderBound = false;

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function renderBlock(block) {
    const panel = el('div', 'panel');
    panel.dataset.blockId = block.id;

    const header = el('div', 'panel-header');
    header.appendChild(el('div', 'panel-title', App.t(block.name_key)));
    panel.appendChild(header);

    const body = el('div', 'panel-body');
    body.appendChild(el('div', 'text-sm text-secondary', App.t(block.description_key)));

    const toggleRow = el('div');
    toggleRow.style.cssText = 'display:flex;align-items:center;justify-content:space-between;gap:16px;margin-top:16px';
    const toggleLabel = el('label', 'text-sm font-medium text-primary', App.t('st_block_enabled'));
    const switchLabel = el('label', 'toggle-switch');
    switchLabel.style.flexShrink = '0';
    const toggle = document.createElement('input');
    toggle.type = 'checkbox';
    toggle.id = `block-${block.id}-enabled`;
    toggle.checked = !!block.enabled;
    toggleLabel.htmlFor = toggle.id;
    switchLabel.append(toggle, el('span', 'toggle-slider'));
    toggleRow.append(toggleLabel, switchLabel);
    body.appendChild(toggleRow);

    const inputs = {};
    for (const field of block.config_fields || []) {
      const group = el('div', 'form-group');
      group.style.marginTop = '16px';
      const input = document.createElement('input');
      input.type = 'number';
      input.className = 'form-input';
      input.id = `block-${block.id}-${field.name}`;
      input.min = String(field.min);
      input.max = String(field.max);
      input.step = '1';
      input.style.maxWidth = '160px';
      input.value = String(block.config?.[field.name] ?? field.default);
      const label = el('label', 'form-label', App.t(field.label_key));
      label.htmlFor = input.id;
      group.append(label, input);
      body.appendChild(group);
      inputs[field.name] = input;
    }

    const result = el('div', 'form-hint');
    result.setAttribute('aria-live', 'polite');
    result.style.marginTop = '12px';

    const actions = el('div');
    actions.style.cssText = 'display:flex;gap:8px;flex-wrap:wrap;margin-top:16px';
    const save = el('button', 'btn btn-primary', App.t('st_block_save'));
    save.type = 'button';
    actions.appendChild(save);

    let run = null;
    if (block.scheduled) {
      run = el('button', 'btn btn-secondary', App.t('st_block_run_now'));
      run.type = 'button';
      run.disabled = !block.enabled;
      run.title = block.enabled ? '' : App.t('st_block_off_hint');
      actions.appendChild(run);
    }
    body.append(actions, result);
    panel.appendChild(body);

    save.addEventListener('click', async () => {
      const config = {};
      for (const [name, input] of Object.entries(inputs)) {
        config[name] = Number.parseInt(input.value, 10);
      }
      save.disabled = true;
      try {
        const data = await ApiClient.patch(`/api/blocks/${encodeURIComponent(block.id)}`,
          { enabled: toggle.checked, config });
        Object.assign(block, data.block);
        if (run) {
          run.disabled = !block.enabled;
          run.title = block.enabled ? '' : App.t('st_block_off_hint');
        }
        showToast('success', App.t('st_block_saved'), App.t(block.name_key));
      } catch (error) {
        showToast('error', App.t('st_block_save_failed'), error?.message || '');
      } finally {
        save.disabled = false;
      }
    });

    if (run) {
      run.addEventListener('click', async () => {
        run.disabled = true;
        try {
          const data = await ApiClient.post(`/api/blocks/${encodeURIComponent(block.id)}/run`, {});
          const outcome = data.result || {};
          result.textContent = App.t('st_block_run_result', {
            checked: outcome.checked ?? 0,
            reminded: outcome.reminded ?? 0,
          });
        } catch (error) {
          showToast('error', App.t('st_block_run_failed'), error?.message || '');
        } finally {
          run.disabled = !block.enabled;
        }
      });
    }
    return panel;
  }

  function render() {
    const list = document.getElementById('blocks-list');
    if (!list) return;
    list.textContent = '';
    for (const block of blocksState) list.appendChild(renderBlock(block));
  }

  async function load() {
    const list = document.getElementById('blocks-list');
    if (!list) return;
    try {
      const data = await ApiClient.get('/api/blocks');
      blocksState = Array.isArray(data.blocks) ? data.blocks : [];
      render();
    } catch (error) {
      list.textContent = '';
      list.appendChild(el('div', 'form-hint', App.t('st_blocks_load_failed')));
    }
    // Bound once; read into a local first so the hook is checked, not assigned.
    const registerRerender = App.onRerender;
    if (!rerenderBound && typeof registerRerender === 'function') {
      rerenderBound = true;
      App.onRerender(render);
    }
  }

  global.AdminBlocks = { load, render };
  document.addEventListener('DOMContentLoaded', load);
})(window);
