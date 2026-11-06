/* ============================================================
   The shop snippet (js/pt-ref.js)
   ============================================================

   It runs on the producer's shop, not in PartnerType, so these tests pin
   what the shop and its visitors are promised: no cookie before consent, a
   cookie that lives as long as the administrator set, the last click wins,
   and nothing but a code-shaped value ever reaches the cookie.
   ============================================================ */
import { afterEach, describe, expect, it } from "vitest";
import { loadScript } from "./helpers/load-script.js";

function visit(url) {
  window.happyDOM.setURL(url);
  loadScript("js/pt-ref.js");
  return window.PartnerTypeRef;
}

function clearCookies() {
  for (const part of document.cookie.split(";")) {
    const name = part.split("=")[0].trim();
    if (name) document.cookie = `${name}=; expires=Thu, 01 Jan 1970 00:00:00 GMT; Path=/`;
  }
}

/** Record every cookie the snippet writes while `run` executes. */
function cookieWrites(run) {
  let owner = Object.getPrototypeOf(document);
  while (owner && !Object.getOwnPropertyDescriptor(owner, "cookie")) {
    owner = Object.getPrototypeOf(owner);
  }
  const descriptor = Object.getOwnPropertyDescriptor(owner, "cookie");
  const writes = [];
  Object.defineProperty(document, "cookie", {
    configurable: true,
    get: () => descriptor.get.call(document),
    set: (value) => { writes.push(value); descriptor.set.call(document, value); },
  });
  try {
    run();
  } finally {
    delete document.cookie;
  }
  return writes;
}

afterEach(() => {
  clearCookies();
  delete window.PartnerTypeRefDays;
});

describe("pt-ref snippet", () => {
  it("stores nothing until the visitor consents", () => {
    const ref = visit("https://shop.example.com/?pt_ref=abcde12345&pt_click=Click_0123456789ab");
    expect(document.cookie).toBe("");
    expect(ref.get()).toBeNull();
    ref.consent();
    expect(ref.get()).toBe("Click_0123456789ab");
  });

  it("falls back to the partner code when there is no click id", () => {
    const ref = visit("https://shop.example.com/product?pt_ref=abcde12345");
    ref.consent();
    expect(ref.get()).toBe("abcde12345");
  });

  it("lets the last click win", () => {
    visit("https://shop.example.com/?pt_ref=firstcode1").consent();
    const ref = visit("https://shop.example.com/?pt_ref=secondcode");
    ref.consent();
    expect(ref.get()).toBe("secondcode");
  });

  it("keeps the earlier referral when a page has none", () => {
    visit("https://shop.example.com/?pt_ref=firstcode1").consent();
    const ref = visit("https://shop.example.com/cart");
    ref.consent();
    expect(ref.get()).toBe("firstcode1");
  });

  it.each([
    "https://shop.example.com/?pt_ref=%3Cscript%3E",
    "https://shop.example.com/?pt_ref=a%3B%20domain%3Devil.example",
    `https://shop.example.com/?pt_ref=${"x".repeat(65)}`,
  ])("ignores a value that is not a code: %s", (url) => {
    const ref = visit(url);
    ref.consent();
    expect(ref.get()).toBeNull();
    expect(document.cookie).toBe("");
  });

  it("writes a first-party cookie for the configured number of days", () => {
    const writes = cookieWrites(() => {
      window.PartnerTypeRefDays = 7;
      visit("https://shop.example.com/?pt_ref=abcde12345").consent();
    });
    expect(writes).toEqual(["pt_ref=abcde12345; Max-Age=604800; Path=/; SameSite=Lax; Secure"]);
  });

  it.each([0, 400, "many"])("uses 30 days when the setting is %s", (setting) => {
    const writes = cookieWrites(() => {
      window.PartnerTypeRefDays = setting;
      visit("https://shop.example.com/?pt_ref=abcde12345").consent();
    });
    expect(writes[0]).toContain("Max-Age=2592000");
  });

  it("forgets the referral when consent is withdrawn", () => {
    const ref = visit("https://shop.example.com/?pt_ref=abcde12345");
    ref.consent();
    ref.clear();
    expect(ref.get()).toBeNull();
  });
});
