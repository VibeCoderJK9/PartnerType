import { readFileSync } from "node:fs";
import path from "node:path";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { loadScript } from "./helpers/load-script.js";

const PROJECT_ROOT = path.resolve(import.meta.dirname, "..", "..");

loadScript("js/icons.js");
loadScript("js/audit.js");

const SVG_NS = "http://www.w3.org/2000/svg";

beforeEach(() => {
  document.body.innerHTML = "";
});

describe("Icons.el", () => {
  it("builds an SVG element in the SVG namespace", () => {
    const icon = Icons.el("star");
    expect(icon.namespaceURI).toBe(SVG_NS);
    expect(icon.tagName.toLowerCase()).toBe("svg");
  });

  it("matches the stroke convention the existing inline SVGs use", () => {
    const icon = Icons.el("star");
    expect(icon.getAttribute("viewBox")).toBe("0 0 24 24");
    expect(icon.getAttribute("fill")).toBe("none");
    expect(icon.getAttribute("stroke")).toBe("currentColor");
    expect(icon.getAttribute("stroke-width")).toBe("2");
  });

  it("defaults to 18px and honours an explicit size", () => {
    expect(Icons.el("star").getAttribute("width")).toBe("18");
    expect(Icons.el("star", { size: 32 }).getAttribute("height")).toBe("32");
  });

  it("carries the icon name and any extra class", () => {
    const icon = Icons.el("circle-check", { cls: "kpi-icon" });
    expect(icon.getAttribute("class")).toBe("icon icon-circle-check kpi-icon");
  });

  it("renders every shape from the source file", () => {
    // circle-check is a circle plus a path; a single-shape parse would miss one.
    const icon = Icons.el("circle-check");
    expect(icon.childNodes).toHaveLength(2);
    expect(icon.childNodes[0].tagName.toLowerCase()).toBe("circle");
    expect(icon.childNodes[1].tagName.toLowerCase()).toBe("path");
  });

  it("is hidden from screen readers by default", () => {
    const icon = Icons.el("star");
    expect(icon.getAttribute("aria-hidden")).toBe("true");
    expect(icon.querySelector("title")).toBeNull();
  });

  it("becomes a labelled image when given a title", () => {
    const icon = Icons.el("star", { title: "Poziom Gold" });
    expect(icon.getAttribute("role")).toBe("img");
    expect(icon.getAttribute("aria-hidden")).toBeNull();
    expect(icon.querySelector("title").textContent).toBe("Poziom Gold");
  });

  it("warns and returns null for an unknown name", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    expect(Icons.el("nie-ma-takiej")).toBeNull();
    expect(warn).toHaveBeenCalledWith("[icons] unknown icon: nie-ma-takiej");
    warn.mockRestore();
  });
});

describe("Icons.into", () => {
  it("replaces existing content with the icon", () => {
    const host = document.createElement("div");
    host.textContent = "💰";
    Icons.into(host, "banknote");
    expect(host.childNodes).toHaveLength(1);
    expect(host.firstChild.getAttribute("class")).toContain("icon-banknote");
  });

  it("ignores a missing target instead of throwing", () => {
    expect(() => Icons.into(null, "star")).not.toThrow();
  });

  it("leaves the target empty when the icon name is unknown", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const host = document.createElement("div");
    host.textContent = "stare";
    Icons.into(host, "nie-ma-takiej");
    expect(host.childNodes).toHaveLength(0);
    warn.mockRestore();
  });
});

describe("Icons.label", () => {
  it("puts the icon before the text", () => {
    const button = document.createElement("button");
    Icons.label(button, "send", "Wyślij");
    expect(button.childNodes).toHaveLength(2);
    expect(button.firstChild.tagName.toLowerCase()).toBe("svg");
    expect(button.lastChild.textContent).toBe("Wyślij");
  });

  it("never parses the label as markup", () => {
    // The label can carry a partner-supplied name, so it must stay text.
    const button = document.createElement("button");
    Icons.label(button, "send", "<img src=x onerror=alert(1)>");
    expect(button.querySelector("img")).toBeNull();
    expect(button.lastChild.textContent).toBe("<img src=x onerror=alert(1)>");
  });
});

describe("Icons.has", () => {
  it("reports which names are available", () => {
    expect(Icons.has("star")).toBe(true);
    expect(Icons.has("nie-ma-takiej")).toBe(false);
  });
});

describe("icon set covers what the interface asks for", () => {
  it("has an icon for every audit action", () => {
    // A name missing here renders the generic fallback and logs a warning, so
    // catch it in CI rather than in the audit log at Pitch Camp.
    const missing = Object.entries(ACTION_ICONS)
      .filter(([, meta]) => !Icons.has(meta.icon))
      .map(([action, meta]) => `${action} -> ${meta.icon}`);
    expect(missing).toEqual([]);
  });

  it("maps every audit action the backend writes", () => {
    // Keep js/audit.js in step with audit.log(...) calls in api/*.py.
    const sources = ["leads", "products", "settings_api", "partner_bonuses", "agent_api", "users", "tiers", "payouts"]
      .map((name) => path.join(PROJECT_ROOT, "api", `${name}.py`))
      .filter((file) => {
        try { readFileSync(file); return true; } catch { return false; }
      })
      .map((file) => readFileSync(file, "utf8"))
      .join("\n");
    const emitted = [...sources.matchAll(/audit\.log\(\s*[^,]+,\s*["']([a-z_]+\.[a-z_]+)["']/g)]
      .map((match) => match[1]);
    const unmapped = [...new Set(emitted)].filter((action) => !ACTION_ICONS[action]);
    expect(unmapped).toEqual([]);
  });

  it("generates every icon the controllers name", () => {
    const used = new Set();
    for (const file of ["js/audit.js", "js/analytics.js", "js/agent.js", "js/users.js",
      "js/admin-dashboard.js", "js/admin-leads.js", "js/comments.js", "js/commissions.js",
      "js/settings.js", "js/partner.js", "js/profile.js", "js/partner-earnings.js"]) {
      const source = readFileSync(path.join(PROJECT_ROOT, file), "utf8");
      for (const match of source.matchAll(/(?:Icons\.(?:el|into|label|markup)\(\s*[^,]+,\s*|icon: )'([a-z0-9-]+)'/g)) {
        used.add(match[1]);
      }
    }
    // Guard against the regex silently matching nothing and passing vacuously.
    expect(used.size).toBeGreaterThan(20);
    const missing = [...used].filter((name) => !Icons.has(name));
    expect(missing).toEqual([]);
  });
});
