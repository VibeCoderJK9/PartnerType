import { readFileSync } from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { Window } from "happy-dom";
import { describe, expect, it, vi } from "vitest";

const PROJECT_ROOT = path.resolve(import.meta.dirname, "..", "..");

function orderedScripts(pagePath) {
  const html = readFileSync(path.join(PROJECT_ROOT, pagePath), "utf8");
  return [...html.matchAll(/<script\s+src="([^"]+)"/g)].filter((match) => match[1] !== "/locales.js").map((match) =>
    path.normalize(path.join(path.dirname(pagePath), match[1])),
  );
}

function executePageScripts(pagePath) {
  const window = new Window({ url: `http://localhost:8000/${pagePath}` });
  window.fetch = vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    json: async () => ({ user: { id: 1, role: "admin" } }),
  });
  window.console = console;
  const context = vm.createContext(window);
  for (const scriptPath of orderedScripts(pagePath)) {
    const source = readFileSync(path.join(PROJECT_ROOT, scriptPath), "utf8");
    vm.runInContext(source, context, { filename: scriptPath });
  }
  window.document.dispatchEvent(new window.Event("DOMContentLoaded"));
  return window;
}

describe.each([
  "pages/admin/dashboard.html",
  "pages/partner/dashboard.html",
])("cookie session entrypoint %s", (pagePath) => {
  it("loads the actual ordered scripts without removed token APIs", () => {
    const scripts = orderedScripts(pagePath);
    expect(scripts).toContain("js/api.js");
    expect(scripts).toContain("js/notifications.js");
    executePageScripts(pagePath);
  });
});
