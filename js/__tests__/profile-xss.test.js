import { readFileSync } from "node:fs";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { loadScript } from "./helpers/load-script.js";

globalThis.ApiClient = { get: vi.fn(), post: vi.fn(), patch: vi.fn(), clearToken: vi.fn() };
loadScript("js/icons.js");
loadScript("js/utils.js");
loadScript("js/app.js");
loadScript("js/profile.js");

const PROJECT_ROOT = path.resolve(import.meta.dirname, "..", "..");
const PAYLOAD = '<img src=x onerror="window.__profilePwned=true">';

function profileDom() {
  document.body.innerHTML = `
    <div id="profile-avatar"></div><div id="profile-name"></div><div id="profile-company"></div>
    <div id="profile-email"></div><div id="profile-phone"></div><div id="profile-joined"></div><div id="profile-level"></div>
    <form id="profile-form"><input id="pf-name"><input id="pf-company"><input id="pf-phone"><textarea id="pf-bio"></textarea>
      <input id="pf-email"><button id="save-profile-btn"></button></form>
    <form id="password-form"><input id="pw-current"><input id="pw-new"><input id="pw-new2"><div id="pw-error"></div>
      <button id="save-password-btn"></button></form>
    <div id="psb1"></div><div id="psb2"></div><div id="psb3"></div><div id="psb4"></div><div id="pw-strength-label"></div>`;
}

beforeEach(() => {
  profileDom();
  sessionStorage.clear();
  window.__profilePwned = false;
  ApiClient.get.mockReset();
  ApiClient.post.mockReset();
  ApiClient.patch.mockReset();
  ApiClient.clearToken.mockReset();
  vi.restoreAllMocks();
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("profile session and output boundary", () => {
  it("renders malicious public profile fields and tier names as inert text", () => {
    renderProfileCard({ name: PAYLOAD, company: PAYLOAD, email: PAYLOAD, phone: PAYLOAD, created_at: "2026-08-22", role: "partner", tier_name: PAYLOAD, tier_color: PAYLOAD });
    expect(document.getElementById("profile-name").textContent).toBe(PAYLOAD);
    expect(document.getElementById("profile-company").textContent).toBe(PAYLOAD);
    expect(document.getElementById("profile-level").querySelector("img, script")).toBeNull();
    // A tier colour that is not a plain six-digit hex never reaches style.
    expect(document.querySelector("#profile-level .level-badge").style.color).toBe("");
    expect(document.querySelector("img, script")).toBeNull();
    expect(window.__profilePwned).toBe(false);
  });

  it("uses server-backed identity rather than a cached storage role", async () => {
    document.body.dataset.page = "partner-profile";
    sessionStorage.setItem("ph_session", JSON.stringify({ id: 99, role: "admin" }));
    const rehydrate = vi.spyOn(Ui, "rehydrateSession").mockResolvedValue({ id: 7, role: "partner", name: "Server Partner" });
    ApiClient.get.mockResolvedValue({ user: { id: 7, role: "partner", name: "Server Partner", created_at: "2026-08-22" } });
    await initProfilePage();
    expect(rehydrate).toHaveBeenCalledWith(ApiClient);
    expect(ApiClient.get).toHaveBeenCalledWith("/api/users/7");
    expect(document.getElementById("profile-name").textContent).toBe("Server Partner");
  });

  it("caches only public display fields after a confirmed profile save", async () => {
    const original = { id: 7, role: "partner", name: "Before" };
    setupProfileForm(original);
    document.getElementById("pf-name").value = "After";
    document.getElementById("pf-company").value = "ACME";
    document.getElementById("pf-phone").value = "123";
    document.getElementById("pf-bio").value = "Bio";
    ApiClient.patch.mockResolvedValue({ user: { ...original, name: "After", company: "ACME", phone: "123", bio: "Bio", created_at: "2026-08-22", password_hash: "secret", session_token: "secret", smtp_password: "secret" } });
    document.getElementById("profile-form").dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    await vi.waitFor(() => expect(JSON.parse(sessionStorage.getItem("ph_session"))).toMatchObject({ name: "After" }));
    const cached = JSON.parse(sessionStorage.getItem("ph_session"));
    expect(cached).toMatchObject({ id: 7, role: "partner", name: "After" });
    expect(cached).not.toHaveProperty("password_hash");
    expect(cached).not.toHaveProperty("session_token");
    expect(cached).not.toHaveProperty("smtp_password");
  });

  it("logs out after password change through cookie API and shared navigation", async () => {
    vi.useFakeTimers();
    setupPasswordForm({ id: 7 });
    document.getElementById("pw-current").value = "OldPassword1!";
    document.getElementById("pw-new").value = "NewPassword1!";
    document.getElementById("pw-new2").value = "NewPassword1!";
    ApiClient.post.mockResolvedValue({});
    const navigate = vi.spyOn(Ui, "safeNavigate").mockReturnValue(true);
    document.getElementById("password-form").dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    await vi.runAllTimersAsync();
    expect(ApiClient.post).toHaveBeenCalledWith("/api/auth/logout", {});
    expect(ApiClient.clearToken).toHaveBeenCalled();
    expect(navigate).toHaveBeenCalledWith("../../index.html");
  });

  it("loads utils before partner and profile in the real partner page", () => {
    const html = readFileSync(path.join(PROJECT_ROOT, "pages/partner/profile.html"), "utf8");
    const scripts = [...html.matchAll(/<script\s+src="([^"]+)"/g)].filter((match) => match[1] !== "/locales.js").map((match) => match[1]);
    expect(scripts).toEqual(expect.arrayContaining(["../../js/api.js", "../../js/app.js", "../../js/utils.js", "../../js/partner.js", "../../js/profile.js"]));
    expect(scripts.indexOf("../../js/app.js")).toBeLessThan(scripts.indexOf("../../js/utils.js"));
    expect(scripts.indexOf("../../js/utils.js")).toBeLessThan(scripts.indexOf("../../js/partner.js"));
    expect(scripts.indexOf("../../js/partner.js")).toBeLessThan(scripts.indexOf("../../js/profile.js"));
  });
});

// ─── TIER NAME FOLLOWS THE READER'S LANGUAGE ──────────────────────
//
// The producer names his own commission tiers ("Diament", "Partner Premium");
// tier_name_en is a second column he fills in himself, read through the same
// Ui.producerLabel every other producer label uses. Empty means "not
// translated yet" and the reader gets the Polish original.
describe("profile tier badge follows the reader's language", () => {
  afterEach(() => {
    App.lang = "pl";
  });

  it("shows the producer's own English tier name on the badge when reading English", () => {
    App.lang = "en";
    renderProfileCard({ name: "Ada", role: "partner", tier_name: "Diament", tier_name_en: "Diamond", tier_color: "#2244ff", created_at: "2026-08-22" });
    expect(document.querySelector("#profile-level .level-badge").textContent).toContain("Diamond");
  });

  it("falls back to the Polish tier name in English when no translation exists yet", () => {
    App.lang = "en";
    renderProfileCard({ name: "Ada", role: "partner", tier_name: "Diament", tier_name_en: "", tier_color: "#2244ff", created_at: "2026-08-22" });
    expect(document.querySelector("#profile-level .level-badge").textContent).toContain("Diament");
  });

  it("redraws the tier badge in English on a language switch without discarding an unsaved profile-form draft", () => {
    document.body.dataset.page = "partner-profile";
    const user = { id: 7, role: "partner", name: "Ada Partner", tier_name: "Diament", tier_name_en: "Diamond", tier_color: "#2244ff", created_at: "2026-08-22" };
    populateForm(user);
    renderProfileCard(user);
    // Mirrors initProfilePage's own App.onRerender registration: the card is
    // redrawn on a language switch, the form is never touched by it.
    App.onRerender(() => renderProfileCard(user));
    document.getElementById("pf-company").value = "Unsaved Draft Co";

    App.setLang("en");

    expect(document.getElementById("pf-company").value).toBe("Unsaved Draft Co");
    expect(document.querySelector("#profile-level .level-badge").textContent).toContain("Diamond");
  });

  it("renders a malicious English tier name as inert text, exactly like the Polish original", () => {
    App.lang = "en";
    renderProfileCard({ name: "Ada", role: "partner", tier_name: "Safe Name", tier_name_en: PAYLOAD, tier_color: PAYLOAD, created_at: "2026-08-22" });
    expect(document.querySelector("#profile-level").querySelector("img, script")).toBeNull();
    expect(window.__profilePwned).toBe(false);
  });
});
