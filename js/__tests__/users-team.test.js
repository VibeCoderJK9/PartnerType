/* ============================================================
   users-team.test.js

   Pins the producer's own team screen under the profile block:
   pages/admin/users.html driven by js/users.js, including the role
   selector of the invite modal (Moderator / Observer).

   Every control here is pressed through the full pointerdown/mousedown/
   mouseup/click sequence (js/__tests__/reported-regressions.test.js's
   pressLikeAMouse, copied verbatim below), never a bare click dispatch.
   A manual pass in a browser -- inviting a real Moderator, copying the link,
   and confirming the account reaches Settings but not the Audit log -- is the
   evidence this file cannot provide.
   ============================================================ */
import { readFileSync } from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { Window } from "happy-dom";
import { describe, expect, it, vi } from "vitest";
import { appStub } from "./helpers/copy.js";

const PROJECT_ROOT = path.resolve(import.meta.dirname, "..", "..");
const USERS_HTML_PATH = path.join(PROJECT_ROOT, "pages/admin/users.html");

function runClassicScript(window, relativePath) {
  const source = readFileSync(path.join(PROJECT_ROOT, relativePath), "utf8");
  const context = window.__vmContext || (window.__vmContext = vm.createContext(window));
  vm.runInContext(source, context, { filename: relativePath });
}

function invoke(window, source) {
  return vm.runInContext(source, window.__vmContext);
}

// Copied verbatim from js/__tests__/reported-regressions.test.js -- not
// reimplemented. Dispatches the full pointerdown -> mousedown -> mouseup ->
// click sequence and reports whether the control survived mousedown, which
// is the failure mode a bare click() dispatch cannot catch (a listener
// rebuild that destroys the control between mousedown and mouseup).
function pressLikeAMouse(window, button) {
  const rect = button.getBoundingClientRect();
  const init = {
    bubbles: true,
    cancelable: true,
    view: window,
    button: 0,
    clientX: Math.round(rect.left + rect.width / 2),
    clientY: Math.round(rect.top + rect.height / 2),
  };
  button.dispatchEvent(new window.MouseEvent("mousedown", init));
  const survived = button.isConnected;
  button.dispatchEvent(new window.MouseEvent("mouseup", init));
  button.dispatchEvent(new window.MouseEvent("click", init));
  return survived;
}

const THREE_MEMBER_TEAM = [
  { id: 1, name: "Anna Admin", email: "anna@firma.pl", role: "admin", account_status: "active", created_at: "2026-01-05T09:00:00" },
  { id: 2, name: "Karol Kowalski", email: "karol@firma.pl", role: "manager", account_status: "active", created_at: "2026-02-10T09:00:00" },
  { id: 3, name: "Zofia Zych", email: "zofia@firma.pl", role: "manager", account_status: "pending", created_at: "2026-03-15T09:00:00" },
];

function teamWindow({ getImpl } = {}) {
  const window = new Window({ url: "http://localhost/pages/admin/users.html" });
  window.ApiClient = {
    get: getImpl || vi.fn().mockResolvedValue({ users: [] }),
    post: vi.fn(),
  };
  window.showToast = vi.fn();
  // users.js reads its copy from the dictionary, so the realm needs App.
  window.App = appStub('pl');
  window.confirm = vi.fn(() => true);
  window.formatDate = vi.fn((iso) => (iso ? String(iso).slice(0, 10) : "—"));
  window.Ui = {
    escapeHtml: (value) => String(value ?? "")
      .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
      .replaceAll("'", "&#x27;").replaceAll('"', "&quot;"),
    plPlural: vi.fn((count, one, few, many) => (count === 1 ? one : count % 10 >= 2 && count % 10 <= 4 ? few : many)),
  };
  Object.defineProperty(window.navigator, "clipboard", {
    value: { writeText: vi.fn().mockResolvedValue(undefined) },
    configurable: true,
  });
  window.document.body.dataset.page = "not-auto-init"; // drive team functions explicitly below

  const html = readFileSync(USERS_HTML_PATH, "utf8");
  const bodyMatch = html.match(/<body[^>]*>([\s\S]*)<\/body>/);
  const bodyInner = bodyMatch[1].replace(/<script[\s\S]*?<\/script>/g, "");
  window.document.body.innerHTML = bodyInner;

  runClassicScript(window, "js/icons.js");
  runClassicScript(window, "js/users.js");
  return window;
}

// ─── 3-5. Loading and rendering the roster ──────────────────────────────────

describe("the team roster renders in server order with the reader marked", () => {
  it("renders three rows in the response order, with the correct Polish role titles", async () => {
    const window = teamWindow({
      getImpl: vi.fn().mockResolvedValue({ users: THREE_MEMBER_TEAM }),
    });

    await invoke(window, "loadTeam()");

    const rows = window.document.querySelectorAll("#team-tbody tr");
    expect(rows).toHaveLength(3);
    const names = Array.from(rows).map((r) => r.querySelector("td:first-child .cell-primary").textContent);
    expect(names).toEqual(["Anna Admin", "Karol Kowalski", "Zofia Zych"]);
    const roles = Array.from(rows).map((r) => r.querySelectorAll("td")[1].firstChild.textContent);
    expect(roles.map((r) => r.split("\n")[0].trim()))
      .toEqual(["Administrator", "Moderator", "Moderator"]);
  });

  it("marks only the row matching the acting identity as the reader's own account", async () => {
    const window = teamWindow({
      getImpl: vi.fn().mockResolvedValue({ users: THREE_MEMBER_TEAM }),
    });
    invoke(window, "currentUserId = 2");

    await invoke(window, "loadTeam()");

    const rows = window.document.querySelectorAll("#team-tbody tr");
    const marked = Array.from(rows).filter((r) => r.textContent.includes("To Ty"));
    expect(marked).toHaveLength(1);
    expect(marked[0].querySelector(".cell-primary").textContent).toBe("Karol Kowalski");
  });

  it("renders one row and leaves the empty block hidden for a single-account response", async () => {
    const window = teamWindow({
      getImpl: vi.fn().mockResolvedValue({ users: [THREE_MEMBER_TEAM[0]] }),
    });

    await invoke(window, "loadTeam()");

    const rows = window.document.querySelectorAll("#team-tbody tr");
    expect(rows).toHaveLength(1);
    expect(window.document.getElementById("team-empty-state").hidden).toBe(true);
    expect(window.document.getElementById("team-table-wrapper").hidden).toBe(false);
  });

  it("shows the error block and leaves the table body empty on a failed load", async () => {
    const window = teamWindow({
      getImpl: vi.fn().mockRejectedValue(new Error("network")),
    });

    await invoke(window, "loadTeam()");

    expect(window.document.getElementById("team-error-state").hidden).toBe(false);
    expect(window.document.querySelectorAll("#team-tbody tr")).toHaveLength(0);
  });
});

// ─── 6. Opening the invite modal with a real mouse press ───────────────────

describe("the invite control under a real mouse press", () => {
  it("survives mousedown and opens the modal with empty fields, no error and no result block", () => {
    const window = teamWindow();
    invoke(window, "setupTeamInviteModal()");

    const inviteBtn = window.document.getElementById("team-invite-btn");
    const survivedMousedown = pressLikeAMouse(window, inviteBtn);

    expect(survivedMousedown).toBe(true);
    expect(inviteBtn.isConnected).toBe(true);
    const overlay = window.document.getElementById("team-invite-modal");
    expect(overlay.classList.contains("show")).toBe(true);
    expect(window.document.getElementById("team-invite-name").value).toBe("");
    expect(window.document.getElementById("team-invite-email").value).toBe("");
    expect(window.document.getElementById("team-invite-error").style.display).toBe("none");
    expect(window.document.getElementById("team-invite-result").hidden).toBe(true);
  });
});

// ─── 7. Empty-field validation ───────────────────────────────────────────────

describe("sending the team invite validates before issuing a request", () => {
  it("shows the inline error and issues no request when the address is empty", async () => {
    const window = teamWindow();
    invoke(window, "setupTeamInviteModal()");
    window.document.getElementById("team-invite-name").value = "Jan Kowalski";
    window.document.getElementById("team-invite-email").value = "";

    await invoke(window, "sendTeamInvite()");

    const errEl = window.document.getElementById("team-invite-error");
    expect(errEl.style.display).toBe("block");
    expect(errEl.textContent.length).toBeGreaterThan(0);
    expect(window.ApiClient.post).not.toHaveBeenCalled();
  });
});

// ─── 8-9. Successful send, both mail outcomes ───────────────────────────────

describe("a successful send posts identity plus the chosen team role", () => {
  it("posts to the team-invite route with name, address and role, then reveals the link and the mail status", async () => {
    const window = teamWindow();
    invoke(window, "setupTeamInviteModal()");
    window.ApiClient.post.mockResolvedValue({
      user: { id: 9, name: "Jan Kowalski", email: "jan@firma.pl" },
      invite_url: "http://localhost/set-password.html?token=abc123",
      sent: true,
    });
    window.document.getElementById("team-invite-name").value = "Jan Kowalski";
    window.document.getElementById("team-invite-email").value = "jan@firma.pl";

    await invoke(window, "sendTeamInvite()");

    expect(window.ApiClient.post).toHaveBeenCalledTimes(1);
    const [url, body] = window.ApiClient.post.mock.calls[0];
    expect(url).toBe("/api/users/team-invite");
    expect(Object.keys(body).sort()).toEqual(["email", "name", "role"]);
    expect(body.name).toBe("Jan Kowalski");
    expect(body.email).toBe("jan@firma.pl");
    // The select defaults to Moderator; the server refuses anything outside
    // users.TEAM_ROLES, so this widens what can be asked for without
    // widening what can be granted.
    expect(body.role).toBe("manager");

    expect(window.document.getElementById("team-invite-link").value).toBe(
      "http://localhost/set-password.html?token=abc123",
    );
    expect(window.document.getElementById("team-invite-mail-status").textContent).toContain("jan@firma.pl");
    expect(window.document.getElementById("team-invite-result").hidden).toBe(false);
  });

  it("still reveals the link and says so when the response reports the mail did not go", async () => {
    const window = teamWindow();
    invoke(window, "setupTeamInviteModal()");
    window.ApiClient.post.mockResolvedValue({
      user: { id: 9, name: "Jan Kowalski", email: "jan@firma.pl" },
      invite_url: "http://localhost/set-password.html?token=xyz789",
      sent: false,
    });
    window.document.getElementById("team-invite-name").value = "Jan Kowalski";
    window.document.getElementById("team-invite-email").value = "jan@firma.pl";

    await invoke(window, "sendTeamInvite()");

    expect(window.document.getElementById("team-invite-link").value).toBe(
      "http://localhost/set-password.html?token=xyz789",
    );
    expect(window.document.getElementById("team-invite-mail-status").textContent).not.toBe("");
    expect(window.document.getElementById("team-invite-result").hidden).toBe(false);
  });
});

// ─── 10. Refused send ────────────────────────────────────────────────────────

describe("a refused send", () => {
  it("shows the server's message on the error line and adds no row", async () => {
    const window = teamWindow({
      getImpl: vi.fn().mockResolvedValue({ users: [THREE_MEMBER_TEAM[0]] }),
    });
    await invoke(window, "loadTeam()");
    invoke(window, "setupTeamInviteModal()");
    window.ApiClient.post.mockRejectedValue(new Error("Ten adres e-mail jest już zajęty."));
    window.document.getElementById("team-invite-name").value = "Anna Admin";
    window.document.getElementById("team-invite-email").value = "anna@firma.pl";

    await invoke(window, "sendTeamInvite()");

    const errEl = window.document.getElementById("team-invite-error");
    expect(errEl.textContent).toBe("Ten adres e-mail jest już zajęty.");
    expect(errEl.style.display).toBe("block");
    expect(window.document.querySelectorAll("#team-tbody tr")).toHaveLength(1);
  });
});

// ─── 11. Copy control under a real mouse press ──────────────────────────────

describe("the copy control under a real mouse press", () => {
  it("places the link on the clipboard and survives mousedown", async () => {
    const window = teamWindow();
    invoke(window, "setupTeamInviteModal()");
    window.document.getElementById("team-invite-link").value = "http://localhost/set-password.html?token=abc123";
    window.document.getElementById("team-invite-result").hidden = false;

    const copyBtn = window.document.getElementById("team-invite-copy-btn");
    const survivedMousedown = pressLikeAMouse(window, copyBtn);

    expect(survivedMousedown).toBe(true);
    expect(copyBtn.isConnected).toBe(true);
    await vi.waitFor(() =>
      expect(window.navigator.clipboard.writeText).toHaveBeenCalledWith(
        "http://localhost/set-password.html?token=abc123",
      ),
    );
  });
});

// ─── 12. The role selector ─────────────────────────────────────

describe("the invite modal's role selector", () => {
  it("offers exactly the two team roles and no way to ask for an administrator", () => {
    const window = teamWindow();
    const select = window.document.getElementById("team-invite-role");

    const values = Array.from(select.querySelectorAll("option")).map((o) => o.value);
    expect(values).toEqual(["manager", "observer"]);
    // A moderator who could mint an admin is not a contained role. The
    // server refuses it too (api/invitations.py checks users.TEAM_ROLES) --
    // this asserts the interface never offers it in the first place.
    expect(values).not.toContain("admin");
  });

  it("posts the observer role when it is the one chosen", async () => {
    const window = teamWindow();
    invoke(window, "setupTeamInviteModal()");
    window.ApiClient.post.mockResolvedValue({
      user: { id: 11, name: "Ola Obserwator", email: "ola@firma.pl" },
      invite_url: "http://localhost/set-password.html?token=obs1",
      sent: true,
    });
    window.document.getElementById("team-invite-name").value = "Ola Obserwator";
    window.document.getElementById("team-invite-email").value = "ola@firma.pl";
    window.document.getElementById("team-invite-role").value = "observer";

    await invoke(window, "sendTeamInvite()");

    const [, body] = window.ApiClient.post.mock.calls[0];
    expect(body.role).toBe("observer");
  });

  it("resets to Moderator each time the modal is reopened, so a previous observer choice cannot leak into the next invite", () => {
    const window = teamWindow();
    invoke(window, "setupTeamInviteModal()");
    window.document.getElementById("team-invite-role").value = "observer";

    invoke(window, "openTeamInviteModal()");

    expect(window.document.getElementById("team-invite-role").value).toBe("manager");
  });

  it("renders an observer row with its own Polish label, not the raw role value", async () => {
    const window = teamWindow({
      getImpl: vi.fn().mockResolvedValue({
        users: [{ id: 7, name: "Ola Obserwator", email: "ola@firma.pl", role: "observer", account_status: "active", created_at: "2026-04-01T09:00:00" }],
      }),
    });

    await invoke(window, "loadTeam()");

    const roleCell = window.document.querySelector("#team-tbody tr td:nth-child(2)");
    expect(roleCell.firstChild.textContent.trim()).toBe("Obserwator");
    expect(roleCell.textContent).not.toContain("observer");
  });
});
