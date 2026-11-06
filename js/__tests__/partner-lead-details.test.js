import { readFileSync } from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { Window } from 'happy-dom';
import { beforeEach, afterEach, expect, it, vi } from 'vitest';
import { appStub } from './helpers/copy.js';

const root = path.resolve(import.meta.dirname, '../..');
let w;
const fields = [
  { id: 1, label: 'Tekst', field_type: 'text', active: true, partner_editable: true },
  { id: 2, label: 'Liczba', field_type: 'number', active: true, partner_editable: true },
  { id: 3, label: 'Zgoda', field_type: 'checkbox', active: true, partner_editable: true },
  { id: 4, label: 'Zablokowane', field_type: 'text', active: true, partner_editable: false },
];
function lead(overrides = {}) {
  return { id: 'LD-001', company: 'Firma', client_name: 'Jan', client_email: 'jan@example.pl',
    client_phone: '', notes: 'Notatki', status: null, registration_status: 'pending',
    revision: 3, schema_revision: '4-0', field_schema: fields,
    field_values: [{ field_id: 2, value: 0 }, { field_id: 3, value: false }],
    permissions: { edit_details: true }, ...overrides };
}
const q = selector => w.document.querySelector(selector);
const tick = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };
const input = (selector, value) => {
  q(selector).value = value;
  q(selector).dispatchEvent(new w.Event('input', { bubbles: true }));
};
beforeEach(() => {
  w = new Window();
  // The real dictionary, not `{ lang: 'pl' }`: since the modal was rebuilt
  // into named sections its headings, status words and queue
  // explanation come from App.t, so a stub without `t` makes render() throw
  // and every assertion here fail as "Edycja zablokowana".
  w.App = appStub('pl');
  w.formatCurrency = value => `${value} PLN`;
  w.showToast = vi.fn();
  w.document.body.innerHTML = '<button id="opener">Szczegóły</button>';
  w.ApiClient = { get: vi.fn(async url => url.endsWith('/comments') ? { comments: [] } : { lead: lead() }),
    patch: vi.fn(async (_, payload) => ({ lead: lead({ ...payload, revision: 4 }) })), post: vi.fn() };
  const context = vm.createContext(w);
  // utils.js comes first because both pages that host this modal load it:
  // the producer's own field labels are read through Ui.fieldLabel.
  for (const file of ['js/utils.js', 'js/lead-fields.js', 'js/partner-lead-details.js']) {
    const filename = path.join(root, file);
    vm.runInContext(readFileSync(filename, 'utf8'), context, { filename });
  }
});
afterEach(() => w.happyDOM.abort());
async function open(overrides = {}) {
  w.ApiClient.get.mockImplementation(async url => url.endsWith('/comments') ? { comments: [] } : { lead: lead(overrides) });
  await w.PartnerLeadDetails.open('LD-001', q('#opener'));
}

it('loads real details, edits allowed fields and sends explicit versions', async () => {
  await open();
  expect(q('[data-field-id="2"]').value).toBe('0');
  expect(q('[data-field-id="3"]').checked).toBe(false);
  expect(q('[data-field-id="4"]')).toBeNull();
  input('[name="client_name"]', 'Żaneta 😀');
  input('[name="notes"]', 'Nowa notatka');
  q('[data-detail-save]').click(); await tick();
  expect(w.ApiClient.patch).toHaveBeenCalledTimes(1);
  const [url, payload] = w.ApiClient.patch.mock.calls[0];
  expect(url).toBe('/api/leads/LD-001/details');
  expect(payload).toMatchObject({ client_name: 'Żaneta 😀', notes: 'Nowa notatka',
    expected_revision: 3, schema_revision: '4-0', lead_field_values: { 2: 0, 3: false } });
  expect(payload.operation_id).toEqual(expect.any(String));
  expect(payload).not.toHaveProperty('company');
  expect(payload.lead_field_values).not.toHaveProperty('4');
});

// Leads.product (the Polish text recorded at submission) is never
// rewritten -- product_en is a live catalogue projection the modal only
// reads when the partner's own reading language is English.
it("shows the catalogue's current English product name in English mode", async () => {
  w.App = appStub('en');
  await open({ product: 'Wdrożenie ERP', product_en: 'ERP rollout' });
  expect(q('[role="dialog"]').textContent).toContain('ERP rollout');
  expect(q('[role="dialog"]').textContent).not.toContain('Wdrożenie ERP');
});

it('falls back to the persisted Polish product text when untranslated', async () => {
  w.App = appStub('en');
  await open({ product: 'Wdrożenie ERP', product_en: '' });
  expect(q('[role="dialog"]').textContent).toContain('Wdrożenie ERP');
});

it.each(['won', 'lost', null])('renders locked detail without save controls (%s)', async status => {
  await open({ status, registration_status: status ? 'accepted' : 'rejected', permissions: { edit_details: false } });
  expect(q('[data-detail-save]')).toBeNull();
  expect(q('[name="notes"]')).toBeNull();
  expect(q('[role="dialog"]').textContent).toContain('tylko podgląd');
});

it('renders malicious names, labels, notes and history inertly', async () => {
  const bad = '<img src=x onerror=alert(1)>';
  w.ApiClient.get.mockImplementation(async url => url.endsWith('/comments')
    ? { comments: [{ body: bad, author_name: bad }] }
    : { lead: lead({ company: bad, notes: bad, field_schema: [{ ...fields[0], label: bad }] }) });
  await w.PartnerLeadDetails.open('LD-001', q('#opener'));
  expect(q('[role="dialog"]').textContent).toContain(bad);
  expect(q('[role="dialog"] img')).toBeNull();
});

it('allows a needs_info response when custom fields are read-only', async () => {
  await open({ registration_status: 'needs_info', field_schema: fields.map(f => ({ ...f, partner_editable: false })) });
  w.ApiClient.post.mockResolvedValue({ lead: lead({ registration_status: 'needs_info', revision: 4 }) });
  input('[name="information"]', 'Proszę poprawić zablokowane pole.');
  q('[data-detail-answer]').click(); await tick();
  expect(w.ApiClient.post).toHaveBeenCalledWith('/api/leads/LD-001/information',
    expect.objectContaining({ message: 'Proszę poprawić zablokowane pole.', expected_revision: 3 }));
});

it('keeps one write in flight and does not dismiss a saving modal', async () => {
  await open();
  let resolve;
  w.ApiClient.patch.mockImplementation(() => new Promise(r => { resolve = r; }));
  q('[data-detail-save]').click(); q('[data-detail-save]').click();
  q('[data-detail-close]').click();
  expect(w.ApiClient.patch).toHaveBeenCalledTimes(1);
  expect(q('[role="dialog"]')).not.toBeNull();
  resolve({ lead: lead() }); await tick();
});

it('retains an unsent information response when saving contact details', async () => {
  await open({ registration_status: 'needs_info' });
  input('[name="information"]', 'Niewysłana odpowiedź');
  input('[name="client_name"]', 'Nowy kontakt');
  q('[data-detail-save]').click(); await tick();
  expect(q('.detail-draft').textContent).toContain('Niewysłana odpowiedź');
  expect(w.ApiClient.post).not.toHaveBeenCalled();
});

it('blocks editing without schema while keeping base data visible', async () => {
  await open({ field_schema: undefined });
  expect(q('[role="dialog"]').textContent).toContain('Firma');
  expect(q('[data-detail-save]')).toBeNull();
});

it('clears optional answers explicitly instead of silently preserving deleted text', async () => {
  await open({ field_values: [{ field_id: 1, value: 'remove' }] });
  input('[data-field-id="1"]', '');
  q('[data-detail-save]').click(); await tick();
  expect(w.ApiClient.patch.mock.calls[0][1].lead_field_values['1']).toBeNull();
});

it('keeps the draft for a field the producer revoked while the schema changed elsewhere', async () => {
  await open();
  input('[data-field-id="1"]', 'Szkic w polu 1');
  const conflict = new Error('conflict'); conflict.status = 409;
  w.ApiClient.patch.mockRejectedValueOnce(conflict);
  w.ApiClient.get.mockImplementation(async url => url.endsWith('/comments') ? { comments: [] }
    : { lead: lead({ schema_revision: '5-0',
        field_schema: fields.map(f => (f.id === 1 ? { ...f, partner_editable: false } : f)) }) });
  q('[data-detail-save]').click(); await tick();
  expect(q('[data-field-id="1"]')).toBeNull();
  expect(q('[role="dialog"]').textContent).toContain('Dane lub uprawnienia zmieniły się');
  expect(q('.detail-draft').textContent).toContain('Szkic w polu 1');
});

it('locks the form and preserves the draft when the lead closes while being edited', async () => {
  await open();
  input('[name="notes"]', 'Notatka w trakcie edycji');
  const conflict = new Error('conflict'); conflict.status = 409;
  w.ApiClient.patch.mockRejectedValueOnce(conflict);
  w.ApiClient.get.mockImplementation(async url => url.endsWith('/comments') ? { comments: [] }
    : { lead: lead({ status: 'won', registration_status: 'accepted', permissions: { edit_details: false } }) });
  q('[data-detail-save]').click(); await tick();
  expect(q('[data-detail-save]')).toBeNull();
  expect(q('[role="dialog"]').textContent).toContain('tylko podgląd');
  expect(q('.detail-draft').textContent).toContain('Notatka w trakcie edycji');
});

it('treats a network failure as an unconfirmed result, refetches and keeps the draft', async () => {
  await open();
  input('[name="notes"]', 'Bez odpowiedzi serwera');
  w.ApiClient.patch.mockRejectedValueOnce(new Error('Brak połączenia z serwerem.'));
  q('[data-detail-save]').click(); await tick();
  expect(w.ApiClient.get).toHaveBeenCalled();
  expect(q('[role="dialog"]').textContent).toContain('Nie potwierdzono wyniku operacji');
  expect(q('.detail-draft').textContent).toContain('Bez odpowiedzi serwera');
});

it('discards a stale detail response once a second lead has already opened', async () => {
  let resolveFirst;
  w.ApiClient.get.mockImplementation(url => {
    if (url === '/api/leads/LD-001') return new Promise(r => { resolveFirst = r; });
    if (url.endsWith('/comments')) return Promise.resolve({ comments: [] });
    return Promise.resolve({ lead: lead({ id: 'LD-002', company: 'Drugi lead' }) });
  });
  const first = w.PartnerLeadDetails.open('LD-001', q('#opener'));
  const second = w.PartnerLeadDetails.open('LD-002', q('#opener'));
  await second;
  expect(q('.modal-title').textContent).toBe('Drugi lead');
  resolveFirst({ lead: lead({ id: 'LD-001', company: 'Pierwszy lead' }) });
  await tick();
  await first;
  expect(q('.modal-title').textContent).toBe('Drugi lead');
});

it('keeps an earlier unsaved edit visible across more than one refetch', async () => {
  await open();
  input('[name="client_name"]', 'Utrwalony szkic');
  w.ApiClient.get.mockImplementation(async url => url.endsWith('/comments') ? { comments: [] } : { lead: lead() });
  q('[data-detail-refresh]').click(); await tick();
  expect(q('.detail-draft').textContent).toContain('Utrwalony szkic');
  q('[data-detail-refresh]').click(); await tick();
  expect(q('.detail-draft').textContent).toContain('Utrwalony szkic');
});

it('marks an over-limit field aria-invalid, links a described error, and moves focus to it', async () => {
  await open();
  input('[name="notes"]', 'a'.repeat(5001));
  q('[data-detail-save]').click(); await tick();
  const el = q('[name="notes"]');
  expect(el.getAttribute('aria-invalid')).toBe('true');
  const describedBy = el.getAttribute('aria-describedby');
  expect(describedBy).toBeTruthy();
  expect(q(`#${describedBy}`).textContent.length).toBeGreaterThan(0);
  expect(w.document.activeElement).toBe(el);
  expect(w.ApiClient.patch).not.toHaveBeenCalled();
});

it('marks a missing required custom field aria-invalid with a described error', async () => {
  const required = { id: 5, label: 'Wymagane', field_type: 'text', active: true, partner_editable: true, required: true };
  await open({ field_schema: [...fields, required] });
  q('[data-detail-save]').click(); await tick();
  const el = q('[data-field-id="5"]');
  expect(el.getAttribute('aria-invalid')).toBe('true');
  expect(el.getAttribute('aria-describedby')).toBeTruthy();
  expect(w.ApiClient.patch).not.toHaveBeenCalled();
});

it.each([
  ['[name="notes"]', 5000, 5001],
  ['[data-field-id="1"]', 4000, 4001],
])('accepts exactly the codepoint limit for %s but rejects one over it, counting emoji as one', async (selector, limit, over) => {
  await open();
  input(selector, '😀'.repeat(limit));
  q('[data-detail-save]').click(); await tick();
  expect(w.ApiClient.patch).toHaveBeenCalledTimes(1);
  w.ApiClient.patch.mockClear();
  input(selector, '😀'.repeat(over));
  q('[data-detail-save]').click(); await tick();
  expect(w.ApiClient.patch).not.toHaveBeenCalled();
});

it('opens with focus inside the dialog and traps Tab within it', async () => {
  await open();
  const focusables = [...w.document.querySelectorAll('[role="dialog"] input, [role="dialog"] textarea, [role="dialog"] button')]
    .filter(el => !el.disabled);
  expect(w.document.activeElement).toBe(focusables[0]);
  focusables[focusables.length - 1].focus();
  q('.modal-overlay').dispatchEvent(new w.KeyboardEvent('keydown', { key: 'Tab', bubbles: true }));
  expect(w.document.activeElement).toBe(focusables[0]);
  w.document.activeElement.dispatchEvent(new w.KeyboardEvent('keydown', { key: 'Tab', shiftKey: true, bubbles: true }));
});

it('closes on Escape without any mutation when the form is clean', async () => {
  await open();
  q('.modal-overlay').dispatchEvent(new w.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  await tick();
  expect(q('[role="dialog"]')).toBeNull();
  expect(w.ApiClient.patch).not.toHaveBeenCalled();
});

it('asks for confirmation on Escape when dirty, focuses the safe option, and stays open on cancel', async () => {
  await open();
  input('[name="notes"]', 'Niezapisane zmiany');
  q('.modal-overlay').dispatchEvent(new w.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  await tick();
  expect(q('[data-discard-confirm]')).not.toBeNull();
  expect(w.document.activeElement).toBe(q('[data-discard-back]'));
  q('[data-discard-back]').click();
  expect(q('[data-discard-confirm]')).toBeNull();
  expect(q('[role="dialog"]')).not.toBeNull();
  expect(w.ApiClient.patch).not.toHaveBeenCalled();
});

it('offers a retry when the initial load itself fails, without touching the list', async () => {
  w.ApiClient.get.mockImplementation(async () => { const err = new Error('down'); throw err; });
  await w.PartnerLeadDetails.open('LD-001', q('#opener'));
  expect(q('[role="dialog"]').textContent).toContain('Nie udało się wczytać aktualnych danych');
  expect(q('[data-detail-retry]')).not.toBeNull();
  w.ApiClient.get.mockImplementation(async url => url.endsWith('/comments') ? { comments: [] } : { lead: lead() });
  q('[data-detail-retry]').click(); await tick();
  expect(q('[data-detail-save]')).not.toBeNull();
});

it('re-enables the form and keeps the draft editable on a plain validation rejection', async () => {
  await open();
  input('[name="client_email"]', 'not-an-email');
  const badRequest = new Error('bad'); badRequest.status = 400;
  w.ApiClient.patch.mockRejectedValueOnce(badRequest);
  const readsBeforeSave = w.ApiClient.get.mock.calls.length;
  q('[data-detail-save]').click(); await tick();
  expect(q('[role="dialog"]').textContent).toContain('Sprawdź dane formularza');
  expect(q('[name="client_email"]').disabled).toBe(false);
  expect(q('[name="client_email"]').value).toBe('not-an-email');
  // A 400 is a form problem, not a stale read: no refetch, so the visible
  // input is still exactly what the partner typed, not a rebuilt draft box.
  expect(w.ApiClient.get.mock.calls.length).toBe(readsBeforeSave);
});

it('clears a stale character-limit error once the field is short enough on retry', async () => {
  await open();
  input('[name="notes"]', 'a'.repeat(5001));
  q('[data-detail-save]').click(); await tick();
  expect(q('[name="notes"]').getAttribute('aria-invalid')).toBe('true');
  input('[name="notes"]', 'krótka notatka');
  q('[data-detail-save]').click(); await tick();
  expect(q('[name="notes"]').getAttribute('aria-invalid')).toBeNull();
  expect(q('[name="notes"]').getAttribute('aria-describedby')).toBeNull();
  expect(w.ApiClient.patch).toHaveBeenCalledTimes(1);
});

it('loads dependencies before the controller on the actual partner page', () => {
  const html = readFileSync(path.join(root, 'pages/partner/leads.html'), 'utf8');
  const scripts = [...html.matchAll(/<script src="([^"]+)"/g)].filter((match) => match[1] !== "/locales.js").map(match => match[1]);
  expect(scripts.indexOf('../../js/lead-fields.js')).toBeGreaterThan(0);
  expect(scripts.indexOf('../../js/partner-lead-details.js')).toBeGreaterThan(scripts.indexOf('../../js/lead-fields.js'));
  expect(scripts.indexOf('../../js/partner.js')).toBeGreaterThan(scripts.indexOf('../../js/partner-lead-details.js'));
});

/* ── Readable layout and the queue ─────────────────────────────
   Lead details read as clear sections and include the queue information.

   The queue share and the projected commission both come from the API's own
   projection (api/leads.py::with_commission_projection) -- these pin that the
   modal RENDERS them and never multiplies a rate by a value itself. */

it('groups the facts into named sections instead of one flat run of paragraphs', async () => {
  await open();

  const titles = [...w.document.querySelectorAll('.detail-section-title')].map(el => el.textContent);
  expect(titles).toContain('Status zgłoszenia');
  expect(titles).toContain('Dane transakcji');
  expect(titles).toContain('Prowizja');
  expect(titles).toContain('Kontakt');
});

it('shows the queue position and the share it earns, as the API computed them', async () => {
  await open({ queue_position: 3, queue_share: 25 });

  const callout = q('.detail-callout');
  expect(callout.textContent).toContain('3');
  expect(callout.textContent).toContain('25%');
  expect(callout.textContent).toContain('zgłosił już ktoś przed Tobą');
});

it('says plainly that a first submission earns the full rate, rather than staying silent about the queue', async () => {
  await open({ queue_position: 1, queue_share: 100 });

  expect(q('.detail-callout').textContent).toContain('100%');
  expect(q('.detail-callout').className).not.toContain('is-warning');
});

it('leaves the queue box out entirely when the API sent no share to show', async () => {
  await open({ queue_share: undefined });

  expect(q('.detail-callout')).toBeNull();
});

it('labels an unlocked amount as projected and a recorded one as recorded', async () => {
  await open({ commission: null, commission_projected: 700 });
  expect(q('.detail-money').textContent).toContain('Przewidywana');

  await w.PartnerLeadDetails.open('LD-001', q('#opener'));
  await open({ commission: 700, commission_projected: 700 });
  expect(q('.detail-money').textContent).toContain('Naliczona');
});

it('never quotes a commission on a rejected submission, which can never earn one', async () => {
  // commission_projected exists at every stage, so printing it here would
  // promise a payment on a dead deal.
  await open({ registration_status: 'rejected', commission: null, commission_projected: 700 });

  expect(q('.detail-money')).toBeNull();
  const section = [...w.document.querySelectorAll('.detail-section')]
    .find(s => s.textContent.startsWith('Prowizja'));
  expect(section.textContent).toContain('Bez prowizji');
});

it('says where the money is once the producer has paid it out', async () => {
  await open({ commission: 700, commission_paid_at: '2026-09-13T08:00:00' });

  const section = [...w.document.querySelectorAll('.detail-section')]
    .find(s => s.textContent.startsWith('Prowizja'));
  expect(section.textContent).toContain('Wypłacona');
});

it('keeps a win the producer has not confirmed out of the payout-due wording', async () => {
  await open({ commission: null, commission_projected: 700, won_at: '2026-09-01T00:00:00' });

  const section = [...w.document.querySelectorAll('.detail-section')]
    .find(s => s.textContent.startsWith('Prowizja'));
  expect(section.textContent).toContain('czeka na potwierdzenie');
  expect(section.textContent).not.toContain('Czeka na wypłatę');
});
