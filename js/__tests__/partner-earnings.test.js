import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { loadScript } from "./helpers/load-script.js";
import { appStub } from "./helpers/copy.js";

// The controller reads its copy from the dictionary; api.js does too, and both
// are loaded here, so App has to exist before either runs.
globalThis.App = appStub("pl");

loadScript("js/icons.js");
loadScript("js/api.js");
loadScript("js/utils.js");
loadScript("js/partner-earnings.js");

function buildEarningsDom(page = "partner-earnings") {
  document.body.innerHTML = '<div id="earnings-content"></div>';
  document.body.dataset.page = page;
}

beforeEach(() => {
  sessionStorage.clear();
  buildEarningsDom();
  globalThis.initPartnerPage = vi.fn().mockResolvedValue({ id: 42, role: "partner" });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("Ui.escapeHtml", () => {
  it("converts all HTML-significant characters to entities", () => {
    expect(Ui.escapeHtml(`&<>"'`)).toBe("&amp;&lt;&gt;&quot;&#x27;");
  });

  it("returns the empty string for null", () => {
    expect(Ui.escapeHtml(null)).toBe("");
  });

  it("returns the empty string for undefined", () => {
    expect(Ui.escapeHtml(undefined)).toBe("");
  });

  it("coerces a number to its string form", () => {
    expect(Ui.escapeHtml(42)).toBe("42");
  });

  it("renders a script-tag-shaped tier name as escaped text, creating no live script element", () => {
    renderEarnings({ tier_name: "<script>alert(1)</script>" }, {});

    const container = document.getElementById("earnings-content");
    expect(container.querySelector("script")).toBeNull();
    expect(container.innerHTML).toContain("&lt;script&gt;alert(1)&lt;/script&gt;");
  });
});

describe("renderEarnings", () => {
  it("renders a base-rate summary row containing the tier name and the base rate", () => {
    renderEarnings({ tier_name: "Silver", base_rate: 12 }, {});

    const html = document.getElementById("earnings-content").innerHTML;
    expect(html).toContain("Silver");
    expect(html).toContain("12%");
  });

  it("renders one extra row per percent bonus, each showing the name and its value to two decimal places", () => {
    renderEarnings(
      {
        bonus_percent: [
          { name: "Bonus A", default_value: 1.5 },
          { name: "Bonus B", default_value: 9 },
        ],
      },
      {},
    );

    const html = document.getElementById("earnings-content").innerHTML;
    expect(html).toContain("Bonus A");
    expect(html).toContain("+1.50%");
    expect(html).toContain("Bonus B");
    expect(html).toContain("+9.00%");
  });

  it("renders one extra row per cash bonus, each showing the value with no decimals and the currency word", () => {
    renderEarnings(
      {
        bonus_cash: [
          { name: "Cash A", default_value: 100 },
          { name: "Cash B", default_value: 250 },
        ],
      },
      {},
    );

    const html = document.getElementById("earnings-content").innerHTML;
    expect(html).toContain("Cash A");
    expect(html).toContain("+100&nbsp;zł");
    expect(html).toContain("Cash B");
    expect(html).toContain("+250&nbsp;zł");
  });

  it("prefers a bonus's effective value over its default value when present", () => {
    renderEarnings(
      {
        bonus_percent: [
          { name: "Bonus A", effective_value: 3.25, default_value: 1 },
        ],
      },
      {},
    );

    const html = document.getElementById("earnings-content").innerHTML;
    expect(html).toContain("+3.25%");
    expect(html).not.toContain("+1.00%");
  });

  it("falls back to the default value when the effective value is null", () => {
    renderEarnings(
      {
        bonus_cash: [
          { name: "Cash A", effective_value: null, default_value: 75 },
        ],
      },
      {},
    );

    const html = document.getElementById("earnings-content").innerHTML;
    expect(html).toContain("+75&nbsp;zł");
  });

  it("renders without throwing when the tier name, colour, base rate, percent total and cash total are all missing, using the documented defaults", () => {
    expect(() => renderEarnings({}, {})).not.toThrow();

    const html = document.getElementById("earnings-content").innerHTML;
    expect(html).toContain("Brak poziomu");
    expect(html).toContain("0%");
  });
});

describe("initEarnings", () => {
  it("navigates to the root index path and never calls the API when no session is stored", async () => {
    const getMock = vi.spyOn(ApiClient, "get");
    globalThis.initPartnerPage.mockResolvedValue(null);
    const navigate = vi.spyOn(Ui, "safeNavigate").mockReturnValue(true);

    await initEarnings();

    expect(navigate).toHaveBeenCalledWith("../../index.html");
    expect(getMock).not.toHaveBeenCalled();
  });

  it("calls the API with a path containing the stored partner id, then renders the result", async () => {
    const getMock = vi.spyOn(ApiClient, "get").mockResolvedValue({
      partner: { tier_name: "Gold", base_rate: 20 },
    });

    await initEarnings();

    expect(getMock).toHaveBeenCalledWith("/api/partner-bonuses/42");
    expect(document.getElementById("earnings-content").innerHTML).toContain(
      "Gold",
    );
  });

  it("writes an error block containing the rejection message into the content container when the API get rejects", async () => {
    vi.spyOn(ApiClient, "get").mockRejectedValue(new Error("boom"));

    await initEarnings();

    const html = document.getElementById("earnings-content").innerHTML;
    expect(html).toContain("empty-state");
    expect(html).toContain("boom");
  });
});

describe("DOMContentLoaded page gate", () => {
  it("leaves the content container untouched when the body page marker is not the earnings one", () => {
    buildEarningsDom("some-other-page");
    document.getElementById("earnings-content").textContent = "UNTOUCHED";

    document.dispatchEvent(new Event("DOMContentLoaded"));

    expect(document.getElementById("earnings-content").textContent).toBe(
      "UNTOUCHED",
    );
  });
});

describe("renderEarnings — the single percentage total", () => {
  it("sums the tier base rate and each active percent bonus into the one percentage total the API already computed", () => {
    renderEarnings(
      { tier_name: "Gold", base_rate: 10, total_percent: 15, bonus_percent: [{ name: "Extra", default_value: 5 }] },
      {},
      [],
    );

    const total = document.getElementById("earnings-percent-total");
    expect(total).not.toBeNull();
    expect(total.textContent).toBe("15.00%");
  });

  it("never folds the cash total into the percentage total element", () => {
    renderEarnings(
      {
        tier_name: "Gold",
        base_rate: 10,
        total_percent: 10,
        total_cash: 300,
        bonus_cash: [{ name: "Bonus C", default_value: 300 }],
      },
      {},
      [],
    );

    const percentTotal = document.getElementById("earnings-percent-total");
    const cashTotal = document.getElementById("earnings-cash-total");
    expect(percentTotal.textContent).toBe("10.00%");
    expect(percentTotal.textContent).not.toContain("300");
    expect(cashTotal.textContent).toContain("300");
  });
});

describe("renderEarnings — per-lead commissions and the grand total", () => {
  it("sums only the displayed (locked) per-lead commissions plus the cash total into the grand total, and nothing else", () => {
    renderEarnings(
      { total_percent: 25, total_cash: 50 },
      {},
      [
        { id: 1, commission: 100, status: "accepted" },
        { id: 2, commission: 200, status: "paid" },
      ],
    );

    const grandTotal = document.getElementById("earnings-grand-total");
    expect(grandTotal.textContent).toContain("350");
  });

  it("labels a not-yet-locked commission as provisional, excludes it from the grand total, and surfaces it in the provisional note", () => {
    renderEarnings(
      { total_cash: 0 },
      {},
      [
        { id: 1, commission: 100, status: "accepted" },
        { id: 2, commission: null, status: "in_progress" },
      ],
    );

    expect(document.getElementById("earnings-grand-total").textContent).toContain("100");
    const tbody = document.getElementById("earnings-leads-tbody");
    expect(tbody.textContent).toContain("Przewidywana");
    const note = document.getElementById("earnings-provisional-note");
    expect(note).not.toBeNull();
    expect(note.textContent).toContain("1");
  });

  it("shows the queue-position badge next to a lead's figure when its queue position is above one", () => {
    renderEarnings(
      {},
      {},
      [{ id: 1, commission: 50, status: "accepted", queue_position: 2 }],
      "100,50,25,10,0",
    );

    const tbody = document.getElementById("earnings-leads-tbody");
    expect(tbody.textContent).toContain("2. zgłoszenie — 50% prowizji");
  });

  it("renders an explicit empty state with the grand total shown as zero, not blank or an em dash, when the partner has no earnings", () => {
    renderEarnings({ tier_name: "Bronze", base_rate: 0, total_percent: 0, total_cash: 0 }, {}, []);

    expect(document.getElementById("earnings-leads-empty")).not.toBeNull();
    const grandTotal = document.getElementById("earnings-grand-total");
    expect(grandTotal.textContent).not.toContain("—");
    expect(grandTotal.textContent).toContain("0");
  });

  it("with the server summary, states the same total the ledger does, paid cash bonuses included", () => {
    // The card said 28 730 zl and "no cash bonuses"
    // while the ledger below it said 29 730 zl paid, a 1 000 zl bonus included.
    renderEarnings(
      { total_cash: 0, bonus_cash: [] },
      {},
      [{ id: 1, commission: 28730, status: "won" }],
      "100",
      { totals: { paid: 29730, paid_bonus: 1000, due: 0, due_bonus: 0, awaiting: 0, pipeline: 0 },
        periods: { month: [], quarter: [], year: [] }, payouts: [] },
    );

    expect(document.getElementById("earnings-grand-total").textContent).toContain("29");
    expect(document.getElementById("earnings-grand-total").textContent).toContain("730");
    expect(document.getElementById("earnings-cash-total").textContent).toContain("1000");
    expect(document.getElementById("earnings-paid-total").textContent).toContain("28");
  });

  it("renders a markup-bearing bonus name as literal text, creating no live element", () => {
    renderEarnings(
      { bonus_percent: [{ name: "<img src=x onerror=alert(1)>", default_value: 1 }] },
      {},
      [],
    );

    const container = document.getElementById("earnings-content");
    expect(container.querySelector("img, script")).toBeNull();
  });
});

describe("initEarnings — shared load-error copy and a working retry", () => {
  it("shows the shared load-error copy and re-renders after a successful retry", async () => {
    let shouldFail = true;
    const getMock = vi.spyOn(ApiClient, "get").mockImplementation(async (url) => {
      if (url.startsWith("/api/partner-bonuses/")) {
        if (shouldFail) throw new Error("boom");
        return { partner: { tier_name: "Gold", base_rate: 20 } };
      }
      if (url === "/api/leads") return { leads: [] };
      if (url === "/api/settings") return { settings: { lead_queue_shares: "100" } };
      throw new Error(`unexpected url ${url}`);
    });

    await initEarnings();
    expect(document.getElementById("earnings-content").textContent).toContain(
      "Nie udało się załadować danych. Spróbuj ponownie.",
    );

    const retryBtn = document.getElementById("earnings-retry-btn");
    expect(retryBtn).not.toBeNull();

    shouldFail = false;
    retryBtn.click();

    await vi.waitFor(() =>
      expect(document.getElementById("earnings-content").textContent).toContain("Gold"),
    );
    expect(getMock).toHaveBeenCalledWith("/api/leads");
    expect(getMock).toHaveBeenCalledWith("/api/settings");
  });
});

/* ── Settlements over time ──────────────────────────────────────
   On the partner's own screen: what was paid and what waits for payout, for
   a chosen month, quarter, year or in total, plus the wins still queued.

   The figures themselves are the backend's (GET /api/my-earnings, pinned by
   api/tests/unit/test_partner_earnings.py). What these pin is the contract on
   this side: the screen RENDERS what it is given and never re-derives money,
   the picker really switches the breakdown, and a ledger that fails to load
   does not take the rest of the screen down with it. */
const ledgerSummary = {
  totals: {
    paid: 20100, due: 500, awaiting: 900, pipeline: 1200,
    paid_lead_count: 3, due_lead_count: 0, awaiting_lead_count: 1,
  },
  periods: {
    month: [
      { period_key: "2026-09", paid: 12500, due: 500, awaiting: 900 },
      { period_key: "2026-08", paid: 7600, due: 0, awaiting: 0 },
    ],
    quarter: [{ period_key: "2026-Q3", paid: 20100, due: 500, awaiting: 900 }],
    year: [{ period_key: "2026", paid: 20100, due: 500, awaiting: 900 }],
  },
  payouts: [
    { period_type: "month", period_key: "2026-09", amount: 12500, lead_count: 1,
      paid_at: "2026-09-13T08:44:23" },
  ],
};

function ledgerCells() {
  return [...document.querySelectorAll("#earnings-ledger-tbody tr")]
    .map((row) => [...row.cells].map((cell) => cell.textContent));
}

describe("renderEarnings — settlements over time", () => {
  it("renders no ledger at all when the summary is missing, rather than a panel full of zeros", () => {
    renderEarnings({}, {}, [], "100", null);

    expect(document.getElementById("earnings-ledger")).toBeNull();
  });

  it("shows the four totals it was given without adding any of them together", () => {
    renderEarnings({}, {}, [], "100", ledgerSummary);

    // Intl groups with a non-breaking space, so digits are compared bare.
    const tiles = [...document.querySelectorAll(".earn-kpi")]
      .map((t) => t.textContent.replace(/\s/g, ""));

    expect(tiles).toHaveLength(4);
    expect(tiles[0]).toContain("20100");
    expect(tiles[1]).toContain("500");
    expect(tiles[2]).toContain("900");
    expect(tiles[3]).toContain("1200");
    expect(tiles.join("")).not.toContain("22700");  // no grand total is invented
  });

  it("opens on the monthly breakdown, one row per month, newest first as the API returned it", () => {
    renderEarnings({}, {}, [], "100", ledgerSummary);

    const rows = ledgerCells();
    expect(rows).toHaveLength(2);
    expect(rows[0][0]).toBe("2026-09");
    expect(rows[1][0]).toBe("2026-08");
  });

  it("totals each row as paid + awaiting payout + to-be-confirmed, and nothing else", () => {
    renderEarnings({}, {}, [], "100", ledgerSummary);

    const [first] = ledgerCells();
    // 12 500 + 500 + 900
    expect(first[4].replace(/\s/g, "")).toContain("13900");
  });

  it("switches the whole breakdown when the quarter button is pressed", () => {
    renderEarnings({}, {}, [], "100", ledgerSummary);

    document.querySelector('[data-earnings-period="quarter"]').click();

    expect(ledgerCells().map((r) => r[0])).toEqual(["2026-Q3"]);
  });

  it("shows one row built from the totals under 'Total', never a sum of the period rows", () => {
    // A lead with no usable date lands in the totals and in no period, so
    // adding the rows up would quietly under-report it.
    renderEarnings({}, {}, [], "100", {
      ...ledgerSummary,
      totals: { ...ledgerSummary.totals, paid: 99999 },
    });

    document.querySelector('[data-earnings-period="all"]').click();
    const rows = ledgerCells();

    expect(rows).toHaveLength(1);
    expect(rows[0][1].replace(/\s/g, "")).toContain("99999");
  });

  it("marks exactly one period button as pressed at a time", () => {
    renderEarnings({}, {}, [], "100", ledgerSummary);
    document.querySelector('[data-earnings-period="year"]').click();

    const pressed = [...document.querySelectorAll("[data-earnings-period]")]
      .filter((b) => b.getAttribute("aria-pressed") === "true")
      .map((b) => b.dataset.earningsPeriod);

    expect(pressed).toEqual(["year"]);
  });

  it("says so plainly when a breakdown has no rows instead of drawing an empty table", () => {
    renderEarnings({}, {}, [], "100", { ...ledgerSummary, periods: { month: [], quarter: [], year: [] } });

    expect(document.getElementById("earnings-ledger-tbody")).toBeNull();
    expect(document.getElementById("earnings-ledger").textContent)
      .toContain("Nie ma tu jeszcze żadnych rozliczeń.");
  });

  it("lists the producer's own payout receipts read-only", () => {
    renderEarnings({}, {}, [], "100", ledgerSummary);

    const text = document.getElementById("earnings-content").textContent;
    expect(text).toContain("Potwierdzone wypłaty");
    expect(text).toContain("2026-09");
  });

  it("says no payout has been recorded rather than leaving the panel blank", () => {
    renderEarnings({}, {}, [], "100", { ...ledgerSummary, payouts: [] });

    expect(document.getElementById("earnings-content").textContent)
      .toContain("Producent nie zarejestrował jeszcze żadnej wypłaty.");
  });
});

describe("renderEarnings — where a lead's money is", () => {
  /* On a money screen the Status column answers where the money is, not
     which pipeline stage the lead is on. */
  it("calls a paid lead paid", () => {
    renderEarnings({}, {}, [{ id: 1, commission: 100, commission_paid_at: "2026-09-13T00:00:00" }]);

    expect(document.getElementById("earnings-leads-tbody").textContent).toContain("Wypłacona");
  });

  it("calls a confirmed unpaid win awaiting payout", () => {
    renderEarnings({}, {}, [{ id: 1, commission: 100, won_at: "2026-09-01T00:00:00",
      win_confirmed_at: "2026-09-02T00:00:00" }]);

    expect(document.getElementById("earnings-leads-tbody").textContent).toContain("Czeka na wypłatę");
  });

  it("keeps a win the producer has not confirmed in its own state, never as an awaited payout", () => {
    renderEarnings({}, {}, [{ id: 1, commission: 100, won_at: "2026-09-01T00:00:00" }]);

    const text = document.getElementById("earnings-leads-tbody").textContent;
    expect(text).toContain("Do potwierdzenia");
    expect(text).not.toContain("Czeka na wypłatę");
  });

  it("calls a rejected submission rejected rather than projecting a commission for it", () => {
    renderEarnings({}, {}, [{ id: 1, commission: null, registration_status: "rejected" }]);

    expect(document.getElementById("earnings-leads-tbody").textContent).toContain("Odrzucony");
  });
});

/* The producer's OWN words -- the name he gave a bonus and the sentence
   explaining what it is for -- are data, not copy. The English version is a
   second column he fills in himself; empty means "not translated yet", and
   then the partner sees the Polish original rather than an empty chip.
   Ui.producerLabel is the only correct reader; each field falls back on its
   own, because he may translate the name and not the description. */
describe("renderEarnings — the producer's bonus names in the reader's language", () => {
  const withLang = (lang, run) => {
    const previous = globalThis.App;
    globalThis.App = appStub(lang);
    try { run(); } finally { globalThis.App = previous; }
  };

  const bonus = {
    name: "Bonus za webinar",
    name_en: "Webinar bonus",
    description: "Prowadzenie webinaru",
    description_en: "Running a webinar",
    default_value: 2,
    type: "percent",
  };

  it("shows the Polish name to a Polish reader", () => {
    withLang("pl", () => {
      renderEarnings({ bonus_percent: [bonus] }, {});
      expect(document.getElementById("earnings-content").textContent).toContain("Bonus za webinar");
    });
  });

  it("shows the producer's English name to an English reader", () => {
    withLang("en", () => {
      renderEarnings({ bonus_percent: [bonus] }, {});
      const text = document.getElementById("earnings-content").textContent;
      expect(text).toContain("Webinar bonus");
      expect(text).not.toContain("Bonus za webinar");
    });
  });

  it("falls back to the Polish name when he has not translated it yet", () => {
    withLang("en", () => {
      renderEarnings({ bonus_percent: [{ ...bonus, name_en: "" }] }, {});
      expect(document.getElementById("earnings-content").textContent).toContain("Bonus za webinar");
    });
  });

  it("falls back on the description alone when only the name is translated", () => {
    withLang("en", () => {
      renderEarnings({ bonus_percent: [{ ...bonus, description_en: "" }] }, {});
      const text = document.getElementById("earnings-content").textContent;
      expect(text).toContain("Webinar bonus");
      expect(text).toContain("Prowadzenie webinaru");
    });
  });

  it("tells the partner WHY a bonus was given, not only its name", () => {
    withLang("pl", () => {
      renderEarnings({ bonus_percent: [bonus] }, {});
      expect(document.getElementById("earnings-content").textContent).toContain("Prowadzenie webinaru");
    });
  });

  it("translates a granted cash bonus the same way", () => {
    withLang("en", () => {
      renderEarnings({
        bonus_cash: [{ name: "Nagroda kwartalna", name_en: "Quarterly award",
                       type: "cash", effective_value: 500 }],
      }, {});
      expect(document.getElementById("earnings-content").textContent).toContain("Quarterly award");
    });
  });

  it("shows the Polish snapshot for an award granted before the English column existed", () => {
    // A historic bonus_awards row has no name_en key at all --
    // not even an empty string -- so the fallback has to work on a field
    // that is simply missing, not just blank.
    withLang("en", () => {
      renderEarnings({
        bonus_cash: [{ name: "Stara nagroda", type: "cash", effective_value: 100 }],
      }, {});
      const text = document.getElementById("earnings-content").textContent;
      expect(text).toContain("Stara nagroda");
    });
  });

  it("renders markup in either language as literal text, creating no live element", () => {
    withLang("en", () => {
      renderEarnings({
        bonus_percent: [{
          name: "<script>alert(1)</script>",
          name_en: "<img src=x onerror=alert(1)>",
          description: "<b>pl</b>",
          description_en: "<b>en</b>",
          default_value: 1,
          type: "percent",
        }],
      }, {});
      const container = document.getElementById("earnings-content");
      expect(container.querySelector("script")).toBeNull();
      expect(container.querySelector("img")).toBeNull();
      expect(container.querySelector("b")).toBeNull();
      expect(container.textContent).toContain("<img src=x onerror=alert(1)>");
    });
  });
});

// ─── the tier name in the reader's language ────────────────────
// commission_tiers.name_en is the producer's own English wording of a tier
// he named himself ("Diament"). Same rule as the bonus names above: read
// through Ui.producerLabel, empty means "not translated yet", and the
// figures next to it are backend-owned and must never move when only the
// language switches.
describe("renderEarnings — the tier name in the reader's language", () => {
  const withLang = (lang, run) => {
    const previous = globalThis.App;
    globalThis.App = appStub(lang);
    try { run(); } finally { globalThis.App = previous; }
  };

  const partnerSummary = {
    tier_name: "Diament",
    tier_name_en: "Diamond",
    base_rate: 15,
    total_percent: 15,
  };

  it("shows the Polish tier name to a Polish reader", () => {
    withLang("pl", () => {
      renderEarnings(partnerSummary, {});
      expect(document.getElementById("earnings-content").textContent).toContain("Diament");
    });
  });

  it("shows the producer's English tier name to an English reader", () => {
    withLang("en", () => {
      renderEarnings(partnerSummary, {});
      const text = document.getElementById("earnings-content").textContent;
      expect(text).toContain("Diamond");
      expect(text).not.toContain("Diament");
    });
  });

  it("falls back to the Polish tier name when it has not been translated yet", () => {
    withLang("en", () => {
      renderEarnings({ ...partnerSummary, tier_name_en: "" }, {});
      expect(document.getElementById("earnings-content").textContent).toContain("Diament");
    });
  });

  it("keeps the base rate and total percentage identical across a language switch", () => {
    let plHtml;
    withLang("pl", () => {
      renderEarnings(partnerSummary, {});
      plHtml = document.getElementById("earnings-content").innerHTML;
    });
    withLang("en", () => {
      renderEarnings(partnerSummary, {});
      const enHtml = document.getElementById("earnings-content").innerHTML;
      expect(enHtml).toContain("15%");
      expect(plHtml).toContain("15%");
    });
  });

  it("renders a script-tag-shaped English tier name as escaped text, creating no live script element", () => {
    withLang("en", () => {
      renderEarnings({ ...partnerSummary, tier_name_en: "<script>alert(1)</script>" }, {});
      const container = document.getElementById("earnings-content");
      expect(container.querySelector("script")).toBeNull();
      expect(container.innerHTML).toContain("&lt;script&gt;alert(1)&lt;/script&gt;");
    });
  });
});
