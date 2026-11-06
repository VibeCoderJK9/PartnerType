import path from "node:path";
import vm from "node:vm";
import { readFileSync } from "node:fs";
import { Window } from "happy-dom";
import { describe, expect, it, vi } from "vitest";
import { appStub } from "./helpers/copy.js";

const PROJECT_ROOT = path.resolve(import.meta.dirname, "..", "..");

/*
   Stripe-style chat: the icon sits next to the avatar, the chat opens on the
   right and stays open until it is closed, even across screens.

   "Stays open across screens" cannot be literal here: this is a
   multi-page app, so every navigation is a new document. What persists is the
   state (localStorage) and the transcript, and the transcript is read back from
   the server, which already holds the conversation the model remembers.
*/
function realm({ chat = null, history = [], status = null } = {}) {
  const window = new Window({ url: "http://localhost:8000/pages/admin/leads.html" });
  window.localStorage.clear();
  if (chat !== null) window.localStorage.setItem("ph_chat", chat);
  window.sessionStorage.setItem(
    "ph_session",
    JSON.stringify({ id: 1, role: "admin", name: "Ada Admin" }),
  );
  window.document.body.innerHTML = `
    <div class="app">
      <div class="main-area">
        <header class="top-header">
          <div class="header-right">
            <div class="avatar" id="header-avatar">AA</div>
          </div>
        </header>
      </div>
    </div>
  `;
  const get = vi.fn((url) => {
    if (url === "/api/agent/status") {
      return Promise.resolve(status ?? { available: true, model: "partnerhub-agent", message: "ok" });
    }
    if (url === "/api/agent/history") return Promise.resolve({ messages: history });
    return Promise.resolve({});
  });
  window.ApiClient = { get, post: vi.fn().mockResolvedValue({}) };
  window.App = appStub('pl');

  const context = vm.createContext(window);
  for (const file of ["js/icons.js", "js/agent.js"]) {
    vm.runInContext(readFileSync(path.join(PROJECT_ROOT, file), "utf8"), context, { filename: file });
  }
  window.document.dispatchEvent(new window.Event("DOMContentLoaded"));
  return { window, get };
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

describe("the docked AI chat", () => {
  it("puts its button next to the avatar, not floating over the page", () => {
    const { window } = realm();

    const toggle = window.document.getElementById("agent-toggle");
    const avatar = window.document.getElementById("header-avatar");
    expect(toggle).not.toBeNull();
    // Immediately before the avatar, inside the header -- the 56px circle it
    // replaced sat over table rows and pagination on every screen.
    expect(toggle.nextElementSibling).toBe(avatar);
    expect(toggle.closest(".top-header")).not.toBeNull();
  });

  it("builds a dock, not a floating card", () => {
    const { window } = realm();

    expect(window.document.querySelector(".agent-dock")).not.toBeNull();
    expect(window.document.querySelector(".agent-chat-panel")).toBeNull();
    expect(window.document.querySelector(".agent-widget-btn")).toBeNull();
  });

  it("starts closed, and opening it narrows the page rather than covering it", () => {
    const { window } = realm();
    const root = window.document.documentElement;
    expect(root.dataset.chat).toBe("closed");

    window.document.getElementById("agent-toggle").click();

    // `data-chat` is what the layout reads for --chat-width, so the centre
    // column gives up exactly the dock's width.
    expect(root.dataset.chat).toBe("open");
    expect(window.document.getElementById("agent-toggle").getAttribute("aria-expanded")).toBe("true");
  });

  it("remembers being open, so the next screen opens with it", () => {
    const { window } = realm();

    window.document.getElementById("agent-toggle").click();

    expect(window.localStorage.getItem("ph_chat")).toBe("open");
  });

  it("reopens itself on a fresh document when that is how it was left", () => {
    const { window } = realm({ chat: "open" });

    expect(window.document.documentElement.dataset.chat).toBe("open");
    expect(window.document.querySelector(".agent-dock").classList.contains("open")).toBe(true);
  });

  it("closing it is remembered too", () => {
    const { window } = realm({ chat: "open" });

    window.document.getElementById("agent-close").click();

    expect(window.document.documentElement.dataset.chat).toBe("closed");
    expect(window.localStorage.getItem("ph_chat")).toBe("closed");
  });

  it("redraws the conversation the model still remembers", async () => {
    const { window, get } = realm({
      chat: "open",
      history: [
        { role: "user", content: "Pokaż nowe leady" },
        { role: "assistant", content: "Masz trzy nowe zgłoszenia." },
      ],
    });

    await flush();

    expect(get).toHaveBeenCalledWith("/api/agent/history");
    const rendered = [...window.document.querySelectorAll("#agent-messages .agent-msg")];
    // The greeting is replaced: an assistant that clearly remembers a
    // conversation the reader cannot see is worse than no transcript.
    expect(rendered).toHaveLength(2);
    expect(rendered[0].className).toContain("user");
    expect(rendered[0].textContent).toContain("Pokaż nowe leady");
    expect(rendered[1].className).toContain("ai");
  });

  it("keeps the greeting when there is no conversation to restore", async () => {
    const { window } = realm({ chat: "open", history: [] });

    await flush();

    const rendered = [...window.document.querySelectorAll("#agent-messages .agent-msg")];
    expect(rendered).toHaveLength(1);
    expect(rendered[0].textContent).toContain("Działam lokalnie");
  });

  it("renders restored messages as text, never as markup", async () => {
    const { window } = realm({
      chat: "open",
      history: [{ role: "assistant", content: '<img src=x onerror="window.__pwned=true">' }],
    });

    await flush();

    expect(window.__pwned).toBeUndefined();
    expect(window.document.querySelector("#agent-messages img")).toBeNull();
  });
});

describe("a translate_labels confirmation that only partly saved", () => {
  // api/agent_actions.py::_confirm_translate_labels reports written/failed/
  // outcome_unknown/not_attempted rather than one flat success -- the card
  // must say so too, not the generic "Operation complete" every other
  // action type gets.
  const action = {
    id: "safe-token-abcdefghijklmnopqrstuvwxyz",
    risk: "write",
    title: "Potwierdź angielskie nazwy",
    summary: [{ label: "Etap: Kontakt", value: "Contact" }],
    confirm_label: "Zapisz tłumaczenia",
  };

  function chatWithCard(window) {
    window.ApiClient.post.mockResolvedValueOnce({ response: "Sprawdź kartę.", action });
    const input = window.document.getElementById("agent-input");
    input.value = "Przetłumacz nazwy";
    window.document.getElementById("agent-send").click();
    return vi.waitFor(() => expect(window.document.querySelector(".agent-action-card")).not.toBeNull());
  }

  it("shows saved and not-saved names instead of claiming full success", async () => {
    const { window } = realm({ chat: "open" });
    await chatWithCard(window);
    window.ApiClient.post.mockResolvedValueOnce({
      response: "Zapisano częściowo: 1 z 2. Poproś o nową propozycję dla pozostałych.",
      result: {
        translations: {
          partial: true,
          written: [{ kind: "stage", id: 1, polish: "Kontakt", english: "Contact" }],
          failed: {
            kind: "tier", id: 2, polish: "Diament", english: "Diamond",
            code: "stale_action", message: "Poziom nie istnieje.",
          },
          outcome_unknown: null,
          not_attempted: [{ kind: "product", id: 3, polish: "Pakiet Demo", english: "Demo package" }],
        },
      },
    });

    const card = window.document.querySelector(".agent-action-card");
    card.querySelector(".agent-action-confirm").click();
    await vi.waitFor(() => expect(card.dataset.pending).toBe("false"));

    expect(card.classList.contains("is-partial")).toBe(true);
    expect(card.classList.contains("is-complete")).toBe(false);
    const outcome = card.querySelector(".agent-action-outcome");
    expect(outcome).not.toBeNull();
    expect(outcome.textContent).toContain("Kontakt");
    expect(outcome.textContent).toContain("Diament");
    expect(outcome.textContent).toContain("Poziom nie istnieje.");
    expect(outcome.textContent).toContain("Pakiet Demo");
  });

  it("lets the reader ask for a new proposal for the rest with one click", async () => {
    const { window } = realm({ chat: "open" });
    await chatWithCard(window);
    window.ApiClient.post.mockResolvedValueOnce({
      response: "Zapisano częściowo: 1 z 2.",
      result: {
        translations: {
          partial: true,
          written: [{ kind: "stage", id: 1, polish: "Kontakt", english: "Contact" }],
          failed: null,
          outcome_unknown: null,
          not_attempted: [{ kind: "product", id: 3, polish: "Pakiet Demo", english: "Demo package" }],
        },
      },
    });
    const card = window.document.querySelector(".agent-action-card");
    card.querySelector(".agent-action-confirm").click();
    await vi.waitFor(() => expect(card.dataset.pending).toBe("false"));

    window.ApiClient.post.mockResolvedValueOnce({ response: "Ok" });
    card.querySelector(".agent-action-retry").click();

    await vi.waitFor(() => expect(window.ApiClient.post).toHaveBeenCalledTimes(3));
    expect(window.ApiClient.post).toHaveBeenLastCalledWith("/api/agent/chat", {
      prompt: "Przygotuj angielskie tłumaczenia dla pozostałych, jeszcze nieprzetłumaczonych nazw.",
      lang: "pl",
    }, { timeout: 220000 });
  });

  it("flags a fully-written batch with a post-write warning instead of a clean success", async () => {
    const { window } = realm({ chat: "open" });
    await chatWithCard(window);
    window.ApiClient.post.mockResolvedValueOnce({
      response: "Zapisano wszystkie tłumaczenia (1), ale jedna operacja zgłosiła błąd po zapisie — sprawdź wynik.",
      result: {
        translations: {
          partial: true,
          written: [{
            kind: "bonus_name", id: 1, polish: "Bonus", english: "Bonus EN",
            warning: "Zapis powiódł się, ale operacja zgłosiła błąd po zapisie.",
          }],
          failed: null,
          outcome_unknown: null,
          not_attempted: [],
        },
      },
    });

    const card = window.document.querySelector(".agent-action-card");
    card.querySelector(".agent-action-confirm").click();
    await vi.waitFor(() => expect(card.dataset.pending).toBe("false"));

    expect(card.classList.contains("is-partial")).toBe(true);
    expect(card.querySelector(".agent-action-outcome").textContent).toContain("Bonus");
    // Nothing failed or was skipped here -- no point offering a retry.
    expect(card.querySelector(".agent-action-retry")).toBeNull();
  });

  it("a fully clean batch still gets the ordinary 'executed' card, not the partial one", async () => {
    const { window } = realm({ chat: "open" });
    await chatWithCard(window);
    window.ApiClient.post.mockResolvedValueOnce({
      response: "Zapisano angielskie nazwy: 1.",
      result: {
        translations: {
          partial: false,
          written: [{ kind: "stage", id: 1, polish: "Kontakt", english: "Contact" }],
          failed: null,
          outcome_unknown: null,
          not_attempted: [],
        },
      },
    });

    const card = window.document.querySelector(".agent-action-card");
    card.querySelector(".agent-action-confirm").click();
    await vi.waitFor(() => expect(card.dataset.pending).toBe("false"));

    expect(card.classList.contains("is-complete")).toBe(true);
    expect(card.classList.contains("is-partial")).toBe(false);
    expect(card.querySelector(".agent-action-outcome")).toBeNull();
  });
});

describe("an instance without the assistant", () => {
  // Trial and pilot instances are created without a model (tools/instance.py
  // --no-ai). The assistant is then absent, not an "offline" button forever.
  it("removes the button and the dock once the server says it is off", async () => {
    const { window } = realm({ chat: "open", status: { available: false, disabled: true, message: "off" } });
    await flush();

    expect(window.document.getElementById("agent-toggle")).toBeNull();
    expect(window.document.querySelector(".agent-dock")).toBeNull();
    expect(window.document.documentElement.dataset.chat).toBe("closed");
  });

  it("keeps them when the model is merely unavailable", async () => {
    const { window } = realm({ status: { available: false, message: "down" } });
    await flush();

    expect(window.document.getElementById("agent-toggle")).not.toBeNull();
  });
  /*
     AI Act art. 50 (applies from 02.08.2026): people must be told they are
     talking to an AI, and AI-written text should be marked in a way software
     can read, not only by colour.
  */
  it("tells the reader the replies come from an AI model", () => {
    const { window } = realm();

    const notice = window.document.getElementById("agent-ai-notice");
    expect(notice).not.toBeNull();
    expect(notice.textContent).toContain("model AI");
  });

  it("marks the model's own messages as AI-generated", async () => {
    const { window } = realm();
    await flush();

    const aiMessages = window.document.querySelectorAll(".agent-msg.ai");
    expect(aiMessages.length).toBeGreaterThan(0);
    for (const msg of aiMessages) expect(msg.dataset.generatedBy).toBe("ai");
  });
});
