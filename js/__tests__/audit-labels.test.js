/* ============================================================
   Every audit action the backend writes has a name on the screen
   ============================================================

   The audit log is written in Python and read in JavaScript, and nothing
   connected the two: a new event could be recorded for weeks before anyone
   noticed the screen had no name for it, and the screen would fall back to
   the raw key.

   So this reads the actions out of the API modules and requires each one to be
   named in js/audit.js and translated in both dictionaries.
   ============================================================ */
import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { dictionary } from "./helpers/copy.js";

const PROJECT_ROOT = path.resolve(import.meta.dirname, "..", "..");

/** Every `audit.log(..., "<entity>.<action>", ...)` string in the API. */
// api/gateway/ writes the integration.* actions; it is scanned too.
const API_DIRS = ["api", path.join("api", "gateway")];

function emittedActions() {
  const actions = new Set();
  for (const dir of API_DIRS) for (const name of readdirSync(path.join(PROJECT_ROOT, dir))) {
    if (!name.endsWith(".py")) continue;
    const source = readFileSync(path.join(PROJECT_ROOT, dir, name), "utf8");
    // The action is always a literal at the call site -- it is the one thing
    // about an audit row that must never be computed.
    for (const match of source.matchAll(/audit\.log\(\s*[^,]+,\s*["']([a-z_]+\.[a-z_]+)["']/g)) {
      actions.add(match[1]);
    }
  }
  return [...actions].sort();
}

const AUDIT_JS = readFileSync(path.join(PROJECT_ROOT, "js", "audit.js"), "utf8");

function labelKeyFor(action) {
  const match = AUDIT_JS.match(
    new RegExp(`'${action.replace(".", "\\.")}':\\s*'(audit_action_[a-z_]+)'`),
  );
  return match ? match[1] : null;
}

describe("audit log vocabulary", () => {
  const actions = emittedActions();

  it("finds the actions in the API, so an empty sweep cannot pass as a clean one", () => {
    expect(actions.length).toBeGreaterThanOrEqual(20);
    expect(actions).toContain("user.login");
    expect(actions).toContain("user.login_failed");
  });

  it("also reads api/gateway, where every integration.* action is written", () => {
    for (const action of ["enabled", "disabled", "partners_enabled", "partners_disabled",
      "fields_changed", "limits_changed", "key_created", "key_revoked", "oauth_consent",
      "connection_revoked", "credentials_revoked"]) {
      expect(actions).toContain(`integration.${action}`);
    }
  });

  it.each(emittedActions())("%s has a label key in js/audit.js", (action) => {
    expect(labelKeyFor(action)).not.toBeNull();
  });

  it.each(emittedActions())("%s is translated in both languages", (action) => {
    const key = labelKeyFor(action);
    if (!key) return; // reported by the test above; no second failure for one cause
    for (const language of ["pl", "en"]) {
      expect(dictionary(language)[key], `${key} missing from ${language}`).toBeTruthy();
    }
  });
});

describe("sign-in events hidden from the default audit view", () => {
  // api/audit.py SESSION_ACTIONS names the rows the screen leaves out until
  // asked. A misspelt entry would quietly stop hiding its event.
  it("names only actions the API really writes", () => {
    const source = readFileSync(path.join(PROJECT_ROOT, "api", "audit.py"), "utf8");
    const block = source.match(/SESSION_ACTIONS = \(([^)]*)\)/)[1];
    const hidden = [...block.matchAll(/"([a-z_.]+)"/g)].map((match) => match[1]);
    expect(hidden.length).toBeGreaterThanOrEqual(4);
    for (const action of hidden) expect(emittedActions()).toContain(action);
  });
});
