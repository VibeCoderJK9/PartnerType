/* ============================================================
   Knowledge base — the producer shares documents, partners download them
   ============================================================

   One controller (js/knowledge.js) serves both pages. The producer's staff
   see the space used and may add, edit and delete; partners and observers
   only list, search and download. Titles, descriptions and file names come
   from people and must stay text.
   ============================================================ */
import { readFileSync } from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { Window } from "happy-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { dictionary } from "./helpers/copy.js";

const PROJECT_ROOT = path.resolve(import.meta.dirname, "..", "..");
const HOSTILE = '<img src=x onerror="window.__pwned=1">';
const pl = dictionary("pl");
const en = dictionary("en");

function externalScripts(page) {
  const html = readFileSync(path.join(PROJECT_ROOT, page), "utf8");
  return [...html.matchAll(/<script\s+src="([^"]+)"/g)]
    .filter((match) => match[1] !== "/locales.js")
    .map((match) => match[1].replace("../../", ""));
}

function response(data, status = 200) {
  return { ok: status >= 200 && status < 300, status, json: vi.fn().mockResolvedValue(data) };
}

const DOCUMENTS = [
  { id: 3, title: HOSTILE, description: "Line 1\nLine 2", filename: "price-list.pdf", kind: "pdf",
    size_bytes: 2 * 1024 * 1024, created_at: "2026-10-30T09:00:00+00:00", is_new: true, uploaded_by_name: "Ada Admin" },
  { id: 2, title: "Product presentation", description: "", filename: "deck.pptx", kind: "pptx",
    size_bytes: 300 * 1024, created_at: "2026-09-01T09:00:00+00:00", is_new: false, uploaded_by_name: "Ada Admin" },
];

function realm(page, role, { failList = false, usage, upload } = {}) {
  const html = readFileSync(path.join(PROJECT_ROOT, page), "utf8");
  const window = new Window({ url: `http://localhost:8000/${page}` });
  window.document.write(html);
  window.console = console;
  const user = { id: 7, role, name: role === "partner" ? "Ada Partner" : "Ada Admin", company: "ACME" };
  const requests = [];
  window.fetch = vi.fn(async (url, options = {}) => {
    const pathname = new URL(url, window.location.href).pathname;
    const method = options.method || "GET";
    requests.push({ method, pathname, body: options.body });
    if (pathname === "/api/auth/me") return response({ user });
    if (pathname === "/api/knowledge" && method === "GET") {
      if (failList) return response({ error: "down" }, 500);
      const staff = role === "admin" || role === "manager";
      const body = {
        documents: DOCUMENTS.map(({ uploaded_by_name, ...doc }) => (staff ? { ...doc, uploaded_by_name } : doc)),
      };
      if (role === "admin" || role === "manager") {
        body.usage = usage || { used_bytes: 2.3 * 1024 * 1024, quota_bytes: 1024 * 1024 * 1024, max_file_bytes: 10 * 1024 * 1024 };
      }
      return response(body);
    }
    if (pathname === "/api/knowledge" && method === "POST") return upload || response({ id: 9 }, 201);
    if (pathname === "/api/knowledge/3" && method === "PATCH") return response({ id: 3 });
    if (pathname === "/api/knowledge/3" && method === "DELETE") return response({ deleted: 3 });
    if (pathname === "/api/notifications") return response({ unread: 0, notifications: [] });
    return response({});
  });
  const context = vm.createContext(window);
  for (const script of externalScripts(page)) {
    vm.runInContext(readFileSync(path.join(PROJECT_ROOT, script), "utf8"), context, { filename: script });
  }
  window.document.dispatchEvent(new window.Event("DOMContentLoaded"));
  const switchLanguage = (lang) => vm.runInContext(`App.setLang(${JSON.stringify(lang)})`, context);
  return { window, requests, switchLanguage };
}

const cards = (window) => [...window.document.querySelectorAll(".kb-card")];

afterEach(() => vi.restoreAllMocks());

describe("knowledge base for partners", () => {
  it("lists the producer's documents read-only, with a download link and no editing", async () => {
    const { window } = realm("pages/partner/knowledge.html", "partner");
    await vi.waitFor(() => expect(cards(window)).toHaveLength(2));
    const [first] = cards(window);
    expect(first.querySelector(".kb-card-title").textContent).toBe(HOSTILE);
    expect(first.querySelector("img")).toBeNull();
    expect(window.__pwned).toBeUndefined();
    expect(first.querySelector(".kb-card-description").textContent).toBe("Line 1\nLine 2");
    expect(first.querySelector(".kb-kind").textContent).toBe("PDF");
    expect(first.querySelector(".kb-new").textContent).toBe(pl.kb_new);
    expect(first.querySelector(".kb-download").getAttribute("href")).toBe("/api/knowledge/3/file");
    expect(first.querySelector(".kb-download").getAttribute("download")).toBe("price-list.pdf");
    expect(window.document.querySelector(".kb-edit, .kb-delete")).toBeNull();
    expect(window.document.getElementById("kb-add-btn")).toBeNull();
    expect(first.querySelector(".kb-card-meta").textContent).not.toContain("Ada Admin");
    expect(window.document.querySelector('.nav-item[href="knowledge.html"]').classList.contains("active")).toBe(true);
  });

  it("filters by title, description and file name", async () => {
    const { window } = realm("pages/partner/knowledge.html", "partner");
    await vi.waitFor(() => expect(cards(window)).toHaveLength(2));
    const search = window.document.getElementById("kb-search");
    search.value = "DECK";
    search.dispatchEvent(new window.Event("input"));
    expect(cards(window)).toHaveLength(1);
    search.value = "line 2";
    search.dispatchEvent(new window.Event("input"));
    expect(cards(window)).toHaveLength(1);
    search.value = "nothing like this";
    search.dispatchEvent(new window.Event("input"));
    expect(cards(window)).toHaveLength(0);
    expect(window.document.querySelector(".kb-empty").textContent).toContain(pl.kb_no_results);
  });

  it("offers a retry when the list cannot be loaded", async () => {
    const { window, requests } = realm("pages/partner/knowledge.html", "partner", { failList: true });
    await vi.waitFor(() => expect(window.document.querySelector(".kb-empty")?.textContent).toContain(pl.kb_load_failed));
    window.document.querySelector(".kb-empty button").click();
    await vi.waitFor(() => expect(requests.filter((r) => r.pathname === "/api/knowledge")).toHaveLength(2));
  });
});

describe("knowledge base for the producer", () => {
  it("shows usage, the add button and edit/delete on every card", async () => {
    const { window } = realm("pages/admin/knowledge.html", "admin");
    await vi.waitFor(() => expect(cards(window)).toHaveLength(2));
    expect(window.document.getElementById("kb-add-btn").hidden).toBe(false);
    expect(window.document.getElementById("kb-usage").hidden).toBe(false);
    expect(window.document.getElementById("kb-usage-text").textContent).toMatch(/2,3 MB/);
    expect(cards(window)[0].querySelectorAll(".kb-edit, .kb-delete")).toHaveLength(2);
    expect(cards(window)[0].querySelector(".kb-card-meta").textContent).toContain("Ada Admin");
    expect(window.document.querySelector('.nav-item[href="knowledge.html"]').classList.contains("active")).toBe(true);
  });

  it("says so when the server's space setting is broken", async () => {
    const usage = { used_bytes: 0, quota_bytes: null, max_file_bytes: 10 * 1024 * 1024 };
    const { window } = realm("pages/admin/knowledge.html", "admin", { usage });
    await vi.waitFor(() => expect(cards(window)).toHaveLength(2));
    expect(window.document.getElementById("kb-usage-text").textContent).toBe(pl.kb_usage_invalid);
    expect(window.document.getElementById("kb-usage").classList.contains("kb-usage-full")).toBe(true);
  });

  it("asks before deleting and then reloads the list", async () => {
    const { window, requests } = realm("pages/admin/knowledge.html", "admin");
    await vi.waitFor(() => expect(cards(window)).toHaveLength(2));
    cards(window)[0].querySelector(".kb-delete").click();
    expect(window.document.getElementById("kb-delete-modal").classList.contains("show")).toBe(true);
    expect(window.document.getElementById("kb-delete-text").textContent).toContain(HOSTILE);
    expect(window.document.querySelector("#kb-delete-text img")).toBeNull();
    window.document.getElementById("kb-delete-confirm").click();
    await vi.waitFor(() => expect(requests.some((r) => r.method === "DELETE" && r.pathname === "/api/knowledge/3")).toBe(true));
    await vi.waitFor(() => expect(requests.filter((r) => r.method === "GET" && r.pathname === "/api/knowledge")).toHaveLength(2));
  });

  it("edits a title and description without asking for the file again", async () => {
    const { window, requests } = realm("pages/admin/knowledge.html", "admin");
    await vi.waitFor(() => expect(cards(window)).toHaveLength(2));
    cards(window)[0].querySelector(".kb-edit").click();
    expect(window.document.getElementById("kb-modal").classList.contains("show")).toBe(true);
    expect(window.document.getElementById("kb-file-group").hidden).toBe(true);
    expect(window.document.getElementById("kb-title").value).toBe(HOSTILE);
    window.document.getElementById("kb-title").value = "Price list 2027";
    window.document.getElementById("kb-save-btn").click();
    await vi.waitFor(() => expect(requests.some((r) => r.method === "PATCH")).toBe(true));
    const patch = requests.find((r) => r.method === "PATCH");
    expect(patch.pathname).toBe("/api/knowledge/3");
    expect(JSON.parse(patch.body)).toEqual({ title: "Price list 2027", description: "Line 1\nLine 2" });
  });

  it("refuses an upload without a title or a file before sending anything", async () => {
    const { window, requests } = realm("pages/admin/knowledge.html", "admin");
    await vi.waitFor(() => expect(cards(window)).toHaveLength(2));
    window.document.getElementById("kb-add-btn").click();
    expect(window.document.getElementById("kb-file-group").hidden).toBe(false);
    expect(window.document.getElementById("kb-file-hint").textContent).toContain("10");
    window.document.getElementById("kb-save-btn").click();
    await vi.waitFor(() => expect(window.document.getElementById("kb-modal-error").textContent).toBe(pl.kb_title_required));
    window.document.getElementById("kb-title").value = "Price list";
    window.document.getElementById("kb-save-btn").click();
    await vi.waitFor(() => expect(window.document.getElementById("kb-modal-error").textContent).toBe(pl.kb_file_required));
    expect(requests.some((r) => r.method === "POST")).toBe(false);
  });

  function chooseFile(window, name, content, type) {
    const input = window.document.getElementById("kb-file");
    const file = new window.File([content], name, { type });
    Object.defineProperty(input, "files", { value: [file], configurable: true });
    input.dispatchEvent(new window.Event("change"));
  }

  it("uploads the chosen file as JSON with a long timeout and reloads the list", async () => {
    const { window, requests } = realm("pages/admin/knowledge.html", "admin");
    await vi.waitFor(() => expect(cards(window)).toHaveLength(2));
    const timers = vi.spyOn(window, "setTimeout");
    window.document.getElementById("kb-add-btn").click();
    chooseFile(window, "price-list.pdf", "%PDF-1.4", "application/pdf");
    // A file chosen without a title lends it its name.
    expect(window.document.getElementById("kb-title").value).toBe("price-list");
    window.document.getElementById("kb-description").value = "Net prices";
    window.document.getElementById("kb-save-btn").click();
    await vi.waitFor(() => expect(requests.some((r) => r.method === "POST")).toBe(true));
    const post = requests.find((r) => r.method === "POST");
    expect(post.pathname).toBe("/api/knowledge");
    const body = JSON.parse(post.body);
    expect(Object.keys(body).sort()).toEqual(["data", "description", "filename", "title"]);
    expect(body).toMatchObject({ title: "price-list", description: "Net prices", filename: "price-list.pdf" });
    expect(body.data).toMatch(/^data:application\/pdf;base64,/);
    expect(window.atob(body.data.split(",")[1])).toBe("%PDF-1.4");
    expect(timers.mock.calls.some(([, delay]) => delay === 180000)).toBe(true);
    await vi.waitFor(() => expect(requests.filter((r) => r.method === "GET" && r.pathname === "/api/knowledge")).toHaveLength(2));
    expect(window.document.getElementById("kb-modal").classList.contains("show")).toBe(false);
  });

  it("shows the server's refusal in the dialog and gives the button back", async () => {
    const upload = response({ error: "There is no room for this file.", code: "knowledge_quota" }, 413);
    const { window, requests } = realm("pages/admin/knowledge.html", "admin", { upload });
    await vi.waitFor(() => expect(cards(window)).toHaveLength(2));
    window.document.getElementById("kb-add-btn").click();
    chooseFile(window, "deck.pptx", "PK", "application/vnd.openxmlformats-officedocument.presentationml.presentation");
    const save = window.document.getElementById("kb-save-btn");
    save.click();
    const error = window.document.getElementById("kb-modal-error");
    await vi.waitFor(() => expect(error.textContent).toBe("There is no room for this file."));
    expect(error.hidden).toBe(false);
    expect(window.document.getElementById("kb-modal").classList.contains("show")).toBe(true);
    expect(save.disabled).toBe(false);
    expect(save.textContent).toBe(pl.kb_save);
    // Nothing was stored, so the list is not fetched again.
    expect(requests.filter((r) => r.method === "GET" && r.pathname === "/api/knowledge")).toHaveLength(1);
  });

  it("keeps the edit dialog's own labels when the language changes", async () => {
    const { window, switchLanguage } = realm("pages/admin/knowledge.html", "admin");
    await vi.waitFor(() => expect(cards(window)).toHaveLength(2));
    cards(window)[0].querySelector(".kb-edit").click();
    expect(window.document.getElementById("kb-modal-title").textContent).toBe(pl.kb_edit_title);
    switchLanguage("en");
    expect(window.document.getElementById("kb-modal-title").textContent).toBe(en.kb_edit_title);
    expect(window.document.getElementById("kb-save-btn").textContent).toBe(en.kb_save_changes);
    switchLanguage("en");
    expect(window.document.getElementById("kb-modal-title").textContent).toBe(en.kb_edit_title);
  });

  it("keeps saying it is uploading when the language changes mid-upload", async () => {
    let finish;
    const upload = new Promise((resolve) => { finish = resolve; });
    const { window, switchLanguage } = realm("pages/admin/knowledge.html", "admin", { upload });
    await vi.waitFor(() => expect(cards(window)).toHaveLength(2));
    window.document.getElementById("kb-add-btn").click();
    chooseFile(window, "price-list.pdf", "%PDF-1.4", "application/pdf");
    const save = window.document.getElementById("kb-save-btn");
    save.click();
    await vi.waitFor(() => expect(save.textContent).toBe(pl.kb_uploading));
    switchLanguage("en");
    expect(save.textContent).toBe(en.kb_uploading);
    expect(window.document.getElementById("kb-modal-title").textContent).toBe(en.kb_add);
    finish(response({ error: "Refused" }, 400));
    await vi.waitFor(() => expect(save.disabled).toBe(false));
    expect(save.textContent).toBe(en.kb_save);
  });

  it("an observer reads but cannot add, and the entry stays open to him", async () => {
    const { window } = realm("pages/admin/knowledge.html", "observer");
    await vi.waitFor(() => expect(cards(window)).toHaveLength(2));
    expect(window.document.getElementById("kb-add-btn").hidden).toBe(true);
    expect(window.document.getElementById("kb-usage").hidden).toBe(true);
    expect(window.document.querySelector(".kb-edit, .kb-delete")).toBeNull();
    const entry = window.document.querySelector('.nav-item[href="knowledge.html"]');
    expect(entry.classList.contains("nav-item-disabled")).toBe(false);
  });
});

describe("knowledge base helpers", () => {
  it("formats sizes in the active language", () => {
    const { window } = realm("pages/partner/knowledge.html", "partner");
    const { formatSize } = window.KnowledgeBase;
    expect(formatSize(500)).toBe("1 KB");
    expect(formatSize(1536 * 1024)).toBe("1,5 MB");
    expect(formatSize(25 * 1024 * 1024)).toBe("25 MB");
  });
});
