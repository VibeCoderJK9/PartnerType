/* ============================================================
   LeadFields — the producer's own questions about a deal
   ============================================================

   One renderer and one serializer, shared by every screen that shows these
   fields: the submission form now, the lead detail and the assignment form in
   later waves. Written once because three screens disagreeing about how a
   checkbox serializes is exactly how `false` becomes "unanswered".

   Two rules the whole module exists to protect:

   - `0` and `false` are ANSWERS. Anything shaped like `if (!value)` is wrong
     here, so emptiness is tested explicitly and only for text.
   - The schema has a version. A form rendered before the producer edited it
     submits that version, and the server refuses it — otherwise a required
     field added meanwhile is silently skipped.
   ============================================================ */

(function attachLeadFields(global) {
  'use strict';

  const LeadFields = {};

  const TEXT_MAX = 4000;

  function inputIdFor(field) {
    return `lead-field-${field.id}`;
  }

  /**
   * Render the active questions into `container`.
   *
   * Archived fields are skipped: they exist so old answers keep their labels,
   * not so new submissions can still answer them.
   */
  LeadFields.render = function render(container, fields, values) {
    if (!container) return;
    container.replaceChildren();
    const answers = values || {};
    const active = (fields || []).filter(field => field.active);
    if (!active.length) return;

    active.forEach((field) => {
      const group = document.createElement('div');
      group.className = 'form-group';
      group.dataset.leadField = String(field.id);

      const input = document.createElement(
        field.field_type === 'text' ? 'textarea' : 'input',
      );
      input.id = inputIdFor(field);
      input.dataset.fieldId = String(field.id);
      input.dataset.fieldType = field.field_type;

      const label = document.createElement('label');
      label.className = 'form-label';
      label.setAttribute('for', input.id);
      // textContent, never innerHTML: the label is producer-supplied text and
      // has no business being parsed as markup.
      const caption = (typeof Ui !== 'undefined' && Ui.fieldLabel)
        ? Ui.fieldLabel(field) : field.label;
      label.textContent = field.required ? `${caption} *` : caption;

      const existing = answers[field.id];

      if (field.field_type === 'checkbox') {
        input.type = 'checkbox';
        input.className = 'form-checkbox';
        input.checked = existing === true;
        group.append(label, input);
      } else if (field.field_type === 'number') {
        input.type = 'number';
        input.className = 'form-input';
        input.step = 'any';
        // Explicitly not `existing || ''`: zero is an answer.
        input.value = (existing === undefined || existing === null) ? '' : String(existing);
        group.append(label, input);
      } else {
        input.className = 'form-textarea';
        input.rows = 3;
        input.maxLength = TEXT_MAX;
        input.value = (existing === undefined || existing === null) ? '' : String(existing);
        group.append(label, input);
      }

      if (field.required) input.dataset.required = '1';
      container.appendChild(group);
    });
  };

  /**
   * Re-caption the rendered fields without touching what was typed.
   *
   * A producer's label is DATA with its own English column, so a language
   * switch has to reach it -- but re-rendering the group to get there would
   * throw away a half-filled form. This walks the labels only.
   *
   * @param {HTMLElement} container the element `render` drew into
   * @param {Array} fields the same field definitions `render` was given
   */
  LeadFields.relabel = function relabel(container, fields) {
    if (!container) return;
    (fields || []).forEach((field) => {
      const group = container.querySelector(`[data-lead-field="${field.id}"]`);
      const label = group && group.querySelector('label.form-label');
      if (!label) return;
      const caption = (typeof Ui !== 'undefined' && Ui.fieldLabel)
        ? Ui.fieldLabel(field) : field.label;
      label.textContent = field.required ? `${caption} *` : caption;
    });
  };

  /**
   * Read what the person typed, as `{fieldId: value}` ready for the API.
   *
   * Returns `{values, missing}` rather than throwing: the caller decides how
   * to report an unanswered required field, and a half-built payload should
   * never reach the network.
   */
  LeadFields.serialize = function serialize(container) {
    const values = {};
    const missing = [];
    if (!container) return { values, missing };

    container.querySelectorAll('[data-field-id]').forEach((input) => {
      const id = input.dataset.fieldId;
      const type = input.dataset.fieldType;
      const required = input.dataset.required === '1';

      if (type === 'checkbox') {
        // A boolean either way: leaving the box unticked is answering "no",
        // and omitting it would read as "did not answer".
        values[id] = !!input.checked;
        return;
      }

      const raw = String(input.value ?? '').trim();
      if (raw === '') {
        if (required) missing.push(id);
        return;
      }

      if (type === 'number') {
        const parsed = Number(raw);
        if (!Number.isFinite(parsed)) {
          missing.push(id);
          return;
        }
        values[id] = parsed;
        return;
      }

      values[id] = raw;
    });

    return { values, missing };
  };

  /** Mark the fields a person still has to answer, and return the first. */
  LeadFields.highlightMissing = function highlightMissing(container, missing) {
    if (!container) return null;
    container.querySelectorAll('[data-field-id]').forEach((input) => {
      input.classList.remove('input-error');
    });
    let first = null;
    (missing || []).forEach((id) => {
      const input = container.querySelector(`[data-field-id="${id}"]`);
      if (!input) return;
      input.classList.add('input-error');
      if (!first) first = input;
    });
    return first;
  };

  global.LeadFields = LeadFields;
})(typeof window !== 'undefined' ? window : globalThis);
