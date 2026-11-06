import path from "node:path";
import vm from "node:vm";
import { readFileSync } from "node:fs";
import { Window } from "happy-dom";
import { describe, expect, it, vi } from "vitest";
import { appStub } from "./helpers/copy.js";

const PROJECT_ROOT = path.resolve(import.meta.dirname, "..", "..");

const BLOCK = {
  id: "inactive_partner",
  name_key: "block_inactive_name",
  description_key: "block_inactive_desc",
  enabled: false,
  config: { days: 30 },
  config_fields: [{ name: "days", label_key: "block_inactive_days", default: 30, min: 7, max: 180 }],
  events: [],
  scheduled: true,
};

function realm(blocks = [BLOCK]) {
  const window = new Window({ url: "http://localhost:8000/pages/admin/settings.html" });
  window.document.body.innerHTML = '<div id="blocks-list"></div>';
  const patch = vi.fn((url, body) => Promise.resolve({ block: { ...BLOCK, ...body } }));
  const post = vi.fn(() => Promise.resolve({ result: { checked: 4, reminded: 1 } }));
  window.ApiClient = { get: vi.fn().mockResolvedValue({ blocks }), patch, post };
  window.App = appStub("pl");
  window.showToast = vi.fn();
  const context = vm.createContext(window);
  vm.runInContext(readFileSync(path.join(PROJECT_ROOT, "js/admin-blocks.js"), "utf8"), context);
  return { window, patch, post };
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

describe("Settings › Bloki", () => {
  it("draws each block with its translated name, switch and number", async () => {
    const { window } = realm();
    await window.AdminBlocks.load();
    const panel = window.document.querySelector('[data-block-id="inactive_partner"]');
    expect(panel.textContent).toContain("Przypomnienie o nieaktywnym partnerze");
    expect(window.document.getElementById("block-inactive_partner-enabled").checked).toBe(false);
    expect(window.document.getElementById("block-inactive_partner-days").value).toBe("30");
  });

  it("cannot run a block that is off", async () => {
    const { window } = realm();
    await window.AdminBlocks.load();
    const run = [...window.document.querySelectorAll("button")].find((b) => b.textContent === "Sprawdź teraz");
    expect(run.disabled).toBe(true);
  });

  it("saves the switch and the number together, then allows a run", async () => {
    const { window, patch, post } = realm();
    await window.AdminBlocks.load();
    window.document.getElementById("block-inactive_partner-enabled").checked = true;
    window.document.getElementById("block-inactive_partner-days").value = "14";
    const buttons = [...window.document.querySelectorAll("button")];
    buttons.find((b) => b.textContent === "Zapisz").click();
    await flush();
    expect(patch).toHaveBeenCalledWith("/api/blocks/inactive_partner", { enabled: true, config: { days: 14 } });

    const run = buttons.find((b) => b.textContent === "Sprawdź teraz");
    expect(run.disabled).toBe(false);
    run.click();
    await flush();
    expect(post).toHaveBeenCalledWith("/api/blocks/inactive_partner/run", {});
    expect(window.document.querySelector('[data-block-id="inactive_partner"]').textContent)
      .toContain("Wysłane przypomnienia: 1");
  });

  it("renders server text as text, never as markup", async () => {
    const hostile = { ...BLOCK, name_key: '<img src=x onerror="window.__pwned=1">' };
    const { window } = realm([hostile]);
    await window.AdminBlocks.load();
    expect(window.document.querySelector("img")).toBeNull();
    expect(window.__pwned).toBeUndefined();
  });
});
