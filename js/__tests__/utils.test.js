import { beforeEach, describe, expect, it, vi } from "vitest";
import { loadScript } from "./helpers/load-script.js";

loadScript("js/utils.js");

beforeEach(() => {
  sessionStorage.clear();
});

describe("Ui.escapeHtml", () => {
  it("encodes ampersands before angle brackets and both quote types", () => {
    expect(Ui.escapeHtml(`&<>'"`)).toBe("&amp;&lt;&gt;&#x27;&quot;");
  });

  it.each([null, undefined])("normalizes %s to an empty string", (value) => {
    expect(Ui.escapeHtml(value)).toBe("");
  });
});

describe("Ui.normalizeDestination", () => {
  it.each([
    ["dashboard.html", "dashboard.html"],
    ["./leads.html", "leads.html"],
    ["../partner/dashboard.html", "../partner/dashboard.html"],
    ["../../index.html", "../../index.html"],
    ["leads.html?status=new#top", "leads.html?status=new#top"],
  ])("accepts documented app-relative HTML destination %s", (input, output) => {
    expect(Ui.normalizeDestination(input)).toBe(output);
  });

  it.each([
    "https://evil.example/x.html",
    "//evil.example/x.html",
    "/pages/admin/dashboard.html",
    "javascript:alert(1)",
    "data:text/html,<script>alert(1)</script>",
    "../../../etc/passwd.html",
    ".././../admin/dashboard.html",
    "dashboard.php",
    "dashboard.html\\evil",
    "dashboard.html%0ajavascript:alert(1)",
  ])("rejects unsafe destination %s", (input) => {
    expect(Ui.normalizeDestination(input)).toBeNull();
  });
});

describe("Ui.safeNavigate", () => {
  it("assigns only a normalized safe destination", () => {
    const assign = vi.fn();

    expect(Ui.safeNavigate("./leads.html?id=4", assign)).toBe(true);
    expect(assign).toHaveBeenCalledWith("leads.html?id=4");
  });

  it("does not call the navigation sink for unsafe input", () => {
    const assign = vi.fn();

    expect(Ui.safeNavigate("javascript:alert(1)", assign)).toBe(false);
    expect(assign).not.toHaveBeenCalled();
  });
});

describe("Ui.rehydrateSession", () => {
  it("fetches the server-backed public user and caches display data only", async () => {
    const api = { get: vi.fn().mockResolvedValue({ user: { id: 7, role: "admin", name: "Ada" } }) };

    await expect(Ui.rehydrateSession(api)).resolves.toEqual({ id: 7, role: "admin", name: "Ada" });
    expect(api.get).toHaveBeenCalledWith("/api/auth/me");
    expect(JSON.parse(sessionStorage.getItem("ph_session"))).toEqual({ id: 7, role: "admin", name: "Ada" });
    expect(sessionStorage.getItem("ph_token")).toBeNull();
  });
});

describe("Ui.parseServerDate", () => {
  it("reads a naive API timestamp as UTC, not as local time", () => {
    // api/leads.py writes datetime.utcnow().isoformat() with no marker.
    expect(Ui.parseServerDate("2026-08-30T15:50:30.585633").toISOString())
      .toBe("2026-08-30T15:50:30.585Z");
  });

  it("leaves a timestamp that already declares its zone untouched", () => {
    expect(Ui.parseServerDate("2026-08-30T15:50:30Z").toISOString())
      .toBe("2026-08-30T15:50:30.000Z");
    expect(Ui.parseServerDate("2026-08-30T17:50:30+02:00").toISOString())
      .toBe("2026-08-30T15:50:30.000Z");
  });

  it("reports a freshly written timestamp as zero elapsed time", () => {
    // The defect this guards: a lead filed seconds ago read as "2 godz."
    // because the naive UTC string was parsed in CEST.
    const iso = new Date().toISOString().replace("Z", "");
    const elapsedHours = (Date.now() - Ui.parseServerDate(iso).getTime()) / 3600000;

    expect(Math.abs(elapsedHours)).toBeLessThan(0.01);
  });

  it("returns an invalid date for empty or non-string input rather than throwing", () => {
    expect(Number.isNaN(Ui.parseServerDate("").getTime())).toBe(true);
    expect(Number.isNaN(Ui.parseServerDate(null).getTime())).toBe(true);
  });

  it("passes a Date through unchanged", () => {
    const d = new Date("2026-08-30T15:50:30Z");
    expect(Ui.parseServerDate(d)).toBe(d);
  });
});

// Polish has three plural forms, and every call site in this app had been
// written with the English two. That produced the "many" plural form for 2, 3
// and 4 on screens people read daily.
describe("Ui.plPlural — three Polish forms, not two", () => {
  const lead = (n) => `${n} ${Ui.plPlural(n, "lead", "leady", "leadów")}`;

  it("uses the singular for exactly one", () => {
    expect(lead(1)).toBe("1 lead");
  });

  it("uses the 2-4 form where English would already say 'leads'", () => {
    expect(lead(2)).toBe("2 leady");
    expect(lead(3)).toBe("3 leady");
    expect(lead(4)).toBe("4 leady");
  });

  it("uses the many form from five upwards", () => {
    expect(lead(5)).toBe("5 leadów");
    expect(lead(11)).toBe("11 leadów");
    expect(lead(21)).toBe("21 leadów");
  });

  it("treats the teens as many even though they end in 2-4", () => {
    expect(lead(12)).toBe("12 leadów");
    expect(lead(13)).toBe("13 leadów");
    expect(lead(14)).toBe("14 leadów");
  });

  it("returns to the few form in the next decade", () => {
    expect(lead(22)).toBe("22 leady");
    expect(lead(102)).toBe("102 leady");
    expect(lead(112)).toBe("112 leadów");
  });

  it("uses the Polish 'many' form for zero, not the singular", () => {
    expect(lead(0)).toBe("0 leadów");
  });

  it("falls back to the many form for a value that is not a number", () => {
    expect(Ui.plPlural(undefined, "lead", "leady", "leadów")).toBe("leadów");
  });
});

// The producer names his own stages and writes his own questions. Those words
// are DATA, not product copy -- the only place an English version can come
// from is a second column he fills in himself, and an empty one has to read
// as "not translated yet" rather than as an empty caption.
describe("Ui.producerLabel", () => {
  beforeEach(() => {
    globalThis.App = { lang: "pl", t: (key) => key };
  });

  it("gives the Polish name to a Polish reader even when an English one exists", () => {
    App.lang = "pl";
    expect(Ui.producerLabel({ name: "Negocjacje", name_en: "Negotiation" }, "name"))
      .toBe("Negocjacje");
  });

  it("gives the English name to an English reader", () => {
    App.lang = "en";
    expect(Ui.producerLabel({ name: "Negocjacje", name_en: "Negotiation" }, "name"))
      .toBe("Negotiation");
  });

  it.each([undefined, "", "   "])(
    "falls back to the producer's own name when the English one is %p",
    (english) => {
      App.lang = "en";
      expect(Ui.producerLabel({ name: "Negocjacje", name_en: english }, "name"))
        .toBe("Negocjacje");
    },
  );

  it("reads a custom field through the same rule", () => {
    App.lang = "en";
    expect(Ui.fieldLabel({ label: "Branża", label_en: "Industry" })).toBe("Industry");
    expect(Ui.fieldLabel({ label: "Branża", label_en: "" })).toBe("Branża");
  });

  it("has nothing to say about a missing record", () => {
    expect(Ui.producerLabel(null, "name")).toBe("");
    expect(Ui.fieldLabel(undefined)).toBe("");
  });
});

describe("Ui.stageLabel with a producer's English name", () => {
  beforeEach(() => {
    globalThis.App = { lang: "en", t: (key) => key };
  });

  it("uses the producer's English name for his own stage", () => {
    expect(Ui.stageLabel({ name: "Negocjacje", name_en: "Negotiation", kind: "progress" }))
      .toBe("Negotiation");
  });

  it("still takes the two terminals from the dictionary, never from the row", () => {
    expect(Ui.stageLabel({ name: "Wygrany", name_en: "Whatever", kind: "win", is_terminal: 1 }))
      .toBe("stage_terminal_win");
    expect(Ui.stageLabel({ name: "Przegrany", kind: "lose", is_terminal: 1 }))
      .toBe("stage_terminal_lose");
  });

  it("falls back to the key when a stage has no name at all", () => {
    expect(Ui.stageLabel({ key: "negocjacje", kind: "progress" })).toBe("negocjacje");
  });
});
