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

function queueWindow() {
  const window = new Window({ url: "http://localhost/pages/admin/settings.html" });
  window.ApiClient = {
    get: vi.fn().mockResolvedValue({ settings: {} }),
    post: vi.fn().mockResolvedValue({}),
    patch: vi.fn().mockResolvedValue({ settings: {} }),
    delete: vi.fn().mockResolvedValue({}),
  };
  window.App = appStub("pl");
  window.Ui = {
    escapeHtml: (value) => String(value ?? "")
      .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
      .replaceAll("'", "&#x27;").replaceAll('"', "&quot;"),
  };
  window.showToast = vi.fn();
  window.handleApiError = vi.fn();
  window.confirm = vi.fn(() => true);
  window.document.body.dataset.page = "not-auto-init"; // avoid auto initSettingsPage() side effects
  window.document.body.innerHTML = `
    <div class="panel-body">
      <select id="s-queue-policy">
        <option value="queue">Kolejkuj zgłoszenia</option>
        <option value="reject">Odrzucaj duplikaty</option>
        <option value="allow">Pozwól wszystkim na pełną stawkę</option>
      </select>
      <input id="s-queue-max" type="number">
      <div id="queue-shares-list"></div>
      <div id="queue-summary"></div>
      <div id="queue-save-error" hidden></div>
      <button type="button" id="save-queue-btn"></button>
    </div>
    <div id="tiers-grid"></div>
    <div id="bonus-percent-grid"></div>
    <div id="bonus-cash-grid"></div>
  `;
  runClassicScript(window, "js/settings.js");
  return window;
}

function shareInputs(window) {
  return Array.from(window.document.querySelectorAll("#queue-shares-list input"));
}

function shareValues(window) {
  return shareInputs(window).map((input) => input.value);
}

describe("queue configuration — load and populate", () => {
  it("populates the policy control, the depth control and one percentage input per position from the settings response", () => {
    const window = queueWindow();
    invoke(window, "populateQueueSettings({ lead_duplicate_policy: 'reject', lead_queue_shares: '100,60,20,0', lead_queue_max: '4' })");

    expect(window.document.getElementById("s-queue-policy").value).toBe("reject");
    expect(window.document.getElementById("s-queue-max").value).toBe("4");
    expect(shareValues(window)).toEqual(["100", "60", "20", "0"]);
  });
});

describe("queue configuration — depth changes input count", () => {
  it("changing the depth adds or removes percentage inputs so their count always equals the depth", () => {
    const window = queueWindow();
    invoke(window, "populateQueueSettings({ lead_duplicate_policy: 'queue', lead_queue_shares: '100,50,25,10,0', lead_queue_max: '5' })");
    invoke(window, "setupQueueControls()");
    expect(shareInputs(window)).toHaveLength(5);

    const maxInput = window.document.getElementById("s-queue-max");
    maxInput.value = "3";
    maxInput.dispatchEvent(new window.Event("input", { bubbles: true }));
    expect(shareInputs(window)).toHaveLength(3);

    maxInput.value = "5";
    maxInput.dispatchEvent(new window.Event("input", { bubbles: true }));
    expect(shareInputs(window)).toHaveLength(5);
  });
});

describe("queue configuration — client-side validation mirrors the server", () => {
  it("blocks the save and shows an inline message, without issuing a request, when a percentage is above 100", async () => {
    const window = queueWindow();
    invoke(window, "populateQueueSettings({ lead_duplicate_policy: 'queue', lead_queue_shares: '100,50,25,10,0', lead_queue_max: '5' })");
    window.document.getElementById("queue-share-1").value = "150";

    await invoke(window, "saveQueueSettings()");

    expect(window.ApiClient.patch).not.toHaveBeenCalled();
    expect(window.document.getElementById("queue-save-error").hidden).toBe(false);
    expect(window.document.getElementById("queue-save-error").textContent.length).toBeGreaterThan(0);
  });

  it("blocks the save and shows an inline message, without issuing a request, when a percentage is below 0", async () => {
    const window = queueWindow();
    invoke(window, "populateQueueSettings({ lead_duplicate_policy: 'queue', lead_queue_shares: '100,50,25,10,0', lead_queue_max: '5' })");
    window.document.getElementById("queue-share-2").value = "-5";

    await invoke(window, "saveQueueSettings()");

    expect(window.ApiClient.patch).not.toHaveBeenCalled();
    expect(window.document.getElementById("queue-save-error").hidden).toBe(false);
  });

  it("blocks the save the same way when the depth is outside one to five", async () => {
    const window = queueWindow();
    invoke(window, "populateQueueSettings({ lead_duplicate_policy: 'queue', lead_queue_shares: '100,50,25,10,0', lead_queue_max: '5' })");
    window.document.getElementById("s-queue-max").value = "6";

    await invoke(window, "saveQueueSettings()");

    expect(window.ApiClient.patch).not.toHaveBeenCalled();
    expect(window.document.getElementById("queue-save-error").hidden).toBe(false);
  });

  it("blocks the save when values increase from one position to the next", async () => {
    const window = queueWindow();
    invoke(window, "populateQueueSettings({ lead_duplicate_policy: 'queue', lead_queue_shares: '50,50,25,10,0', lead_queue_max: '5' })");
    window.document.getElementById("queue-share-2").value = "80";

    await invoke(window, "saveQueueSettings()");

    expect(window.ApiClient.patch).not.toHaveBeenCalled();
    expect(window.document.getElementById("queue-save-error").hidden).toBe(false);
  });

  it("blocks the save when the final entry is not exactly 0", async () => {
    const window = queueWindow();
    invoke(window, "populateQueueSettings({ lead_duplicate_policy: 'queue', lead_queue_shares: '100,50,25,10,0', lead_queue_max: '5' })");
    window.document.getElementById("queue-share-5").value = "5";

    await invoke(window, "saveQueueSettings()");

    expect(window.ApiClient.patch).not.toHaveBeenCalled();
    expect(window.document.getElementById("queue-save-error").hidden).toBe(false);
  });
});

describe("queue configuration — valid save", () => {
  it("issues a settings update containing exactly the three queue keys, ordered by queue position and ending in 0", async () => {
    const window = queueWindow();
    invoke(window, "populateQueueSettings({ lead_duplicate_policy: 'queue', lead_queue_shares: '100,50,25,10,0', lead_queue_max: '5' })");
    window.document.getElementById("s-queue-policy").value = "reject";
    window.document.getElementById("queue-share-2").value = "40";
    window.ApiClient.patch.mockResolvedValueOnce({
      settings: { lead_duplicate_policy: "reject", lead_queue_shares: "100,40,25,10,0", lead_queue_max: "5" },
    });

    await invoke(window, "saveQueueSettings()");

    expect(window.ApiClient.patch).toHaveBeenCalledTimes(1);
    const [url, body] = window.ApiClient.patch.mock.calls[0];
    expect(url).toBe("/api/settings");
    expect(Object.keys(body).sort()).toEqual(["lead_duplicate_policy", "lead_queue_max", "lead_queue_shares"]);
    expect(body.lead_duplicate_policy).toBe("reject");
    expect(body.lead_queue_max).toBe("5");
    expect(body.lead_queue_shares).toBe("100,40,25,10,0");
    expect(body.lead_queue_shares.endsWith(",0")).toBe(true);
  });

  it("restates what each position now earns in the summary region after a successful save", async () => {
    const window = queueWindow();
    invoke(window, "populateQueueSettings({ lead_duplicate_policy: 'queue', lead_queue_shares: '100,50,25,10,0', lead_queue_max: '5' })");
    window.ApiClient.patch.mockResolvedValueOnce({
      settings: { lead_duplicate_policy: "queue", lead_queue_shares: "100,50,25,10,0", lead_queue_max: "5" },
    });

    await invoke(window, "saveQueueSettings()");

    const summary = window.document.getElementById("queue-summary").textContent;
    expect(summary).toContain("1");
    expect(summary).toContain("100");
    expect(summary).toContain("2");
    expect(summary).toContain("50");
  });
});

describe("queue configuration — rejected save", () => {
  it("shows the error copy and preserves the entered values rather than clearing the form", async () => {
    const window = queueWindow();
    invoke(window, "populateQueueSettings({ lead_duplicate_policy: 'queue', lead_queue_shares: '100,50,25,10,0', lead_queue_max: '5' })");
    window.document.getElementById("queue-share-2").value = "42";
    window.ApiClient.patch.mockRejectedValueOnce(new Error("network down"));

    await invoke(window, "saveQueueSettings()");

    const errorEl = window.document.getElementById("queue-save-error");
    expect(errorEl.hidden).toBe(false);
    expect(errorEl.textContent).toBe("Nie udało się zapisać zmian. Dane nie zostały utracone — spróbuj ponownie.");
    expect(shareValues(window)).toEqual(["100", "42", "25", "10", "0"]);
  });
});
