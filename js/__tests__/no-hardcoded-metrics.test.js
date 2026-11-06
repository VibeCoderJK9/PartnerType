import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

/*
 * no-hardcoded-metrics: a KPI/stat/metric card must never carry a typed-in
 * number in its markup. This class of defect shipped twice before this
 * guard existed -- the dashboard trend badges ("12%", "5%", "18%") in
 * pages/admin/dashboard.html, and the Partners KPI row ("94", "69",
 * "158 200 PLN" in pages/admin/partners.html) both rendered the same fixed
 * figures whether the database held zero rows or thousands. Every element
 * whose class contains kpi-value, stat-value or metric-value must instead
 * carry an id or a data-kpi-metric attribute that a controller binds at
 * runtime, and its literal markup text must contain no decimal digit --
 * only an em dash, whitespace or an empty string are allowed as the
 * not-yet-loaded placeholder.
 */

const PROJECT_ROOT = path.resolve(import.meta.dirname, "..", "..");
const METRIC_CLASS_NAMES = ["kpi-value", "stat-value", "metric-value"];

// Matches an opening <div> tag whose class attribute contains one of the
// three metric class names, capturing the tag's full attribute text and the
// literal text up to the next opening angle bracket. These cards are leaf
// nodes in every screen that has them today (no nested markup inside), so
// "text up to the next <" is exactly the rendered placeholder or value.
const METRIC_CLASS_PATTERN = METRIC_CLASS_NAMES.join("|");
const METRIC_ELEMENT_RE = new RegExp(
  `<div\\b([^>]*\\bclass="[^"]*(?:${METRIC_CLASS_PATTERN})[^"]*"[^>]*)>([^<]*)<`,
  "g",
);

function findMetricElements(html) {
  const elements = [];
  let match;
  const re = new RegExp(METRIC_ELEMENT_RE.source, METRIC_ELEMENT_RE.flags);
  while ((match = re.exec(html)) !== null) {
    elements.push({ attrs: match[1], text: match[2], fullMatch: match[0] });
  }
  return elements;
}

/**
 * The one rule this whole guard enforces, exported as a small helper so the
 * synthetic negative cases below can call the exact function real files are
 * checked with -- proving the guard can actually fail, not just pass.
 */
function checkMetricElement({ attrs, text }) {
  const violations = [];
  const hasId = /\bid="[^"]*"/.test(attrs);
  const hasDataKpiMetric = /\bdata-kpi-metric="[^"]*"/.test(attrs);
  if (!hasId && !hasDataKpiMetric) {
    violations.push("carries neither an id nor a data-kpi-metric attribute");
  }
  if (/[0-9]/.test(text)) {
    violations.push(`literal text "${text.trim()}" contains a decimal digit`);
  }
  return violations;
}

function collectHtmlFilesRecursively(dir) {
  const files = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      files.push(...collectHtmlFilesRecursively(full));
    } else if (entry.isFile() && entry.name.endsWith(".html")) {
      files.push(full);
    }
  }
  return files;
}

function allHtmlFiles() {
  const files = collectHtmlFilesRecursively(path.join(PROJECT_ROOT, "pages"));
  files.push(path.join(PROJECT_ROOT, "index.html"));
  files.push(path.join(PROJECT_ROOT, "register.html"));
  return files;
}

describe("no-hardcoded-metrics guard", () => {
  it("collects a non-empty list of pages, so a broken glob fails loudly", () => {
    const files = allHtmlFiles();
    expect(files.length).toBeGreaterThan(0);
  });

  it("requires every kpi-value / stat-value / metric-value element to carry an id or data-kpi-metric, with no digit in its literal text", () => {
    const failures = [];
    for (const file of allHtmlFiles()) {
      const html = readFileSync(file, "utf8");
      const relative = path.relative(PROJECT_ROOT, file);
      for (const element of findMetricElements(html)) {
        for (const violation of checkMetricElement(element)) {
          failures.push(`${relative}: <div${element.attrs}> -- ${violation}`);
        }
      }
    }
    expect(failures).toEqual([]);
  });

  it("rejects a synthetic metric element with no id and no data-kpi-metric attribute", () => {
    const [element] = findMetricElements('<div class="kpi-value">—</div>');
    expect(checkMetricElement(element)).toContain("carries neither an id nor a data-kpi-metric attribute");
  });

  it("rejects a synthetic metric element whose literal text carries a digit", () => {
    const [element] = findMetricElements('<div class="kpi-value" id="kpi-x">94</div>');
    expect(checkMetricElement(element)[0]).toMatch(/contains a decimal digit/);
  });

  it("accepts an em dash, whitespace and an empty string as the not-yet-loaded placeholder", () => {
    const dash = findMetricElements('<div class="kpi-value" id="kpi-a">—</div>')[0];
    const whitespace = findMetricElements('<div class="stat-value" data-kpi-metric="x">  </div>')[0];
    const empty = findMetricElements('<div class="metric-value" id="kpi-b"></div>')[0];
    expect(checkMetricElement(dash)).toEqual([]);
    expect(checkMetricElement(whitespace)).toEqual([]);
    expect(checkMetricElement(empty)).toEqual([]);
  });
});
