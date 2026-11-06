/* ============================================================
   Affiliate › Settings
   ============================================================

   What makes this section safe: it is drawn only for whoever the admin-only
   API answers, the signing secret is shown once and is gone after Close,
   replacing it asks first, and every value (the shop address, the snippet) is
   a form value or text, never markup.
   ============================================================ */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { loadScript } from "./helpers/load-script.js";
import { appStub, dictionary } from "./helpers/copy.js";

globalThis.App = appStub("pl");
globalThis.showToast = vi.fn();
globalThis.ApiClient = { get: vi.fn(), patch: vi.fn(), post: vi.fn(), put: vi.fn() };

loadScript("js/admin-affiliate-settings.js");

const pl = dictionary("pl");

const SETTINGS = {
  enabled: false,
  shop_url: "",
  cookie_days: 30,
  auto_approve: false,
  auto_approve_days: 14,
  currency: "PLN",
  webhook_url: "https://partners.example.com/api/affiliate/webhook",
  link_base: "https://partners.example.com/r/",
  secret: null,
  stripe_webhook_url: "https://partners.example.com/api/affiliate/stripe",
  stripe_secret: null,
  snippet: "<script>window.PartnerTypeRefDays = 30;</script>\n<script>\n/* snippet */\n</script>\n",
};

async function mount(settings = SETTINGS) {
  document.body.innerHTML = '<div id="int-affiliate"></div>';
  ApiClient.get.mockReset().mockResolvedValue(structuredClone(settings));
  ApiClient.patch.mockReset();
  ApiClient.post.mockReset();
  ApiClient.put.mockReset();
  showToast.mockReset();
  await AffiliateSettings.load();
  return document.getElementById("int-affiliate");
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

describe("affiliate settings", () => {
  beforeEach(() => {
    delete window.navigator.clipboard;
  });

  it("draws nothing when the API refuses (not the administrator)", async () => {
    document.body.innerHTML = '<div id="int-affiliate"></div>';
    ApiClient.get.mockReset().mockRejectedValue(new Error("403"));
    await AffiliateSettings.load();
    expect(document.getElementById("int-affiliate").childElementCount).toBe(0);
  });

  it("shows the current settings, the webhook address and the snippet", async () => {
    const host = await mount();
    expect(host.textContent).toContain(pl.aff_settings_title);
    expect(document.getElementById("aff-enabled").checked).toBe(false);
    expect(document.getElementById("aff-cookie-days").value).toBe("30");
    expect(document.getElementById("aff-auto-days").value).toBe("14");
    expect(document.getElementById("aff-webhook-url").value).toBe(SETTINGS.webhook_url);
    expect(document.getElementById("aff-snippet").value).toBe(SETTINGS.snippet);
    expect(host.textContent).toContain(pl.aff_secret_none);
  });

  it("switches the programme on with one request", async () => {
    await mount();
    ApiClient.patch.mockResolvedValue({ ...SETTINGS, enabled: true });
    const toggle = document.getElementById("aff-enabled");
    toggle.checked = true;
    toggle.dispatchEvent(new Event("change"));
    await flush();
    expect(ApiClient.patch).toHaveBeenCalledWith("/api/affiliate/settings", { enabled: true });
    expect(document.getElementById("aff-enabled").checked).toBe(true);
  });

  it("saves the shop address and the day counts as numbers", async () => {
    await mount();
    ApiClient.patch.mockResolvedValue(SETTINGS);
    document.getElementById("aff-shop-url").value = "https://shop.example.com/";
    document.getElementById("aff-cookie-days").value = "45";
    document.getElementById("aff-auto").checked = true;
    document.getElementById("aff-auto-days").value = "21";
    document.getElementById("aff-save").click();
    await flush();
    expect(ApiClient.patch).toHaveBeenCalledWith("/api/affiliate/settings", {
      shop_url: "https://shop.example.com/", cookie_days: 45, auto_approve: true,
      auto_approve_days: 21,
    });
  });

  it("reports a refused save", async () => {
    await mount();
    ApiClient.patch.mockRejectedValue(new Error("Enter the full shop address"));
    document.getElementById("aff-save").click();
    await flush();
    expect(showToast).toHaveBeenCalledWith("error", pl.int_save_failed, "Enter the full shop address");
  });

  it("shows a new secret once and forgets it on Close", async () => {
    await mount();
    ApiClient.post.mockResolvedValue({ secret: "ptw_fresh-secret-value", fingerprint: "ptw_fresh-se" });
    ApiClient.get.mockResolvedValue({ ...SETTINGS,
      secret: { fingerprint: "ptw_fresh-se", created_at: "2026-11-02T10:00:00Z" } });
    document.getElementById("aff-secret-create").click();
    await flush();
    expect(ApiClient.post).toHaveBeenCalledWith("/api/affiliate/secret", {});
    const box = document.getElementById("aff-secret");
    expect(box.textContent).toContain("ptw_fresh-secret-value");
    expect(box.textContent).toContain(pl.aff_secret_once);
    document.getElementById("aff-secret-close").click();
    expect(document.body.textContent).not.toContain("ptw_fresh-secret-value");
    expect(document.getElementById("aff-secret").textContent).toContain("ptw_fresh-se");
  });

  it("asks before replacing an existing secret", async () => {
    await mount({ ...SETTINGS, secret: { fingerprint: "ptw_old12345", created_at: null } });
    document.getElementById("aff-secret-create").click();
    expect(ApiClient.post).not.toHaveBeenCalled();
    expect(document.getElementById("aff-secret").textContent).toContain(pl.aff_rotate_warning);
    document.getElementById("aff-rotate-cancel").click();
    expect(document.getElementById("aff-rotate-confirm")).toBeNull();
    document.getElementById("aff-secret-create").click();
    ApiClient.post.mockResolvedValue({ secret: "ptw_new", fingerprint: "ptw_new" });
    document.getElementById("aff-rotate-confirm").click();
    await flush();
    expect(ApiClient.post).toHaveBeenCalledWith("/api/affiliate/secret", {});
  });

  it("reports the test event either way", async () => {
    await mount();
    ApiClient.post.mockResolvedValueOnce({ ok: true, status: 200 });
    document.getElementById("aff-test").click();
    await flush();
    expect(showToast).toHaveBeenLastCalledWith("success", pl.aff_test_ok, "");
    ApiClient.post.mockResolvedValueOnce({ ok: false, status: 404, error: "Switched off" });
    document.getElementById("aff-test").click();
    await flush();
    expect(showToast).toHaveBeenLastCalledWith("error", pl.aff_test_failed, "Switched off");
  });

  it("copies with the clipboard when there is one", async () => {
    await mount();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(window.navigator, "clipboard", { value: { writeText }, configurable: true });
    document.getElementById("aff-snippet-copy").click();
    await flush();
    expect(writeText).toHaveBeenCalledWith(SETTINGS.snippet);
    expect(showToast).toHaveBeenCalledWith("success", pl.int_copied, "");
  });

  it("never turns a stored value into markup", async () => {
    const hostile = '"><img src=x onerror="window.pwned=1">';
    const host = await mount({ ...SETTINGS, shop_url: hostile, snippet: hostile,
      secret: { fingerprint: "<b>x</b>", created_at: null } });
    expect(host.querySelector("img, b")).toBeNull();
    expect(document.getElementById("aff-shop-url").value).toBe(hostile);
    expect(window.pwned).toBeUndefined();
  });

  it("stores the Stripe signing secret without ever showing it", async () => {
    await mount();
    expect(document.getElementById("aff-stripe-url").value).toBe(SETTINGS.stripe_webhook_url);
    expect(document.body.textContent).toContain(pl.aff_stripe_none);
    const input = document.getElementById("aff-stripe-secret");
    expect(input.type).toBe("password");
    document.getElementById("aff-stripe-save").click();
    expect(ApiClient.put).not.toHaveBeenCalled();
    ApiClient.put.mockResolvedValue({ ...SETTINGS,
      stripe_secret: { fingerprint: "whsec_abc123", created_at: null } });
    input.value = " whsec_abc123def456 ";
    document.getElementById("aff-stripe-save").click();
    await flush();
    expect(ApiClient.put).toHaveBeenCalledWith("/api/affiliate/stripe-secret",
      { secret: "whsec_abc123def456" });
    expect(document.getElementById("aff-stripe-secret").value).toBe("");
    expect(document.body.textContent).toContain("whsec_abc123");
    expect(document.body.textContent).not.toContain("whsec_abc123def456");
  });

  it("reports a refused Stripe secret", async () => {
    await mount();
    ApiClient.put.mockRejectedValue(new Error("Paste the Stripe secret"));
    document.getElementById("aff-stripe-secret").value = "nope";
    document.getElementById("aff-stripe-save").click();
    await flush();
    expect(showToast).toHaveBeenCalledWith("error", pl.int_save_failed, "Paste the Stripe secret");
  });
});
