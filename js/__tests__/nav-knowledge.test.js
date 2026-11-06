import path from "node:path";
import { readFileSync, readdirSync } from "node:fs";
import { Window } from "happy-dom";
import { describe, expect, it } from "vitest";

const PROJECT_ROOT = path.resolve(import.meta.dirname, "..", "..");

/*
   The knowledge base is a real entry in the left menu of every producer and
   partner page, the last one of the Account section (after Users for the
   producer, after My profile for the partner). It used to be a placeholder
   that went nowhere; none of those is left. Its own page lights it.
*/
const AFTER = { admin: "users.html", partner: "profile.html" };

const PAGES = Object.keys(AFTER).flatMap((role) =>
  readdirSync(path.join(PROJECT_ROOT, "pages", role))
    .filter((file) => file.endsWith(".html"))
    .map((file) => [role, file]),
);

function parse(file) {
  const window = new Window();
  window.document.write(readFileSync(path.join(PROJECT_ROOT, file), "utf8"));
  return window.document;
}

function navItems(document) {
  return [...document.querySelectorAll(".sidebar-nav .nav-item")];
}

describe("knowledge base entry in the sidebar", () => {
  it("covers both the admin and the partner pages, including their own knowledge page", () => {
    expect(PAGES.map(([role, file]) => `${role}/${file}`)).toEqual(
      expect.arrayContaining(["admin/knowledge.html", "partner/knowledge.html"]),
    );
  });

  it.each(PAGES)("%s/%s links the knowledge page once, at the end of the Account section", (role, file) => {
    const items = navItems(parse(`pages/${role}/${file}`));
    const hrefs = items.map((item) => item.getAttribute("href"));
    expect(hrefs.filter((href) => href === "knowledge.html")).toHaveLength(1);
    expect(hrefs.indexOf("knowledge.html")).toBe(hrefs.indexOf(AFTER[role]) + 1);
    expect(hrefs.at(-1)).toBe("knowledge.html");

    const entry = items[hrefs.indexOf("knowledge.html")];
    expect(entry.hasAttribute("data-nav-soon")).toBe(false);
    expect(entry.querySelector("svg.nav-icon")?.getAttribute("data-icon")).toBe("book-open");
    expect(entry.querySelector("[data-i18n]")?.getAttribute("data-i18n")).toBe("nav_knowledge_base");
    expect(entry.classList.contains("active")).toBe(file === "knowledge.html");
  });

  it.each(PAGES)("%s/%s has no placeholder entry left", (role, file) => {
    const html = readFileSync(path.join(PROJECT_ROOT, "pages", role, file), "utf8");
    expect(html).not.toContain("data-nav-soon");
    expect(navItems(parse(`pages/${role}/${file}`)).map((item) => item.getAttribute("href"))).not.toContain("#");
  });

  it.each(Object.keys(AFTER))("the %s knowledge page lights only its own entry", (role) => {
    const active = navItems(parse(`pages/${role}/knowledge.html`)).filter((item) => item.classList.contains("active"));
    expect(active.map((item) => item.getAttribute("href"))).toEqual(["knowledge.html"]);
  });

  it("keeps the affiliate entry where it was", () => {
    for (const [role, money] of [["admin", "commissions.html"], ["partner", "earnings.html"]]) {
      const hrefs = navItems(parse(`pages/${role}/knowledge.html`)).map((item) => item.getAttribute("href"));
      expect(hrefs.indexOf("affiliate.html")).toBe(hrefs.indexOf(money) + 1);
    }
  });

  it("names the entry and the page in every language the app offers", () => {
    for (const file of readdirSync(path.join(PROJECT_ROOT, "locales")).filter((name) => name.endsWith(".json"))) {
      const { messages } = JSON.parse(readFileSync(path.join(PROJECT_ROOT, "locales", file), "utf8"));
      expect(messages.nav_knowledge_base, file).toEqual(expect.any(String));
      expect(messages.kb_page_title, file).toEqual(expect.any(String));
    }
  });
});
