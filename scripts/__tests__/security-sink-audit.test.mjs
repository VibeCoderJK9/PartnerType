import assert from 'node:assert/strict';
import { mkdtemp, mkdir, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import test from 'node:test';

import { auditRepository, scanRepository } from '../security-sink-audit.mjs';

async function fixture(files) {
  const root = await mkdtemp(path.join(tmpdir(), 'partnerzy-sink-audit-'));
  for (const [relativePath, contents] of Object.entries(files)) {
    const target = path.join(root, relativePath);
    await mkdir(path.dirname(target), { recursive: true });
    await writeFile(target, contents, 'utf8');
  }
  return root;
}

function reviewed(finding, disposition = 'trusted-static-markup') {
  return {
    path: finding.path,
    kind: finding.kind,
    snippet_hash: finding.snippet_hash,
    rationale: 'Reviewed by the test fixture.',
    disposition,
    ...(disposition === 'planned-remediation' ? { owner_plan: '02-06' } : {}),
  };
}

test('discovers a sink in a repository-root register.html', async () => {
  const root = await fixture({
    'register.html': '<div id="result"></div><script>result.innerHTML = value;</script>',
    'pages/admin/clean.html': '<p>safe</p>',
    'js/clean.js': 'node.textContent = value;',
    'js/__tests__/ignored.js': 'node.innerHTML = value;',
  });

  const findings = await scanRepository(root);

  assert.equal(findings.length, 1);
  assert.equal(findings[0].path, 'register.html');
  assert.equal(findings[0].line, 1);
  assert.equal(findings[0].kind, 'inner-html');
  assert.match(findings[0].snippet_hash, /^[a-f0-9]{16}$/);
});

test('produces a deterministic inventory independent of filesystem creation order', async () => {
  const first = await fixture({
    'pages/z.html': '<a href="javascript:alert(1)">x</a>',
    'index.html': '<button onclick="go()">go</button>',
  });
  const second = await fixture({
    'index.html': '<button onclick="go()">go</button>',
    'pages/z.html': '<a href="javascript:alert(1)">x</a>',
  });

  assert.deepEqual(await scanRepository(first), await scanRepository(second));
});

test('final mode fails for an unknown finding', async () => {
  const root = await fixture({ 'index.html': '<button onclick="go()">go</button>' });

  const result = await auditRepository({ root, mode: 'final', allowlist: { version: 1, findings: [] } });

  assert.equal(result.ok, false);
  assert.match(result.errors.join('\n'), /unknown finding/i);
});

test('final mode fails for a stale allowlist entry', async () => {
  const root = await fixture({ 'index.html': '<p>safe</p>' });
  const stale = {
    path: 'index.html',
    kind: 'inner-html',
    snippet_hash: '0123456789abcdef',
    rationale: 'Was previously reviewed.',
    disposition: 'trusted-static-markup',
  };

  const result = await auditRepository({ root, mode: 'final', allowlist: { version: 1, findings: [stale] } });

  assert.equal(result.ok, false);
  assert.match(result.errors.join('\n'), /stale allowlist/i);
});

test('planned remediation is accepted only in progress mode', async () => {
  const root = await fixture({ 'register.html': '<div onclick="go()">go</div>' });
  const [finding] = await scanRepository(root);
  const allowlist = { version: 1, findings: [reviewed(finding, 'planned-remediation')] };

  const progress = await auditRepository({ root, mode: 'progress', allowlist });
  const final = await auditRepository({ root, mode: 'final', allowlist });

  assert.equal(progress.ok, true);
  assert.equal(final.ok, false);
  assert.match(final.errors.join('\n'), /planned remediation/i);
});
