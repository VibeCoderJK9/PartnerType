/* ============================================================
   Affiliate › Sales from referrals
   ============================================================

   The list of shop orders and the administrator's decisions on them. The
   buttons are the administrator's only (the server refuses everyone else
   anyway); order ids and partner names come from outside and stay text.
   ============================================================ */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { loadScript } from "./helpers/load-script.js";
import { appStub, dictionary } from "./helpers/copy.js";

globalThis.App = appStub("pl");
globalThis.showToast = vi.fn();
globalThis.ApiClient = { get: vi.fn(), post: vi.fn() };

loadScript("js/admin-affiliate.js");

const pl = dictionary("pl");

function order(overrides = {}) {
  return {
    id: 1, order_id: "A-1001", source: "webhook", status: "pending", amount: "1000.00",
    refunded_amount: null, currency: "PLN", occurred_at: "2026-11-01T10:00:00+00:00",
    partner_id: 5, partner_name: "Anna Partner", lead_id: null, commission: "70.00",
    commission_paid: false, ...overrides,
  };
}

function respond(items, partners = [{ id: 5, name: "Anna Partner" }, { id: 6, name: "Jan Partner" }]) {
  ApiClient.get.mockReset().mockImplementation((url) => {
    if (url === "/api/partners") return Promise.resolve({ partners });
    return Promise.resolve({ items, page: 1, per_page: 50, total: items.length });
  });
}

async function mount(items, role = "admin") {
  sessionStorage.setItem("ph_session", JSON.stringify({ role }));
  document.body.innerHTML = '<div id="affiliate-sales"></div>';
  respond(items);
  // By default the server does what it was asked for every order.
  ApiClient.post.mockReset().mockImplementation((url, body) => {
    const done = { approve: "approved", reject: "rejected" }[url.split("/").pop()];
    return Promise.resolve(done ? { results: body.ids.map((id) => ({ id, result: done })) } : {});
  });
  showToast.mockReset();
  await AffiliateSales.load();
  return document.getElementById("affiliate-sales");
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

function rowButtons(host, id) {
  const row = host.querySelector(`tr[data-id="${id}"]`);
  return [...row.querySelectorAll("button")].map((button) => button.textContent);
}

describe("affiliate sales", () => {
  beforeEach(() => sessionStorage.clear());

  it("starts with the orders waiting for approval", async () => {
    const host = await mount([order()]);
    expect(ApiClient.get).toHaveBeenCalledWith("/api/affiliate/conversions?page=1&status=pending");
    expect(host.textContent).toContain("A-1001");
    expect(host.textContent).toContain("Anna Partner");
    expect(host.textContent).toContain(pl.aff_status_pending);
    expect(rowButtons(host, 1)).toEqual([pl.aff_approve, pl.aff_reject]);
  });

  it("approves one order and reloads the list", async () => {
    const host = await mount([order()]);
    host.querySelector('tr[data-id="1"] button').click();
    await flush();
    expect(ApiClient.post).toHaveBeenCalledWith("/api/affiliate/conversions/approve", { ids: [1] });
    expect(showToast).toHaveBeenCalledWith("success", pl.aff_approved_toast, "");
  });

  it("approves the selected orders together", async () => {
    const host = await mount([order(), order({ id: 2, order_id: "A-1002" })]);
    for (const box of host.querySelectorAll('input[type="checkbox"]')) {
      box.checked = true;
      box.dispatchEvent(new Event("change"));
    }
    const bulk = document.getElementById("aff-approve-selected");
    expect(bulk.textContent).toBe(App.t("aff_approve_selected", { count: 2 }));
    bulk.click();
    await flush();
    expect(ApiClient.post).toHaveBeenCalledWith("/api/affiliate/conversions/approve", { ids: [1, 2] });
  });

  it("says an approval was refused because the partner is inactive", async () => {
    const host = await mount([order()]);
    ApiClient.post.mockResolvedValue({ results: [
      { id: 1, result: "partner_inactive", status: "pending" }] });
    host.querySelector('tr[data-id="1"] button').click();
    await flush();
    expect(showToast).not.toHaveBeenCalledWith("success", expect.anything(), expect.anything());
    expect(showToast).toHaveBeenCalledWith("warning", App.t("aff_result_partner_inactive",
      { orders: App.pluralize(1, "unit_order") }), "");
  });

  it("reports a bulk approval order by order", async () => {
    const host = await mount([order(), order({ id: 2, order_id: "A-1002" }),
      order({ id: 3, order_id: "A-1003" })]);
    for (const box of host.querySelectorAll('input[type="checkbox"]')) {
      box.checked = true;
      box.dispatchEvent(new Event("change"));
    }
    ApiClient.post.mockResolvedValue({ results: [
      { id: 1, result: "approved" }, { id: 2, result: "skipped", status: "approved" },
      { id: 3, result: "skipped", status: "refunded" }] });
    document.getElementById("aff-approve-selected").click();
    await flush();
    expect(showToast).toHaveBeenCalledWith("success", pl.aff_approved_toast,
      App.pluralize(1, "unit_order"));
    expect(showToast).toHaveBeenCalledWith("warning", App.t("aff_result_skipped",
      { orders: App.pluralize(2, "unit_order") }), "");
  });

  it("does not call a rejection done when the order was already decided", async () => {
    const host = await mount([order()]);
    ApiClient.post.mockResolvedValue({ results: [
      { id: 1, result: "skipped", status: "approved" }] });
    host.querySelectorAll('tr[data-id="1"] button')[1].click();
    await flush();
    expect(showToast).not.toHaveBeenCalledWith("success", expect.anything(), expect.anything());
    expect(showToast).toHaveBeenCalledWith("warning", App.t("aff_result_skipped",
      { orders: App.pluralize(1, "unit_order") }), "");
  });

  it("rejects an order", async () => {
    const host = await mount([order()]);
    host.querySelectorAll('tr[data-id="1"] button')[1].click();
    await flush();
    expect(ApiClient.post).toHaveBeenCalledWith("/api/affiliate/conversions/reject", { ids: [1] });
  });

  it("assigns an order without a partner", async () => {
    const host = await mount([order({ status: "unattributed", partner_id: null, partner_name: null,
      commission: null })]);
    expect(host.textContent).toContain(pl.aff_no_partner);
    const select = host.querySelector('tr[data-id="1"] select');
    select.value = "6";
    host.querySelector('tr[data-id="1"] button').click();
    await flush();
    expect(ApiClient.post).toHaveBeenCalledWith("/api/affiliate/conversions/1/assign", { partner_id: 6 });
  });

  it("does not assign until a partner is chosen", async () => {
    const host = await mount([order({ status: "unattributed", partner_id: null })]);
    host.querySelector('tr[data-id="1"] button').click();
    await flush();
    expect(ApiClient.post).not.toHaveBeenCalled();
  });

  it("offers to reverse an unpaid commission refunded after approval", async () => {
    window.confirm = vi.fn(() => true);
    const host = await mount([order({ status: "refund_after_approval", refunded_amount: "1000.00" })]);
    expect(host.textContent).toContain(App.t("aff_refunded_amount", { amount: "" }).trim());
    host.querySelector('tr[data-id="1"] button').click();
    await flush();
    expect(window.confirm).toHaveBeenCalledWith(App.t("aff_reverse_q", { order: "A-1001" }));
    expect(ApiClient.post).toHaveBeenCalledWith("/api/affiliate/conversions/1/reverse", {});
  });

  it("reverses nothing when the administrator says no", async () => {
    window.confirm = vi.fn(() => false);
    const host = await mount([order({ status: "refund_after_approval", refunded_amount: "1000.00" })]);
    host.querySelector('tr[data-id="1"] button').click();
    await flush();
    expect(ApiClient.post).not.toHaveBeenCalled();
  });

  it("forgets the selection when the page changes", async () => {
    sessionStorage.setItem("ph_session", JSON.stringify({ role: "admin" }));
    document.body.innerHTML = '<div id="affiliate-sales"></div>';
    ApiClient.get.mockReset().mockImplementation((url) => {
      if (url === "/api/partners") return Promise.resolve({ partners: [] });
      return Promise.resolve({ items: [order()], page: 1, per_page: 50, total: 120 });
    });
    await AffiliateSales.load();
    const host = document.getElementById("affiliate-sales");
    const box = host.querySelector('input[type="checkbox"]');
    box.checked = true;
    box.dispatchEvent(new Event("change"));
    expect(document.getElementById("aff-approve-selected")).not.toBeNull();
    const next = [...host.querySelectorAll("button")].find((b) => b.textContent === pl.aff_next);
    next.click();
    await flush();
    expect(document.getElementById("aff-approve-selected")).toBeNull();
  });

  it("opens on the status named in the link", async () => {
    window.history.replaceState({}, "", "/pages/admin/affiliate.html?status=refund_after_approval");
    try {
      await mount([order({ status: "refund_after_approval" })]);
      expect(ApiClient.get).toHaveBeenCalledWith(
        "/api/affiliate/conversions?page=1&status=refund_after_approval");
      expect(document.getElementById("aff-status-filter").value).toBe("refund_after_approval");
    } finally {
      window.history.replaceState({}, "", "/");
    }
  });

  it("ignores an unknown status in the link", async () => {
    window.history.replaceState({}, "", "/?status=<script>");
    try {
      await mount([order()]);
      expect(ApiClient.get).toHaveBeenCalledWith("/api/affiliate/conversions?page=1&status=pending");
    } finally {
      window.history.replaceState({}, "", "/");
    }
  });

  it("only informs when that commission is already paid", async () => {
    const host = await mount([order({ status: "refund_after_approval", commission_paid: true })]);
    expect(rowButtons(host, 1)).toEqual([]);
    expect(host.textContent).toContain(pl.aff_paid_info);
  });

  it("only informs about a partial refund after approval", async () => {
    const host = await mount([order({ status: "refund_after_approval", refunded_amount: "250.00" })]);
    expect(rowButtons(host, 1)).toEqual([]);
    expect(host.textContent).toContain(pl.aff_partial_info);
  });

  it("explains why the order of an inactive partner cannot be approved", async () => {
    const host = await mount([order({ partner_active: false, partner_state: "inactive" })]);
    expect(rowButtons(host, 1)).toEqual([pl.aff_reject]);
    expect(host.textContent).toContain(pl.aff_partner_inactive_info);
    expect(host.querySelector('input[type="checkbox"]')).toBeNull();
  });

  it("never suggests reactivating an erased partner", async () => {
    const host = await mount([order({ partner_active: false, partner_state: "erased" })]);
    expect(rowButtons(host, 1)).toEqual([pl.aff_reject]);
    expect(host.textContent).toContain(pl.aff_partner_erased_info);
    expect(host.textContent).not.toContain(pl.aff_partner_inactive_info);
  });

  it("says the partner no longer exists when the account was deleted", async () => {
    const host = await mount([order({ partner_id: null, partner_name: null, partner_active: false,
      partner_state: null, commission: null })]);
    expect(rowButtons(host, 1)).toEqual([pl.aff_reject]);
    expect(host.textContent).toContain(pl.aff_partner_removed_info);
    expect(host.textContent).not.toContain(pl.aff_partner_inactive_info);
    expect(host.querySelector('input[type="checkbox"]')).toBeNull();
  });

  it("shows no decision buttons to a moderator", async () => {
    const host = await mount([order(), order({ id: 2, status: "unattributed" })], "manager");
    expect(rowButtons(host, 1)).toEqual([]);
    expect(rowButtons(host, 2)).toEqual([]);
    expect(host.querySelector('input[type="checkbox"]')).toBeNull();
  });

  it("filters by status", async () => {
    const host = await mount([order()]);
    const select = document.getElementById("aff-status-filter");
    select.value = "approved";
    select.dispatchEvent(new Event("change"));
    await flush();
    expect(ApiClient.get).toHaveBeenLastCalledWith("/api/affiliate/conversions?page=1&status=approved");
    select.value = "";
    select.dispatchEvent(new Event("change"));
    await flush();
    expect(ApiClient.get).toHaveBeenLastCalledWith("/api/affiliate/conversions?page=1");
    expect(host).toBeTruthy();
  });

  it("says when there is nothing to show or the list failed", async () => {
    let host = await mount([]);
    expect(host.textContent).toContain(pl.aff_empty);
    ApiClient.get.mockRejectedValue(new Error("500"));
    document.getElementById("aff-status-filter").dispatchEvent(new Event("change"));
    await flush();
    host = document.getElementById("affiliate-sales");
    expect(host.textContent).toContain(pl.aff_load_failed);
  });

  it("reports a refused decision", async () => {
    const host = await mount([order()]);
    ApiClient.post.mockRejectedValue(new Error("There is no active won stage."));
    host.querySelector('tr[data-id="1"] button').click();
    await flush();
    expect(showToast).toHaveBeenCalledWith("error", pl.aff_action_failed,
      "There is no active won stage.");
  });

  it("keeps order ids and partner names as text", async () => {
    const host = await mount([order({ order_id: "<img src=x onerror=alert(1)>",
      partner_name: "<b>Partner</b>" })]);
    expect(host.querySelector("img, b")).toBeNull();
    expect(host.textContent).toContain("<img src=x onerror=alert(1)>");
  });

  it("pages through a long list", async () => {
    sessionStorage.setItem("ph_session", JSON.stringify({ role: "admin" }));
    document.body.innerHTML = '<div id="affiliate-sales"></div>';
    ApiClient.get.mockReset().mockImplementation((url) => (url === "/api/partners"
      ? Promise.resolve({ partners: [] })
      : Promise.resolve({ items: [order()], page: 1, per_page: 50, total: 120 })));
    await AffiliateSales.load();
    const host = document.getElementById("affiliate-sales");
    expect(host.textContent).toContain(App.t("aff_page", { page: 1, pages: 3 }));
    [...host.querySelectorAll("button")].find((b) => b.textContent === pl.aff_next).click();
    await flush();
    expect(ApiClient.get).toHaveBeenLastCalledWith("/api/affiliate/conversions?page=2&status=pending");
  });
});
