import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { loadScript } from "./helpers/load-script.js";
import { appStub } from "./helpers/copy.js";

// api.js reports network and session failures in the user's language, so it
// reads them from the dictionary like every other module. In the browser
// app.js is always loaded by the time a request is made; here the realm has
// to supply it.
globalThis.App = appStub("pl");

loadScript("js/api.js");

afterEach(() => {
  vi.restoreAllMocks();
});

describe("ApiClient public session cache", () => {
  beforeEach(() => {
    sessionStorage.clear();
    global.fetch = vi.fn();
  });

  it("does not expose token getter or setter APIs", () => {
    expect(ApiClient.getToken).toBeUndefined();
    expect(ApiClient.setToken).toBeUndefined();
  });

  it("keeps clearToken as a compatibility name for clearing display state", () => {
    sessionStorage.setItem("ph_session", JSON.stringify({ role: "admin" }));
    sessionStorage.setItem("ph_token", "legacy-secret");
    ApiClient.clearToken();
    expect(sessionStorage.getItem("ph_session")).toBeNull();
    expect(sessionStorage.getItem("ph_token")).toBeNull();
  });

  it("surfaces the module's verbatim Polish network-failure message on a rejected fetch", async () => {
    global.fetch.mockRejectedValueOnce(new Error("network down"));

    await expect(ApiClient.get("/api/leads")).rejects.toThrow(
      "Brak połączenia z serwerem. Sprawdź czy serwer działa.",
    );
  });

  it("getLoginPath returns the root-relative index path when not inside the pages tree", () => {
    window.history.pushState({}, "", "/dashboard.html");
    expect(getLoginPath()).toBe("/index.html");
  });
});

describe("ApiClient request headers and body", () => {
  beforeEach(() => {
    sessionStorage.clear();
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ ok: true }),
    });
  });

  it("uses same-origin cookies and sends no authorization header", async () => {
    await ApiClient.get("/api/leads");

    const [, config] = global.fetch.mock.calls[0];
    expect(config.credentials).toBe("same-origin");
    expect(config.headers).not.toHaveProperty("Authorization");
  });

  it("sends a JSON content-type header", async () => {
    await ApiClient.get("/api/leads");

    const [, config] = global.fetch.mock.calls[0];
    expect(config.headers["Content-Type"]).toBe("application/json");
  });

  it("asks the server to answer in the language picked in the app", async () => {
    const previous = App.lang;
    App.lang = "en";
    try {
      await ApiClient.get("/api/leads");
    } finally {
      App.lang = previous;
    }

    const [, config] = global.fetch.mock.calls[0];
    expect(config.headers["Accept-Language"]).toBe("en");
  });

  it("serialises the body when one is given", async () => {
    await ApiClient.post("/api/leads", { clientName: "Anna" });

    const [, config] = global.fetch.mock.calls[0];
    expect(config.body).toBe(JSON.stringify({ clientName: "Anna" }));
  });

  it("sends no body at all when none is given", async () => {
    await ApiClient.get("/api/leads");

    const [, config] = global.fetch.mock.calls[0];
    expect(config).not.toHaveProperty("body");
  });
});

describe("ApiClient convenience verbs delegate to request", () => {
  beforeEach(() => {
    sessionStorage.clear();
    vi.spyOn(ApiClient, "request").mockResolvedValue({});
  });

  it("get() delegates with method GET", () => {
    ApiClient.get("/api/leads");
    expect(ApiClient.request).toHaveBeenCalledWith(
      "GET",
      "/api/leads",
      null,
      undefined,
    );
  });

  it("post() delegates with method POST", () => {
    ApiClient.post("/api/leads", { a: 1 });
    expect(ApiClient.request).toHaveBeenCalledWith(
      "POST",
      "/api/leads",
      { a: 1 },
      undefined,
    );
  });

  it("patch() delegates with method PATCH", () => {
    ApiClient.patch("/api/leads/1", { a: 1 });
    expect(ApiClient.request).toHaveBeenCalledWith(
      "PATCH",
      "/api/leads/1",
      { a: 1 },
      undefined,
    );
  });

  it("delete() delegates with method DELETE", () => {
    ApiClient.delete("/api/leads/1");
    expect(ApiClient.request).toHaveBeenCalledWith(
      "DELETE",
      "/api/leads/1",
      null,
      undefined,
    );
  });
});

describe("ApiClient error handling", () => {
  beforeEach(() => {
    sessionStorage.clear();
  });

  it("preserves HTTP status and a machine-readable conflict code", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false, status: 409,
      json: async () => ({ error: "Lead zmieniony", code: "stale_revision" }),
    });
    await expect(ApiClient.patch("/api/leads/1", {})).rejects.toMatchObject({
      message: "Lead zmieniony", status: 409, code: "stale_revision",
    });
  });

  it.each([null, { error: {}, code: {} }])("handles malformed error payload %j", async (payload) => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false, status: 500, json: async () => payload,
    });
    await expect(ApiClient.get("/api/leads")).rejects.toMatchObject({
      message: "Błąd serwera (500)", status: 500,
    });
  });

  it("clears cached display state and throws the session-expired message on a 401", async () => {
    sessionStorage.setItem("ph_session", JSON.stringify({ role: "partner" }));
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 401,
      json: async () => ({}),
    });

    await expect(ApiClient.get("/api/leads")).rejects.toThrow("Sesja wygasła.");
    expect(sessionStorage.getItem("ph_session")).toBeNull();
  });

  it("surfaces the server reason on a 401 from the login endpoint instead of reloading the login page", async () => {
    // A failed sign-in is not an expired session: redirecting to the login page
    // would reload it and wipe both the form and the error the user needs to read.
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 401,
      json: async () => ({ error: "Nieprawidłowy e-mail, hasło lub rola." }),
    });

    await expect(
      ApiClient.post("/api/auth/login", {
        email: "partner@it-solutions.pl",
        password: "partner123",
        role: "admin",
      }),
    ).rejects.toThrow("Nieprawidłowy e-mail, hasło lub rola.");
  });

  it("keeps the second-factor step on screen when the MFA code is rejected with a 401", async () => {
    // A mistyped code must not be treated as an expired session: that would
    // reload the login page, drop the pending sign-in and hide the error.
    sessionStorage.setItem("ph_session", JSON.stringify({ role: "partner" }));
    window.history.pushState({}, "", "/index.html?oauth=abc");
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 401,
      json: async () => ({ error: "Nieprawidłowy kod." }),
    });

    await expect(
      ApiClient.post("/api/auth/mfa/login", { code: "000000" }),
    ).rejects.toMatchObject({ message: "Nieprawidłowy kod.", status: 401 });
    expect(sessionStorage.getItem("ph_session")).not.toBeNull();
    expect(window.location.search).toBe("?oauth=abc");
  });

  it("throws the server-supplied error field on a non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 400,
      json: async () => ({ error: "Nieprawidłowe dane" }),
    });

    await expect(ApiClient.get("/api/leads")).rejects.toThrow(
      "Nieprawidłowe dane",
    );
  });

  it("throws the fallback status-code message when the payload has no error field", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
      json: async () => ({}),
    });

    await expect(ApiClient.get("/api/leads")).rejects.toThrow(
      "Błąd serwera (500)",
    );
  });

  it("tolerates a response body that is not valid JSON instead of throwing a parse error", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => {
        throw new SyntaxError("Unexpected token");
      },
    });

    await expect(ApiClient.get("/api/leads")).resolves.toEqual({});
  });
});

describe("getLoginPath inside the pages tree", () => {
  it("returns the two-level-up index path when inside the admin pages tree", () => {
    window.history.pushState({}, "", "/pages/admin/leads.html");
    expect(getLoginPath()).toBe("../../index.html");
  });

  it("returns the two-level-up index path when inside the partner pages tree", () => {
    window.history.pushState({}, "", "/pages/partner/dashboard.html");
    expect(getLoginPath()).toBe("../../index.html");
  });
});


describe("ApiClient timeout", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    // A fetch that never answers until it is aborted, like a hung server.
    global.fetch = vi.fn((url, config) => new Promise((resolve, reject) => {
      config.signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
    }));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("gives up after 30 seconds with a message the screen can show", async () => {
    const pending = ApiClient.get("/api/leads");
    const outcome = expect(pending).rejects.toMatchObject({
      message: "Serwer nie odpowiedział na czas. Spróbuj ponownie za chwilę.",
      code: "timeout",
    });
    await vi.advanceTimersByTimeAsync(30000);
    await outcome;
  });

  it("waits longer when the caller asks for it (the assistant)", async () => {
    let settled = false;
    const pending = ApiClient.post("/api/agent/chat", {}, { timeout: 220000 });
    pending.catch(() => {}).finally(() => { settled = true; });
    await vi.advanceTimersByTimeAsync(60000);
    expect(settled).toBe(false);
    await vi.advanceTimersByTimeAsync(160000);
    await expect(pending).rejects.toMatchObject({ code: "timeout" });
  });

  it("clears its timer once the answer arrives", async () => {
    global.fetch = vi.fn(() => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ ok: 1 }) }));
    await expect(ApiClient.get("/api/leads")).resolves.toEqual({ ok: 1 });
    expect(vi.getTimerCount()).toBe(0);
  });
});
