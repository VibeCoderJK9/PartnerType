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
  return context;
}

function invoke(window, source) {
  return vm.runInContext(source, window.__vmContext);
}

function orderedScripts(pagePath) {
  const html = readFileSync(path.join(PROJECT_ROOT, pagePath), "utf8");
  return [...html.matchAll(/<script\s+src="([^"]+)"/g)].filter((match) => match[1] !== "/locales.js").map((match) =>
    path.normalize(path.join(path.dirname(pagePath), match[1])),
  );
}

describe("analytics output boundary", () => {
  it("renders partner and stage API values as inert text", () => {
    const payload = '<img src=x onerror="window.__analyticsPwned=true">';
    const window = new Window({ url: "http://localhost/pages/admin/analytics.html" });
    window.document.body.innerHTML = `
      <table><tbody id="an-partners-tbody"></tbody></table>
    `;
    window.ApiClient = { get: vi.fn() };
    window.Charts = {};
    // Faithful to js/utils.js, and App may be absent in this realm.
    window.Ui = {
      registrationLabels: () => ({}),
      producerLabel: (record, field) => {
        if (!record) return "";
        const base = String(record[field] ?? "");
        if (window.App?.lang !== "en") return base;
        return String(record[`${field}_en`] ?? "").trim() || base;
      },
    };
    window.handleApiError = vi.fn();
    window.getInitials = vi.fn(() => payload);
    runClassicScript(window, "js/icons.js");
    runClassicScript(window, "js/analytics.js");

    // renderFunnel is gone with the Lejek konwersji panel -- the
    // donut above it now carries the same stage breakdown. The ranking table
    // is the remaining place where partner-supplied text reaches this screen.
    invoke(window, `renderPartnersTable([${JSON.stringify({
      name: payload,
      company: payload,
      tier_name: 'bronze" onmouseover="window.__analyticsPwned=true',
      total: 1,
      accepted: 1,
      revenue: 100,
      commission: 10,
    })}])`);

    expect(window.document.body.textContent).toContain(payload);
    expect(window.document.querySelector("img, script, [onerror], [onmouseover]")).toBeNull();
    expect(window.__analyticsPwned).toBeUndefined();
  });

  // The tier badge reads the producer's English column too,
  // so that column is a second piece of producer-supplied text
  // reaching this cell and needs the same boundary as tier_name above.
  it("renders the tier's English name as inert text for an English reader", () => {
    const payload = '<img src=x onerror="window.__analyticsPwnedEn=true">';
    const window = new Window({ url: "http://localhost/pages/admin/analytics.html" });
    window.document.body.innerHTML = `
      <table><tbody id="an-partners-tbody"></tbody></table>
    `;
    window.ApiClient = { get: vi.fn() };
    window.Charts = {};
    window.App = appStub("en");
    window.Ui = {
      registrationLabels: () => ({}),
      producerLabel: (record, field) => {
        if (!record) return "";
        const base = String(record[field] ?? "");
        if (window.App?.lang !== "en") return base;
        return String(record[`${field}_en`] ?? "").trim() || base;
      },
    };
    window.handleApiError = vi.fn();
    window.getInitials = vi.fn(() => "XX");
    runClassicScript(window, "js/icons.js");
    runClassicScript(window, "js/analytics.js");

    invoke(window, `renderPartnersTable([${JSON.stringify({
      name: "Partner",
      company: "Alfa sp. z o.o.",
      tier_name: "Brazowy",
      tier_name_en: payload,
      total: 1,
      accepted: 1,
      revenue: 100,
      commission: 10,
    })}])`);

    expect(window.document.body.textContent).toContain(payload);
    expect(window.document.querySelector("img, script, [onerror], [onmouseover]")).toBeNull();
    expect(window.__analyticsPwnedEn).toBeUndefined();
  });

  it("loads the shared admin shell before analytics dependencies", () => {
    expect(orderedScripts("pages/admin/analytics.html")).toEqual([
      // First and render-blocking, so the theme is set before the first paint.
      "js/theme-boot.js",
      "js/icons.js",
      "js/dropdown.js",
      "js/datepicker.js",
      "js/api.js",
      "js/app.js",
      "js/utils.js",
      "js/admin-shell.js",
      "js/charts.js",
      "js/notifications.js",
      "js/analytics.js",
      "js/agent.js",
      // Last, because it takes over functions the two above it define.
      "js/analytics-empty-states.js",
      "js/branding.js",
    ]);
  });
});

describe("audit output boundary", () => {
  it("renders audit users, labels, details and identifiers inertly", () => {
    const payload = '<svg onload="window.__auditPwned=true">';
    const window = new Window({ url: "http://localhost/pages/admin/audit.html" });
    window.document.body.innerHTML = '<table><tbody id="audit-tbody"></tbody></table>';
    window.ApiClient = { get: vi.fn() };
    window.App = { ...appStub("pl"), t: vi.fn(() => "Brak danych") };
    window.getInitials = vi.fn(() => payload);
    runClassicScript(window, "js/icons.js");
    runClassicScript(window, "js/audit.js");

    invoke(window, `labels = { attack: ${JSON.stringify(payload)} }`);
    invoke(window, `renderTable([${JSON.stringify({
      action: "attack",
      created_at: "2026-08-22T10:00:00",
      user_name: payload,
      user_role: payload,
      entity: payload,
      entity_id: payload,
      detail: payload,
      ip: payload,
    })}])`);

    expect(window.document.body.textContent).toContain(payload);
    expect(window.document.querySelector("img, script, [onload]")).toBeNull();
    // The badge renders a Lucide icon, so the page legitimately contains SVG
    // now. Every one of them must be ours -- an <svg> from the payload would
    // not carry the generated icon class.
    const svgs = [...window.document.querySelectorAll("svg")];
    expect(svgs.length).toBeGreaterThan(0);
    expect(svgs.every((node) => node.classList.contains("icon"))).toBe(true);
    expect(window.__auditPwned).toBeUndefined();
  });

  it("displays dynamic API errors as text", async () => {
    const payload = '<img src=x onerror="window.__auditPwned=true">';
    const window = new Window({ url: "http://localhost/pages/admin/audit.html" });
    window.document.body.innerHTML = '<table><tbody id="audit-tbody"></tbody></table>';
    window.ApiClient = { get: vi.fn().mockRejectedValue(new Error(payload)) };
    // The real dictionary, not a fixed string: the error message is now built
    // by App.t('audit_load_error', { message }), so a stub that ignores its
    // arguments would drop the very payload this test is about.
    window.App = appStub("pl");
    window.getInitials = vi.fn(() => "AA");
    runClassicScript(window, "js/icons.js");
    runClassicScript(window, "js/audit.js");

    await invoke(window, "loadAudit()");

    expect(window.document.getElementById("audit-tbody").textContent).toContain(payload);
    expect(window.document.querySelector("img, script, [onerror]")).toBeNull();
  });
});

describe("comment output and delegated actions", () => {
  it("keeps persisted fields inert and resolves delete through the original record ID", async () => {
    const payload = '<img src=x onerror="window.__commentPwned=true">';
    const hostileId = "7' onclick='window.__commentPwned=true";
    const window = new Window({ url: "http://localhost/pages/admin/leads.html" });
    window.document.body.innerHTML = '<div class="comments-container"></div>';
    window.ApiClient = {
      get: vi.fn().mockResolvedValue({ comments: [{
        id: hostileId,
        user_id: 1,
        author_name: payload,
        author_role: "admin",
        body: payload,
        is_internal: false,
        created_at: "2026-08-22T10:00:00",
      }] }),
      delete: vi.fn().mockResolvedValue({}),
      post: vi.fn().mockResolvedValue({}),
    };
    window.App = { ...appStub("pl"), t: vi.fn(() => "Brak danych") };
    window.getInitials = vi.fn(() => payload);
    window.showToast = vi.fn();
    runClassicScript(window, "js/icons.js");
    runClassicScript(window, "js/comments.js");

    await invoke(window, `Comments.load("lead-1", ".comments-container", { id: 1, role: "admin" })`);

    const container = window.document.querySelector(".comments-container");
    expect(container.textContent).toContain(payload);
    expect(container.querySelector("img, script, [onclick], [onerror]")).toBeNull();

    container.querySelector(".comment-delete-btn").click();
    await vi.waitFor(() => expect(window.ApiClient.delete).toHaveBeenCalledTimes(1));
    expect(window.ApiClient.delete).toHaveBeenCalledWith(`/api/comments/${encodeURIComponent(hostileId)}`);
  });

  it("submits through a listener and preserves empty-state translation", async () => {
    const window = new Window({ url: "http://localhost/pages/admin/leads.html" });
    window.document.body.innerHTML = '<div class="comments-container"></div>';
    window.ApiClient = {
      get: vi.fn()
        .mockResolvedValueOnce({ comments: [] })
        .mockResolvedValue({ comments: [] }),
      delete: vi.fn(),
      post: vi.fn().mockResolvedValue({}),
    };
    window.App = { ...appStub("pl"), t: vi.fn(() => "Brak danych") };
    window.getInitials = vi.fn(() => "AA");
    window.showToast = vi.fn();
    runClassicScript(window, "js/icons.js");
    runClassicScript(window, "js/comments.js");

    await invoke(window, `Comments.load(12, ".comments-container", { id: 1, role: "admin" })`);
    expect(window.document.querySelector(".comments-empty").textContent).toBe("Brak danych");
    expect(window.document.querySelector("[onclick]")).toBeNull();

    window.document.getElementById("comment-input").value = "Bezpieczny komentarz";
    window.document.querySelector(".comment-submit-btn").click();
    await vi.waitFor(() => expect(window.ApiClient.post).toHaveBeenCalledTimes(1));
    expect(window.ApiClient.post).toHaveBeenCalledWith("/api/leads/12/comments", {
      body: "Bezpieczny komentarz",
      is_internal: false,
    });
  });

  it("ignores a slower response for a previously opened lead and posts to the lead on screen", async () => {
    const window = new Window({ url: "http://localhost/pages/admin/leads.html" });
    window.document.body.innerHTML = '<div class="comments-container" id="modal-comments-container"></div>';
    const pending = {};
    const thread = (lead) => ({ comments: [{
      id: lead, user_id: 2, author_name: `Autor ${lead}`, author_role: "partner",
      body: `Wątek ${lead}`, is_internal: false, created_at: "2026-08-22T10:00:00",
    }] });
    window.ApiClient = {
      get: vi.fn((url) => new Promise((resolve) => { pending[url] = resolve; })),
      delete: vi.fn(),
      post: vi.fn().mockResolvedValue({}),
    };
    window.App = appStub("pl");
    window.getInitials = vi.fn(() => "AA");
    window.showToast = vi.fn();
    runClassicScript(window, "js/icons.js");
    runClassicScript(window, "js/comments.js");
    const user = '{ id: 1, role: "admin" }';

    const first = invoke(window, `Comments.load("A", "#modal-comments-container", ${user})`);
    const second = invoke(window, `Comments.load("B", "#modal-comments-container", ${user})`);
    pending["/api/leads/B/comments"](thread("B"));
    await second;
    pending["/api/leads/A/comments"](thread("A"));
    await first;

    const container = window.document.getElementById("modal-comments-container");
    expect(container.textContent).toContain("Wątek B");
    expect(container.textContent).not.toContain("Wątek A");

    window.document.getElementById("comment-input").value = "Uwaga do B";
    container.querySelector(".comment-submit-btn").click();
    await vi.waitFor(() => expect(window.ApiClient.post).toHaveBeenCalledTimes(1));
    expect(window.ApiClient.post).toHaveBeenCalledWith("/api/leads/B/comments", {
      body: "Uwaga do B",
      is_internal: false,
    });
  });

  it("reloads the thread into the container it was given, whatever its class", async () => {
    const window = new Window({ url: "http://localhost/pages/admin/leads.html" });
    window.document.body.innerHTML = '<div id="thread-box"></div>';
    window.ApiClient = {
      get: vi.fn().mockResolvedValue({ comments: [] }),
      delete: vi.fn(),
      post: vi.fn().mockResolvedValue({}),
    };
    window.App = appStub("pl");
    window.getInitials = vi.fn(() => "AA");
    window.showToast = vi.fn();
    runClassicScript(window, "js/icons.js");
    runClassicScript(window, "js/comments.js");

    await invoke(window, `Comments.load(5, "#thread-box", { id: 1, role: "admin" })`);
    window.document.getElementById("comment-input").value = "Nowy";
    window.document.querySelector(".comment-submit-btn").click();
    await vi.waitFor(() => expect(window.ApiClient.get).toHaveBeenCalledTimes(2));
    expect(window.ApiClient.get).toHaveBeenLastCalledWith("/api/leads/5/comments");
  });
});
