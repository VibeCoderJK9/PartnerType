#!/usr/bin/env node
//
// build-icons.mjs -- generates js/icons.js from the vendored Lucide set.
//
// Only the icons listed in MANIFEST are emitted, so the browser downloads ~30
// icons instead of the 2050 that ship in lucide-static. lucide-static stays a
// devDependency: nothing from node_modules is served to the browser.
//
// Run after adding a name to MANIFEST:
//   node scripts/build-icons.mjs
//
// The generated js/icons.js is committed.

import { readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');
const ICON_SOURCE = path.join(ROOT, 'node_modules', 'lucide-static', 'icons');
const OUTPUT = path.join(ROOT, 'js', 'icons.js');

// Every icon the interface actually uses. Grouped by where it came from so an
// unused entry is easy to spot when a screen is reworked.
const MANIFEST = [
  // audit log actions (js/audit.js)
  'clipboard-list', 'circle-check', 'circle-x', 'check-check', 'pencil',
  'banknote', 'message-square', 'trash-2', 'key', 'log-out', 'user-plus',
  'lock', 'settings', 'shuffle', 'star', 'gift', 'download',
  // KPI tiles and analytics (js/analytics.js, dashboards)
  'bar-chart-3', 'handshake', 'trending-up', 'percent',
  // assistant panel (js/agent.js)
  'sparkles', 'users', 'plus', 'triangle-alert',
  // sidebar: Pluginy and Baza wiedzy
  'puzzle', 'book-open',
  // controls: sorting, paging, closing, sending, validation
  'chevron-up', 'chevron-down', 'chevron-left', 'chevron-right',
  'x', 'check', 'send', 'arrow-right', 'more-horizontal',
  // user row actions (js/users.js)
  'ban', 'rotate-ccw',
  // remaining audit actions: catalogue, bonuses, assistant
  'package', 'package-plus', 'circle-minus', 'bot', 'shield-alert',
  // lead row actions (js/admin-leads.js)
  'eye',
  // form controls (js/dropdown.js, js/datepicker.js)
  'calendar',
  // partner kanban drag handle (js/partner.js)
  'grip-vertical',
  // sidebar navigation, one distinct icon per entry (js/app.js hydrateNavIcons)
  'layout-dashboard', 'coins', 'history', 'circle-user', 'circle-plus', 'wallet', 'link',
];

// Attributes carried over from the source file. Everything else (class, xmlns,
// width, height) is set by js/icons.js at build time instead.
const SHAPE_PATTERN = /<(path|circle|rect|line|polyline|polygon|ellipse)\b([^>]*?)\/>/g;
const ATTRIBUTE_PATTERN = /([a-zA-Z-]+)="([^"]*)"/g;

function parseShapes(svg, name) {
  const shapes = [];
  for (const match of svg.matchAll(SHAPE_PATTERN)) {
    const [, tag, rawAttributes] = match;
    const attributes = {};
    for (const attribute of rawAttributes.matchAll(ATTRIBUTE_PATTERN)) {
      attributes[attribute[1]] = attribute[2];
    }
    shapes.push([tag, attributes]);
  }
  if (shapes.length === 0) {
    throw new Error(`No shapes parsed from ${name}.svg -- has the lucide-static format changed?`);
  }
  return shapes;
}

async function main() {
  const icons = {};
  for (const name of [...MANIFEST].sort()) {
    const file = path.join(ICON_SOURCE, `${name}.svg`);
    let svg;
    try {
      svg = await readFile(file, 'utf8');
    } catch (error) {
      if (error.code === 'ENOENT') {
        throw new Error(`Icon "${name}" is not in lucide-static. Check the name at lucide.dev/icons.`);
      }
      throw error;
    }
    icons[name] = parseShapes(svg, name);
  }

  const version = JSON.parse(
    await readFile(path.join(ROOT, 'node_modules', 'lucide-static', 'package.json'), 'utf8'),
  ).version;

  const entries = Object.entries(icons)
    .map(([name, shapes]) => `  ${JSON.stringify(name)}: ${JSON.stringify(shapes)},`)
    .join('\n');

  await writeFile(OUTPUT, template(version, entries), 'utf8');
  console.log(`js/icons.js -- ${Object.keys(icons).length} icons from lucide-static ${version}`);
}

function template(version, entries) {
  return `/* ============================================================
   PARTNERTYPE -- ICON SET
   ============================================================
   GENERATED FILE -- DO NOT EDIT BY HAND.
   Source: lucide-static ${version} (ISC). Regenerate with:
       node scripts/build-icons.mjs
   Add the icon name to MANIFEST in that script first.

   Icons are built with createElementNS rather than innerHTML so that
   scripts/security-sink-audit.mjs stays clean and icon markup can never be
   concatenated with user data.
   ============================================================ */

(function (global) {
  'use strict';

  var SVG_NS = 'http://www.w3.org/2000/svg';

  // name -> [[tagName, { attribute: value }], ...]
  var ICON_SHAPES = {
${entries}
  };

  /**
   * Build an icon as a real SVG element.
   * @param {string} name  Lucide icon name, e.g. 'circle-check'.
   * @param {{ size?: number, cls?: string, title?: string }} [options]
   * @returns {SVGElement|null} null when the name is unknown.
   */
  function el(name, options) {
    var shapes = ICON_SHAPES[name];
    if (!shapes) {
      console.warn('[icons] unknown icon: ' + name);
      return null;
    }
    var opts = options || {};
    var size = opts.size || 18;

    var svg = document.createElementNS(SVG_NS, 'svg');
    svg.setAttribute('width', String(size));
    svg.setAttribute('height', String(size));
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('fill', 'none');
    svg.setAttribute('stroke', 'currentColor');
    svg.setAttribute('stroke-width', '2');
    svg.setAttribute('stroke-linecap', 'round');
    svg.setAttribute('stroke-linejoin', 'round');
    svg.setAttribute('class', ('icon icon-' + name + ' ' + (opts.cls || '')).trim());

    // Decorative unless it carries its own label: screen readers already get
    // the adjacent text.
    if (opts.title) {
      var title = document.createElementNS(SVG_NS, 'title');
      title.textContent = opts.title;
      svg.appendChild(title);
      svg.setAttribute('role', 'img');
    } else {
      svg.setAttribute('aria-hidden', 'true');
    }

    for (var i = 0; i < shapes.length; i++) {
      var node = document.createElementNS(SVG_NS, shapes[i][0]);
      var attributes = shapes[i][1];
      for (var key in attributes) {
        if (Object.prototype.hasOwnProperty.call(attributes, key)) {
          node.setAttribute(key, attributes[key]);
        }
      }
      svg.appendChild(node);
    }
    return svg;
  }

  /**
   * Replace an element's content with a single icon.
   * @param {Element} target
   * @param {string} name
   * @param {{ size?: number, cls?: string, title?: string }} [options]
   */
  function into(target, name, options) {
    if (!target) return;
    while (target.firstChild) target.removeChild(target.firstChild);
    var icon = el(name, options);
    if (icon) target.appendChild(icon);
  }

  /**
   * Put an icon before an element's existing text, e.g. inside a button.
   * The text stays a separate text node, so it is never re-parsed as markup.
   * @param {Element} target
   * @param {string} name
   * @param {string} text
   * @param {{ size?: number, cls?: string, title?: string }} [options]
   */
  function label(target, name, text, options) {
    if (!target) return;
    while (target.firstChild) target.removeChild(target.firstChild);
    var icon = el(name, options);
    if (icon) target.appendChild(icon);
    var span = document.createElement('span');
    span.textContent = text;
    target.appendChild(span);
  }

  /**
   * Static markup for the design canvas and other generated HTML.
   * Never use this to inject anything derived from user input.
   * @param {string} name
   * @param {{ size?: number, cls?: string }} [options]
   * @returns {string}
   */
  function markup(name, options) {
    var icon = el(name, options);
    return icon ? icon.outerHTML : '';
  }

  function has(name) {
    return Object.prototype.hasOwnProperty.call(ICON_SHAPES, name);
  }

  global.Icons = { el: el, into: into, label: label, markup: markup, has: has };
})(typeof window !== 'undefined' ? window : globalThis);
`;
}

main().catch((error) => {
  console.error(error.message);
  process.exitCode = 1;
});
