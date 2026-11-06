/* ============================================================
   users-xss.test.js

   Output boundary for the team roster on pages/admin/users.html.

   This screen lists staff only, so the renderer this file guards is
   createTeamRow. The partner-row equivalent of these assertions lives in
   admin-partners.test.js, which owns every partner row in the product.
   ============================================================ */
import { readFileSync } from "node:fs";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { loadScript } from "./helpers/load-script.js";

globalThis.ApiClient = {
  get: vi.fn(),
  post: vi.fn(),
  patch: vi.fn(),
};

loadScript("js/icons.js");
loadScript("js/utils.js");
loadScript("js/app.js");
loadScript("js/users.js");

const PROJECT_ROOT = path.resolve(import.meta.dirname, "..", "..");
const PAYLOAD = `'); window.__usersPwned = true; ('<img src=x onerror="window.__usersPwned=true">`;

function member(overrides = {}) {
  return {
    id: 17,
    name: PAYLOAD,
    email: `<svg onload="window.__usersPwned=true">@example.com`,
    role: "manager",
    account_status: "active",
    created_at: "2026-08-22",
    ...overrides,
  };
}

beforeEach(() => {
  document.body.innerHTML =
    '<table><tbody id="team-tbody"></tbody></table>' +
    '<div id="team-table-wrapper"></div><div id="team-empty-state"></div>' +
    '<div id="team-error-state"></div><div id="team-loading-state"></div>' +
    '<span id="team-count-label"></span>';
  window.__usersPwned = false;
  teamMembers = [];
  currentUserId = null;
  ApiClient.get.mockReset();
  ApiClient.post.mockReset();
  ApiClient.patch.mockReset();
  vi.restoreAllMocks();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("team roster renderer output boundary", () => {
  it("renders every persisted field inertly, with no live node and no inline handler", () => {
    teamMembers = [member({ account_status: `<img src=x onerror="window.__usersPwned=true">` })];
    renderTeam();

    const tbody = document.getElementById("team-tbody");
    expect(tbody.textContent).toContain(PAYLOAD);
    expect(tbody.textContent).toContain("<svg onload=");
    expect(tbody.querySelector("img, script, svg[onload]")).toBeNull();
    expect(tbody.querySelector("[onclick]")).toBeNull();
    expect(window.__usersPwned).toBe(false);
  });

  it("renders an unrecognised role as inert text rather than dropping the row or trusting the value", () => {
    teamMembers = [member({ role: `<img src=x onerror="window.__usersPwned=true">` })];
    renderTeam();

    const tbody = document.getElementById("team-tbody");
    expect(tbody.querySelectorAll("tr")).toHaveLength(1);
    expect(tbody.querySelector("img")).toBeNull();
    expect(window.__usersPwned).toBe(false);
  });

  it("marks the reader's own row by id comparison, never by matching rendered text", () => {
    teamMembers = [member(), member({ id: 18, name: "Zwykły Członek" })];
    currentUserId = 18;
    renderTeam();

    const marked = [...document.querySelectorAll("#team-tbody tr")]
      .filter((row) => row.textContent.includes("To Ty"));
    expect(marked).toHaveLength(1);
    expect(marked[0].querySelector(".cell-primary").textContent).toBe("Zwykły Członek");
    expect(window.__usersPwned).toBe(false);
  });

  it("loads the shared utility before the users consumer", () => {
    const html = readFileSync(path.join(PROJECT_ROOT, "pages/admin/users.html"), "utf8");
    const scripts = [...html.matchAll(/<script\s+src="([^"]+)"/g)].filter((match) => match[1] !== "/locales.js").map((match) => match[1]);
    expect(scripts.indexOf("../../js/app.js")).toBeLessThan(scripts.indexOf("../../js/utils.js"));
    expect(scripts.indexOf("../../js/utils.js")).toBeLessThan(scripts.indexOf("../../js/users.js"));
  });

  it("keeps the local single-quote escaper and inline handlers out of the module", () => {
    const source = readFileSync(path.join(PROJECT_ROOT, "js/users.js"), "utf8");
    expect(source).not.toMatch(/function\s+escHtml\s*\(/);
    expect(source).not.toMatch(/onclick\s*=/);
  });

  it("no longer renders partner accounts on this screen at all", () => {
    const source = readFileSync(path.join(PROJECT_ROOT, "js/users.js"), "utf8");
    // Every partner surface lives on pages/admin/partners.html. A partner
    // fetch or a partner-invite route appearing here means the two screens
    // have started to overlap.
    expect(source).not.toContain("/api/users/invite");
    expect(source).not.toMatch(/ApiClient\.get\(['"]\/api\/users['"]\)/);
  });
});
