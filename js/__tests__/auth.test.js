import { readFileSync } from "node:fs";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { loadScript } from "./helpers/load-script.js";

const PROJECT_ROOT = path.resolve(import.meta.dirname, "..", "..");

// Load order matters: the login controller references the API client and
// the shared app module inside its handlers.
loadScript("js/api.js");
loadScript("js/app.js");
loadScript("js/auth.js");

function buildLoginDom() {
  document.body.innerHTML = `
    <div class="auth-form-container">
      <div class="role-tabs">
        <button type="button" class="role-tab" data-role="producent">Producent</button>
        <button type="button" class="role-tab" data-role="partner">Partner</button>
      </div>
      <form id="auth-form">
        <input id="auth-email" type="email" />
        <div class="password-field">
          <input id="auth-password" type="password" />
          <button type="button" id="toggle-password"></button>
        </div>
        <div id="auth-error">
          <span data-i18n="login_error"></span>
        </div>
        <a href="#" class="forgot-link">Zapomniałem hasła</a>
        <button type="submit" id="login-btn">Zaloguj się</button>
      </form>
    </div>
  `;
}

function initPage() {
  document.dispatchEvent(new Event("DOMContentLoaded"));
}

function fillForm(email, password) {
  document.getElementById("auth-email").value = email;
  document.getElementById("auth-password").value = password;
}

function submitForm() {
  document
    .getElementById("auth-form")
    .dispatchEvent(new Event("submit", { cancelable: true, bubbles: true }));
}

function click(el) {
  el.dispatchEvent(new MouseEvent("click", { bubbles: true }));
}

// handleLogin has a single await point (the ApiClient.post call) before the
// rest of the branch runs synchronously to completion, so two microtask
// ticks are always enough to observe the settled state.
async function flush() {
  await Promise.resolve();
  await Promise.resolve();
}

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  selectedRole = "producent";
  buildLoginDom();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("initialisation", () => {
  it("wires the form when no session is stored", () => {
    initPage();

    // Proof the form is wired: a role-tab click now has an effect.
    const partnerTab = document.querySelector('.role-tab[data-role="partner"]');
    click(partnerTab);
    expect(partnerTab.classList.contains("active")).toBe(true);
  });

  it("redirects straight to the stored role's dashboard and does not wire the form when public session state exists", () => {
    sessionStorage.setItem(
      "ph_session",
      JSON.stringify({ id: 5, role: "partner" }),
    );

    initPage();

    expect(window.location.pathname).toContain("pages/partner/dashboard.html");

    // Proof the form was never wired: a role-tab click has no effect.
    const producerTab = document.querySelector('.role-tab[data-role="producent"]');
    click(producerTab);
    expect(producerTab.classList.contains("active")).toBe(false);
  });
});

describe("corrupted session cache", () => {
  it.each(["{not json", "null", '"partner"', "{}"])("clears an unusable cached session %j and still wires the form", (value) => {
    sessionStorage.setItem("ph_session", value);
    const before = window.location.href;

    expect(() => initPage()).not.toThrow();

    expect(sessionStorage.getItem("ph_session")).toBeNull();
    expect(window.location.href).toBe(before);
    const partnerTab = document.querySelector('.role-tab[data-role="partner"]');
    click(partnerTab);
    expect(partnerTab.classList.contains("active")).toBe(true);
  });
});

describe("role tabs", () => {
  it("sets the active class on the clicked tab and clears it from the others", () => {
    initPage();
    const producerTab = document.querySelector('.role-tab[data-role="producent"]');
    const partnerTab = document.querySelector('.role-tab[data-role="partner"]');
    producerTab.classList.add("active");

    click(partnerTab);

    expect(partnerTab.classList.contains("active")).toBe(true);
    expect(producerTab.classList.contains("active")).toBe(false);
  });

  it("updates the email placeholder for the clicked role", () => {
    initPage();
    click(document.querySelector('.role-tab[data-role="partner"]'));
    expect(document.getElementById("auth-email").placeholder).toBe(
      "partner@firma.pl",
    );

    // Both doors hint at the SHAPE of an address, never at a real account.
    click(document.querySelector('.role-tab[data-role="producent"]'));
    expect(document.getElementById("auth-email").placeholder).toBe(
      "admin@firma.pl",
    );
  });

});

// The login page shows no demo account and no password: such a panel would be
// shown to everyone who opens the page and invite guessing credentials.
describe("no demo credentials on the login page", () => {
  it("prints no account, and the page carries no element to print one into", () => {
    const html = readFileSync(path.join(PROJECT_ROOT, "index.html"), "utf8");

    expect(html).not.toContain("demo-box");
    expect(html).not.toContain("demo-fill-btn");
    expect(html).not.toContain("demo-email");
    expect(html).not.toContain("demo-pass");
    expect(html).not.toContain("admin123");
    expect(html).not.toContain("owner@vendor.example");
  });

  it("keeps no credential table in the controller either", () => {
    const source = readFileSync(path.join(PROJECT_ROOT, "js", "auth.js"), "utf8");

    expect(source).not.toMatch(/password:\s*['"]/);
    expect(source).not.toContain("DEMO_USERS");
  });
});

describe("validation", () => {
  it("shows the fill-all-fields message and never calls the API when both fields are empty", () => {
    initPage();
    const postMock = vi.spyOn(ApiClient, "post");

    submitForm();

    expect(document.querySelector("#auth-error span").textContent).toBe(
      "Wypełnij wszystkie pola.",
    );
    expect(
      document.getElementById("auth-error").classList.contains("show"),
    ).toBe(true);
    expect(postMock).not.toHaveBeenCalled();
  });

  it("shows the same message when only the email is filled in", () => {
    initPage();
    const postMock = vi.spyOn(ApiClient, "post");
    fillForm("owner@vendor.example", "");

    submitForm();

    expect(document.querySelector("#auth-error span").textContent).toBe(
      "Wypełnij wszystkie pola.",
    );
    expect(postMock).not.toHaveBeenCalled();
  });

  it("shows the same message when only the password is filled in", () => {
    initPage();
    const postMock = vi.spyOn(ApiClient, "post");
    fillForm("", "admin123");

    submitForm();

    expect(document.querySelector("#auth-error span").textContent).toBe(
      "Wypełnij wszystkie pola.",
    );
    expect(postMock).not.toHaveBeenCalled();
  });

  it("trims surrounding whitespace from the email before sending it", async () => {
    initPage();
    const postMock = vi
      .spyOn(ApiClient, "post")
      .mockResolvedValue({ user: { id: 1, role: "admin" } });
    fillForm("  owner@vendor.example  ", "admin123");

    submitForm();
    await flush();

    expect(postMock).toHaveBeenCalledWith("/api/auth/login", {
      email: "owner@vendor.example",
      password: "admin123",
      role: "producent",
    });
  });
});

describe("submit success", () => {
  it("stores only the serialised public user and navigates to the admin dashboard", async () => {
    initPage();
    vi.spyOn(ApiClient, "post").mockResolvedValue({
      user: { id: 1, role: "admin" },
    });
    fillForm("owner@vendor.example", "admin123");

    submitForm();
    await flush();

    expect(sessionStorage.getItem("ph_token")).toBeNull();
    expect(sessionStorage.getItem("ph_session")).toBe(
      JSON.stringify({ id: 1, role: "admin" }),
    );
    expect(window.location.pathname).toContain("pages/admin/dashboard.html");
  });

  it("navigates to the partner dashboard on a partner login", async () => {
    initPage();
    vi.spyOn(ApiClient, "post").mockResolvedValue({
      user: { id: 2, role: "partner" },
    });
    fillForm("partner@it-solutions.pl", "partner123");

    submitForm();
    await flush();

    expect(window.location.pathname).toContain(
      "pages/partner/dashboard.html",
    );
  });

  it("sends the role currently selected in the tabs as part of the request payload", async () => {
    initPage();
    const postMock = vi.spyOn(ApiClient, "post").mockResolvedValue({
      user: { id: 3, role: "partner" },
    });
    click(document.querySelector('.role-tab[data-role="partner"]'));
    fillForm("partner@it-solutions.pl", "partner123");

    submitForm();
    await flush();

    expect(postMock.mock.calls[0][1].role).toBe("partner");
  });
});

describe("submit failure", () => {
  it("shows the server error, marks both inputs as errored, and re-enables the submit button with its original markup", async () => {
    initPage();
    vi.spyOn(ApiClient, "post").mockRejectedValue(
      new Error("Nieprawidłowe dane logowania"),
    );
    const btn = document.getElementById("login-btn");
    const originalHtml = btn.innerHTML;
    fillForm("owner@vendor.example", "wrongpass");

    submitForm();
    await flush();

    expect(document.querySelector("#auth-error span").textContent).toBe(
      "Nieprawidłowe dane logowania",
    );
    expect(
      document.getElementById("auth-email").classList.contains("error"),
    ).toBe(true);
    expect(
      document.getElementById("auth-password").classList.contains("error"),
    ).toBe(true);
    expect(
      document.getElementById("auth-error").classList.contains("show"),
    ).toBe(true);
    expect(btn.disabled).toBe(false);
    expect(btn.innerHTML).toBe(originalHtml);
  });

  it("falls back to the translated login-error string when the rejection carries no message", async () => {
    initPage();
    vi.spyOn(ApiClient, "post").mockRejectedValue(new Error());
    fillForm("owner@vendor.example", "wrongpass");

    submitForm();
    await flush();

    expect(document.querySelector("#auth-error span").textContent).toBe(
      App.t("login_error"),
    );
  });

  it("does not store session display state when the request rejects", async () => {
    initPage();
    vi.spyOn(ApiClient, "post").mockRejectedValue(new Error("nope"));
    fillForm("owner@vendor.example", "wrongpass");

    submitForm();
    await flush();

    expect(sessionStorage.getItem("ph_token")).toBeNull();
    expect(sessionStorage.getItem("ph_session")).toBeNull();
  });
});

describe("in-flight state", () => {
  it("disables the submit button and shows a spinner while the request is pending", async () => {
    initPage();
    let resolvePost;
    const pending = new Promise((resolve) => {
      resolvePost = resolve;
    });
    vi.spyOn(ApiClient, "post").mockReturnValue(pending);
    fillForm("owner@vendor.example", "admin123");

    submitForm();

    const btn = document.getElementById("login-btn");
    expect(btn.disabled).toBe(true);
    expect(btn.innerHTML).toContain("spinner");

    resolvePost({ user: { id: 1, role: "admin" } });
    await pending;
    await flush();
  });
});

describe("password visibility toggle", () => {
  it("flips the password input type between masked and text and back", () => {
    initPage();
    const passInput = document.getElementById("auth-password");
    const toggleBtn = document.getElementById("toggle-password");
    expect(passInput.type).toBe("password");

    click(toggleBtn);
    expect(passInput.type).toBe("text");

    click(toggleBtn);
    expect(passInput.type).toBe("password");
  });
});

describe("password recovery link", () => {
  it("asks the server for a link and reports the same thing for any address", async () => {
    // The wording must not depend on whether the account exists, or the form
    // becomes a way of discovering who has an account here.
    buildLoginDom();
    initPage();
    fillForm("ktokolwiek@test.pl", "");
    const post = vi.spyOn(ApiClient, "post").mockResolvedValueOnce({});

    click(document.querySelector(".forgot-link"));
    await flush();

    expect(post).toHaveBeenCalledWith("/api/auth/password-reset", {
      email: "ktokolwiek@test.pl",
    });
    const notice = document.getElementById("auth-error").textContent;
    expect(notice).toContain("Jeśli konto o tym adresie istnieje");
  });

  it("says the same thing when the server refuses", async () => {
    buildLoginDom();
    initPage();
    fillForm("ktokolwiek@test.pl", "");
    vi.spyOn(ApiClient, "post").mockRejectedValueOnce(new Error("429"));

    click(document.querySelector(".forgot-link"));
    await flush();

    expect(document.getElementById("auth-error").textContent).toContain(
      "Jeśli konto o tym adresie istnieje",
    );
  });

  it("asks for the address first instead of sending an empty request", async () => {
    buildLoginDom();
    initPage();
    const post = vi.spyOn(ApiClient, "post");

    click(document.querySelector(".forgot-link"));
    await flush();

    expect(post).not.toHaveBeenCalled();
    expect(document.getElementById("auth-error").textContent).toContain("adres e-mail");
  });
});

describe("second factor at sign-in", () => {
  it("asks for the code instead of signing in when the server owes a second factor", async () => {
    buildLoginDom();
    initPage();
    fillForm("wlasciciel@test.pl", "Haslo-123");
    vi.spyOn(ApiClient, "post").mockResolvedValueOnce({
      mfa_required: true,
      challenge: "bilet-123",
    });

    submitForm();
    await flush();

    // No session is cached, because none exists yet.
    expect(sessionStorage.getItem("ph_session")).toBeNull();
    expect(document.getElementById("mfa-login-code")).not.toBeNull();
  });

  it("finishes the login with the code and the challenge it was given", async () => {
    buildLoginDom();
    initPage();
    fillForm("wlasciciel@test.pl", "Haslo-123");
    const post = vi.spyOn(ApiClient, "post")
      .mockResolvedValueOnce({ mfa_required: true, challenge: "bilet-123" })
      .mockResolvedValueOnce({ user: { id: 1, role: "admin", name: "Wlasciciel" } });
    submitForm();
    await flush();

    document.getElementById("mfa-login-code").value = "123456";
    click(document.getElementById("mfa-login-btn"));
    await flush();

    expect(post).toHaveBeenLastCalledWith("/api/auth/mfa/login", {
      challenge: "bilet-123",
      code: "123456",
    });
  });

  it("lets a mistyped code be retried without starting from the password", async () => {
    buildLoginDom();
    initPage();
    fillForm("wlasciciel@test.pl", "Haslo-123");
    vi.spyOn(ApiClient, "post")
      .mockResolvedValueOnce({ mfa_required: true, challenge: "bilet-123" })
      .mockRejectedValueOnce(new Error("Nieprawidłowy kod. Spróbuj ponownie."));
    submitForm();
    await flush();

    document.getElementById("mfa-login-code").value = "000000";
    click(document.getElementById("mfa-login-btn"));
    await flush();

    expect(document.getElementById("mfa-login-error").textContent).toContain("Nieprawidłowy kod");
    expect(document.getElementById("mfa-login-code").value).toBe("");
  });
});

// The consent page sends a signed-out user here as index.html?oauth=<id>; after
// the login (and the second factor) they must land back on THAT consent
// screen. Only an id of the server's own shape is accepted -- never a URL, so
// the parameter cannot become an open redirect.
describe("return to a pending OAuth consent", () => {
  const OAUTH_ID = "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789_-abcde";

  beforeEach(() => {
    window.history.replaceState(null, "", `/index.html?oauth=${OAUTH_ID}`);
  });

  function expectConsentPage() {
    expect(window.location.pathname).toBe("/pages/oauth/consent.html");
    expect(window.location.search).toBe(`?req=${OAUTH_ID}`);
  }

  it("goes straight back to the consent page when already signed in", () => {
    sessionStorage.setItem("ph_session", JSON.stringify({ id: 1, role: "admin" }));
    initPage();
    expectConsentPage();
  });

  it("returns there after a password login", async () => {
    initPage();
    vi.spyOn(ApiClient, "post").mockResolvedValue({ user: { id: 2, role: "partner" } });
    fillForm("partner@firma.pl", "Haslo-123");
    submitForm();
    await flush();
    expectConsentPage();
  });

  it("returns there after the second factor", async () => {
    initPage();
    fillForm("wlasciciel@test.pl", "Haslo-123");
    vi.spyOn(ApiClient, "post")
      .mockResolvedValueOnce({ mfa_required: true, challenge: "bilet-123" })
      .mockResolvedValueOnce({ user: { id: 1, role: "admin" } });
    submitForm();
    await flush();
    document.getElementById("mfa-login-code").value = "123456";
    click(document.getElementById("mfa-login-btn"));
    await flush();
    expectConsentPage();
  });

  it.each([
    ["https://evil.example"],
    ["https%3A%2F%2Fevil.example%2Fcb"],
    ["short"],
    ["..%2F..%2Fpages%2Fadmin%2Fusers.html"],
    [`${OAUTH_ID}%21`],
    ["a".repeat(65)],
  ])("ignores oauth=%s and goes to the dashboard as usual", (value) => {
    window.history.replaceState(null, "", `/index.html?oauth=${value}`);
    sessionStorage.setItem("ph_session", JSON.stringify({ id: 1, role: "admin" }));
    initPage();
    expect(window.location.pathname).toBe("/pages/admin/dashboard.html");
  });

  it("keeps the role target when there is no oauth parameter", () => {
    window.history.replaceState(null, "", "/index.html");
    expect(afterLoginTarget("partner")).toBe("pages/partner/dashboard.html");
    expect(afterLoginTarget("observer")).toBe("pages/admin/dashboard.html");
  });
});
