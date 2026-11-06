/* ============================================================
   notifications-translations — the bonus name, in the reader's language
   ============================================================

   A bonus grant notification stores BOTH language snapshots
   (params.bonus / params.bonus_en) from the moment it was written. The
   dictionary substitution itself (App.t(key, params)) has no idea which
   param holds a producer-written label and which holds plain data -- so the
   renderer picks the reader's-language value for `bonus` specifically,
   through the same Ui.producerLabel every other producer label uses, and
   leaves every other param (a customer's name, a lead id) untouched.
   ============================================================ */
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

function notificationsWindow(lang = "pl") {
  const window = new Window({ url: "http://localhost/pages/partner/earnings.html" });
  window.ApiClient = { get: vi.fn(), post: vi.fn() };
  window.App = appStub(lang);
  window.App.onRerender = vi.fn();
  window.document.body.innerHTML = `
    <div class="header-right"></div>
    <div id="notif-list"></div>
  `;
  // js/utils.js is the real shared module (Ui.producerLabel), matching the
  // pattern established for other producer-label tests -- proving the
  // actual renderer, not a stand-in.
  runClassicScript(window, "js/utils.js");
  runClassicScript(window, "js/notifications.js");
  return window;
}

function bonusNotification(overrides = {}) {
  return {
    id: 1,
    type: "success",
    is_read: 0,
    created_at: "2026-09-01T00:00:00Z",
    title: "Przyznano bonus: Nagroda kwartalna",
    body: "500.00 PLN trafi do najbliższej wypłaty.",
    title_key: "ntf_bonus_granted_title",
    body_key: "ntf_bonus_granted_body",
    link: "earnings.html",
    params: { bonus: "Nagroda kwartalna", bonus_en: "Quarterly award", amount: "500.00" },
    ...overrides,
  };
}

describe("bonus grant notification — the reader's own language", () => {
  it("shows the Polish bonus name to a Polish reader", () => {
    const window = notificationsWindow("pl");
    window.Notifications.renderNotifications([bonusNotification()]);

    expect(window.document.querySelector(".notif-item-title").textContent)
      .toBe("Przyznano bonus: Nagroda kwartalna");
  });

  it("shows the producer's English bonus name to an English reader", () => {
    const window = notificationsWindow("en");
    window.Notifications.renderNotifications([bonusNotification()]);

    const text = window.document.querySelector(".notif-item-title").textContent;
    expect(text).toBe("Bonus granted: Quarterly award");
    expect(text).not.toContain("Nagroda kwartalna");
  });

  it("falls back to the Polish snapshot for a grant with no English translation yet", () => {
    const window = notificationsWindow("en");
    window.Notifications.renderNotifications([bonusNotification({
      params: { bonus: "Nagroda kwartalna", bonus_en: "", amount: "500.00" },
    })]);

    expect(window.document.querySelector(".notif-item-title").textContent)
      .toBe("Bonus granted: Nagroda kwartalna");
  });

  it("falls back to the Polish snapshot for an old notification with no bonus_en param at all", () => {
    const window = notificationsWindow("en");
    window.Notifications.renderNotifications([bonusNotification({
      params: { bonus: "Nagroda kwartalna", amount: "500.00" },
    })]);

    expect(window.document.querySelector(".notif-item-title").textContent)
      .toBe("Bonus granted: Nagroda kwartalna");
  });

  it("does not translate an arbitrary notification's params, only the bonus name", () => {
    const window = notificationsWindow("en");
    window.Notifications.renderNotifications([{
      id: 2,
      type: "info",
      is_read: 0,
      created_at: "2026-09-01T00:00:00Z",
      title: "Lead zgłoszony: Firma X",
      body: "Zgłoszenie LD-001 zostało przyjęte do weryfikacji.",
      title_key: "ntf_lead_submitted_title",
      body_key: "ntf_lead_submitted_body",
      link: "leads.html",
      params: { client: "Firma X", client_en: "Should never appear", lead: "LD-001" },
    }]);

    const text = window.document.querySelector(".notif-item-title").textContent;
    expect(text).toBe("Lead submitted: Firma X");
    expect(text).not.toContain("Should never appear");
  });

  it("renders a translated bonus name containing markup as literal text", () => {
    const window = notificationsWindow("en");
    window.Notifications.renderNotifications([bonusNotification({
      params: { bonus: "Nagroda", bonus_en: "<img src=x onerror=alert(1)>", amount: "1.00" },
    })]);

    const item = window.document.querySelector(".notif-item-title");
    expect(item.querySelector("img")).toBeNull();
    expect(item.textContent).toContain("<img src=x onerror=alert(1)>");
  });

  it("an old notification written before this migration still reads by its stored literal", () => {
    const window = notificationsWindow("en");
    window.Notifications.renderNotifications([{
      id: 3,
      type: "success",
      is_read: 0,
      created_at: "2026-01-01T00:00:00Z",
      title: "Przyznano bonus: Stara nagroda",
      body: "",
      title_key: "",
      body_key: "",
      link: "earnings.html",
      params: {},
    }]);

    expect(window.document.querySelector(".notif-item-title").textContent)
      .toBe("Przyznano bonus: Stara nagroda");
  });
});


describe("amounts in notifications — the reader's own number format", () => {
  const wonNotification = (amount) => ({
    id: 3, type: "success", is_read: 0, created_at: "2026-09-01T00:00:00Z",
    title: "Sprzedaż wygrana: Jan Kowalski", body: `Prowizja do zatwierdzenia: ${amount} PLN`,
    title_key: "ntf_sale_won_title", body_key: "ntf_sale_won_body", link: "leads.html",
    params: { client: "Jan Kowalski", amount },
  });

  it("re-formats a stored '16,800' the Polish way for a Polish reader", () => {
    // "Prowizja do zatwierdzenia: 2,520 PLN".
    const window = notificationsWindow("pl");
    window.Notifications.renderNotifications([wonNotification("16,800")]);

    const body = window.document.querySelector("#notif-list").textContent;
    expect(body).toContain("16\u00a0800 PLN");
    expect(body).not.toContain("16,800");
  });

  it("keeps the English grouping for an English reader", () => {
    const window = notificationsWindow("en");
    window.Notifications.renderNotifications([wonNotification("16,800")]);

    expect(window.document.querySelector("#notif-list").textContent).toContain("16,800 PLN");
  });
});
