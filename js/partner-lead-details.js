/* Owner-scoped lead details. No drafts or customer data in persistent storage.
   Kept separate from partner.js so submission and dashboard remain untouched. */
(function (global) {
  'use strict';
  let state = null;
  let generation = 0;
  const node = (tag, text, className = '') => {
    const el = document.createElement(tag);
    el.textContent = String(text ?? '');
    el.className = className;
    return el;
  };
  const valueText = value => value === null || value === undefined || value === ''
    ? App.t('pld_not_provided') : typeof value === 'boolean'
      ? (value ? App.t('pld_yes') : App.t('pld_no')) : String(value);
  const button = (label, attribute, action, primary = false) => {
    const el = node('button', label, `btn ${primary ? 'btn-primary' : 'btn-secondary'}`);
    el.type = 'button';
    el.setAttribute(attribute, '');
    el.addEventListener('click', action);
    return el;
  };
  const baseFields = [
    ['client_name', 'pld_contact_name', 200],
    ['client_email', 'pld_contact_email', 254],
    ['client_phone', 'pld_contact_phone', 50],
    ['notes', 'pld_contact_notes', 5000],
  ];

  function field(host, name, label, value, limit) {
    const group = node('div', '', 'form-group');
    const title = node('label', label, 'form-label');
    const input = node(name === 'notes' || name === 'information' ? 'textarea' : 'input', '', 'form-input');
    input.id = `detail-${name}`;
    input.name = name;
    input.value = value ?? '';
    input.dataset.limit = String(limit);
    title.htmlFor = input.id;
    if (name === 'client_email') input.type = 'email';
    if (input.tagName === 'TEXTAREA') input.rows = 4;
    group.append(title, input);
    host.append(group);
    return input;
  }

  // Stable per-field key, independent of DOM position: a base contact field
  // keyed by name, a custom question keyed by its id. Used so an unsaved
  // edit survives the full DOM rebuild every refetch performs.
  function fieldKey(el) {
    return el.dataset.fieldId ? `custom:${el.dataset.fieldId}` : `base:${el.name || el.id}`;
  }
  function captureField(s, el) {
    if (!el || !(el.name || el.dataset.fieldId)) return;
    const label = s.dialog.querySelector(`label[for="${el.id}"]`)?.textContent || el.name || el.id;
    const value = el.type === 'checkbox' ? el.checked : el.value;
    s.draftMap.set(fieldKey(el), { label, value });
  }
  function markInvalid(el, message) {
    el.setAttribute('aria-invalid', 'true');
    const id = `${el.id}-error`;
    let msgEl = el.parentElement && [...el.parentElement.children].find(c => c.id === id);
    if (!msgEl) {
      msgEl = node('p', '', 'field-error');
      msgEl.id = id;
      msgEl.setAttribute('role', 'alert');
      el.insertAdjacentElement('afterend', msgEl);
    }
    msgEl.textContent = message;
    el.setAttribute('aria-describedby', id);
  }
  function clearInvalid(el) {
    el.removeAttribute('aria-invalid');
    const id = el.getAttribute('aria-describedby');
    el.removeAttribute('aria-describedby');
    if (!id) return;
    const msgEl = el.parentElement && [...el.parentElement.children].find(c => c.id === id);
    msgEl?.remove();
  }
  // Whatever is still unsaved, shown as inert text below the (possibly
  // rebuilt) form. Sourced from the live draft map, never from the DOM, so a
  // field the producer just revoked keeps its last typed answer visible even
  // though its control disappeared, and a second refetch does not overwrite
  // what an earlier refetch already preserved.
  function refreshDraftBox(s) {
    const entries = [...s.draftMap.values()];
    if (!entries.length) return;
    const box = node('details', '', 'detail-draft');
    box.open = true;
    box.append(node('summary', App.t('pld_unsaved_draft_only_on_this')));
    entries.forEach(entry => box.append(node('p', `${entry.label}: ${valueText(entry.value)}`)));
    s.dialog.append(box);
  }

  function close(force = false) {
    if (!state || state.busy) return;
    if (!force && state.draftMap.size > 0) {
      if (state.overlay.querySelector('[data-discard-confirm]')) return;
      const confirm = node('div', '', 'detail-confirm');
      confirm.setAttribute('data-discard-confirm', '');
      confirm.append(node('p', App.t('pld_discard_unsaved_changes')));
      const back = button(App.t('pld_continue_editing'), 'data-discard-back', () => {
        confirm.remove(); state.dialog.querySelector('input, textarea, button')?.focus();
      });
      confirm.append(back, button(App.t('pld_discard_changes'), 'data-discard', () => close(true)));
      state.dialog.append(confirm);
      back.focus();
      return;
    }
    const old = state;
    state = null;
    generation++;
    old.overlay.remove();
    document.body.style.overflow = old.overflow;
    if (old.opener?.isConnected) old.opener.focus();
    else [...document.querySelectorAll('[data-detail-lead]')].find(el => el.dataset.detailLead === old.id)?.focus();
  }

  // ─── LAYOUT HELPERS ─────────────────────────────────────────────────────
  //
  // Named sections, a two-column label/value grid instead of glued-together
  // sentences, badges for the two statuses, money formatted as money and dates
  // as dates. Same facts, same order of importance — just legible.

  function section(host, titleKey) {
    const wrap = node('section', '', 'detail-section');
    wrap.append(node('h3', App.t(titleKey), 'detail-section-title'));
    host.append(wrap);
    return wrap;
  }

  /** One label/value pair in the two-column grid. `value` may be a node. */
  function row(grid, label, value) {
    grid.append(node('dt', label, 'detail-dt'));
    const dd = node('dd', '', 'detail-dd');
    if (value instanceof Node) dd.append(value);
    else dd.textContent = valueText(value);
    grid.append(dd);
    return dd;
  }

  function grid(host) {
    const dl = node('dl', '', 'detail-grid');
    host.append(dl);
    return dl;
  }

  function money(value) {
    const number = Number(value);
    if (!Number.isFinite(number)) return App.t('pld_not_provided');
    return new Intl.NumberFormat(App.t('pld_en_gb'),
      { style: 'currency', currency: 'PLN', maximumFractionDigits: 0 }).format(number);
  }

  function date(iso) {
    if (!iso) return '—';
    const parsed = new Date(/(?:Z|[+-]\d{2}:?\d{2})$/.test(String(iso)) ? iso : `${iso}Z`);
    if (Number.isNaN(parsed.getTime())) return '—';
    return new Intl.DateTimeFormat(App.t('pld_en_gb'),
      { day: '2-digit', month: 'short', year: 'numeric' }).format(parsed);
  }

  function badge(text, tone) {
    return node('span', text, `badge ${tone || 'badge-neutral'}`);
  }

  const REGISTRATION_TONES = {
    pending: 'badge-warning', needs_info: 'badge-info',
    accepted: 'badge-success', rejected: 'badge-error',
  };

  /* Where this lead's money actually is — the same four states the earnings
     screen shows, read from the same columns api/payouts.py settles against.
     A partner opening a lead asks "did he pay me for this one?", and until now
     the modal answered with a bare number and no state at all. */
  function payoutState(lead) {
    if (lead.commission_paid_at) {
      return { text: App.t('pd_payout_paid', { date: date(lead.commission_paid_at) }), tone: 'badge-success' };
    }
    if (lead.won_at && !lead.win_confirmed_at) {
      return { text: App.t('pd_payout_awaiting'), tone: 'badge-warning' };
    }
    if (lead.commission != null) {
      return { text: App.t('pd_payout_due'), tone: 'badge-info' };
    }
    return null;
  }

  /* The queue is the one part of the commission formula a partner cannot work
     out from his own rate: somebody else got to this client first, so this
     deal pays a share of the rate, so it is spelled out here. `queue_share` comes from the API's own projection — it is
     never recomputed in the browser. */
  function queueCallout(host, lead) {
    const position = Number(lead.queue_position || 1);
    const share = Number(lead.queue_share);
    if (!Number.isFinite(share)) return;
    const box = node('div', '', `detail-callout${position > 1 ? ' is-warning' : ''}`);
    box.append(node('div', App.t('pd_queue_title'), 'detail-callout-title'));
    box.append(node('p', position > 1
      ? App.t('pd_queue_nth', { position, share: Math.round(share * 100) / 100 })
      : App.t('pd_queue_first', { share: Math.round(share * 100) / 100 })));
    if (position > 1) box.append(node('p', App.t('pd_queue_explainer'), 'text-xs text-tertiary'));
    host.append(box);
  }

  function render(s) {
    const lead = s.lead;
    s.dialog.replaceChildren();

    // ── header: who the deal is with, and its two statuses at a glance ──
    const header = node('div', '', 'modal-header detail-header');
    const titleWrap = node('div', '', 'detail-header-identity');
    const title = node('h2', lead.company, 'modal-title');
    title.id = 'partner-detail-title';
    titleWrap.append(title, node('div', lead.id, 'detail-header-id'));
    const badges = node('div', '', 'detail-header-badges');
    const registrationLabel = Ui.registrationLabel(lead.registration_status)
      || valueText(lead.registration_status);
    badges.append(badge(registrationLabel, REGISTRATION_TONES[lead.registration_status]));
    if (lead.stage_name) badges.append(badge(lead.stage_name, 'badge-brand'));
    header.append(titleWrap, badges);
    s.dialog.append(header);

    const body = node('div', '', 'modal-body');

    // ── status ──────────────────────────────────────────────────────────
    const status = section(body, 'pd_section_status');
    const statusGrid = grid(status);
    row(statusGrid, App.t('pd_registration'), registrationLabel);
    row(statusGrid, App.t('pd_sales_stage'), lead.stage_name
      || App.t(lead.status ? 'pd_stage_unavailable' : 'pd_outside_pipeline'));
    row(statusGrid, App.t('pd_created'), date(lead.created_at));
    row(statusGrid, App.t('pd_updated'), date(lead.updated_at));
    if (lead.won_at) row(statusGrid, App.t('pd_won_at'), date(lead.won_at));
    queueCallout(status, lead);
    const editable = Array.isArray(lead.field_schema)
      && typeof lead.schema_revision === 'string'
      && Number.isInteger(lead.revision)
      && lead.permissions?.edit_details === true;
    const schemaReady = Array.isArray(lead.field_schema) && typeof lead.schema_revision === 'string';
    if (!editable) status.append(node('p', schemaReady
      ? App.t('pld_this_lead_is_closed_or')
      : App.t('pld_field_settings_unavailable_refresh_before'),
      'detail-note'));

    // ── the deal ────────────────────────────────────────────────────────
    const deal = section(body, 'pd_section_deal');
    const dealGrid = grid(deal);
    row(dealGrid, App.t('pld_product'), Ui.producerLabel(lead, 'product'));
    row(dealGrid, App.t('pld_quantity'), lead.quantity);
    row(dealGrid, App.t('pld_value'), money(lead.value));
    row(dealGrid, 'NIP', lead.nip);
    if (lead.website) row(dealGrid, App.t('pld_website'), lead.website);

    // ── commission ──────────────────────────────────────────────────────
    // One number, labelled as what it is. `commission` is the amount actually
    // recorded against the deal; `commission_projected` is the forecast that
    // exists at every stage. Showing either without saying which is how a
    // forecast gets read as a payment.
    const commissionSection = section(body, 'pd_section_money');
    const recorded = lead.commission != null;
    // A rejected submission earns nothing, ever. `commission_projected` exists
    // at every stage, so printing it here would quote a dead deal a payment --
    // the same trap partner.js::commissionIndicator already avoids on the list.
    const dead = !recorded && lead.registration_status === 'rejected';
    if (dead) {
      commissionSection.append(badge(App.t('pt_no_commission'), 'badge-neutral'));
    } else {
      const amount = recorded ? lead.commission : lead.commission_projected;
      const figure = node('div', '', 'detail-money');
      figure.append(node('span', money(amount), 'detail-money-value'));
      figure.append(badge(App.t(recorded ? 'pd_commission_recorded' : 'pd_commission_projected'),
        recorded ? 'badge-success' : 'badge-neutral'));
      commissionSection.append(figure);
      if (Number.isFinite(Number(lead.commission_rate))) {
        commissionSection.append(node('p', App.t('pd_commission_rate', { rate: lead.commission_rate }),
          'text-sm text-tertiary'));
      }
      const payout = payoutState(lead);
      if (payout) commissionSection.append(badge(payout.text, payout.tone));
    }

    // ── contact ─────────────────────────────────────────────────────────
    const contact = section(body, 'pd_section_contact');
    if (editable) {
      for (const [name, labelKey, limit] of baseFields) field(contact, name, App.t(labelKey), lead[name], limit);
    } else {
      const contactGrid = grid(contact);
      for (const [name, labelKey] of baseFields) row(contactGrid, App.t(labelKey), lead[name]);
    }

    // ── the producer's own questions ────────────────────────────────────
    const custom = section(body, 'pd_section_custom');
    s.custom = node('div');
    const answers = Object.fromEntries((lead.field_values || []).map(v => [v.field_id, v.value]));
    const writable = editable ? lead.field_schema.filter(f => f.active && f.partner_editable) : [];
    LeadFields.render(s.custom, writable, answers);
    // Native maxlength counts UTF-16 units, unlike the server's codepoints.
    s.custom.querySelectorAll('textarea').forEach(el => {
      el.removeAttribute('maxlength'); el.dataset.limit = '4000';
    });
    custom.append(s.custom);
    const definitions = lead.field_schema || lead.field_values?.map(v => ({ ...v, id: v.field_id })) || [];
    const readOnlyFields = definitions.filter(f => !writable.some(w => w.id === f.id));
    if (readOnlyFields.length) {
      const customGrid = grid(custom);
      readOnlyFields.forEach(f => row(customGrid, Ui.fieldLabel(f), answers[f.id]));
    }
    if (!definitions.length) custom.append(node('p', App.t('pld_no_additional_fields'), 'detail-note'));

    // ── conversation with the producer ──────────────────────────────────
    const history = section(body, 'pd_section_history');
    s.history = node('div', App.t('pld_loading'), 'detail-history');
    history.append(s.history);
    if (lead.registration_status === 'needs_info' && Number.isInteger(lead.revision)) {
      field(history, 'information', App.t('pld_your_response'), '', 4000);
      history.append(button(App.t('pld_send_information'), 'data-detail-answer', () => save(s, true)));
    }

    s.feedback = node('p', '', 'detail-feedback');
    s.feedback.setAttribute('role', 'status');
    body.append(s.feedback);
    s.dialog.append(body);
    const footer = node('div', '', 'modal-footer');
    footer.append(button(App.t('pld_close_lead_details'), 'data-detail-close', () => close()),
      button(App.t('pld_refresh_data'), 'data-detail-refresh', () => refresh(s)));
    if (editable) footer.append(button(App.t('pld_save_changes'), 'data-detail-save', () => save(s), true));
    s.dialog.append(footer);
  }

  function unavailable(s) {
    if (state !== s) return;
    s.busy = false;
    close(true);
    showToast('error', App.t('pld_lead_unavailable_refresh_the_list'));
  }
  async function history(s, sequence) {
    try {
      const result = await ApiClient.get(`/api/leads/${encodeURIComponent(s.id)}/comments`);
      if (state !== s || sequence !== s.sequence) return;
      s.history.replaceChildren();
      for (const comment of result.comments || []) {
        const entry = node('div', '', 'comment-item');
        entry.append(node('p', `${comment.author_name || ''} ${comment.created_at || ''}`), node('p', comment.body));
        s.history.append(entry);
      }
      if (!s.history.childNodes.length) s.history.textContent = App.t('pld_no_comments');
    } catch (err) {
      if (state !== s || sequence !== s.sequence) return;
      if ([401, 403, 404].includes(err.status)) return unavailable(s);
      s.history.textContent = App.t('pld_history_unavailable_refresh_data');
    }
  }
  async function refresh(s) {
    if (state !== s || s.busy) return;
    const sequence = ++s.sequence;
    s.ready = false;
    s.dialog.querySelectorAll('input, textarea, button').forEach(el => { el.disabled = true; });
    s.dialog.setAttribute('aria-busy', 'true');
    try {
      const result = await ApiClient.get(`/api/leads/${encodeURIComponent(s.id)}`);
      if (state !== s || sequence !== s.sequence) return;
      if (!result.lead || result.lead.id !== s.id) throw new Error('Invalid detail');
      s.lead = result.lead;
      render(s);
      s.ready = true;
      refreshDraftBox(s);
      s.dialog.querySelector('input, textarea, button')?.focus();
      await history(s, sequence);
    } catch (err) {
      if (state !== s || sequence !== s.sequence) return;
      if ([401, 403, 404].includes(err.status)) return unavailable(s);
      if (!s.feedback) { s.feedback = node('p'); s.dialog.append(s.feedback); }
      s.feedback.textContent = App.t('pld_current_data_unavailable_editing_disabled');
      s.dialog.append(button(App.t('pld_try_again'), 'data-detail-retry', () => refresh(s)),
        button(App.t('pld_close_lead_details'), 'data-detail-close', () => close()));
    } finally {
      if (state === s && sequence === s.sequence) s.dialog.setAttribute('aria-busy', 'false');
    }
  }
  async function save(s, answer = false) {
    if (state !== s || s.busy || !s.ready) return;
    const inputs = [...s.dialog.querySelectorAll('input, textarea')];
    inputs.forEach(clearInvalid);
    const invalid = inputs.find(el => el.dataset.limit && Array.from(el.value.trim()).length > Number(el.dataset.limit));
    if (invalid) {
      const message = App.t('pld_field_character_limit_exceeded');
      s.feedback.textContent = message;
      markInvalid(invalid, message);
      invalid.focus();
      return;
    }
    let payload;
    if (answer) {
      const message = s.dialog.querySelector('[name="information"]').value.trim();
      if (!message) { s.feedback.textContent = App.t('pld_enter_a_response'); return; }
      payload = { message };
    } else {
      if (!s.lead.permissions?.edit_details) return;
      const fields = LeadFields.serialize(s.custom);
      if (fields.missing.length) {
        const first = LeadFields.highlightMissing(s.custom, fields.missing);
        const message = App.t('pld_complete_the_required_fields');
        fields.missing.forEach(id => {
          const el = s.custom.querySelector(`[data-field-id="${id}"]`);
          if (el) markInvalid(el, App.t('pld_complete_this_field'));
        });
        s.feedback.textContent = message;
        first?.focus();
        return;
      }
      // Unlike submission, an edit must explicitly represent clearing.
      s.custom.querySelectorAll('[data-field-id]').forEach(el => {
        if (!(el.dataset.fieldId in fields.values)) fields.values[el.dataset.fieldId] = null;
      });
      payload = { schema_revision: s.lead.schema_revision, lead_field_values: fields.values };
      baseFields.forEach(([name]) => { payload[name] = s.dialog.querySelector(`[name="${name}"]`).value; });
    }
    payload.expected_revision = s.lead.revision;
    payload.operation_id = global.crypto.randomUUID();
    s.busy = true;
    s.dialog.setAttribute('aria-busy', 'true');
    s.dialog.querySelectorAll('input, textarea, button').forEach(el => { el.disabled = true; });
    s.feedback.textContent = App.t('pld_saving');
    try {
      await (answer ? ApiClient.post(`/api/leads/${encodeURIComponent(s.id)}/information`, payload)
        : ApiClient.patch(`/api/leads/${encodeURIComponent(s.id)}/details`, payload));
      if (state !== s) return;
      s.busy = false;
      // The write just succeeded: whatever it carried is no longer a draft.
      // An unsent needs_info answer sits beside the contact/custom form and
      // is untouched by a details save, and vice versa.
      if (answer) s.draftMap.delete('base:information');
      else for (const key of [...s.draftMap.keys()]) if (key !== 'base:information') s.draftMap.delete(key);
      await refresh(s);
      if (state !== s) return;
      if (!s.ready) {
        s.feedback.textContent = App.t('pld_save_confirmed_but_refresh_failed');
        return;
      }
      s.feedback.textContent = answer ? App.t('pld_information_sent_awaiting_producer_decision')
        : App.t('pld_changes_saved');
      document.dispatchEvent(new CustomEvent('partner-lead-updated', { detail: { lead: s.lead } }));
    } catch (err) {
      if (state !== s) return;
      s.busy = false;
      if ([401, 403, 404].includes(err.status)) return unavailable(s);
      if (err.status === 400) {
        s.dialog.querySelectorAll('input, textarea, button').forEach(el => { el.disabled = false; });
        s.feedback.textContent = App.t('pld_check_the_form_changes_were');
      } else {
        // Stale/revoked schema, closed lead, or an unconfirmed network
        // result: the draft map already holds the latest typed values, so a
        // refetch only needs to redisplay it, never recompute it from a DOM
        // that is about to be rebuilt.
        await refresh(s);
        if (state !== s) return;
        s.feedback.textContent = err.status === 409
          ? App.t('pld_data_or_permissions_changed_review')
          : App.t('pld_result_unconfirmed_review_current_data');
      }
    } finally {
      if (state === s) { s.busy = false; s.dialog.setAttribute('aria-busy', 'false'); }
    }
  }

  async function open(id, opener) {
    if (state) {
      close();
      if (state) return;
    }
    const overlay = node('div', '', 'modal-overlay show partner-detail-overlay');
    const dialog = node('div', App.t('pld_loading'), 'modal modal-lg partner-detail');
    dialog.setAttribute('role', 'dialog');
    dialog.setAttribute('aria-modal', 'true');
    dialog.setAttribute('aria-label', App.t('pld_lead_details'));
    dialog.setAttribute('aria-labelledby', 'partner-detail-title');
    const s = { id, opener, overlay, dialog, sequence: ++generation, busy: false, ready: false,
      draftMap: new Map(), overflow: document.body.style.overflow };
    state = s;
    overlay.append(dialog);
    overlay.addEventListener('click', e => { e.stopPropagation(); if (e.target === overlay) close(); });
    overlay.addEventListener('input', e => { captureField(s, e.target); });
    overlay.addEventListener('change', e => { captureField(s, e.target); });
    overlay.addEventListener('keydown', e => {
      if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(); }
      if (e.key !== 'Tab') return;
      const focusables = [...dialog.querySelectorAll('input, textarea, button, summary')].filter(el => !el.disabled);
      const first = focusables[0], last = focusables[focusables.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last?.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first?.focus(); }
      else if (!first) e.preventDefault();
    });
    document.body.append(overlay);
    document.body.style.overflow = 'hidden';
    await refresh(s);
  }
  // An open modal is entirely code-drawn, so a language or theme switch has to
  // redraw it. Re-rendering from `s.lead` (never a refetch) keeps the unsaved
  // draft intact: refreshDraftBox puts it straight back underneath.
  if (typeof App !== 'undefined' && App.onRerender) {
    App.onRerender(() => {
      if (!state || state.busy || !state.lead) return;
      render(state);
      refreshDraftBox(state);
    });
  }

  global.PartnerLeadDetails = { open };
})(window);
