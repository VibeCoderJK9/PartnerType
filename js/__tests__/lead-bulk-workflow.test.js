import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import path from 'node:path';
import { Window } from 'happy-dom';
import { afterEach, expect, it, vi } from 'vitest';
import { appStub } from "./helpers/copy.js";

const windows = [];
afterEach(() => { windows.splice(0).forEach(w => w.happyDOM.abort()); });

function setup(revisions = ['3', '4']) {
  const w = new Window({ url: 'http://localhost:8000/pages/admin/leads.html' });
  windows.push(w);
  w.document.body.dataset.page = 'admin-leads';
  w.document.body.innerHTML = `<div class="page-header"><div class="page-header-actions"></div></div>
    <div class="main-area"><table class="data-table"><thead><tr><th>Lead</th></tr></thead>
    <tbody id="leads-tbody">${revisions.map((r, i) => `<tr data-id="LD-${i}" ${r === null ? '' : `data-revision="${r}"`}><td>Client</td></tr>`).join('')}</tbody></table></div>`;
  w.App = appStub('pl');
  w.ApiClient = { post: vi.fn().mockResolvedValue({ ok: [{id: 'LD-0'}], error: [] }) };
  w.AdminLeads = { reload: vi.fn().mockResolvedValue() };
  w.showToast = vi.fn();
  vm.runInContext(readFileSync(path.resolve(import.meta.dirname, '../leads6.js'), 'utf8'), vm.createContext(w));
  w.document.dispatchEvent(new w.Event('DOMContentLoaded'));
  w.document.querySelectorAll('tbody .row-check').forEach(cb => {
    cb.checked = true;
    cb.dispatchEvent(new w.Event('change', { bubbles: true }));
  });
  return w;
}

it('sends displayed revisions and stable per-item keys on retry', async () => {
  const w = setup();
  w.ApiClient.post.mockRejectedValueOnce(new Error('network'));
  w.document.getElementById('bulk-accept-btn').click();
  await vi.waitFor(() => expect(w.showToast).toHaveBeenCalled());
  const first = w.ApiClient.post.mock.calls[0][1];
  expect(first.items.map(i => i.expected_revision)).toEqual([3, 4]);
  expect(first.items.every(i => typeof i.operation_id === 'string' && i.operation_id.length > 0)).toBe(true);
  w.document.getElementById('bulk-accept-btn').click();
  await vi.waitFor(() => expect(w.ApiClient.post).toHaveBeenCalledTimes(2));
  expect(w.ApiClient.post.mock.calls[1][1]).toEqual(first);
  await vi.waitFor(() => expect(w.AdminLeads.reload).toHaveBeenCalledTimes(1));
});

it('requires a reason before sending rejection', async () => {
  const w = setup();
  w.document.getElementById('bulk-reject-btn').click();
  expect(w.ApiClient.post).not.toHaveBeenCalled();
  const field = w.document.getElementById('bulk-reject-reason');
  const submit = w.document.getElementById('bulk-reject-confirm-btn');
  submit.click();
  expect(w.ApiClient.post).not.toHaveBeenCalled();
  field.value = '  Brak danych  ';
  submit.click();
  await vi.waitFor(() => expect(w.ApiClient.post).toHaveBeenCalled());
  expect(w.ApiClient.post.mock.calls[0][1].items.every(i => i.message === 'Brak danych')).toBe(true);
});

it('excludes rows without revision and shows a warning', async () => {
  const w = setup([null, '4']);
  w.document.getElementById('bulk-accept-btn').click();
  await vi.waitFor(() => expect(w.ApiClient.post).toHaveBeenCalled());
  expect(w.ApiClient.post.mock.calls[0][1].items.map(i => i.id)).toEqual(['LD-1']);
  expect(w.showToast.mock.calls.flat().join(' ')).toContain('Pominięto');
});

it('blocks double-click and displays partial failures as inert text', async () => {
  const w = setup();
  let finish;
  w.ApiClient.post.mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  w.document.getElementById('bulk-accept-btn').click();
  w.document.getElementById('bulk-accept-btn').click();
  expect(w.ApiClient.post).toHaveBeenCalledTimes(1);
  finish({ok: [{id: 'LD-0'}], error: [{id: 'LD-1', status:409, reason:'<img src=x onerror=evil()>'}]});
  await vi.waitFor(() => expect(w.AdminLeads.reload).toHaveBeenCalled());
  const result = w.document.getElementById('bulk-result');
  expect(result.textContent).toContain('LD-1');
  expect(result.textContent).toContain('<img');
  expect(result.querySelector('img')).toBeNull();
});

it('does not send an empty batch when every revision is unavailable', () => {
  const w = setup([null, '']);
  w.document.getElementById('bulk-accept-btn').click();
  expect(w.ApiClient.post).not.toHaveBeenCalled();
  expect(w.showToast).toHaveBeenCalled();
});

it('preserves a failed rejection draft and does not report a reload failure as a write failure', async () => {
  const w = setup();
  w.ApiClient.post.mockRejectedValueOnce(new Error('network'));
  w.document.getElementById('bulk-reject-btn').click();
  const field = w.document.getElementById('bulk-reject-reason');
  field.value = 'Brak danych';
  w.document.getElementById('bulk-reject-confirm-btn').click();
  await vi.waitFor(() => expect(w.showToast).toHaveBeenCalled());
  expect(field.value).toBe('Brak danych');
  w.AdminLeads.reload.mockRejectedValueOnce(new Error('reload failed'));
  w.document.getElementById('bulk-reject-confirm-btn').click();
  await vi.waitFor(() => expect(w.showToast.mock.calls.flat()).toContain('Wynik zapisany'));
  expect(w.ApiClient.post).toHaveBeenCalledTimes(2);
});
