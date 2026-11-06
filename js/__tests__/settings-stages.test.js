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

const STAGE_ERROR_COPY = "Nie udało się zapisać zmian. Dane nie zostały utracone — spróbuj ponownie.";

function stageWindow() {
  const window = new Window({ url: "http://localhost/pages/admin/settings.html" });
  window.ApiClient = {
    get: vi.fn().mockResolvedValue({ stages: [] }),
    post: vi.fn().mockResolvedValue({}),
    patch: vi.fn().mockResolvedValue({}),
    delete: vi.fn().mockResolvedValue({}),
  };
  window.App = appStub("pl");
  window.Ui = {
    escapeHtml: (value) => String(value ?? "")
      .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
      .replaceAll("'", "&#x27;").replaceAll('"', "&quot;"),
    stageLabel: (stage) => stage.kind === "win"
      ? window.App.t("stage_terminal_win")
      : stage.kind === "lose"
        ? window.App.t("stage_terminal_lose")
        : stage.name,
    // The real tables, not invented ones: the fixed intake stages on this
    // screen are rendered from the same source the leads list renders its
    // badge from, so stubbing them would defeat the point.
    REGISTRATION_STATUSES: ["pending", "needs_info", "accepted", "rejected"],
    REGISTRATION_LABELS: {
      pending: "Oczekujący",
      needs_info: "Prośba o dodatkowe informacje",
      accepted: "Zaakceptowany",
      rejected: "Odrzucony",
    },
    // Won and Lost are drawn as the same chip the leads list uses; the stub
    // keeps the parts this screen relies on -- the label and the stage id.
    stageBadge(stage) {
      const badge = window.document.createElement("span");
      badge.className = "badge";
      badge.textContent = window.Ui.stageLabel(stage);
      return badge;
    },
    registrationBadge(value) {
      const badge = window.document.createElement("span");
      badge.className = "badge badge-solid";
      badge.textContent = window.Ui.REGISTRATION_LABELS[value] || value;
      return badge;
    },
  };
  window.showToast = vi.fn();
  window.handleApiError = vi.fn();
  window.confirm = vi.fn(() => true);
  window.document.body.dataset.page = "not-auto-init"; // avoid auto initSettingsPage() side effects
  window.document.body.innerHTML = `
    <div class="panel-body">
      <div id="stage-registration-list"></div>
      <div id="stage-save-hint"></div>
      <ol id="stage-list" aria-live="polite"></ol>
      <div id="stage-terminal-list"></div>
      <div id="stage-save-error" hidden>
        <span id="stage-error-text"></span>
        <button type="button" id="stage-error-retry"></button>
      </div>
      <button type="button" id="add-stage-btn"></button>
      <span id="stage-count"></span>
    </div>
    <template id="stage-row-template">
      <li class="stage-row" data-stage-id="">
        <input type="color" class="stage-color" data-field="color">
        <input type="text" class="form-input stage-name" data-field="name">
        <div>
          <button type="button" class="stage-move-up" data-action="move-up" aria-label="Przesuń etap w górę"></button>
          <button type="button" class="stage-move-down" data-action="move-down" aria-label="Przesuń etap w dół"></button>
        </div>
        <span class="stage-saved" hidden></span>
        <button type="button" class="stage-remove" data-action="remove-stage" aria-label="Usuń etap"></button>
      </li>
    </template>
    <div id="tiers-grid"></div>
    <div id="bonus-percent-grid"></div>
    <div id="bonus-cash-grid"></div>
  `;
  runClassicScript(window, "js/icons.js");
  runClassicScript(window, "js/settings.js");
  return window;
}

function savedMarkerOf(row) {
  return row.querySelector(".stage-saved");
}

function producerRows(window) {
  return Array.from(window.document.querySelectorAll("#stage-list > *"));
}

function terminalRows(window) {
  return Array.from(window.document.querySelectorAll("#stage-terminal-list > *"));
}

function nameOf(row) {
  const input = row.querySelector('[data-field="name"]');
  // A producer stage is an editable row; Won and Lost are fixed chips.
  return input ? input.value : row.textContent;
}

const SEED_STAGES = [
  { id: 1, key: "contact", name: "Nawiązanie kontaktu", sort_order: 0, kind: "progress", is_terminal: 0, color: "#94a3b8", active: 1 },
  { id: 2, key: "presentation", name: "Prezentacja", sort_order: 1, kind: "progress", is_terminal: 0, color: "#3b82f6", active: 1 },
  { id: 3, key: "offer", name: "Oferta", sort_order: 2, kind: "progress", is_terminal: 0, color: "#8b5cf6", active: 1 },
  { id: 4, key: "negotiation", name: "Negocjacje", sort_order: 3, kind: "progress", is_terminal: 0, color: "#f59e0b", active: 1 },
  { id: 5, key: "won", name: "Wygrany", sort_order: 4, kind: "win", is_terminal: 1, color: "#10b981", active: 1 },
  { id: 6, key: "lost", name: "Przegrany", sort_order: 5, kind: "lose", is_terminal: 1, color: "#ef4444", active: 1 },
];

describe("stage pipeline — load and populate", () => {
  it("keeps producer stages editable and locks both terminal names and colours", async () => {
    const window = stageWindow();
    window.App = appStub('en');
    window.ApiClient.get.mockResolvedValueOnce({ stages: SEED_STAGES });
    await invoke(window, "loadStages()");
    expect(producerRows(window).every(row => [...row.querySelectorAll('input')].every(input => !input.disabled))).toBe(true);
    // Fixed chips: nothing on them can be typed into at all.
    expect(terminalRows(window).every(row => row.querySelectorAll('input, button').length === 0)).toBe(true);
    expect(terminalRows(window).map(nameOf)).toEqual(['Won', 'Lost']);
    expect(producerRows(window).map(nameOf)).toContain('Nawiązanie kontaktu');
  });

  it("preserves an unsaved producer stage name when its labels are redrawn", async () => {
    const window = stageWindow();
    window.ApiClient.get.mockResolvedValueOnce({ stages: SEED_STAGES });
    await invoke(window, "loadStages()");
    producerRows(window)[0].querySelector('[data-field="name"]').value = 'Moja niezapisana zmiana';
    window.App = appStub('en');
    invoke(window, 'preserveSettingsDrafts(renderStages)');
    expect(nameOf(producerRows(window)[0])).toBe('Moja niezapisana zmiana');
    expect(terminalRows(window).map(nameOf)).toEqual(['Won', 'Lost']);
  });
  it("renders one row per stage in sort order with the two terminal stages last", async () => {
    const window = stageWindow();
    window.ApiClient.get.mockResolvedValueOnce({ stages: SEED_STAGES });

    await invoke(window, "loadStages()");

    expect(producerRows(window).map(nameOf)).toEqual(["Nawiązanie kontaktu", "Prezentacja", "Oferta", "Negocjacje"]);
    expect(terminalRows(window).map(nameOf)).toEqual(["Wygrany", "Przegrany"]);
  });
});

describe("stage pipeline — renaming", () => {
  it("issues an update carrying only the changed field and re-renders from the response", async () => {
    const window = stageWindow();
    window.ApiClient.get.mockResolvedValueOnce({ stages: SEED_STAGES });
    await invoke(window, "loadStages()");

    window.ApiClient.patch.mockResolvedValueOnce({
      stage: { ...SEED_STAGES[1], name: "Weryfikacja" },
    });

    await invoke(window, "renameStage(2, 'Weryfikacja')");

    expect(window.ApiClient.patch).toHaveBeenCalledTimes(1);
    expect(window.ApiClient.patch).toHaveBeenCalledWith("/api/lead-stages/2", { name: "Weryfikacja" });
    expect(producerRows(window).map(nameOf)).toEqual(["Nawiązanie kontaktu", "Weryfikacja", "Oferta", "Negocjacje"]);
  });
});

describe("stage pipeline — reordering", () => {
  it("issues updates that leave the sort order contiguous with the terminals last", async () => {
    const window = stageWindow();
    window.ApiClient.get.mockResolvedValueOnce({ stages: SEED_STAGES });
    await invoke(window, "loadStages()");

    window.ApiClient.patch.mockImplementation((url, body) => {
      const id = Number(url.split("/").pop());
      const stage = SEED_STAGES.find((s) => s.id === id);
      return Promise.resolve({ stage: { ...stage, ...body } });
    });

    // Move the second stage (id 2, sort_order 1) up, swapping it with the first (id 1, sort_order 0).
    await invoke(window, "moveStage(2, 'up')");

    expect(window.ApiClient.patch).toHaveBeenCalledTimes(2);
    const calledIds = window.ApiClient.patch.mock.calls.map(([url]) => url).sort();
    expect(calledIds).toEqual(["/api/lead-stages/1", "/api/lead-stages/2"]);
    expect(producerRows(window).map(nameOf)).toEqual(["Prezentacja", "Nawiązanie kontaktu", "Oferta", "Negocjacje"]);
    expect(terminalRows(window).map(nameOf)).toEqual(["Wygrany", "Przegrany"]);
  });
});

describe("stage pipeline — six-stage limit", () => {
  it("blocks adding a seventh producer stage in the browser with an inline message and issues no request", async () => {
    const window = stageWindow();
    const sixProducerStages = [
      ...SEED_STAGES.slice(0, 4),
      { id: 7, key: "extra_1", name: "Dodatkowy 1", sort_order: 4, kind: "progress", is_terminal: 0, color: "#000000" },
      { id: 8, key: "extra_2", name: "Dodatkowy 2", sort_order: 5, kind: "progress", is_terminal: 0, color: "#111111" },
      SEED_STAGES[4], SEED_STAGES[5],
    ];
    window.ApiClient.get.mockResolvedValueOnce({ stages: sixProducerStages });
    await invoke(window, "loadStages()");

    await invoke(window, "handleAddStageClick()");

    expect(window.ApiClient.post).not.toHaveBeenCalled();
    expect(window.document.getElementById("stage-save-error").hidden).toBe(false);
    expect(window.document.getElementById("stage-error-text").textContent.length).toBeGreaterThan(0);
  });
});

describe("stage pipeline — the commission trigger is gone", () => {
  it("offers no per-stage commission control at all", async () => {
    // The producer does not pick which stage pays.
    // Commission is projected at every stage and recorded on the win
    // terminal, so a "this stage pays" radio would be a control with
    // nothing behind it.
    const window = stageWindow();
    window.ApiClient.get.mockResolvedValueOnce({ stages: SEED_STAGES });
    await invoke(window, "loadStages()");

    expect(window.document.querySelectorAll('[data-field="trigger"]')).toHaveLength(0);
    expect(typeof window.setCommissionTrigger).toBe("undefined");
  });
});

describe("stage pipeline — removal blocked by occupied leads", () => {
  it("shows the server's refusal including the lead count and leaves the row in place", async () => {
    const window = stageWindow();
    window.ApiClient.get.mockResolvedValueOnce({ stages: SEED_STAGES });
    await invoke(window, "loadStages()");

    window.ApiClient.delete.mockRejectedValueOnce(new Error("Nie można usunąć: 3 leadów znajduje się na tym etapie."));

    await invoke(window, "removeStage(2)");

    expect(window.ApiClient.delete).toHaveBeenCalledWith("/api/lead-stages/2");
    const errorEl = window.document.getElementById("stage-save-error");
    expect(errorEl.hidden).toBe(false);
    expect(window.document.getElementById("stage-error-text").textContent).toContain("3");
    expect(producerRows(window).map(nameOf)).toEqual(["Nawiązanie kontaktu", "Prezentacja", "Oferta", "Negocjacje"]);
  });
});

describe("stage pipeline — cancelled removal", () => {
  it("issues no request when the destructive confirmation is cancelled", async () => {
    const window = stageWindow();
    window.confirm = vi.fn(() => false);
    window.ApiClient.get.mockResolvedValueOnce({ stages: SEED_STAGES });
    await invoke(window, "loadStages()");

    await invoke(window, "removeStage(2)");

    expect(window.ApiClient.delete).not.toHaveBeenCalled();
  });
});

describe("stage pipeline — rejected mutation", () => {
  it("shows the shared error copy and preserves the entered value rather than reverting it", async () => {
    const window = stageWindow();
    window.ApiClient.get.mockResolvedValueOnce({ stages: SEED_STAGES });
    await invoke(window, "loadStages()");

    window.ApiClient.patch.mockRejectedValueOnce(new Error("network down"));

    await invoke(window, "renameStage(2, 'Nowa nazwa')");

    const errorEl = window.document.getElementById("stage-save-error");
    expect(errorEl.hidden).toBe(false);
    expect(window.document.getElementById("stage-error-text").textContent).toBe(STAGE_ERROR_COPY);
    // renderStages() must not have run on failure -- the row still shows the
    // originally loaded name because the DOM was never touched by the reject
    // path; the caller is responsible for the value already sitting in the
    // input having been typed by the user before the call was made.
    expect(producerRows(window).map(nameOf)).toEqual(["Nawiązanie kontaktu", "Prezentacja", "Oferta", "Negocjacje"]);
  });
});

describe("stage pipeline — markup in a stage name", () => {
  it("appears as literal text rather than being parsed as markup", async () => {
    const window = stageWindow();
    const maliciousName = '<img src=x onerror=alert(1)>';
    window.ApiClient.get.mockResolvedValueOnce({
      stages: [{ ...SEED_STAGES[0], name: maliciousName }, ...SEED_STAGES.slice(1)],
    });

    await invoke(window, "loadStages()");

    const firstRow = producerRows(window)[0];
    expect(nameOf(firstRow)).toBe(maliciousName);
    expect(window.document.getElementById("stage-list").querySelectorAll("img")).toHaveLength(0);
  });
});

// ─── A stage edit says whether it was saved ───────────────────────────
// The mechanism is save-on-change; these tests cover the marker that makes
// the outcome visible.

describe("stage pipeline — saved marker", () => {
  it("shows a Zapisano marker on the edited row after a successful rename", async () => {
    const window = stageWindow();
    window.ApiClient.get.mockResolvedValueOnce({ stages: SEED_STAGES });
    await invoke(window, "loadStages()");

    window.ApiClient.patch.mockResolvedValueOnce({
      stage: { ...SEED_STAGES[1], name: "Weryfikacja" },
    });

    await invoke(window, "renameStage(2, 'Weryfikacja')");

    const row = producerRows(window).find(r => r.dataset.stageId === "2");
    const marker = savedMarkerOf(row);
    expect(marker.hidden).toBe(false);
    expect(marker.textContent).toContain("Zapisano");
  });

  it("leaves no saved marker and shows the error block after a failed rename", async () => {
    const window = stageWindow();
    window.ApiClient.get.mockResolvedValueOnce({ stages: SEED_STAGES });
    await invoke(window, "loadStages()");

    window.ApiClient.patch.mockRejectedValueOnce(new Error("network down"));

    await invoke(window, "renameStage(2, 'Nowa nazwa')");

    const row = producerRows(window).find(r => r.dataset.stageId === "2");
    expect(savedMarkerOf(row).hidden).toBe(true);
    expect(window.document.getElementById("stage-save-error").hidden).toBe(false);
  });

  it("marks the newly edited row without leaving a stale marker on an earlier one once its lifetime elapses", async () => {
    vi.useFakeTimers({ toFake: ["Date", "setTimeout", "clearTimeout"] });
    try {
      const window = stageWindow();
      // The window is its own vm context (happy-dom), so its setTimeout/Date
      // are not the same objects vi.useFakeTimers() patches on globalThis --
      // point them at the faked ones so the scheduled cleanup obeys the
      // advanced clock below instead of real wall-clock time.
      window.setTimeout = globalThis.setTimeout;
      window.clearTimeout = globalThis.clearTimeout;
      window.Date = globalThis.Date;
      window.ApiClient.get.mockResolvedValueOnce({ stages: SEED_STAGES });
      await invoke(window, "loadStages()");

      window.ApiClient.patch.mockResolvedValueOnce({
        stage: { ...SEED_STAGES[0], name: "Nowy zmieniony" },
      });
      await invoke(window, "renameStage(1, 'Nowy zmieniony')");
      let rowOne = producerRows(window).find(r => r.dataset.stageId === "1");
      expect(savedMarkerOf(rowOne).hidden).toBe(false);

      await vi.advanceTimersByTimeAsync(3500);

      rowOne = producerRows(window).find(r => r.dataset.stageId === "1");
      expect(savedMarkerOf(rowOne).hidden).toBe(true);

      window.ApiClient.patch.mockResolvedValueOnce({
        stage: { ...SEED_STAGES[1], name: "Weryfikacja" },
      });
      await invoke(window, "renameStage(2, 'Weryfikacja')");

      const rowTwo = producerRows(window).find(r => r.dataset.stageId === "2");
      expect(savedMarkerOf(rowTwo).hidden).toBe(false);
      rowOne = producerRows(window).find(r => r.dataset.stageId === "1");
      expect(savedMarkerOf(rowOne).hidden).toBe(true);
    } finally {
      vi.useRealTimers();
    }
  });

  it("states up front, in the stage pane, that stages save automatically and a marker confirms it", () => {
    const html = readFileSync(path.join(PROJECT_ROOT, "pages/admin/settings.html"), "utf8");
    const match = html.match(/id="stage-save-hint"[^>]*>([\s\S]*?)<\/div>/);
    expect(match).not.toBeNull();
    const text = match[1].replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
    expect(text.length).toBeGreaterThan(20);
    expect(text.toLowerCase()).toContain("saved");
  });

  it("builds the saved marker with textContent via Icons rather than an innerHTML string", async () => {
    const window = stageWindow();
    window.ApiClient.get.mockResolvedValueOnce({ stages: SEED_STAGES });
    await invoke(window, "loadStages()");

    window.ApiClient.patch.mockResolvedValueOnce({
      stage: { ...SEED_STAGES[1], name: "Weryfikacja" },
    });
    await invoke(window, "renameStage(2, 'Weryfikacja')");

    const row = producerRows(window).find(r => r.dataset.stageId === "2");
    const marker = savedMarkerOf(row);
    expect(marker.querySelector("svg")).not.toBeNull();
    expect(marker.querySelector("span").textContent).toBe("Zapisano");
  });
});

/*
   The fixed registration stages (accepted, rejected, needs information) are
   visible on Settings. The model carries a separate registration axis --
   four fixed values on leads.registration_status, each with server behaviour
   attached -- and this screen draws it beside the sales funnel.
*/
describe("the fixed intake stages are visible above the funnel", () => {
  it("lists all four, in the order a lead passes through them", () => {
    const window = stageWindow();

    invoke(window, "renderStages()");

    const badges = Array.from(
      window.document.querySelectorAll("#stage-registration-list > *"),
    );
    expect(badges.map((b) => b.textContent)).toEqual([
      "Oczekujący",
      "Prośba o dodatkowe informacje",
      "Zaakceptowany",
      "Odrzucony",
    ]);
  });

  it("draws them read-only: no input, no remove button, nothing to reorder", () => {
    const window = stageWindow();

    invoke(window, "renderStages()");

    const host = window.document.getElementById("stage-registration-list");
    expect(host.querySelectorAll("input")).toHaveLength(0);
    expect(host.querySelectorAll("button")).toHaveLength(0);
  });

  it("re-rendering does not stack a second copy", () => {
    const window = stageWindow();

    invoke(window, "renderStages()");
    invoke(window, "renderStages()");

    expect(
      window.document.querySelectorAll("#stage-registration-list > *"),
    ).toHaveLength(4);
  });
});
