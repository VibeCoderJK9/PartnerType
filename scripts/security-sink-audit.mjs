#!/usr/bin/env node

import { createHash } from 'node:crypto';
import { readFile, readdir } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const EXCLUDED_SEGMENTS = new Set([
  'node_modules', 'coverage', 'dist', 'build', 'vendor', '__tests__',
  'test', 'tests', 'fixtures', '__fixtures__',
]);

const DETECTORS = [
  ['inner-html', /\.innerHTML\s*(?:\+?=)/],
  ['dynamic-event-handler', /(?:\bon[a-z]+\s*=|\.on[a-z]+\s*=|setAttribute\s*\(\s*['"]on[a-z]+['"]|\bnew\s+Function\s*\()/i],
  ['unsafe-navigation-scheme', /(?:(?:href|src|action)\s*=|(?:location(?:\.href)?|window\.open\s*\()\s*)[^\n]*(?:javascript|data)\s*:/i],
  ['interpolated-url-style', /(?:(?:href|src|style)\s*=\s*["'][^"']*\$\{|\burl\s*\([^)]*\$\{|(?:location(?:\.href)?|window\.open)\b[^\n]*`[^`]*\$\{)/i],
];

function normalizeSnippet(line) {
  return line.trim().replace(/\s+/g, ' ').slice(0, 240);
}

function snippetHash(pathName, kind, snippet) {
  return createHash('sha256')
    .update(`${pathName}\0${kind}\0${snippet}`)
    .digest('hex')
    .slice(0, 16);
}

function isExcluded(relativePath) {
  return relativePath.split('/').some((segment) => EXCLUDED_SEGMENTS.has(segment));
}

async function walk(root, relativeDirectory, extension, files) {
  const absoluteDirectory = path.join(root, relativeDirectory);
  let entries;
  try {
    entries = await readdir(absoluteDirectory, { withFileTypes: true });
  } catch (error) {
    if (error.code === 'ENOENT') return;
    throw error;
  }
  entries.sort((left, right) => left.name.localeCompare(right.name));
  for (const entry of entries) {
    const relativePath = path.posix.join(relativeDirectory, entry.name);
    if (isExcluded(relativePath)) continue;
    if (entry.isDirectory()) await walk(root, relativePath, extension, files);
    else if (entry.isFile() && entry.name.endsWith(extension)) files.push(relativePath);
  }
}

async function productionFiles(root) {
  const files = [];
  const rootEntries = await readdir(root, { withFileTypes: true });
  for (const entry of rootEntries.sort((left, right) => left.name.localeCompare(right.name))) {
    if (entry.isFile() && entry.name.endsWith('.html')) files.push(entry.name);
  }
  await walk(root, 'pages', '.html', files);
  await walk(root, 'js', '.js', files);
  return [...new Set(files)].sort();
}

function lineFindings(pathName, line, lineNumber) {
  const snippet = normalizeSnippet(line);
  if (!snippet) return [];
  const findings = [];
  for (const [kind, detector] of DETECTORS) {
    if (!detector.test(line)) continue;
    findings.push({
      path: pathName,
      line: lineNumber,
      kind,
      snippet,
      snippet_hash: snippetHash(pathName, kind, snippet),
    });
  }
  return findings;
}

export async function scanRepository(root = process.cwd()) {
  const findings = [];
  for (const relativePath of await productionFiles(root)) {
    const source = await readFile(path.join(root, relativePath), 'utf8');
    source.split(/\r?\n/).forEach((line, index) => {
      findings.push(...lineFindings(relativePath, line, index + 1));
    });
  }
  const ordered = findings.sort((left, right) =>
    left.path.localeCompare(right.path)
      || left.line - right.line
      || left.kind.localeCompare(right.kind)
      || left.snippet_hash.localeCompare(right.snippet_hash));
  const unique = new Map();
  for (const finding of ordered) {
    const key = findingKey(finding);
    if (!unique.has(key)) unique.set(key, finding);
  }
  return [...unique.values()];
}

function findingKey(finding) {
  return `${finding.path}\0${finding.kind}\0${finding.snippet_hash}`;
}

export async function auditRepository({ root = process.cwd(), mode = 'progress', allowlist }) {
  if (!['progress', 'final'].includes(mode)) {
    throw new TypeError(`Unsupported mode: ${mode}`);
  }
  const findings = await scanRepository(root);
  const entries = Array.isArray(allowlist?.findings) ? allowlist.findings : [];
  const errors = [];
  if (allowlist?.version !== 1) errors.push('Allowlist version must be 1.');

  const liveByKey = new Map(findings.map((finding) => [findingKey(finding), finding]));
  const allowByKey = new Map();
  for (const entry of entries) {
    const key = findingKey(entry);
    if (allowByKey.has(key)) errors.push(`Duplicate allowlist entry: ${entry.path} ${entry.kind} ${entry.snippet_hash}`);
    allowByKey.set(key, entry);
    if (!entry.rationale || typeof entry.rationale !== 'string') {
      errors.push(`Allowlist rationale missing: ${entry.path} ${entry.kind} ${entry.snippet_hash}`);
    }
    if (!['trusted-static-markup', 'planned-remediation'].includes(entry.disposition)) {
      errors.push(`Invalid disposition: ${entry.path} ${entry.kind} ${entry.snippet_hash}`);
    }
    if (entry.disposition === 'planned-remediation' && !/^02-\d{2}$/.test(entry.owner_plan || '')) {
      errors.push(`Planned remediation must name an owner plan (NN-NN): ${entry.path} ${entry.kind} ${entry.snippet_hash}`);
    }
  }

  for (const finding of findings) {
    if (!allowByKey.has(findingKey(finding))) {
      errors.push(`Unknown finding: ${finding.path}:${finding.line} ${finding.kind} ${finding.snippet_hash}`);
    }
  }
  for (const entry of entries) {
    if (!liveByKey.has(findingKey(entry))) {
      errors.push(`Stale allowlist entry: ${entry.path} ${entry.kind} ${entry.snippet_hash}`);
    } else if (mode === 'final' && entry.disposition === 'planned-remediation') {
      errors.push(`Planned remediation is not permitted in final mode: ${entry.path} ${entry.kind} ${entry.snippet_hash}`);
    }
  }

  return { ok: errors.length === 0, mode, files_scanned: (await productionFiles(root)).length, findings, errors };
}

async function runCli() {
  const args = process.argv.slice(2);
  const valueAfter = (flag, fallback) => {
    const index = args.indexOf(flag);
    return index >= 0 ? args[index + 1] : fallback;
  };
  const root = path.resolve(valueAfter('--root', process.cwd()));
  const mode = valueAfter('--mode', 'progress');
  const allowlistPath = path.resolve(root, valueAfter('--allowlist', 'security-sink-allowlist.json'));
  const allowlist = JSON.parse(await readFile(allowlistPath, 'utf8'));
  const result = await auditRepository({ root, mode, allowlist });
  if (args.includes('--json')) console.log(JSON.stringify(result, null, 2));
  else {
    console.log(`Security sink audit (${mode}): ${result.files_scanned} files, ${result.findings.length} findings`);
    for (const error of result.errors) console.error(`ERROR: ${error}`);
  }
  if (!result.ok) process.exitCode = 1;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  await runCli();
}
