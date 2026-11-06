/* ============================================================
   loadScript — classic-script loader for vitest
   ============================================================

   This project has no bundler and no ES modules: every js/ file is a
   classic script loaded by a <script> tag in the browser. A bare
   `import '../api.js'` in a test file does NOT expose that file's
   top-level `const`/`function` declarations as bare identifiers --
   vitest compiles every imported file as an ES module regardless of
   whether it contains import/export syntax, so those bindings stay
   module-scoped and are invisible to the importing test file.

   loadScript works around this by reading the raw source and running
   it with node's vm.runInThisContext, which executes the code in the
   current (happy-dom) global realm exactly like a classic <script> tag
   would. Passing the real absolute file path as the `filename` option
   is what lets the V8 coverage provider attribute the executed lines
   back to the original source file -- omitting it silently drops
   coverage attribution.

   IMPORTANT constraints for callers:
   - Call loadScript for a given script at most once per test file.
     Loading the same script twice in one realm re-declares its
     top-level bindings and throws.
   - Two scripts that both declare the same top-level `let`/`const`
     name cannot be loaded into the same test file (same realm) --
     load them from separate test files instead. (Notably users.js and
     admin.js each declare a top-level `leads` and `users` array.)
   ============================================================ */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import vm from "node:vm";

const HELPER_DIR = path.dirname(fileURLToPath(import.meta.url));
// helpers/ -> __tests__/ -> js/ -> project root (three levels up)
const PROJECT_ROOT = path.resolve(HELPER_DIR, "..", "..", "..");

/**
 * Load a classic (non-module) script relative to the project root,
 * executing it in the current global realm so its top-level
 * declarations become bare identifiers in the calling test file.
 *
 * @param {string} relativePath - path relative to the project root, e.g. "js/api.js"
 */
export function loadScript(relativePath) {
  const absolutePath = path.join(PROJECT_ROOT, relativePath);
  const source = readFileSync(absolutePath, "utf-8");
  vm.runInThisContext(source, { filename: absolutePath });
}
