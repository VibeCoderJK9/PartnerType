import { beforeEach, describe, expect, it, vi } from "vitest";
import { loadScript } from "./helpers/load-script.js";

loadScript("js/charts.js");

function makeContext() {
  const strokeStyles = [];
  const fillStyles = [];
  const context = {
    beginPath: vi.fn(), moveTo: vi.fn(), lineTo: vi.fn(), stroke: vi.fn(),
    fill: vi.fn(), arc: vi.fn(), closePath: vi.fn(), fillText: vi.fn(),
    scale: vi.fn(), setLineDash: vi.fn(), quadraticCurveTo: vi.fn(),
    createLinearGradient: vi.fn(() => ({ addColorStop: vi.fn() })),
  };
  Object.defineProperty(context, "strokeStyle", {
    set(value) { strokeStyles.push(value); }, get() { return strokeStyles.at(-1); },
  });
  Object.defineProperty(context, "fillStyle", {
    set(value) { fillStyles.push(value); }, get() { return fillStyles.at(-1); },
  });
  return { context, strokeStyles, fillStyles };
}

let canvasContext;

beforeEach(() => {
  document.documentElement.setAttribute("data-theme", "light");
  document.body.innerHTML = '<canvas id="chart"></canvas><div id="legend"></div>';
  canvasContext = makeContext();
  const canvas = document.getElementById("chart");
  canvas.getBoundingClientRect = () => ({ width: 320, height: 180 });
  canvas.getContext = () => canvasContext.context;
});

describe("Charts public surface", () => {
  it("exports callable renderers and palette accessors without legacy constants", () => {
    expect(Object.keys(Charts).sort()).toEqual([
      "drawBars", "drawDonut", "drawLine", "getColors", "getStatusColors", "renderTrend",
    ]);
    for (const value of Object.values(Charts)) expect(value).toBeTypeOf("function");
    expect(Charts.getStatusColors().accepted).toBe(Charts.getColors().success);
  });
});

describe("Charts palette branches", () => {
  it("uses the local success color for the optional commission line", () => {
    expect(() => Charts.drawLine("chart", [
      { label: "I", leads: 2, commission: 10 },
      { label: "II", leads: 4, commission: 20 },
    ], { showCommission: true })).not.toThrow();

    expect(canvasContext.strokeStyles).toContain(Charts.getColors().success);
  });

  it("uses local status colors in the donut and renders unknown labels inertly", () => {
    const payload = '<img src=x onerror="window.__chartPwned=true">';

    expect(() => Charts.drawDonut("chart", { accepted: 2, [payload]: 1 }, "legend")).not.toThrow();

    const legend = document.getElementById("legend");
    expect(legend.textContent).toContain(payload);
    expect(legend.querySelector("img, script")).toBeNull();
    expect(canvasContext.fillStyles).toContain(Charts.getStatusColors().accepted);
  });
});

// The dashboard KPI badges are bound to /api/stats, which puts renderTrend on
// the path every producer sees on their first screen.
describe("renderTrend — the badge a producer reads before they have any data", () => {
  beforeEach(() => {
    document.body.innerHTML += '<span id="trend"></span>';
  });

  it("shows nothing at all when there is no trend yet", () => {
    // A grey "—" on three of five KPI cards would read as broken values on a
    // fresh install.
    Charts.renderTrend("trend", { dir: "neutral", pct: 0 });

    expect(document.querySelector("#trend .trend-badge")).toBeNull();
    expect(document.getElementById("trend").textContent).toBe("");
  });

  it("draws an up chevron with the figure when the metric actually rose", () => {
    Charts.renderTrend("trend", { dir: "up", pct: 12 });

    const badge = document.querySelector("#trend .trend-badge");
    expect(badge.textContent).toContain("12%");
    expect(badge.className).toContain("trend-up");
    const points = badge.querySelector("svg polyline").getAttribute("points");
    expect(points).toBe("18 15 12 9 6 15");
  });

  it("draws a down chevron for a fall", () => {
    Charts.renderTrend("trend", { dir: "down", pct: 7 });

    const badge = document.querySelector("#trend .trend-badge");
    expect(badge.className).toContain("trend-down");
    expect(badge.querySelector("svg polyline").getAttribute("points")).toBe("6 9 12 15 18 9");
  });

  // Note what this does and does not prove: a markup string is not > 0, so it
  // never reaches the figure branch at all -- the guard rejects it before the
  // badge is built. That is the property worth pinning (a non-numeric pct
  // renders nothing rather than reaching the DOM), not the node-building itself.
  it("renders nothing for a non-numeric percentage instead of injecting it", () => {
    Charts.renderTrend("trend", { dir: "up", pct: '<img src=x onerror="window.__trendPwned=true">' });

    expect(document.querySelector("#trend .trend-badge")).toBeNull();
    expect(document.querySelector("#trend img")).toBeNull();
    expect(document.getElementById("trend").textContent).toBe("");
    expect(window.__trendPwned).toBeUndefined();
  });

  // The node-building itself is what this one pins: the figure reaches the DOM
  // as a text node, so it is inert no matter what the API returns.
  it("puts the figure in a text node, never in markup", () => {
    Charts.renderTrend("trend", { dir: "up", pct: 42 });

    const badge = document.querySelector("#trend .trend-badge");
    const textNodes = [...badge.childNodes].filter(n => n.nodeType === Node.TEXT_NODE);
    expect(textNodes.map(n => n.textContent).join("")).toContain("42%");
    expect(badge.innerHTML).not.toContain("42%<");
  });

  it("leaves the element untouched when the payload carries no trend at all", () => {
    document.getElementById("trend").textContent = "placeholder";

    Charts.renderTrend("trend", null);

    expect(document.getElementById("trend").textContent).toBe("placeholder");
  });
});

/* These pin the chart colours to the design system's tokens instead of to
   literals, so the charts never paint a colour found nowhere else. */
describe("Charts read their colours from the theme tokens", () => {
  const INDIGO = ["rgba(99,102,241,1)", "rgba(129,140,248,1)"];

  beforeEach(() => {
    document.documentElement.style.setProperty("--brand-500", "#da9348");
    document.documentElement.style.setProperty("--brand-600", "#c98232");
    document.documentElement.style.setProperty("--success-600", "#146c43");
  });

  // Honey itself (#da9348) has 2.4 : 1 on white, so in light mode the chart
  // takes the darker step 600; on a dark background honey is enough.
  it("takes the deeper brand step (--brand-600) in light mode, for 3:1 on white", () => {
    document.documentElement.setAttribute("data-theme", "light");

    expect(Charts.getColors().brand).toBe("#c98232");
    expect(INDIGO).not.toContain(Charts.getColors().brand);
  });

  it("takes the brand itself (--brand-500) in dark mode", () => {
    document.documentElement.setAttribute("data-theme", "dark");

    expect(Charts.getColors().brand).toBe("#da9348");
    expect(INDIGO).not.toContain(Charts.getColors().brand);
  });

  it("colours every bar of a single series alike, never by rank", () => {
    // Rank colouring (1st purple, 2nd green, 3rd amber) made the palette mean
    // nothing and repainted the survivors whenever the ranking changed.
    const gradientStops = [];
    canvasContext.context.createLinearGradient = () => ({
      addColorStop: (offset, color) => gradientStops.push([offset, color]),
    });

    Charts.drawBars("chart", [
      { label: "Alfa", value: 12 }, { label: "Beta", value: 9 }, { label: "Gamma", value: 4 },
    ]);

    const solidStops = gradientStops.filter(([offset]) => offset === 0).map(([, color]) => color);
    expect(solidStops).toHaveLength(3);
    expect(new Set(solidStops).size).toBe(1);
    expect(solidStops[0]).toBe(Charts.getColors().brand);
  });
});
