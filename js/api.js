/* ============================================================
   PARTNERTYPE — API CLIENT
   Wrapper around fetch() that handles cookie auth, errors, base URL.
   ============================================================ */

const API_BASE = "";  // Same origin — server serves both API + static

// A request that gets no answer must not wait for ever. Past this, the caller
// gets an ordinary error it already knows how to show (with its "Try again"). The assistant passes its
// own, longer budget -- a model turn is bounded by the server at 210 s
// (gunicorn.conf.py), not by this.
const DEFAULT_TIMEOUT_MS = 30000;

const ApiClient = {
  // ── Token management ────────────────────────────────────────
  // Compatibility name used by existing logout and 401 callers. It clears
  // public display state (and any legacy pre-cookie secret), never a live token.
  clearToken() {
    sessionStorage.removeItem("ph_token");
    sessionStorage.removeItem("ph_session");
  },

  // ── Core request ────────────────────────────────────────────
  async request(method, path, body = null, opts = {}) {
    const headers = { "Content-Type": "application/json" };
    // Server messages (errors, assistant cards) come back in the language the
    // user picked in the app, not the browser's own preference.
    if (typeof App !== "undefined" && App.lang) headers["Accept-Language"] = App.lang;

    const config = { method, headers, credentials: "same-origin" };
    if (body !== null) {
      config.body = JSON.stringify(body);
    }

    const timeoutMs = opts.timeout || DEFAULT_TIMEOUT_MS;
    const controller = typeof AbortController === "function" ? new AbortController() : null;
    let timedOut = false;
    const timer = controller
      ? setTimeout(() => { timedOut = true; controller.abort(); }, timeoutMs)
      : null;
    if (controller) config.signal = controller.signal;

    let res;
    try {
      res = await fetch(API_BASE + path, config);
    } catch (err) {
      if (timedOut) {
        const error = new Error(App.t("err_timeout"));
        error.code = "timeout";
        throw error;
      }
      throw new Error(App.t("err_no_connection"));
    } finally {
      if (timer) clearTimeout(timer);
    }

    let data;
    try {
      data = await res.json();
    } catch {
      data = {};
    }

    // A 401 from a login endpoint (password or second-factor step) is a failed
    // sign-in attempt, not an expired session: redirecting there would reload
    // the login page and wipe the form before the caller can show why the
    // attempt failed.
    if (res.status === 401 && !isLoginRequest(path)) {
      // Session expired — redirect to login
      this.clearToken();
      window.location.href = getLoginPath();
      const error = new Error(App.t("err_session_expired"));
      error.status = res.status;
      throw error;
    }

    if (!res.ok) {
      const message = typeof data?.error === "string" && data.error
        ? data.error : App.t("err_server_status", { status: res.status });
      const error = new Error(message);
      error.status = res.status;
      if (typeof data?.code === "string") error.code = data.code;
      throw error;
    }

    return data;
  },

  // ── Convenience methods ──────────────────────────────────────
  get(path, opts)            { return this.request("GET",   path, null, opts); },
  post(path, body, opts)     { return this.request("POST",  path, body, opts); },
  patch(path, body, opts)    { return this.request("PATCH", path, body, opts); },
  put(path, body, opts)      { return this.request("PUT",   path, body, opts); },
  delete(path, opts)         { return this.request("DELETE", path, null, opts); },
};

// ─── PATH HELPERS ─────────────────────────────────────────────────────────────
function isLoginRequest(path) {
  return path === "/api/auth/login" || path === "/api/auth/mfa/login";
}


// Works regardless of how deep in the pages/ tree we are
function getLoginPath() {
  const p = window.location.pathname;
  if (p.includes("/pages/admin/") || p.includes("/pages/partner/")) {
    return "../../index.html";
  }
  return "/index.html";
}

// ─── GLOBAL ERROR DISPLAY ─────────────────────────────────────────────────────
window.handleApiError = function(err, context = "") {
  console.error("[API Error]", context, err);
  if (typeof showToast === "function") {
    showToast("error", App.t("err_generic_title"), err.message || String(err));
  }
};
