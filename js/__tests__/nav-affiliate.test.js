import path from "node:path";
import { readFileSync, readdirSync } from "node:fs";
import { Window } from "happy-dom";
import { describe, expect, it } from "vitest";

const PROJECT_ROOT = path.resolve(import.meta.dirname, "..", "..");

/*
   The affiliate programme has its own entry in the left menu of every page,
   right after the money entry of that role (Commissions for the producer,
   Earnings for the partner), and its own page lights it. Nothing about the
   programme stays inside Commissions, Settings or the partner dashboard.
*/
const AFTER = { admin: "commissions.html", partner: "earnings.html" };

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

describe("affiliate entry in the sidebar", () => {
  it("covers both the admin and the partner pages, including their own affiliate page", () => {
    expect(PAGES.map(([role, file]) => `${role}/${file}`)).toEqual(
      expect.arrayContaining(["admin/affiliate.html", "partner/affiliate.html"]),
    );
  });

  it.each(PAGES)("%s/%s links the affiliate page once, right after the money entry", (role, file) => {
    const items = navItems(parse(`pages/${role}/${file}`));
    const hrefs = items.map((item) => item.getAttribute("href"));
    expect(hrefs.filter((href) => href === "affiliate.html")).toHaveLength(1);
    expect(hrefs.indexOf("affiliate.html")).toBe(hrefs.indexOf(AFTER[role]) + 1);

    const entry = items[hrefs.indexOf("affiliate.html")];
    expect(entry.querySelector("svg.nav-icon")?.getAttribute("data-icon")).toBe("link");
    expect(entry.querySelector("[data-i18n]")?.getAttribute("data-i18n")).toBe("nav_affiliate");
    expect(entry.classList.contains("active")).toBe(file === "affiliate.html");
  });

  it.each(Object.keys(AFTER))("the %s affiliate page lights only its own entry", (role) => {
    const active = navItems(parse(`pages/${role}/affiliate.html`)).filter((item) => item.classList.contains("active"));
    expect(active.map((item) => item.getAttribute("href"))).toEqual(["affiliate.html"]);
  });

  it("names the entry in every language the app offers", () => {
    for (const file of readdirSync(path.join(PROJECT_ROOT, "locales")).filter((name) => name.endsWith(".json"))) {
      const { messages } = JSON.parse(readFileSync(path.join(PROJECT_ROOT, "locales", file), "utf8"));
      expect(messages.nav_affiliate, file).toEqual(expect.any(String));
      expect(messages.partner_center_afiliacja, file).toEqual(expect.any(String));
    }
  });

  it("keeps the programme out of Commissions, Settings and the partner dashboard", () => {
    const moved = {
      "pages/admin/commissions.html": ["affiliate-sales", "tab-affiliate", "admin-affiliate.js"],
      "pages/admin/settings.html": ["int-affiliate", "admin-affiliate-settings.js"],
      "pages/partner/dashboard.html": ["affiliate-card", "partner-affiliate.js"],
    };
    for (const [file, markers] of Object.entries(moved)) {
      const html = readFileSync(path.join(PROJECT_ROOT, file), "utf8");
      for (const marker of markers) expect(html, `${file} still carries ${marker}`).not.toContain(marker);
    }
  });

  it("puts the producer's orders and settings on the admin affiliate page", () => {
    const document = parse("pages/admin/affiliate.html");
    expect(document.getElementById("affiliate-sales")).not.toBeNull();
    expect(document.getElementById("int-affiliate")).not.toBeNull();
  });

  it("puts the partner's referral link on the partner affiliate page", () => {
    const document = parse("pages/partner/affiliate.html");
    expect(document.getElementById("affiliate-card")).not.toBeNull();
    expect(document.getElementById("affiliate-card-body")).not.toBeNull();
    expect(document.getElementById("affiliate-off")).not.toBeNull();
  });
});
