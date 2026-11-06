/* The DOM-sink audit backs the rule that people-typed text is never parsed as
   HTML. Running it with the unit tests keeps the allowlist from drifting: a
   new innerHTML, inline event handler or interpolated URL fails here until it
   is rewritten with DOM APIs or reviewed into security-sink-allowlist.json. */
import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { auditRepository } from "../../scripts/security-sink-audit.mjs";

const PROJECT_ROOT = path.resolve(import.meta.dirname, "..", "..");

describe("security sink audit", () => {
  it("passes with no unknown or stale findings", async () => {
    const allowlist = JSON.parse(readFileSync(path.join(PROJECT_ROOT, "security-sink-allowlist.json"), "utf8"));
    const result = await auditRepository({ root: PROJECT_ROOT, mode: "progress", allowlist });
    expect(result.errors).toEqual([]);
    expect(result.ok).toBe(true);
  });
});
