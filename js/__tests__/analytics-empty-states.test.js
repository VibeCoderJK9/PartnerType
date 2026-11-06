import { readFileSync } from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { Window } from "happy-dom";
import { describe, expect, it, vi } from "vitest";
import { appStub } from "./helpers/copy.js";

const PROJECT_ROOT = path.resolve(import.meta.dirname, "..", "..");

function runClassicScript(window, relativePath) {
  const source = readFileSync(path.join(PROJECT_ROOT, relativePath), "utf8");
  const context = window.__vmContext || (window.__vmContext = vm.createContext(window));
  vm.runInContext(source, context, { filename: relativePath });
  return context;
}

function invoke(window, source) {
  return vm.runInContext(source, window.__vmContext);
}

// The three empty-state lines live in analytics.html and were never revealed:
// a programme with no leads yet showed an empty ring, a flat line and a blank
// white panel, which reads as a broken screen rather than an empty one.
function analyticsRealm() {
  const window = new Window({ url: "http://localhost/pages/admin/analytics.html" });
  window.document.body.innerHTML = `
    <div class="chart-container">
      <canvas id="an-line"></canvas>
      <p class="empty-state" id="an-line-empty" hidden>Brak danych o leadach w wybranym okresie.</p>
    </div>
    <div class="chart-container">
      <canvas id="an-donut"></canvas>
      <p class="empty-state" id="an-donut-empty" hidden>Brak danych o statusach leadów.</p>
    </div>
    <div class="donut-legend" id="an-donut-legend"></div>
    <div class="chart-container">
      <canvas id="an-bars"></canvas>
      <p class="empty-state" id="an-bars-empty" hidden>Brak danych do rankingu partnerów.</p>
    </div>
    <table><tbody id="an-partners-tbody"></tbody></table>
  `;
  window.ApiClient = { get: vi.fn() };
  window.Charts = {
    drawLine: vi.fn(), drawDonut: vi.fn(), drawBars: vi.fn(),
    // donutLookup resolves a pre-funnel bucket's colour ROLE through this, so
    // the slice follows the theme instead of carrying a baked hex.
    getColors: vi.fn(() => ({ warning: "rgba(245,158,11,1)", info: "rgba(59,130,246,1)", error: "rgba(239,68,68,1)" })),
  };
  window.handleApiError = vi.fn();
  window.App = appStub('pl');
  window.getInitials = vi.fn(() => "XX");
  // The intake buckets take their names from here, the same source the Lead
  // stages screen renders its fixed badges from — resolved at render time now,
  // so a language switch reaches them.
  window.Ui = {
    registrationLabels: () => ({
      pending: "Oczekujący",
      needs_info: "Prośba o dodatkowe informacje",
      rejected: "Odrzucony",
    }),
    // Faithful to js/utils.js: the English column only when the reader is on
    // English, and an empty one falls back to the producer's Polish original.
    producerLabel: (record, field) => {
      if (!record) return "";
      const base = String(record[field] ?? "");
      if (window.App.lang !== "en") return base;
      return String(record[`${field}_en`] ?? "").trim() || base;
    },
  };
  runClassicScript(window, "js/icons.js");
  runClassicScript(window, "js/analytics.js");
  return window;
}

const EMPTY_STATS = {
  monthly: [
    { label: "kwi", leads: 0 }, { label: "maj", leads: 0 }, { label: "cze", leads: 0 },
    { label: "lip", leads: 0 }, { label: "sie", leads: 0 }, { label: "wrz", leads: 0 },
  ],
  pipeline_dist: [],
  partner_ranking: [],
};

describe("analytics charts", () => {
  it("keeps stage colours and terminal metadata through the real page's inline chart wrapper", async () => {
    const window = analyticsRealm();
    const originalDraw = window.Charts.drawDonut;
    // The wrapper is a file, loaded the same way the page loads it -- after analytics.js, whose functions it
    // takes over.
    runClassicScript(window, "js/analytics-empty-states.js");
    invoke(window, `renderAnalyticsCharts(${JSON.stringify({
      monthly: [], partner_ranking: [],
      pipeline_dist: [{ key: "won", name: "Wygrany", color: "#10b981", count: 3, kind: "win", is_terminal: 1 }],
    })})`);
    await vi.waitFor(() => expect(originalDraw).toHaveBeenCalled());
    expect(originalDraw.mock.calls[0][3]).toEqual([
      { key: "won", name: "Wygrany", color: "#10b981", kind: "win", is_terminal: 1 },
    ]);
  });
  // One donut for the whole set: the pre-funnel registration
  // buckets, then the producer's own stages, ending on win and lose. A stage
  // colour travels as a hex on the bucket; a pre-funnel bucket carries a
  // colour ROLE instead, resolved here so a theme switch recolours it.
  it("draws every bucket the backend described, stages and pre-funnel alike", async () => {
    const window = analyticsRealm();

    invoke(window, `renderAnalyticsCharts(${JSON.stringify({
      monthly: [{ label: "wrz", leads: 3 }],
      pipeline_dist: [
        { key: "pending", name: "ignorowana nazwa z backendu", color_role: "warning", count: 1 },
        { key: "won", name: "Wygrany", color: "#10b981", count: 2 },
      ],
      partner_ranking: [{ company: "Alfa sp. z o.o.", total: 4, revenue: 1000, commission: 50 }],
    })})`);

    await vi.waitFor(() => expect(window.Charts.drawBars).toHaveBeenCalled());
    expect(window.Charts.drawDonut).toHaveBeenCalledWith(
      "an-donut", { pending: 1, won: 2 }, "an-donut-legend",
      [
        { key: "pending", name: "Oczekujący", color: "rgba(245,158,11,1)", kind: undefined, is_terminal: false },
        { key: "won", name: "Wygrany", color: "#10b981", kind: "win", is_terminal: true },
      ],
    );
  });

  // The bars answer the activity question, so they rank by leads submitted --
  // not by accepted leads, and not by the money the table below is ordered on.
  it("ranks the Top 5 bars by how many leads each partner brought in", async () => {
    const window = analyticsRealm();

    invoke(window, `renderAnalyticsCharts(${JSON.stringify({
      monthly: [{ label: "wrz", leads: 3 }],
      pipeline_dist: [{ key: "won", name: "Wygrany", color: "#10b981", count: 2 }],
      partner_ranking: [
        { company: "Beta sp. z o.o.", total: 1, revenue: 9000, commission: 900 },
        { company: "Alfa sp. z o.o.", total: 7, revenue: 100, commission: 10 },
        { company: "Cichy sp. z o.o.", total: 0, revenue: 0, commission: 0 },
      ],
    })})`);

    await vi.waitFor(() => expect(window.Charts.drawBars).toHaveBeenCalled());
    const [, bars] = window.Charts.drawBars.mock.calls[0];
    expect(bars.map((bar) => bar.value)).toEqual([7, 1, 0]);
    expect(bars[0].label).toContain("Alfa");
    // A partner on zero still gets a column: "top 5" means five partners, and
    // dropping the quiet ones hides the fact that they are quiet.
    expect(bars[2].label).toContain("Cichy");
  });

  it("says why each panel is blank instead of drawing an empty chart", async () => {
    const window = analyticsRealm();

    invoke(window, `renderAnalyticsCharts(${JSON.stringify(EMPTY_STATS)})`);

    await vi.waitFor(() =>
      expect(window.document.getElementById("an-line-empty").hidden).toBe(false),
    );
    for (const id of ["an-line", "an-donut", "an-bars"]) {
      expect(window.document.getElementById(`${id}-empty`).hidden).toBe(false);
      expect(window.document.getElementById(id).hidden).toBe(true);
    }
    expect(window.Charts.drawLine).not.toHaveBeenCalled();
    expect(window.Charts.drawDonut).not.toHaveBeenCalled();
    expect(window.Charts.drawBars).not.toHaveBeenCalled();
  });

  it("draws the charts and hides the messages as soon as there is data", async () => {
    const window = analyticsRealm();

    invoke(window, `renderAnalyticsCharts(${JSON.stringify({
      monthly: [{ label: "wrz", leads: 3 }],
      pipeline_dist: [
        { key: "won", name: "Wygrany", color: "#10b981", count: 2 },
        { key: "contact", name: "Kontakt", color: "#94a3b8", count: 1 },
      ],
      partner_ranking: [{ company: "Alfa sp. z o.o.", total: 2, revenue: 100, commission: 10 }],
    })})`);

    await vi.waitFor(() => expect(window.Charts.drawLine).toHaveBeenCalled());
    for (const id of ["an-line", "an-donut", "an-bars"]) {
      expect(window.document.getElementById(`${id}-empty`).hidden).toBe(true);
      expect(window.document.getElementById(id).hidden).toBe(false);
    }
    expect(window.Charts.drawLine).toHaveBeenCalled();
    expect(window.Charts.drawDonut).toHaveBeenCalled();
    expect(window.Charts.drawBars).toHaveBeenCalled();
  });

  it("clears a stale donut legend when the data goes empty", async () => {
    const window = analyticsRealm();
    window.document.getElementById("an-donut-legend").textContent = "Wygrane 4";

    invoke(window, `renderAnalyticsCharts(${JSON.stringify(EMPTY_STATS)})`);

    await vi.waitFor(() =>
      expect(window.document.getElementById("an-donut-legend").textContent).toBe(""),
    );
  });
});

// The ranking's tier badge reads a name the producer wrote himself, so it
// follows the reader's language exactly like the partner's own Pulpit badge
// beside it.
describe("analytics ranking tier badge", () => {
  const ranked = (extra) => [{
    id: 7, company: "Alfa sp. z o.o.", total: 1, accepted: 1, revenue: 100, commission: 10,
    tier_name: "Zloty", tier_color: "#d4a017", tier_rate: 12.5, ...extra,
  }];

  function render(lang, partners) {
    const window = analyticsRealm();
    window.App = appStub(lang);
    invoke(window, `renderPartnersTable(${JSON.stringify(partners)})`);
    return window.document.getElementById("an-partners-tbody").textContent;
  }

  it("shows the producer's English tier name to an English reader", () => {
    const text = render("en", ranked({ tier_name_en: "Gold" }));
    expect(text).toContain("Gold");
    expect(text).not.toContain("Zloty");
  });

  it("falls back to the Polish original when the tier is not translated", () => {
    const text = render("en", ranked({ tier_name_en: "" }));
    expect(text).toContain("Zloty");
  });

  it("leaves the Polish reader on the Polish name", () => {
    const text = render("pl", ranked({ tier_name_en: "Gold" }));
    expect(text).toContain("Zloty");
    expect(text).not.toContain("Gold");
  });
});
