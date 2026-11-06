/* ============================================================
   serveLeadList — the lead list's server half, for controller tests
   ============================================================

   The producer's lead list filters, sorts and slices in the
   database (api/leads.py::get_leads_page) instead of holding the whole
   programme in the browser. A stub that answers every `/api/leads` request with
   the same fixture array would still let these tests pass while the screen sent
   a filter nobody applied -- which is exactly the failure the change is meant
   to make impossible.

   So this mirrors the server's own semantics closely enough to keep the
   assertions honest: same filters, same sort keys, same page arithmetic, same
   response shape. When the two disagree, one of them is wrong, and a test says
   so. `api/tests/unit/test_leads.py` holds the matching proof on the real
   query.
   ============================================================ */

const AWAITING_DECISION = ["pending", "needs_info"];

function text(value) {
  return String(value ?? "").toLowerCase();
}

function number(value) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function matches(lead, params, actionDays, now) {
  const q = params.get("q");
  if (q) {
    const needle = text(q);
    const haystack = [lead.client_name, lead.company, lead.id, lead.sector].map(text);
    if (!haystack.some((field) => field.includes(needle))) return false;
  }

  const registration = params.get("registration_status");
  if (registration && lead.registration_status !== registration) return false;

  const status = params.get("status");
  if (status && lead.status !== status) return false;

  const partnerId = params.get("partner_id");
  if (partnerId && String(lead.partner_id) !== String(partnerId)) return false;

  const from = params.get("date_from");
  if (from && new Date(lead.created_at) < new Date(from)) return false;

  const to = params.get("date_to");
  if (to && new Date(lead.created_at) > new Date(`${to}T23:59:59`)) return false;

  const minValue = params.get("min_value");
  if (minValue && number(lead.value) < number(minValue)) return false;

  if (params.get("overdue") === "1") {
    if (!AWAITING_DECISION.includes(lead.registration_status)) return false;
    const deadline = new Date(lead.created_at).getTime() + actionDays * 86400000;
    if (deadline >= now) return false;
  }

  return true;
}

// Mirrors api/leads.py::_LEAD_SORT_COLUMNS.
const SORT_VALUE = {
  created_at:          (lead) => new Date(lead.created_at).getTime(),
  client_name:         (lead) => text(lead.client_name),
  company:             (lead) => text(lead.company),
  partner_company:     (lead) => text(lead.partner_company ?? lead.company),
  value:               (lead) => number(lead.value),
  commission:          (lead) => number(lead.commission),
  registration_status: (lead) => text(lead.registration_status),
  status:              (lead) => text(lead.status),
};

function compare(a, b) {
  if (a < b) return -1;
  if (a > b) return 1;
  return 0;
}

function sortLeads(rows, sort, dir) {
  const ordered = [...rows];
  if (!sort || sort === "attention") {
    // Everything still waiting on the producer first, oldest of those on top,
    // and the sort direction is deliberately ignored -- as on the server.
    ordered.sort((a, b) =>
      compare(AWAITING_DECISION.includes(a.registration_status) ? 0 : 1,
              AWAITING_DECISION.includes(b.registration_status) ? 0 : 1)
      || compare(new Date(a.created_at).getTime(), new Date(b.created_at).getTime()));
    return ordered;
  }
  const read = SORT_VALUE[sort];
  if (!read) throw new Error(`Pole 'sort' ma nieprawidłową wartość: ${sort}`);
  const direction = dir === "desc" ? -1 : 1;
  ordered.sort((a, b) => compare(read(a), read(b)) * direction
    || compare(text(b.id), text(a.id)));
  return ordered;
}

/**
 * Answer one GET /api/leads the way the server would.
 *
 * @param {string} url requested url, with or without a query string
 * @param {Array<object>} leads the fixture collection standing in for the table
 * @param {{actionDays?: number, now?: number|(() => number)}} [options]
 *   `actionDays` is the producer's decision window; `now` is the clock the
 *   overdue filter measures against, which a test that freezes time in its own
 *   realm has to hand over -- this module's `Date` is not that realm's.
 * @returns {{leads: Array<object>, total: number, page?: number, per_page?: number, pages?: number}}
 */
export function serveLeadList(url, leads, options = {}) {
  const [, query = ""] = String(url).split("?");
  const params = new URLSearchParams(query);
  const actionDays = options.actionDays ?? 2;
  const now = typeof options.now === "function" ? options.now() : (options.now ?? Date.now());

  const matched = sortLeads(
    leads.filter((lead) => matches(lead, params, actionDays, now)),
    params.get("sort"), params.get("dir"),
  );

  // Paging is opt-in on the server too: without it the caller gets everything.
  if (!params.has("page") && !params.has("per_page")) {
    return { leads: matched, total: matched.length };
  }

  const perPage = Math.max(1, Math.min(Number(params.get("per_page")) || 25, 100));
  const page = Math.max(1, Number(params.get("page")) || 1);
  const start = (page - 1) * perPage;
  return {
    leads: matched.slice(start, start + perPage),
    total: matched.length,
    page,
    per_page: perPage,
    pages: Math.ceil(matched.length / perPage),
  };
}

/** True for any url addressing the list itself rather than a single lead. */
export function isLeadListUrl(url) {
  return String(url).split("?")[0] === "/api/leads";
}
