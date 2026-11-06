import { readFileSync } from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { Window } from "happy-dom";
import { describe, expect, it, vi } from "vitest";
import { appStub } from "./helpers/copy.js";

const PROJECT_ROOT = path.resolve(import.meta.dirname, "..", "..");

function createAgent(response, role = "admin") {
  const window = new Window({ url: `http://localhost/pages/${role}/dashboard.html` });
  window.sessionStorage.setItem("ph_session", JSON.stringify({ id: 1, role }));
  // the controller reads its copy from the dictionary
  window.App = appStub('pl');
  window.ApiClient = {
    get: vi.fn().mockResolvedValue({
      available: true,
      model: "partnerhub-agent",
      message: "Lokalny model jest gotowy.",
    }),
    post: vi.fn().mockResolvedValue(response),
  };
  const context = vm.createContext(window);
  for (const script of ["js/icons.js", "js/agent.js"]) {
    vm.runInContext(readFileSync(path.join(PROJECT_ROOT, script), "utf8"), context, { filename: script });
  }
  window.document.dispatchEvent(new window.Event("DOMContentLoaded"));
  return window;
}

async function send(window, text) {
  const input = window.document.getElementById("agent-input");
  input.value = text;
  window.document.getElementById("agent-send").click();
  await vi.waitFor(() => expect(window.ApiClient.post).toHaveBeenCalledTimes(1));
  await vi.waitFor(() => expect(window.document.querySelector(".agent-typing-msg")).toBeNull());
}

describe("agent plain-text security contract", () => {
  it("renders user and AI tags, markdown links, event attributes and URLs literally", async () => {
    const payload = '<img src=x onerror="window.__agentPwned=true"> **bold** [click](javascript:alert(1))';
    const window = createAgent({ response: payload });

    await send(window, payload);

    const messages = [...window.document.querySelectorAll(".agent-msg-content")];
    expect(messages.at(-2).textContent).toBe(payload);
    expect(messages.at(-1).textContent).toBe(payload);
    expect(messages.at(-1).innerHTML).toContain("&lt;img");
    expect(messages.at(-1).querySelector("img, a, script, [onerror]")).toBeNull();
    expect(window.__agentPwned).toBeUndefined();
  });

  it("renders dynamic API errors as inert literal text", async () => {
    const payload = '<svg onload="window.__agentPwned=true">javascript:alert(1)';
    const window = createAgent({ error: payload });

    await send(window, "test");

    const content = [...window.document.querySelectorAll(".agent-msg-content")].at(-1);
    // The warning is now a Lucide icon next to the text, so the error text
    // stands alone in textContent.
    expect(content.textContent).toBe(payload);
    expect(content.querySelector("img, script, [onload]")).toBeNull();
    // The only SVG here is the warning icon; one from the payload would not
    // carry the generated icon class.
    const svgs = [...content.querySelectorAll("svg")];
    expect(svgs).toHaveLength(1);
    expect(svgs[0].classList.contains("icon-triangle-alert")).toBe(true);
    expect(window.__agentPwned).toBeUndefined();
  });

  it("preserves typing and successful message sequencing", async () => {
    let resolve;
    const pending = new Promise((done) => { resolve = done; });
    const window = createAgent({ response: "unused" });
    window.ApiClient.post.mockReturnValueOnce(pending);
    const input = window.document.getElementById("agent-input");
    input.value = "Pytanie";
    window.document.getElementById("agent-send").click();

    expect(window.document.querySelector(".agent-typing-msg")).not.toBeNull();
    expect(input.disabled).toBe(true);
    resolve({ response: "Odpowiedź" });
    await vi.waitFor(() => expect(window.document.querySelector(".agent-typing-msg")).toBeNull());

    const contents = [...window.document.querySelectorAll(".agent-msg-content")];
    expect(contents.at(-2).textContent).toBe("Pytanie");
    expect(contents.at(-1).textContent).toBe("Odpowiedź");
    expect(input.disabled).toBe(false);
  });

  it("renders a malicious confirmation payload as text and requires a separate click", async () => {
    const payload = '<img src=x onerror="window.__agentPwned=true">';
    const response = {
      response: "Sprawdź kartę.",
      action: {
        id: "safe-token-abcdefghijklmnopqrstuvwxyz",
        risk: "financial",
        title: payload,
        summary: [{ label: payload, value: payload }],
        confirm_label: payload,
      },
    };
    const window = createAgent(response);
    window.ApiClient.post
      .mockResolvedValueOnce(response)
      .mockResolvedValueOnce({ response: "Operacja potwierdzona." });

    await send(window, "Przygotuj zmianę");

    const card = window.document.querySelector(".agent-action-card");
    expect(card.textContent).toContain(payload);
    expect(card.querySelector("img, script, [onerror]")).toBeNull();
    expect(window.__agentPwned).toBeUndefined();

    card.querySelector(".agent-action-confirm").click();
    await vi.waitFor(() => expect(window.ApiClient.post).toHaveBeenCalledTimes(2));
    expect(window.ApiClient.post).toHaveBeenLastCalledWith(
      "/api/agent/actions/safe-token-abcdefghijklmnopqrstuvwxyz/confirm",
      {},
    );
    await vi.waitFor(() => expect(card.dataset.pending).toBe("false"));
  });

  it("renders a partial translate_labels outcome's names and reasons as inert text", async () => {
    // The polish/english/message strings in a
    // partial batch report are producer-authored (a bonus name, a product
    // name), exactly like the confirmation card above -- they must render
    // through textContent too, never as markup.
    const payload = '<img src=x onerror="window.__agentPwned=true">';
    const action = {
      id: "safe-token-abcdefghijklmnopqrstuvwxyz",
      risk: "write",
      title: "Potwierdź angielskie nazwy",
      summary: [{ label: "Etap", value: "Contact" }],
      confirm_label: "Zapisz tłumaczenia",
    };
    const confirmResult = {
      response: "Zapisano częściowo: 1 z 3.",
      result: {
        translations: {
          partial: true,
          written: [{ kind: "stage", id: 1, polish: payload, english: "Contact" }],
          failed: {
            kind: "tier", id: 2, polish: payload, english: payload,
            code: "stale_action", message: payload,
          },
          outcome_unknown: null,
          not_attempted: [{ kind: "product", id: 3, polish: payload, english: payload }],
        },
      },
    };
    const window = createAgent({ response: "Sprawdź kartę.", action });
    window.ApiClient.post
      .mockResolvedValueOnce({ response: "Sprawdź kartę.", action })
      .mockResolvedValueOnce(confirmResult);

    await send(window, "Przetłumacz nazwy");
    const card = window.document.querySelector(".agent-action-card");
    card.querySelector(".agent-action-confirm").click();
    await vi.waitFor(() => expect(card.dataset.pending).toBe("false"));

    const outcome = card.querySelector(".agent-action-outcome");
    expect(outcome).not.toBeNull();
    expect(outcome.textContent).toContain(payload);
    expect(outcome.querySelector("img, script, [onerror]")).toBeNull();
    expect(window.__agentPwned).toBeUndefined();
    // Honesty, not just safety: a partial batch never wears the "complete" class.
    expect(card.classList.contains("is-partial")).toBe(true);
    expect(card.classList.contains("is-complete")).toBe(false);
  });

  it("is available to a partner with partner-specific capabilities", async () => {
    const window = createAgent({ response: "Twoje leady" }, "partner");

    expect(window.document.getElementById("agent-container")).not.toBeNull();
    expect(window.document.getElementById("agent-messages").textContent).toContain("Twoje leady");
    expect(window.document.getElementById("agent-quick-actions").textContent).toContain("Przygotuj lead");
    expect(window.document.getElementById("agent-quick-actions").textContent).not.toContain("Partnerzy");
  });
});
