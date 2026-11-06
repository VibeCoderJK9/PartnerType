import path from "node:path";
import vm from "node:vm";
import { readFileSync, readdirSync } from "node:fs";
import { Window } from "happy-dom";
import { describe, expect, it } from "vitest";

const PROJECT_ROOT = path.resolve(import.meta.dirname, "..", "..");

/*
   Every icon in the bar is unique, drawn from our own icon set. The sidebar is copied into each
   page, so every copy is checked: each entry names an icon js/icons.js knows,
   and no two entries in one sidebar name the same one.
*/
const PAGES = ["admin", "partner"].flatMap((role) =>
  readdirSync(path.join(PROJECT_ROOT, "pages", role))
    .filter((file) => file.endsWith(".html"))
    .map((file) => `pages/${role}/${file}`),
);

function iconsRealm() {
  const window = new Window();
  const context = vm.createContext(window);
  vm.runInContext(readFileSync(path.join(PROJECT_ROOT, "js/icons.js"), "utf8"), context);
  return window;
}

function navEntries(file) {
  const window = new Window();
  window.document.write(readFileSync(path.join(PROJECT_ROOT, file), "utf8"));
  return [...window.document.querySelectorAll(".sidebar-nav .nav-item")].map((item) => ({
    href: item.getAttribute("href"),
    icon: item.querySelector("svg.nav-icon")?.getAttribute("data-icon") ?? null,
  }));
}

describe("sidebar icons", () => {
  const { Icons } = iconsRealm();

  it.each(PAGES)("%s names an icon from the set for every entry", (file) => {
    const entries = navEntries(file);
    expect(entries.length).toBeGreaterThan(0);
    for (const entry of entries) {
      expect(entry.icon, `${entry.href} has no data-icon`).not.toBeNull();
      expect(Icons.has(entry.icon), `${entry.icon} is not in js/icons.js`).toBe(true);
    }
  });

  it.each(PAGES)("%s never repeats an icon", (file) => {
    const icons = navEntries(file).map((entry) => entry.icon);
    expect(new Set(icons).size).toBe(icons.length);
  });

  it("gives a page the same icon for the same entry on every page of that role", () => {
    const seen = new Map();
    for (const file of PAGES) {
      const role = file.split("/")[1];
      for (const { href, icon } of navEntries(file)) {
        const key = `${role}:${href}`;
        if (seen.has(key)) expect(icon, `${key} in ${file}`).toBe(seen.get(key));
        else seen.set(key, icon);
      }
    }
  });

  it("draws the named icon in place of the placeholder", () => {
    const window = new Window({ url: "http://localhost:8000/pages/admin/users.html" });
    window.document.write(readFileSync(path.join(PROJECT_ROOT, "pages/admin/users.html"), "utf8"));
    const context = vm.createContext(window);
    for (const file of ["js/icons.js", "js/app.js"]) {
      vm.runInContext(readFileSync(path.join(PROJECT_ROOT, file), "utf8"), context, { filename: file });
    }
    window.document.dispatchEvent(new window.Event("DOMContentLoaded"));

    const icon = window.document.querySelector('.nav-item[href="users.html"] svg.nav-icon');
    expect(icon.getAttribute("data-icon")).toBe("users");
    expect(icon.querySelector("path, circle")).not.toBeNull();
  });
});
