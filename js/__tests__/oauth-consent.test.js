/* ============================================================
   Consent page for „Połącz z PartnerType”
   ============================================================

   The page is where a person decides whether an application -- whose name
   the application itself chose -- may read their CRM. So the tests pin what
   makes that decision safe: the name is only ever text, the redirect host is
   shown on its own, a local-only client is called out, nothing happens
   without a click, and the browser goes only where the SERVER said.

   The real page markup is mounted (scripts stripped) so the ids the
   controller needs are the ids the page actually has.
   ============================================================ */
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { loadScript } from "./helpers/load-script.js";
import { dictionary } from "./helpers/copy.js";

const PROJECT_ROOT = path.resolve(import.meta.dirname, "..", "..");
const PAGE = path.join(PROJECT_ROOT, "pages", "oauth", "consent.html");
const CONTROLLER = "js/oauth-consent.js";

const ID = "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789_-abcde"; // 43 characters
const REDIRECT = "https://claude.ai/api/mcp/auth_callback?code=c0de&state=xyz&iss=http%3A%2F%2Flocalhost%3A8000";

// A stand-in for App that reads the real dictionaries and can switch language.
const hooks = [];
const AppStub = {
  lang: "pl",
  t(key, params) {
    const value = dictionary(this.lang)[key];
    if (value === undefined) return key;
    if (!params) return value;
    return String(value).replace(/\{(\w+)\}/g, (whole, name) =>
      (params[name] === undefined ? whole : String(params[name])));
  },
  applyTranslations() {},
  onRerender(fn) {
    hooks.push(fn);
  },
};
globalThis.App = AppStub;

if (existsSync(path.join(PROJECT_ROOT, CONTROLLER))) loadScript(CONTROLLER);
// The login page's controller, for the no-loop case at the bottom. It only
// needs App (stubbed above) until a form is submitted.
loadScript("js/auth.js");

function mountPage(search = `?req=${ID}`) {
  window.history.replaceState(null, "", `/pages/oauth/consent.html${search}`);
  const html = existsSync(PAGE) ? readFileSync(PAGE, "utf8") : "<body></body>";
  const body = /<body[^>]*>([\s\S]*)<\/body>/i.exec(html)[1]
    .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, "");
  document.body.innerHTML = body;
}

const PAYLOAD = {
  client_name: "Claude",
  client_name_self_declared: true,
  redirect_host: "claude.ai",
  redirect_is_loopback: false,
  all_redirects_loopback: false,
  scope: "read",
  resource: "http://localhost:8000/mcp",
  resource_kind: "mcp",
  user: { name: "Anna Producentka", role: "admin" },
  expires_at: "2026-10-18T20:00:00+00:00",
};

function reply(status, body) {
  return Promise.resolve({ status, ok: status >= 200 && status < 300, json: () => Promise.resolve(body) });
}

let fetchMock;
let assign;
let storageAtNavigation;

beforeEach(() => {
  AppStub.lang = "pl";
  sessionStorage.clear();
  fetchMock = vi.fn();
  globalThis.fetch = fetchMock;
  storageAtNavigation = [];
  assign = vi.spyOn(window.location, "assign").mockImplementation(() => {
    storageAtNavigation.push(sessionStorage.getItem("ph_session"));
  });
  mountPage();
});

afterEach(() => {
  vi.restoreAllMocks();
});

const $ = (id) => document.getElementById(id);
const flush = () => new Promise((resolve) => setTimeout(resolve, 0));
const visible = (id) => $(id) !== null && !$(id).hidden;

function controller() {
  expect(globalThis.OAuthConsent, "js/oauth-consent.js is not loaded").toBeTypeOf("object");
  return globalThis.OAuthConsent;
}

async function loadWith(status, body) {
  fetchMock.mockReturnValueOnce(reply(status, body));
  await controller().load();
  await flush();
}

describe("the page itself", () => {
  it("has every element the controller fills, and no form element", () => {
    controller();
    for (const id of ["oauth-client-name", "oauth-redirect-host", "oauth-loopback-warning", "oauth-punycode-warning",
      "oauth-signed-in", "oauth-allow", "oauth-deny", "oauth-status"]) {
      expect($(id), id).not.toBeNull();
    }
    expect($("oauth-status").getAttribute("aria-live")).toBe("polite");
    expect(document.querySelector("form")).toBeNull();
    expect($("oauth-loopback-warning").hidden).toBe(true);
    expect($("oauth-punycode-warning").hidden).toBe(true);
  });
});

describe("rendering a pending request", () => {
  it("shows the application's name as literal text, never as markup", async () => {
    await loadWith(200, { ...PAYLOAD, client_name: "<img src=x onerror=alert(1)>" });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(`/api/oauth/requests/${ID}`);
    expect(init.credentials).toBe("same-origin");

    expect($("oauth-client-name").textContent).toBe("<img src=x onerror=alert(1)>");
    expect(document.querySelector("img[src='x']")).toBeNull();
    expect($("oauth-client-name").querySelector("img")).toBeNull();
  });

  it("puts the redirect host in its own element, labels the name as self-declared and says who connects", async () => {
    await loadWith(200, PAYLOAD);

    expect($("oauth-redirect-host").textContent).toBe("claude.ai");
    // Static markup ships in English (the stubbed App does not re-translate
    // it); what the controller writes itself is in the reader's language.
    expect(document.body.textContent).toContain(dictionary("en").oauth_client_self_declared);
    expect(document.body.textContent).toContain(dictionary("en").oauth_scope_read);
    expect($("oauth-signed-in").textContent).toBe("Łączysz jako Anna Producentka (Administrator)");
    expect(document.body.textContent).toContain(dictionary("en").oauth_eu_warning);
    expect(visible("oauth-allow")).toBe(true);
    expect(visible("oauth-deny")).toBe(true);
  });

  it("warns about a client that only ever returns to this computer", async () => {
    await loadWith(200, { ...PAYLOAD, redirect_is_loopback: true, all_redirects_loopback: true,
      redirect_host: "127.0.0.1" });
    expect(visible("oauth-loopback-warning")).toBe(true);
  });

  it("warns when this request returns to this computer, even if the client also registered an internet address", async () => {
    await loadWith(200, { ...PAYLOAD, redirect_is_loopback: true, all_redirects_loopback: false,
      redirect_host: "127.0.0.1" });
    expect(visible("oauth-loopback-warning")).toBe(true);
  });

  it("does not warn when this request returns to the internet, even if other registered addresses are local", async () => {
    await loadWith(200, { ...PAYLOAD, redirect_is_loopback: false, all_redirects_loopback: true });
    expect(visible("oauth-loopback-warning")).toBe(false);
  });

  it("keeps the loopback warning hidden for a client on the internet", async () => {
    await loadWith(200, PAYLOAD);
    expect(visible("oauth-loopback-warning")).toBe(false);
  });

  it("warns when the return address is an encoded international name", async () => {
    await loadWith(200, { ...PAYLOAD, redirect_host: "xn--clude-6ve.ai", redirect_host_punycode: true });
    expect($("oauth-redirect-host").textContent).toBe("xn--clude-6ve.ai");
    expect(visible("oauth-punycode-warning")).toBe(true);
    expect($("oauth-punycode-warning").textContent).toContain(dictionary("en").oauth_punycode_warning);
  });

  it("keeps the punycode warning hidden for a plain host", async () => {
    await loadWith(200, { ...PAYLOAD, redirect_host_punycode: false });
    expect(visible("oauth-punycode-warning")).toBe(false);
  });

  it("re-renders in English from the last payload, without asking the server again", async () => {
    await loadWith(200, PAYLOAD);
    expect(hooks.length).toBeGreaterThanOrEqual(1);

    AppStub.lang = "en";
    hooks.forEach((fn) => fn());

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect($("oauth-signed-in").textContent).toBe("Connecting as Anna Producentka (Administrator)");
    expect($("oauth-redirect-host").textContent).toBe("claude.ai");
  });
});

describe("without a session", () => {
  it("drops the stale ph_session entry before sending the browser to the login page", async () => {
    sessionStorage.setItem("ph_session", JSON.stringify({ id: 1, role: "admin" }));
    await loadWith(401, { error: "Nieautoryzowany dostęp. Zaloguj się." });

    expect(assign).toHaveBeenCalledTimes(1);
    expect(assign).toHaveBeenCalledWith(`/index.html?oauth=${ID}`);
    // At the moment of navigation the entry was already gone -- otherwise the
    // login page would see it and bounce straight back here.
    expect(storageAtNavigation).toEqual([null]);
  });
});

describe("refusals", () => {
  it("explains a missing second factor and links to the user's own profile", async () => {
    sessionStorage.setItem("ph_session", JSON.stringify({ id: 3, role: "partner" }));
    await loadWith(403, { error: "x", code: "mfa_setup_required" });

    expect($("oauth-status").textContent).toBe(dictionary("pl").oauth_mfa_required);
    const link = document.getElementById("oauth-profile-link");
    expect(link.hidden).toBe(false);
    expect(link.getAttribute("href")).toBe("/pages/partner/profile.html");
    expect(visible("oauth-allow")).toBe(false);
    expect(visible("oauth-deny")).toBe(false);
  });

  it("links a staff member to the admin profile", async () => {
    sessionStorage.setItem("ph_session", JSON.stringify({ id: 1, role: "admin" }));
    await loadWith(403, { error: "x", code: "mfa_setup_required" });
    expect($("oauth-profile-link").getAttribute("href")).toBe("/pages/admin/profile.html");
  });

  it.each([
    ["integrations_disabled", "oauth_integrations_off"],
    ["partners_disabled", "oauth_partners_off"],
    ["role_not_allowed", "oauth_role_not_allowed"],
  ])("403 %s shows its own message and hides the buttons", async (code, key) => {
    await loadWith(403, { error: "x", code });
    expect($("oauth-status").textContent).toBe(dictionary("pl")[key]);
    expect(visible("oauth-allow")).toBe(false);
    expect(visible("oauth-deny")).toBe(false);
    expect(visible("oauth-profile-link")).toBe(false);
  });

  it("404 says the request expired", async () => {
    await loadWith(404, { error: "x", code: "request_expired" });
    expect($("oauth-status").textContent).toBe(dictionary("pl").oauth_expired);
    expect(visible("oauth-allow")).toBe(false);
  });

  it("an unexpected status falls back to the generic message", async () => {
    await loadWith(500, { error: "x" });
    expect($("oauth-status").textContent).toBe(dictionary("pl").oauth_error_generic);
  });

  it.each([["?req=../../evil"], [""], ["?req=short"], ["?req=https%3A%2F%2Fevil.example"]])(
    "a malformed or missing id (%s) shows the invalid-request message and fetches nothing",
    async (search) => {
      mountPage(search);
      await controller().load();
      await flush();
      expect(fetchMock).not.toHaveBeenCalled();
      expect($("oauth-status").textContent).toBe(dictionary("pl").oauth_invalid_request);
      expect(visible("oauth-allow")).toBe(false);
    },
  );
});

describe("the decision", () => {
  it("Allow posts approve:true as same-origin JSON and goes exactly where the server said", async () => {
    await loadWith(200, PAYLOAD);
    let resolve;
    fetchMock.mockReturnValueOnce(new Promise((r) => { resolve = r; }));

    $("oauth-allow").click();
    await Promise.resolve();
    expect($("oauth-allow").disabled).toBe(true);
    expect($("oauth-deny").disabled).toBe(true);

    const [url, init] = fetchMock.mock.calls[1];
    expect(url).toBe(`/api/oauth/requests/${ID}/decision`);
    expect(init.method).toBe("POST");
    expect(init.credentials).toBe("same-origin");
    expect(init.headers["Content-Type"]).toBe("application/json");
    expect(JSON.parse(init.body)).toEqual({ approve: true });

    resolve({ status: 200, ok: true, json: () => Promise.resolve({ redirect_to: REDIRECT }) });
    await flush();
    expect(assign).toHaveBeenCalledTimes(1);
    expect(assign).toHaveBeenCalledWith(REDIRECT);
    expect($("oauth-status").textContent).toBe(dictionary("pl").oauth_redirecting);
  });

  it("Deny posts approve:false and follows the same navigation rule", async () => {
    await loadWith(200, PAYLOAD);
    const denied = "https://claude.ai/api/mcp/auth_callback?error=access_denied&state=xyz&iss=x";
    fetchMock.mockReturnValueOnce(reply(200, { redirect_to: denied }));

    $("oauth-deny").click();
    await flush();
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({ approve: false });
    expect(assign).toHaveBeenCalledWith(denied);
  });

  it("a second click while the first is pending sends nothing more", async () => {
    await loadWith(200, PAYLOAD);
    fetchMock.mockReturnValueOnce(new Promise(() => {}));
    $("oauth-allow").click();
    $("oauth-allow").click();
    $("oauth-deny").click();
    await flush();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it.each([
    [{ redirect_to: 42 }],
    [{ redirect_to: "javascript:alert(1)" }],
    [{ redirect_to: "data:text/html,hi" }],
    [{ redirect_to: "//evil.example/cb" }],
    [{}],
  ])("a bad redirect_to (%j) shows an error and never navigates", async (body) => {
    await loadWith(200, PAYLOAD);
    fetchMock.mockReturnValueOnce(reply(200, body));
    $("oauth-allow").click();
    await flush();
    expect(assign).not.toHaveBeenCalled();
    expect($("oauth-status").textContent).toBe(dictionary("pl").oauth_error_generic);
  });

  it("a refusal at decision time shows its message and no navigation", async () => {
    await loadWith(200, PAYLOAD);
    fetchMock.mockReturnValueOnce(reply(403, { error: "x", code: "integrations_disabled" }));
    $("oauth-allow").click();
    await flush();
    expect(assign).not.toHaveBeenCalled();
    expect($("oauth-status").textContent).toBe(dictionary("pl").oauth_integrations_off);
    expect(visible("oauth-allow")).toBe(false);
  });

  it("a network failure at decision time shows the generic message", async () => {
    await loadWith(200, PAYLOAD);
    fetchMock.mockReturnValueOnce(Promise.reject(new Error("offline")));
    $("oauth-allow").click();
    await flush();
    expect(assign).not.toHaveBeenCalled();
    expect($("oauth-status").textContent).toBe(dictionary("pl").oauth_error_generic);
  });

  it("an expired session at decision time goes to the login page with the same id", async () => {
    sessionStorage.setItem("ph_session", JSON.stringify({ id: 1, role: "admin" }));
    await loadWith(200, PAYLOAD);
    fetchMock.mockReturnValueOnce(reply(401, { error: "x" }));
    $("oauth-allow").click();
    await flush();
    expect(assign).toHaveBeenCalledWith(`/index.html?oauth=${ID}`);
    expect(storageAtNavigation).toEqual([null]);
  });
});

describe("no login loop", () => {
  it("a stale ph_session plus a 401 ends on the login form, not back on this page", async () => {
    sessionStorage.setItem("ph_session", JSON.stringify({ id: 1, role: "admin" }));
    await loadWith(401, { error: "Nieautoryzowany dostęp. Zaloguj się." });
    expect(assign).toHaveBeenCalledWith(`/index.html?oauth=${ID}`);

    // The browser is now on the login page, with the storage this page left.
    window.history.replaceState(null, "", `/index.html?oauth=${ID}`);
    document.body.innerHTML = `
      <div class="auth-form-container">
        <div class="role-tabs">
          <button type="button" class="role-tab" data-role="producent"></button>
          <button type="button" class="role-tab" data-role="partner"></button>
        </div>
        <form id="auth-form">
          <input id="auth-email"><input id="auth-password">
          <div id="auth-error"><span data-i18n="login_error"></span></div>
          <button type="submit" id="login-btn"></button>
        </form>
      </div>`;
    document.dispatchEvent(new Event("DOMContentLoaded"));

    expect(window.location.pathname).toBe("/index.html");
    const tab = document.querySelector('.role-tab[data-role="partner"]');
    tab.click();
    expect(tab.classList.contains("active")).toBe(true);
  });
});
