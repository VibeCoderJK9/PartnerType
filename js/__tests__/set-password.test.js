/* ============================================================
   set-password.test.js

   The invited partner's entry point: a fresh token previews
   the invitee's name and e-mail and reveals the password form; an unknown,
   expired or already-accepted token -- and a missing token -- all render
   one clear Polish explanation instead of a blank screen or a stack trace.
   ============================================================ */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { loadScript } from "./helpers/load-script.js";
import { appStub } from "./helpers/copy.js";

// Load order matters: the controller references ApiClient and Icons inside
// its handlers, both stubbed below since this file drives the controller
// directly rather than exercising the real network/icon layers.
globalThis.ApiClient = { get: vi.fn(), post: vi.fn() };
  // the controller reads its copy from the dictionary
  window.App = appStub('pl');
globalThis.Icons = { el: vi.fn(), into: vi.fn() };

loadScript("js/set-password.js");

function buildFixture() {
  document.body.innerHTML = `
    <div id="sp-invalid" hidden>
      <div id="sp-invalid-icon"></div>
      <a href="index.html">Przejdź do logowania</a>
    </div>
    <form id="sp-form" hidden>
      <div id="sp-invitee"></div>
      <input type="password" id="sp-password">
      <button type="button" id="sp-toggle-password"></button>
      <div id="sp-pw-strength">
        <div id="sp-sb1"></div>
        <div id="sp-sb2"></div>
        <div id="sp-sb3"></div>
        <div id="sp-sb4"></div>
      </div>
      <div id="sp-strength-label"></div>
      <input type="password" id="sp-password2">
      <div id="sp-match-hint"></div>
      <div id="sp-error" role="alert"></div>
      <button type="submit" id="sp-submit">Ustaw hasło</button>
    </form>
  `;
}

function initPage() {
  document.dispatchEvent(new Event("DOMContentLoaded"));
}

async function flush() {
  await Promise.resolve();
  await Promise.resolve();
}

function fillPasswords(pw, pw2 = pw) {
  document.getElementById("sp-password").value = pw;
  document.getElementById("sp-password2").value = pw2;
}

function submitForm() {
  document
    .getElementById("sp-form")
    .dispatchEvent(new Event("submit", { cancelable: true, bubbles: true }));
}

beforeEach(() => {
  buildFixture();
  ApiClient.get.mockReset();
  ApiClient.post.mockReset();
  window.history.pushState({}, "", "/set-password.html");
  vi.clearAllMocks();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("token preview", () => {
  it("renders the invitee's name and e-mail as text and shows the form when the server accepts the token", async () => {
    window.history.pushState({}, "", "/set-password.html?token=fresh123");
    ApiClient.get.mockResolvedValueOnce({
      invitation: { name: "Ewa Zaproszona", email: "ewa@test.pl" },
    });

    initPage();
    await flush();

    expect(ApiClient.get).toHaveBeenCalledWith("/api/invitations/fresh123");
    expect(document.getElementById("sp-invitee").textContent).toContain("Ewa Zaproszona");
    expect(document.getElementById("sp-invitee").textContent).toContain("ewa@test.pl");
    expect(document.getElementById("sp-form").hidden).toBe(false);
    expect(document.getElementById("sp-invalid").hidden).toBe(true);
  });

  it("renders one Polish explanation and a link back to login, with no password form, when the server rejects the token", async () => {
    window.history.pushState({}, "", "/set-password.html?token=stale456");
    ApiClient.get.mockRejectedValueOnce(new Error("Zaproszenie jest nieprawidłowe, wygasło lub zostało już wykorzystane."));

    initPage();
    await flush();

    expect(document.getElementById("sp-invalid").hidden).toBe(false);
    expect(document.getElementById("sp-form").hidden).toBe(true);
    expect(document.querySelector("#sp-invalid a[href='index.html']")).not.toBeNull();
  });

  it("behaves exactly like a rejected token when no token is present at all", async () => {
    window.history.pushState({}, "", "/set-password.html");

    initPage();
    await flush();

    expect(ApiClient.get).not.toHaveBeenCalled();
    expect(document.getElementById("sp-invalid").hidden).toBe(false);
    expect(document.getElementById("sp-form").hidden).toBe(true);
  });
});

describe("local validation before the server is called", () => {
  beforeEach(async () => {
    window.history.pushState({}, "", "/set-password.html?token=fresh123");
    ApiClient.get.mockResolvedValueOnce({
      invitation: { name: "Ewa Zaproszona", email: "ewa@test.pl" },
    });
    initPage();
    await flush();
  });

  it("blocks the request and shows the mismatch hint when the two passwords differ", async () => {
    fillPasswords("Haslo12345!", "SomethingElse999!");

    submitForm();
    await flush();

    expect(ApiClient.post).not.toHaveBeenCalled();
    expect(document.getElementById("sp-match-hint").textContent).toContain("nie są zgodne");
    expect(document.getElementById("sp-error").textContent).toBe("Hasła nie są zgodne.");
  });

  it("blocks the request and says so when the password is shorter than the minimum", async () => {
    fillPasswords("short1");

    submitForm();
    await flush();

    expect(ApiClient.post).not.toHaveBeenCalled();
    expect(document.getElementById("sp-error").textContent).toBe(
      "Hasło musi mieć co najmniej 8 znaków.",
    );
  });
});

describe("form UI helpers", () => {
  beforeEach(async () => {
    window.history.pushState({}, "", "/set-password.html?token=fresh123");
    ApiClient.get.mockResolvedValueOnce({
      invitation: { name: "Ewa Zaproszona", email: "ewa@test.pl" },
    });
    initPage();
    await flush();
  });

  it("scores the password strength meter as the invitee types", () => {
    const pwInput = document.getElementById("sp-password");
    pwInput.value = "BardzoSilneHaslo123!";
    pwInput.dispatchEvent(new Event("input"));

    expect(document.getElementById("sp-strength-label").textContent).toBe("Silne");
    expect(document.getElementById("sp-sb4").className).toContain("strong");
  });

  it("renders the green match hint once both passwords agree", () => {
    document.getElementById("sp-password").value = "BardzoSilneHaslo123!";
    const pw2 = document.getElementById("sp-password2");
    pw2.value = "BardzoSilneHaslo123!";
    pw2.dispatchEvent(new Event("input"));

    expect(document.getElementById("sp-match-hint").textContent).toBe("Hasła są zgodne");
  });

  it("flips the password input type between masked and text and back", () => {
    const pwInput = document.getElementById("sp-password");
    expect(pwInput.type).toBe("password");

    document.getElementById("sp-toggle-password").dispatchEvent(new MouseEvent("click", { bubbles: true }));
    expect(pwInput.type).toBe("text");

    document.getElementById("sp-toggle-password").dispatchEvent(new MouseEvent("click", { bubbles: true }));
    expect(pwInput.type).toBe("password");
  });
});

describe("submit", () => {
  it("posts the password exactly once to the accept endpoint and then navigates to the login page", async () => {
    window.history.pushState({}, "", "/set-password.html?token=fresh123");
    ApiClient.get.mockResolvedValueOnce({
      invitation: { name: "Ewa Zaproszona", email: "ewa@test.pl" },
    });
    ApiClient.post.mockResolvedValueOnce({ ok: true });
    initPage();
    await flush();
    fillPasswords("BardzoSilneHaslo123!");

    submitForm();
    await flush();

    expect(ApiClient.post).toHaveBeenCalledTimes(1);
    expect(ApiClient.post).toHaveBeenCalledWith("/api/invitations/fresh123/accept", {
      password: "BardzoSilneHaslo123!",
    });
    expect(window.location.pathname).toContain("index.html");
  });

  it("shows the server's message and leaves the form usable when the server rejects the submit", async () => {
    window.history.pushState({}, "", "/set-password.html?token=fresh123");
    ApiClient.get.mockResolvedValueOnce({
      invitation: { name: "Ewa Zaproszona", email: "ewa@test.pl" },
    });
    ApiClient.post.mockRejectedValueOnce(new Error("Zaproszenie jest nieprawidłowe, wygasło lub zostało już wykorzystane."));
    initPage();
    await flush();
    fillPasswords("BardzoSilneHaslo123!");

    submitForm();
    await flush();

    expect(document.getElementById("sp-error").textContent).toBe(
      "Zaproszenie jest nieprawidłowe, wygasło lub zostało już wykorzystane.",
    );
    expect(document.getElementById("sp-submit").disabled).toBe(false);
  });
});

describe("recovery mode (reset=1)", () => {
  it("shows the password form without asking the server whose account it is", async () => {
    // Previewing a reset link would turn a stolen link into a way of reading
    // the owner's name and address before anything is proven.
    window.history.pushState({}, "", "/set-password.html?token=reset789&reset=1");

    initPage();
    await flush();

    expect(ApiClient.get).not.toHaveBeenCalled();
    expect(document.getElementById("sp-form").hidden).toBe(false);
    expect(document.getElementById("sp-invitee").textContent).not.toContain("@");
  });

  it("posts the new password to the recovery endpoint, not the invitation one", async () => {
    window.history.pushState({}, "", "/set-password.html?token=reset789&reset=1");
    ApiClient.post.mockResolvedValueOnce({ message: "ok" });
    initPage();
    await flush();
    fillPasswords("Nowe-Haslo-123");

    submitForm();
    await flush();

    expect(ApiClient.post).toHaveBeenCalledWith(
      "/api/auth/password-reset/reset789",
      { password: "Nowe-Haslo-123" },
    );
  });

  it("still uses the invitation endpoint when the flag is absent", async () => {
    window.history.pushState({}, "", "/set-password.html?token=invite789");
    ApiClient.get.mockResolvedValueOnce({
      invitation: { name: "Ewa", email: "ewa@test.pl" },
    });
    initPage();
    await flush();
    ApiClient.post.mockResolvedValueOnce({});
    fillPasswords("Nowe-Haslo-123");

    submitForm();
    await flush();

    expect(ApiClient.post).toHaveBeenCalledWith(
      "/api/invitations/invite789/accept",
      { password: "Nowe-Haslo-123" },
    );
  });
});
