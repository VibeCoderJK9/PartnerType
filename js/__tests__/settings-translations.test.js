/* ============================================================
   settings-translations — the producer's second box, for bonuses
   ============================================================

   A bonus name and its description are words the PRODUCER invents, so the
   dictionary can never hold them. The pattern the stages and the registration
   questions already use is a second column he fills in himself, revealed by
   the "Pokaż nazwy po angielsku" switch: empty means "not translated yet".

   What these tests pin, all of it broken at least once elsewhere in this app:
   - the editor shows the PL and EN SOURCE values side by side, always, in
     either interface language -- an editor that "helpfully" resolved the
     label would make the Polish original unreachable in English;
   - the switch hides the box without erasing what is in it;
   - a language change mid-edit keeps unsaved text, in the inline card AND in
     an open "new bonus" modal, which resolved its copy when it opened.
   ============================================================ */
import { readFileSync } from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { Window } from "happy-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
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

const BONUSES = [
  {
    id: 1, type: "percent", name: "Bonus za webinar", name_en: "Webinar bonus",
    description: "Prowadzenie webinaru", description_en: "Running a webinar",
    default_value: 2, active: 1, sort_order: 0,
  },
  {
    id: 2, type: "cash", name: "Nagroda kwartalna", name_en: "",
    description: "Za wyniki kwartału", description_en: "",
    default_value: 500, active: 1, sort_order: 1,
  },
];

function settingsWindow() {
  const window = new Window({ url: "http://localhost/pages/admin/settings.html" });
  window.ApiClient = {
    get: vi.fn().mockResolvedValue({ bonuses: BONUSES }),
    post: vi.fn().mockResolvedValue({}),
    patch: vi.fn().mockResolvedValue({}),
    delete: vi.fn().mockResolvedValue({}),
  };
  const hooks = [];
  window.App = appStub("pl");
  window.App.onRerender = (fn) => hooks.push(fn);
  window.__hooks = hooks;
  window.__setLang = (next) => {
    const fresh = appStub(next);
    Object.keys(fresh).forEach(key => { window.App[key] = fresh[key]; });
    window.App.onRerender = (fn) => hooks.push(fn);
    hooks.forEach(fn => fn());
  };
  window.Ui = {
    escapeHtml: (value) => String(value ?? "")
      .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
      .replaceAll("'", "&#x27;").replaceAll('"', "&quot;"),
    fieldLabel: (field) => field.label,
    stageLabel: (stage) => stage.name,
    REGISTRATION_STATUSES: [],
    REGISTRATION_LABELS: {},
    registrationBadge: () => window.document.createElement("span"),
  };
  window.showToast = vi.fn();
  window.handleApiError = vi.fn();
  window.confirm = vi.fn(() => true);
  window.document.body.dataset.page = "not-auto-init";
  window.document.body.innerHTML = `
    <div id="stage-registration-list"></div>
    <ol id="stage-list"></ol>
    <div id="stage-terminal-list"></div>
    <div id="tiers-grid"></div>
    <label><input type="checkbox" id="bonus-en-toggle"></label>
    <div class="text-xs" id="percent-bonus-summary"></div>
    <div class="text-xs" id="cash-bonus-summary"></div>
    <button type="button" id="add-percent-btn"></button>
    <button type="button" id="add-cash-btn"></button>
    <div class="bonus-defs-grid" id="bonus-percent-grid"></div>
    <div class="bonus-defs-grid" id="bonus-cash-grid"></div>
    <div id="reg-fields-list"></div>
    <div id="lead-fields-list"></div>
  `;
  runClassicScript(window, "js/icons.js");
  runClassicScript(window, "js/settings.js");
  return window;
}

function percentCard(window) {
  return window.document.querySelector('#bonus-percent-grid [data-bonus-id="1"]');
}

function boxOf(card, field) {
  return card.querySelector(`[data-field="${field}"]`);
}

let window;

beforeEach(async () => {
  window = settingsWindow();
  await invoke(window, "loadBonusDefs()");
  invoke(window, "setupEnglishNameToggles()");
});

describe("the second box", () => {
  it("gives every bonus an English name box and an English description box", () => {
    const card = percentCard(window);

    expect(boxOf(card, "name_en")).not.toBeNull();
    expect(boxOf(card, "description_en")).not.toBeNull();
  });

  it("fills them with what the producer has already written", () => {
    const card = percentCard(window);

    expect(boxOf(card, "name_en").value).toBe("Webinar bonus");
    expect(boxOf(card, "description_en").value).toBe("Running a webinar");
  });

  it("leaves an untranslated bonus's boxes empty rather than copying the Polish in", () => {
    const cashCard = window.document.querySelector('#bonus-cash-grid [data-bonus-id="2"]');

    expect(boxOf(cashCard, "name_en").value).toBe("");
    expect(boxOf(cashCard, "name").value).toBe("Nagroda kwartalna");
  });

  it("keeps the boxes hidden until the switch is on", () => {
    expect(boxOf(percentCard(window), "name_en").hidden).toBe(true);
  });

  it("reveals every English box across both bonus grids when the switch goes on", () => {
    const toggle = window.document.getElementById("bonus-en-toggle");
    toggle.checked = true;
    toggle.dispatchEvent(new window.Event("change"));

    const hidden = [...window.document.querySelectorAll('[data-field="name_en"], [data-field="description_en"]')]
      .filter(input => input.hidden);
    expect(hidden).toHaveLength(0);
  });

  it("hides the boxes again without erasing what is in them", () => {
    const toggle = window.document.getElementById("bonus-en-toggle");
    toggle.checked = true;
    toggle.dispatchEvent(new window.Event("change"));
    boxOf(percentCard(window), "name_en").value = "Webinar bonus v2";
    toggle.checked = false;
    toggle.dispatchEvent(new window.Event("change"));

    const box = boxOf(percentCard(window), "name_en");
    expect(box.hidden).toBe(true);
    expect(box.value).toBe("Webinar bonus v2");
  });
});

describe("saving", () => {
  it("sends the two languages as separate fields", async () => {
    const card = percentCard(window);
    boxOf(card, "name_en").value = "Webinar bonus";
    boxOf(card, "description_en").value = "Running a webinar";

    await invoke(window, "saveBonus(1)");

    expect(window.ApiClient.patch).toHaveBeenCalledWith(
      "/api/bonus-defs/1",
      expect.objectContaining({
        name: "Bonus za webinar",
        name_en: "Webinar bonus",
        description: "Prowadzenie webinaru",
        description_en: "Running a webinar",
      }),
    );
  });

  it("sends an empty English field when he clears it, so the translation is dropped", async () => {
    const card = percentCard(window);
    boxOf(card, "name_en").value = "   ";

    await invoke(window, "saveBonus(1)");

    expect(window.ApiClient.patch.mock.calls[0][1].name_en).toBe("");
    // Clearing the translation never touches the Polish original.
    expect(window.ApiClient.patch.mock.calls[0][1].name).toBe("Bonus za webinar");
  });

  it("offers both English boxes when a new bonus is created, and posts them", async () => {
    invoke(window, "createBonus('percent')");

    window.document.getElementById("modal-bonus-name").value = "Bonus za podcast";
    window.document.getElementById("modal-bonus-name-en").value = "Podcast bonus";
    window.document.getElementById("modal-bonus-desc-en").value = "Recording a podcast";
    // happy-dom rejects the modal's own default 1.0 against step 0.1 on float
    // arithmetic alone; Chrome accepts it. Relax the step so this test is
    // about the English payload and not about that discrepancy.
    window.document.getElementById("modal-bonus-value").step = "any";
    window.document.getElementById("modal-submit-btn").click();
    await new Promise(resolve => setTimeout(resolve, 0));

    expect(window.ApiClient.post).toHaveBeenCalledWith(
      "/api/bonus-defs",
      expect.objectContaining({
        name: "Bonus za podcast",
        name_en: "Podcast bonus",
        description_en: "Recording a podcast",
      }),
    );
  });
});

describe("a language switch mid-edit", () => {
  it("keeps both source values in the editor, in either interface language", () => {
    window.__setLang("en");

    const card = percentCard(window);
    expect(boxOf(card, "name").value).toBe("Bonus za webinar");
    expect(boxOf(card, "name_en").value).toBe("Webinar bonus");
  });

  it("does not lose text the producer has typed but not saved", () => {
    boxOf(percentCard(window), "name_en").value = "Webinar bonus, second take";

    window.__setLang("en");

    expect(boxOf(percentCard(window), "name_en").value).toBe("Webinar bonus, second take");
  });

  it("re-labels the open new-bonus modal and keeps what is typed in it", () => {
    invoke(window, "createBonus('percent')");
    window.document.getElementById("modal-bonus-name").value = "Bonus za podcast";
    window.document.getElementById("modal-bonus-name-en").value = "Podcast bonus";

    window.__setLang("en");

    const modal = window.document.getElementById("generic-modal-overlay");
    expect(modal).not.toBeNull();
    expect(modal.textContent).toContain("Bonus name in English");
    expect(modal.textContent).not.toContain("Nazwa bonusu po angielsku");
    expect(window.document.getElementById("modal-bonus-name").value).toBe("Bonus za podcast");
    expect(window.document.getElementById("modal-bonus-name-en").value).toBe("Podcast bonus");
  });

  it("keeps the English boxes visible across the switch when the switch is on", () => {
    const toggle = window.document.getElementById("bonus-en-toggle");
    toggle.checked = true;
    toggle.dispatchEvent(new window.Event("change"));

    window.__setLang("en");

    expect(boxOf(percentCard(window), "name_en").hidden).toBe(false);
  });
});
