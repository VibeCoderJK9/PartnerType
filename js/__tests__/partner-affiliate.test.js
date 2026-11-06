/* ============================================================
   Affiliate › My referral link (partner)
   ============================================================ */
import { describe, expect, it, vi } from "vitest";
import { loadScript } from "./helpers/load-script.js";
import { appStub, dictionary } from "./helpers/copy.js";

globalThis.App = appStub("pl");
globalThis.showToast = vi.fn();
globalThis.ApiClient = { get: vi.fn(), post: vi.fn() };

loadScript("js/partner-affiliate.js");

const pl = dictionary("pl");

const SUMMARY = {
  enabled: true,
  link: { id: 1, code: "abcde12345", url: "https://partners.example.com/r/abcde12345" },
  clicks: 12,
  purchases: 3,
  pending_count: 1,
  pending_commission: "35.00",
  approved_commission: "140.00",
  currency: "PLN",
};

async function mount(summary) {
  document.body.innerHTML = '<p id="affiliate-off" hidden></p>'
    + '<div id="affiliate-card" hidden><div id="affiliate-card-body"></div></div>';
  ApiClient.get.mockReset();
  if (summary instanceof Error) ApiClient.get.mockRejectedValue(summary);
  else ApiClient.get.mockResolvedValue(structuredClone(summary));
  ApiClient.post.mockReset();
  showToast.mockReset();
  await PartnerAffiliate.load();
  return document.getElementById("affiliate-card");
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));
const money = (value) => new Intl.NumberFormat("pl-PL", { style: "currency", currency: "PLN" })
  .format(value);

describe("my referral link", () => {
  it("stays hidden while the programme is off", async () => {
    expect((await mount({ ...SUMMARY, enabled: false })).hidden).toBe(true);
  });

  it("says the programme is off instead of leaving the page empty", async () => {
    await mount({ ...SUMMARY, enabled: false });
    const off = document.getElementById("affiliate-off");
    expect(off.hidden).toBe(false);
    expect(off.textContent).toBe(pl.aff_partner_off);
  });

  it("says the link could not be loaded when the summary fails", async () => {
    await mount(new Error("offline"));
    const off = document.getElementById("affiliate-off");
    expect(off.hidden).toBe(false);
    expect(off.textContent).toBe(pl.aff_partner_failed);
  });

  it("clears the notice once the card is shown", async () => {
    await mount(SUMMARY);
    expect(document.getElementById("affiliate-off").hidden).toBe(true);
  });

  it("stays hidden when the summary cannot be read", async () => {
    expect((await mount(new Error("403"))).hidden).toBe(true);
  });

  it("shows the link and what it brought", async () => {
    const card = await mount(SUMMARY);
    expect(card.hidden).toBe(false);
    expect(document.getElementById("affiliate-link").value).toBe(SUMMARY.link.url);
    expect(document.getElementById("affiliate-clicks").textContent).toBe("12");
    expect(document.getElementById("affiliate-purchases").textContent).toBe("3");
    expect(document.getElementById("affiliate-pending").textContent).toBe(money(35));
    expect(document.getElementById("affiliate-approved").textContent).toBe(money(140));
  });

  it("creates the link on request", async () => {
    await mount({ ...SUMMARY, link: null, clicks: 0, purchases: 0 });
    expect(document.body.textContent).toContain(pl.aff_no_link_yet);
    ApiClient.post.mockResolvedValue(SUMMARY);
    document.getElementById("affiliate-create-link").click();
    await flush();
    expect(ApiClient.post).toHaveBeenCalledWith("/api/affiliate/me/link", {});
    expect(document.getElementById("affiliate-link").value).toBe(SUMMARY.link.url);
  });

  it("reports a failure to create the link", async () => {
    await mount({ ...SUMMARY, link: null });
    ApiClient.post.mockRejectedValue(new Error("The affiliate programme is switched off."));
    document.getElementById("affiliate-create-link").click();
    await flush();
    expect(showToast).toHaveBeenCalledWith("error", pl.aff_action_failed,
      "The affiliate programme is switched off.");
  });

  it("copies the link", async () => {
    await mount(SUMMARY);
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(window.navigator, "clipboard", { value: { writeText }, configurable: true });
    document.getElementById("affiliate-copy").click();
    await flush();
    expect(writeText).toHaveBeenCalledWith(SUMMARY.link.url);
    expect(showToast).toHaveBeenCalledWith("success", pl.aff_link_copied, "");
  });

  it("selects the link for a manual copy without a clipboard", async () => {
    await mount(SUMMARY);
    Object.defineProperty(window.navigator, "clipboard", { value: undefined, configurable: true });
    document.getElementById("affiliate-copy").click();
    await flush();
    expect(showToast).toHaveBeenCalledWith("warning", pl.int_copy_failed, "");
  });
});
