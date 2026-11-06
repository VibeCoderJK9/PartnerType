import { readFileSync } from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { Window } from "happy-dom";
import { describe, expect, it, vi } from "vitest";
import { appStub } from "./helpers/copy.js";

const PROJECT_ROOT = path.resolve(import.meta.dirname, "..", "..");

function runClassicScript(window, relativePath) {
  const source = readFileSync(path.join(PROJECT_ROOT, relativePath), "utf8");
  const context = window.__vmContext || (window.__vmContext = vm.createContext(window));
  vm.runInContext(source, context, { filename: relativePath });
}

function invoke(window, source) {
  return vm.runInContext(source, window.__vmContext);
}

function orderedScripts(pagePath) {
  const html = readFileSync(path.join(PROJECT_ROOT, pagePath), "utf8");
  return [...html.matchAll(/<script\s+src="([^"]+)"/g)].filter((match) => match[1] !== "/locales.js").map((match) =>
    path.normalize(path.join(path.dirname(pagePath), match[1])),
  );
}

function commonWindow(page) {
  const window = new Window({ url: `http://localhost/pages/admin/${page}.html` });
  window.ApiClient = {
    get: vi.fn().mockResolvedValue({ bonuses: [], tiers: [], partners: [] }),
    post: vi.fn().mockResolvedValue({ partner: {} }),
    patch: vi.fn().mockResolvedValue({}),
    delete: vi.fn().mockResolvedValue({ partner: {} }),
  };
  window.App = appStub("pl");
  window.Ui = {
    escapeHtml: (value) => String(value ?? "")
      .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
      .replaceAll("'", "&#x27;").replaceAll('"', "&quot;"),
    // The producer's own label, read the way js/utils.js reads it: his English
    // column when the reader is English and he filled it in, his Polish
    // original otherwise. This screen must escape BOTH -- a payload hidden in
    // name_en would otherwise reach the DOM untested.
    producerLabel: (record, field) => {
      if (!record) return "";
      const base = String(record[field] ?? "");
      if (window.App.lang !== "en") return base;
      return String(record[`${field}_en`] ?? "").trim() || base;
    },
    // Mirrors js/utils.js — settings.js counts partners per tier through it.
    plPlural: (count, one, few, many) => {
      const n = Math.abs(Number(count));
      if (!Number.isFinite(n)) return many;
      if (n === 1) return one;
      const lastTwo = n % 100;
      if (lastTwo >= 12 && lastTwo <= 14) return many;
      const last = n % 10;
      return last >= 2 && last <= 4 ? few : many;
    },
  };
  window.getInitials = vi.fn((value) => String(value));
  window.showToast = vi.fn();
  window.handleApiError = vi.fn();
  window.confirm = vi.fn(() => true);
  return window;
}

describe("commission editor output and actions", () => {
  it("keeps partner, tier and bonus payloads out of elements, handlers and styles", async () => {
    const payload = '<img src=x onerror="window.__commercePwned=true">';
    const window = commonWindow("commissions");
    window.document.body.innerHTML = `
      <table><tbody id="partner-comm-tbody"></tbody></table>
      <input id="partner-select-all" type="checkbox">
      <div id="bonus-percent-grid"></div><button id="add-percent-btn"></button><span id="count-percent-btn"></span>
      <div id="panel-partner-name"></div><div id="panel-partner-sub"></div>
      <div id="commission-summary-box"></div><select id="panel-tier-select"></select>
      <div id="bonus-toggles-container"></div><div id="slide-overlay"></div><div id="slide-panel"></div>
    `;
    runClassicScript(window, "js/commissions.js");
    invoke(window, `state.partners = [${JSON.stringify({
      id: 17,
      name: payload,
      company: payload,
      color: "red;background:url(javascript:window.__commercePwned=true)",
      tier_name: payload,
      base_rate: 10,
      bonus_percent: 1,
      bonus_cash: 0,
      total_percent: 11,
      active_bonus_count: 1,
    })}]; state.tiers = [${JSON.stringify({ id: 3, name: payload, base_rate: 5 })}]; state.bonusDefs = [${JSON.stringify({
      id: 8, name: payload, description: payload, type: "percent", default_value: 1, active: true,
    })}]; renderPartnerTable(); renderBonusDefs();`);

    const partner = {
      id: 17, name: payload, company: payload, tier_id: 3, tier_name: payload,
      base_rate: 10, total_percent: 11, total_cash: 0,
      bonus_percent: [{ bonus_def_id: 8, name: payload, effective_value: 1, custom_value: 1 }],
      bonus_cash: [],
    };
    invoke(window, `renderSlidePanel(${JSON.stringify(partner)})`);

    expect(window.document.querySelector('[data-field="name"]').value).toBe(payload);
    expect(window.document.querySelector("img, script, [onclick], [onchange], [oninput], [onerror]")).toBeNull();
    expect(window.document.body.getAttribute("style") || "").not.toContain("javascript:");
    expect(window.__commercePwned).toBeUndefined();

    window.ApiClient.get.mockResolvedValueOnce({ partner });
    window.document.querySelector('[data-commission-action="open-partner"]').click();
    await vi.waitFor(() => expect(window.ApiClient.get).toHaveBeenCalledWith("/api/partner-bonuses/17"));
  });

  it("delegates bonus save and delete with the original numeric identifier", async () => {
    const window = commonWindow("commissions");
    window.document.body.innerHTML = '<div id="bonus-percent-grid"></div><button id="add-percent-btn"></button><span id="count-percent-btn"></span>';
    runClassicScript(window, "js/commissions.js");
    invoke(window, `state.bonusDefs = [{ id: 8, name: "Webinar", description: "Opis", type: "percent", default_value: 2 }]; renderBonusDefs()`);

    window.document.querySelector('[data-commission-action="save-bonus"]').click();
    await vi.waitFor(() => expect(window.ApiClient.patch).toHaveBeenCalledTimes(1));
    expect(window.ApiClient.patch).toHaveBeenCalledWith("/api/bonus-defs/8", expect.objectContaining({ name: "Webinar" }));

    window.document.querySelector('[data-commission-action="delete-bonus"]').click();
    await vi.waitFor(() => expect(window.ApiClient.delete).toHaveBeenCalledWith("/api/bonus-defs/8"));
  });
});

describe("settings editor output and actions", () => {
  it("renders malicious tier and bonus fields inertly and delegates editor actions", async () => {
    const payload = '<svg onload="window.__settingsPwned=true">';
    const window = commonWindow("settings");
    window.document.body.innerHTML = `
      <div id="tiers-grid"></div><button id="add-tier-btn"></button>
      <div id="bonus-percent-grid"></div><button id="add-percent-btn"></button><span id="count-percent-btn"></span>
      <div id="bonus-cash-grid"></div><button id="add-cash-btn"></button><span id="count-cash-btn"></span>
    `;
    runClassicScript(window, "js/settings.js");
    invoke(window, `state.tiers = [${JSON.stringify({ id: 4, name: payload, base_rate: 5, color: "red;background:url(javascript:alert(1))", active: true })}]; state.bonusDefs = [${JSON.stringify({ id: 9, name: payload, description: payload, default_value: 2, type: "percent" })}]; renderTiers(); renderBonuses()`);

    expect(window.document.querySelector('[data-field="name"]').value).toBe(payload);
    expect(window.document.querySelector("img, script, [onclick], [onload]")).toBeNull();
    expect(window.document.querySelector('[data-field="color"]').value).toBe("#da9348");
    expect(window.__settingsPwned).toBeUndefined();

    window.document.querySelector('[data-settings-action="save-tier"]').click();
    await vi.waitFor(() => expect(window.ApiClient.patch).toHaveBeenCalledTimes(1));
    expect(window.ApiClient.patch).toHaveBeenCalledWith("/api/tiers/4", expect.objectContaining({ name: payload }));

    window.document.querySelector('[data-settings-action="delete-bonus"]').click();
    await vi.waitFor(() => expect(window.ApiClient.delete).toHaveBeenCalledWith("/api/bonus-defs/9"));
  });
});

describe("commerce entry points", () => {
  it("loads utils immediately after app without disturbing specialized scripts", () => {
    expect(orderedScripts("pages/admin/commissions.html")).toEqual([
      "js/theme-boot.js", "js/icons.js", "js/dropdown.js", "js/datepicker.js", "js/api.js", "js/app.js", "js/utils.js", "js/admin-shell.js", "js/notifications.js", "js/commissions.js", "js/agent.js", "js/branding.js"]);
    expect(orderedScripts("pages/admin/settings.html")).toEqual([
      // js/admin-products.js loads the Products tab's catalogue controller,
      // right after admin-shell.js and before settings.js; js/admin-blocks.js
      // draws the Blocks tab right after it, and js/admin-integrations.js the
      // Integrations tab after that.
      "js/theme-boot.js", "js/icons.js", "js/dropdown.js", "js/datepicker.js", "js/api.js", "js/app.js", "js/utils.js", "js/admin-shell.js", "js/admin-products.js", "js/admin-blocks.js", "js/admin-integrations.js", "js/charts.js", "js/notifications.js", "js/settings.js", "js/agent.js", "js/branding.js",
    ]);
  });
});
