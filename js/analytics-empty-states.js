/* ============================================================
   PARTNERTYPE — ANALYTICS EMPTY STATES
   ============================================================

   Wraps the three chart draws and the partner ranking so a programme with no
   data yet says so, instead of showing an empty ring, a flat line and a blank
   white panel — which reads as a broken screen rather than an empty one.

   It wraps rather than edits: Charts owns the drawing, analytics.js owns the
   fetching, and neither should have to know which of its callers wants an
   empty state. Loaded after both, which is what lets it take their functions
   and hand back its own.

   Kept in a file, so no page carries an inline <script> and `script-src`
   does not have to allow them.
   ============================================================ */
  (() => {
    const toggleChart = (canvasId, emptyId, empty) => {
      const canvas = document.getElementById(canvasId);
      const emptyState = document.getElementById(emptyId);
      if (canvas) canvas.hidden = empty;
      if (emptyState) emptyState.hidden = !empty;
      return !empty;
    };

    const drawLine = Charts.drawLine.bind(Charts);
    Charts.drawLine = (canvasId, series, options) => {
      const values = Array.isArray(series) ? series : [];
      if (toggleChart(canvasId, `${canvasId}-empty`, !values.length || values.every(item => Number(item.leads) === 0))) {
        drawLine(canvasId, values, options);
      }
    };

    const drawDonut = Charts.drawDonut.bind(Charts);
    Charts.drawDonut = (canvasId, data, legendId, stages) => {
      const entries = Object.values(data || {});
      if (toggleChart(canvasId, `${canvasId}-empty`, !entries.length || entries.every(value => Number(value) === 0))) {
        drawDonut(canvasId, data, legendId, stages);
      }
    };

    const drawBars = Charts.drawBars.bind(Charts);
    Charts.drawBars = (canvasId, items) => {
      const values = Array.isArray(items) ? items : [];
      if (toggleChart(canvasId, `${canvasId}-empty`, !values.length)) {
        // Intentionally omit page-local colors so the shared Charts palette remains authoritative.
        drawBars(canvasId, values);
      }
    };

    const renderPartnerRows = renderPartnersTable;
    renderPartnersTable = partners => {
      renderPartnerRows(partners);
      if (partners.length) return;
      const row = document.createElement('tr');
      const cell = document.createElement('td');
      cell.colSpan = 8;
      cell.className = 'text-sm text-secondary';
      cell.style.cssText = 'text-align:center;padding:40px';
      cell.textContent = App.t('brak_danych_do_rankingu_partnerow');
      row.append(cell);
      document.getElementById('an-partners-tbody')?.replaceChildren(row);
    };
  })();
