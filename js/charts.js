/* ============================================================
   PARTNERTYPE — CHARTS
   Pure Canvas rendering — no external libraries.
   ============================================================ */

const Charts = (() => {
  'use strict';

  function isLightMode() {
    return document.documentElement.getAttribute('data-theme') === 'light';
  }

  /* ─── CHART COLOURS ───────────────────────────────────────
     Every mark colour is a design-system token, read from the live theme.
     These used to be literals from the pre-1.1.2 "Nebula" palette -- indigo
     #6366F1 for the brand -- which stopped being this app's brand colour when
     the theme moved to the rust ramp (oklch hue 55). Nothing warned: the charts
     simply kept painting a colour that appears nowhere else in the interface.

     The STEP per mode is chosen, not copied from the UI accent. Each of the
     four categorical steps below was run through the measurable checks
     (lightness band, chroma floor, protan/deutan separation, normal-vision
     separation, contrast against the panel surface) for light on #ffffff and
     dark on #1a1f27, and all five pass in both modes. Two consequences worth
     keeping:
       - `warning` (amber) is NOT a categorical slot here. It sits one hue away
         from the rust brand, and the pair is muddy for a red-green reader. It
         stays available as a reserved STATUS colour only.
       - `neutral` is deliberately below the chroma floor: it is the "no stage
         yet" slot, which must not read as one of the coloured categories. */
  // In light mode bars use honey step 600 (#c98232): honey #da9348 itself has
  // only 2.4 : 1 on white, and a chart mark needs 3 : 1. In dark mode green
  // uses step 500, because 600 is the dark green for text on white.
  const CHART_TOKENS = {
    light: {
      brand:   '--brand-600',
      success: '--success-600',
      error:   '--error-600',
      warning: '--warning-500',
      info:    '--info-500',
      neutral: '--gray-500',
    },
    dark: {
      brand:   '--brand-500',
      success: '--success-500',
      error:   '--error-500',
      warning: '--warning-500',
      info:    '--info-400',
      neutral: '--gray-400',
    },
  };

  // The validated values, for the case where the stylesheet has not applied
  // (a canvas drawn before CSS, or a test realm with no theme).
  const CHART_FALLBACK = {
    light: { brand: '#c98232', success: '#146c43', error: '#b42318', warning: '#d4a72c', info: '#1e4fae', neutral: '#6e7378' },
    dark:  { brand: '#da9348', success: '#2f9e6a', error: '#d93a2b', warning: '#d4a72c', info: '#9dbcff', neutral: '#888d92' },
  };

  /* Resolve a CSS colour to rgba() through a 1x1 canvas.

     Tokens are authored in oklch(), and the gradients below need the same
     colour at a lower alpha. Reading the computed value gives a string whose
     colour space depends on the browser, and string-editing an alpha into
     `oklch(0.55 0.16 55)` is how the old `color.replace(',1)', ',0.4)')` line
     silently produced an invalid gradient stop. Painting one pixel and reading
     it back returns plain sRGB, in every browser, for any input notation. */
  let probeCtx = null;
  function toRgba(cssColor, alpha = 1) {
    if (!probeCtx) {
      const probe = document.createElement('canvas');
      probe.width = probe.height = 1;
      probeCtx = probe.getContext('2d');
    }
    if (!probeCtx) return cssColor;
    probeCtx.clearRect(0, 0, 1, 1);
    // An unparseable value leaves fillStyle untouched, so seed it with a value
    // we recognise rather than inheriting the previous colour.
    probeCtx.fillStyle = '#000000';
    probeCtx.fillStyle = cssColor;
    probeCtx.fillRect(0, 0, 1, 1);
    try {
      const [r, g, b] = probeCtx.getImageData(0, 0, 1, 1).data;
      return `rgba(${r},${g},${b},${alpha})`;
    } catch (err) {
      return cssColor;
    }
  }

  function cssToken(name, fallback) {
    const value = getComputedStyle(document.documentElement)
      .getPropertyValue(name).trim();
    return value || fallback;
  }

  function getColors() {
    const light = isLightMode();
    const mode = light ? 'light' : 'dark';
    const tokens = CHART_TOKENS[mode];
    const fallback = CHART_FALLBACK[mode];
    const resolved = {};
    for (const [role, token] of Object.entries(tokens)) {
      resolved[role] = toRgba(cssToken(token, fallback[role]));
    }
    return {
      ...resolved,
      // The area fill under the line: the same brand hue, barely tinted.
      brandBg: withAlpha(resolved.brand, 0.15),
      grid:    light ? 'rgba(0,0,0,0.06)' : 'rgba(255,255,255,0.08)',
      // Axis labels are recessive, but they still have to be readable at 10px
      // on the panel surface.
      text:    light ? 'rgba(0,0,0,0.62)' : 'rgba(255,255,255,0.82)',
      // The donut hole and the line dots sit ON a panel, so they have to be
      // the panel's own background. Hardcoding it meant a theme whose panel
      // colour changed left a white disc floating on a dark chart.
      dotBg:   toRgba(cssToken('--bg-secondary', light ? '#ffffff' : '#1a1f27')),
    };
  }

  // Same colour, different alpha -- works because getColors() hands out rgba().
  function withAlpha(rgba, alpha) {
    const parts = String(rgba).match(/-?[\d.]+/g);
    if (!parts || parts.length < 3) return toRgba(rgba, alpha);
    return `rgba(${parts[0]},${parts[1]},${parts[2]},${alpha})`;
  }

  function getStatusColors() {
    const colors = getColors();
    return {
      new:         colors.brand,
      pending:     colors.warning,
      in_progress: colors.info,
      accepted:    colors.success,
      rejected:    colors.error,
      paid:        withAlpha(colors.success, 0.5),
    };
  }

  // Resolved per draw, not frozen at load: a language switch repaints the
  // charts, and a baked Polish label would survive it.
  const STATUS_KEYS = ['new', 'pending', 'in_progress', 'accepted', 'rejected', 'paid'];
  function statusLabel(key) {
    if (!STATUS_KEYS.includes(key)) return null;
    return (typeof App !== 'undefined' && App.t) ? App.t('chart_status_' + key) : key;
  }

  // ─── HELPERS ─────────────────────────────────────────────

  function dpr(canvas) {
    const ratio = window.devicePixelRatio || 1;
    const rect  = canvas.getBoundingClientRect();
    canvas.width  = rect.width  * ratio;
    canvas.height = rect.height * ratio;
    canvas.getContext('2d').scale(ratio, ratio);
    return { w: rect.width, h: rect.height };
  }

  function roundRect(ctx, x, y, w, h, r) {
    r = Math.min(r, h / 2, w / 2);
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.lineTo(x + w - r, y);
    ctx.quadraticCurveTo(x + w, y, x + w, y + r);
    ctx.lineTo(x + w, y + h - r);
    ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
    ctx.lineTo(x + r, y + h);
    ctx.quadraticCurveTo(x, y + h, x, y + h - r);
    ctx.lineTo(x, y + r);
    ctx.quadraticCurveTo(x, y, x + r, y);
    ctx.closePath();
  }

  // ─── LINE CHART (monthly leads) ──────────────────────────

  function drawLine(canvasId, series, opts = {}) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    const { w, h } = dpr(canvas);
    const ctx = canvas.getContext('2d');
    const pad = { top: 20, right: 20, bottom: 36, left: 48 };
    const cw  = w - pad.left - pad.right;
    const ch  = h - pad.top  - pad.bottom;

    const values  = series.map(d => d.leads || 0);
    const maxVal  = Math.max(...values, 1);
    // Built here, not taken from the API: the backend's `label` is a
    // pre-formatted Polish string, so it could never follow a language switch.
    const monthFormatter = new Intl.DateTimeFormat(
      (typeof App !== 'undefined' && App.lang === 'en') ? 'en-GB' : 'pl-PL',
      { month: 'short' },
    );
    const labels = series.map((d) => {
      if (!Number.isInteger(d.year) || !Number.isInteger(d.month)) return d.label;
      const name = monthFormatter.format(new Date(d.year, d.month - 1, 1))
        .replace(/\.$/, '');
      return `${name.charAt(0).toUpperCase()}${name.slice(1)} ${String(d.year).slice(-2)}`;
    });
    const stepX   = cw / Math.max(values.length - 1, 1);

    const colors = getColors();

    // Grid lines
    ctx.strokeStyle = colors.grid;
    ctx.lineWidth = 1;
    for (let i = 0; i <= 4; i++) {
      const y = pad.top + (ch * i) / 4;
      ctx.beginPath(); ctx.moveTo(pad.left, y); ctx.lineTo(pad.left + cw, y); ctx.stroke();
      ctx.fillStyle = colors.text;
      ctx.font = '10px Inter, sans-serif';
      ctx.textAlign = 'right';
      ctx.fillText(Math.round(maxVal * (1 - i / 4)), pad.left - 6, y + 4);
    }

    // Labels
    ctx.fillStyle = colors.text;
    ctx.font = '10px Inter, sans-serif';
    ctx.textAlign = 'center';
    labels.forEach((lbl, i) => {
      const x = pad.left + i * stepX;
      ctx.fillText(lbl, x, h - 8);
    });

    // Area fill
    const grad = ctx.createLinearGradient(0, pad.top, 0, pad.top + ch);
    grad.addColorStop(0, colors.brandBg);
    grad.addColorStop(1, withAlpha(colors.brand, 0));

    ctx.beginPath();
    values.forEach((v, i) => {
      const x = pad.left + i * stepX;
      const y = pad.top + ch - (v / maxVal) * ch;
      i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    });
    ctx.lineTo(pad.left + (values.length - 1) * stepX, pad.top + ch);
    ctx.lineTo(pad.left, pad.top + ch);
    ctx.fillStyle = grad;
    ctx.fill();

    // Line
    ctx.beginPath();
    values.forEach((v, i) => {
      const x = pad.left + i * stepX;
      const y = pad.top + ch - (v / maxVal) * ch;
      i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    });
    ctx.strokeStyle = colors.brand;
    ctx.lineWidth   = 2.5;
    ctx.lineJoin    = 'round';
    ctx.stroke();

    // Dots + tooltip areas
    values.forEach((v, i) => {
      const x = pad.left + i * stepX;
      const y = pad.top + ch - (v / maxVal) * ch;
      ctx.beginPath();
      ctx.arc(x, y, 4, 0, Math.PI * 2);
      ctx.fillStyle   = colors.brand;
      ctx.fill();
      ctx.strokeStyle = colors.dotBg;
      ctx.lineWidth   = 2;
      ctx.stroke();
    });

    // Optional second series (commission)
    if (opts.showCommission && series[0]?.commission !== undefined) {
      const commValues = series.map(d => d.commission || 0);
      const maxComm    = Math.max(...commValues, 1);
      ctx.beginPath();
      commValues.forEach((v, i) => {
        const x = pad.left + i * stepX;
        const y = pad.top + ch - (v / maxComm) * ch;
        i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
      });
      ctx.strokeStyle = colors.success;
      ctx.lineWidth   = 2;
      ctx.setLineDash([4, 3]);
      ctx.stroke();
      ctx.setLineDash([]);
    }
  }

  // ─── BAR CHART (partners comparison) ─────────────────────

  const MAX_BAR_WIDTH = 48;

  // The label under a bar is cut at eight characters, so a partner whose
  // company name is longer stops being identifiable -- so the full one shows
  // on hover. Every draw records the column each item occupies;
  // one listener per canvas turns the column under the pointer into the
  // browser's own tooltip. A WeakMap, so a canvas that goes away takes its
  // slots with it.
  const barSlots = new WeakMap();

  function attachBarTooltip(canvas) {
    if (canvas.dataset.barTooltip === 'on') return;
    canvas.dataset.barTooltip = 'on';
    canvas.addEventListener('mousemove', (event) => {
      const slots = barSlots.get(canvas) || [];
      const hit = slots.find(slot => event.offsetX >= slot.x0 && event.offsetX <= slot.x1);
      const text = hit ? hit.text : '';
      if (canvas.title !== text) canvas.title = text;
    });
    canvas.addEventListener('mouseleave', () => { canvas.title = ''; });
  }

  function drawBars(canvasId, items, opts = {}) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    const { w, h } = dpr(canvas);
    const ctx = canvas.getContext('2d');
    const pad = { top: 20, right: 20, bottom: 44, left: 48 };
    const cw  = w - pad.left - pad.right;
    const ch  = h - pad.top  - pad.bottom;

    const values = items.map(d => d.value || 0);
    const maxVal = Math.max(...values, 1);
    const gapW   = cw / items.length;
    // A bar keeps the same width whether the chart holds one partner or five,
    // so a single partner does not read as a rendering fault.
    const barW   = Math.min(MAX_BAR_WIDTH, Math.max(8, gapW * 0.6));

    const colors = getColors();

    // Grid
    ctx.strokeStyle = colors.grid; ctx.lineWidth = 1;
    for (let i = 0; i <= 4; i++) {
      const y = pad.top + (ch * i) / 4;
      ctx.beginPath(); ctx.moveTo(pad.left, y); ctx.lineTo(pad.left + cw, y); ctx.stroke();
      ctx.fillStyle = colors.text;
      ctx.font = '10px Inter, sans-serif';
      ctx.textAlign = 'right';
      ctx.fillText(Math.round(maxVal * (1 - i / 4)), pad.left - 6, y + 4);
    }

    const slots = [];

    items.forEach((item, i) => {
      const v   = item.value || 0;
      const x   = pad.left + i * gapW + (gapW - barW) / 2;
      // The whole column, not just the bar -- the truncated label sits below
      // the bar, and that is exactly where the pointer goes to read it.
      slots.push({
        x0: pad.left + i * gapW,
        x1: pad.left + (i + 1) * gapW,
        // `title` is the untruncated name when the caller had to shorten the
        // label to fit. Falling back to the label would make the tooltip
        // repeat the very text the reader could already not read in full.
        text: `${item.title || item.label} \u2014 ${v}`,
      });
      const barH = (v / maxVal) * ch;
      const y   = pad.top + ch - barH;
      /* One measure, one colour, never coloured by rank. Identity lives in the
         label under each bar. A caller may still pass a per-item `color` when
         the colour belongs to the thing itself (a producer's stage). */
      const color = toRgba(item.color || opts.color || colors.brand);

      // Bar gradient
      const g = ctx.createLinearGradient(0, y, 0, y + barH);
      g.addColorStop(0, color);
      g.addColorStop(1, withAlpha(color, 0.4));
      ctx.fillStyle = g;
      roundRect(ctx, x, y, barW, barH, 4);
      ctx.fill();

      // Label below
      ctx.fillStyle = colors.text;
      ctx.textAlign = 'center';
      const lbl = item.label.length > 10 ? item.label.substring(0,8)+'...' : item.label;
      ctx.fillText(lbl, x + barW/2, h - 8);

      // Value on top
      if (v > 0) {
        ctx.fillStyle = colors.text;
        ctx.font = 'bold 9px Inter, sans-serif';
        ctx.fillText(v, x + barW / 2, y - 4);
      }
    });

    barSlots.set(canvas, slots);
    attachBarTooltip(canvas);
  }

  // ─── DONUT CHART (status distribution) ───────────────────

  /*
     The donut draws the SALES-STAGE distribution, so its colours and its labels
     have to come from the producer's own stages -- the same source the kanban,
     the funnel bar and the stage badges read.

     `status_dist` returns stage keys, so a hardcoded status map would miss
     every lookup and draw every slice in one neutral grey.

     `stages` is the array from /api/lead-stages. Without it the legacy map is
     still used, so any caller not yet passing stages behaves as before.
  */
  function stageLookup(stages) {
    const byKey = new Map();
    (Array.isArray(stages) ? stages : []).forEach((stage) => byKey.set(stage.key, stage));
    return byKey;
  }

  function drawDonut(canvasId, data, legendId, stages) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    const { w, h } = dpr(canvas);
    const ctx  = canvas.getContext('2d');
    const cx   = w / 2;
    const cy   = h / 2;
    const colors = getColors();
    const statusColors = getStatusColors();
    const byKey = stageLookup(stages);

    // A lead with no stage is not in the funnel at all -- it is still awaiting
    // the producer's decision. Named rather than shown as a blank slice.
    // A hex is what a producer configures on a stage; an rgba() string is what
    // getColors() hands back for a themed role (the pre-funnel buckets on
    // Analytics use those, so they follow light/dark). Both are accepted;
    // anything else falls through to the status palette.
    const usableColor = (value) => /^#[0-9a-fA-F]{6}$/.test(String(value || ''))
      || /^rgba?\(/.test(String(value || ''));
    const colorFor = (key) => {
      const stage = byKey.get(key);
      if (stage && usableColor(stage.color)) return stage.color;
      if (key === 'null' || key === null) return colors.neutral;
      return statusColors[key] || colors.neutral;
    };
    const labelFor = (key) => {
      const stage = byKey.get(key);
      if (stage) return (typeof Ui !== 'undefined' && Ui.stageLabel) ? Ui.stageLabel(stage) : (stage.name || key);
      if (key === 'null' || key === null) {
        return (typeof App !== 'undefined' && App.t) ? App.t('chart_before_funnel') : 'Before the funnel';
      }
      return statusLabel(key) || key;
    };

    const entries = Object.entries(data).filter(([,v]) => v > 0);
    const total   = entries.reduce((s, [,v]) => s + v, 0);

    const r = Math.min(cx, cy) - 10;
    const hole = r * 0.62;

    if (total === 0) {
       ctx.beginPath();
       ctx.arc(cx, cy, r, 0, Math.PI * 2);
       ctx.strokeStyle = colors.grid;
       ctx.lineWidth = r - hole;
       ctx.stroke();
       return;
    }

    let startAngle = -Math.PI / 2;

    entries.forEach(([status, count]) => {
      const slice = (count / total) * Math.PI * 2;
      ctx.beginPath();
      ctx.moveTo(cx, cy);
      ctx.arc(cx, cy, r, startAngle, startAngle + slice);
      ctx.closePath();
      ctx.fillStyle = colorFor(status);
      ctx.fill();
      startAngle += slice;
    });

    // Hole
    ctx.beginPath();
    ctx.arc(cx, cy, hole, 0, Math.PI * 2);
    ctx.fillStyle = colors.dotBg;
    ctx.fill();

    // Center text
    ctx.fillStyle = colors.text;
    ctx.font = `bold ${Math.round(hole * 0.35)}px Inter, sans-serif`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(total, cx, cy - 4);
    ctx.font = `${Math.round(hole * 0.18)}px Inter, sans-serif`;
    ctx.fillText(
      (typeof App !== 'undefined' && App.t) ? App.t('chart_donut_unit') : 'leads',
      cx, cy + hole * 0.22);

    // Legend
    const legend = document.getElementById(legendId);
    if (legend) {
      legend.replaceChildren();
      // Every bucket the caller described, including the empty ones -- an
      // empty stage is a fact about the funnel and a zero-size slice cannot
      // show it -- followed by anything with a count that was not described
      // (the dashboard's donut passes stages only, so its pre-funnel `null`
      // bucket arrives this way and must not be dropped).
      const described = [...byKey.keys()].map((key) => [key, Number(data[key]) || 0]);
      const extra = entries.filter(([key]) => !byKey.has(key));
      const listed = byKey.size ? [...described, ...extra] : entries;
      listed.forEach(([status, count]) => {
        const row = document.createElement('div');
        row.className = 'donut-legend-row';
        const label = document.createElement('span');
        label.className = 'donut-legend-label';
        const dot = document.createElement('span');
        dot.style.width = '8px';
        dot.style.height = '8px';
        dot.style.borderRadius = '50%';
        dot.style.background = colorFor(status);
        dot.style.display = 'inline-block';
        dot.style.flexShrink = '0';
        label.append(dot, document.createTextNode(labelFor(status)));
        const value = document.createElement('span');
        value.className = 'donut-legend-val';
        value.textContent = count;
        row.append(label, value);
        legend.appendChild(row);
      });
    }
  }

  // ─── TREND BADGE ─────────────────────────────────────────

  const SVG_NS = 'http://www.w3.org/2000/svg';

  /** Build the up/down chevron as real SVG nodes, never as spliced markup. */
  function trendArrow(dir) {
    const svg = document.createElementNS(SVG_NS, 'svg');
    svg.setAttribute('width', '10');
    svg.setAttribute('height', '10');
    svg.setAttribute('fill', 'none');
    svg.setAttribute('stroke', 'currentColor');
    svg.setAttribute('stroke-width', '3');
    svg.setAttribute('viewBox', '0 0 24 24');
    const line = document.createElementNS(SVG_NS, 'polyline');
    line.setAttribute('points', dir === 'up' ? '18 15 12 9 6 15' : '6 9 12 15 18 9');
    svg.appendChild(line);
    return svg;
  }

  // Built from DOM nodes rather than an innerHTML string: ARCHITECTURE.md requires
  // dynamic values to reach the page through text sinks, and `pct` arrives from
  // an API response. The class name is still chosen from a fixed set here, so
  // nothing derived from the response ever becomes markup.
  function renderTrend(elementId, trend) {
    const el = document.getElementById(elementId);
    if (!el || !trend) return;
    const { dir, pct } = trend;
    const cls = dir === 'up' ? 'trend-up' : dir === 'down' ? 'trend-down' : 'trend-neutral';

    const badge = document.createElement('span');
    badge.className = `trend-badge ${cls}`;

    // A neutral trend already reads as "—", so appending a second dash produced
    // "— —" on every empty metric. One dash, and no arrow without a figure.
    if (pct > 0 && (dir === 'up' || dir === 'down')) {
      badge.appendChild(trendArrow(dir));
      badge.appendChild(document.createTextNode(` ${pct}%`));
    } else if (pct > 0) {
      badge.textContent = `${pct}%`;
    } else {
      // No badge at all when there is nothing to report. A grey dash next to
      // three of five KPI cards reads as a broken or missing value, and it
      // says less than empty space does.
      el.replaceChildren();
      return;
    }

    el.replaceChildren(badge);
  }

  return { drawLine, drawBars, drawDonut, renderTrend, getColors, getStatusColors };
})();
