/* The partner sign-up page links "regulamin programu partnerskiego" to the
   producer's own terms. The address comes from the server, but the page itself
   only ever turns an absolute http(s) address into a link. */
import { readFileSync } from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { Window } from "happy-dom";
import { describe, expect, it, vi } from "vitest";

const PROJECT_ROOT = path.resolve(import.meta.dirname, "..", "..");

async function signUpPageWith(info) {
  const window = new Window({ url: "http://localhost/register.html" });
  window.document.body.innerHTML = '<a data-partner-terms href="#old">regulamin programu partnerskiego</a>';
  window.fetch = vi.fn(async () => ({ ok: true, json: async () => info }));
  const context = vm.createContext(window);
  vm.runInContext(readFileSync(path.join(PROJECT_ROOT, "js/branding.js"), "utf8"), context, { filename: "js/branding.js" });
  const link = window.document.querySelector("a[data-partner-terms]");
  await vi.waitFor(() => expect(window.fetch).toHaveBeenCalled());
  await new Promise((resolve) => setTimeout(resolve, 0));
  return link;
}

describe("partner terms link", () => {
  it.each(["https://acme.example/regulamin", "http://acme.example/terms.pdf"])("links to %s", async (url) => {
    const link = await signUpPageWith({ partner_terms_url: url });
    expect(link.getAttribute("href")).toBe(url);
    expect(link.rel).toBe("noopener noreferrer");
  });

  it.each([
    "javascript:alert(1)",
    " JavaScript:alert(1)",
    "data:text/html,<script>alert(1)</script>",
    "//evil.example/terms",
    "/terms.html",
    "https://",
  ])("leaves the words as plain text for %j", async (url) => {
    const link = await signUpPageWith({ partner_terms_url: url });
    expect(link.hasAttribute("href")).toBe(false);
  });
});
