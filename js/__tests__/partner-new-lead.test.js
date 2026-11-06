import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { loadScript } from "./helpers/load-script.js";
import { appStub } from "./helpers/copy.js";

// partner.js reads its copy from the dictionary at load time
globalThis.App = appStub("pl");

loadScript("js/icons.js");
loadScript("js/utils.js");
loadScript("js/partner.js");

/*
 * DOM fixture mirrors pages/partner/new-lead.html's form + sidebar markup
 * (post-fix shape): the value-hint's inner element carries a distinct id
 * (commission-preview-hint) from the sidebar's "of the value" sub-line
 * (commission-source-value), so both are independently addressable --
 * unlike the live bug where both shared id="commission-preview". The
 * duplicate-warning region and submit-blocked-reason line mirror the
 * markup around the NIP/website fields and the submit button.
 */
function buildNewLeadFixture() {
  document.body.innerHTML = `
    <form id="new-lead-form" novalidate>
      <input type="text" id="lead-client-name" class="form-input" value="Jan Kowalski">
      <input type="text" id="lead-company" class="form-input" value="Acme Sp. z o.o.">
      <input type="email" id="lead-email" class="form-input" value="jan@firma.pl">
      <input type="tel" id="lead-phone" class="form-input" value="">
      <select id="lead-sector" class="form-select"><option value="IT" selected>IT</option></select>
      <input type="text" id="lead-nip" class="form-input" value="1111111111">
      <input type="text" id="lead-website" class="form-input" value="nova-sklep.pl">
      <div id="duplicate-warning" hidden>
        <p id="duplicate-warning-text"></p>
        <span id="duplicate-warning-badge"></span>
      </div>
      <select id="lead-product" class="form-select">
        <option value="">Wybierz produkt...</option>
      </select>
      <div id="product-catalog-message" class="form-hint" hidden></div>
      <input type="number" id="lead-quantity" class="form-input" value="2">
      <input type="number" id="lead-value" class="form-input" min="0" step="1000" value="5000">
      <div class="form-hint" id="value-preview-hint" style="display:none">
        Twoja szacunkowa prowizja (<span id="commission-rate-hint">&mdash;</span>): <strong id="commission-preview-hint">&mdash;</strong>
      </div>
      <textarea id="lead-notes" class="form-textarea"></textarea>
      <p id="submit-blocked-reason" hidden></p>
      <button type="submit" id="submit-lead-btn">Wyślij zgłoszenie</button>
    </form>
    <div class="commission-box">
      <div class="commission-label">Twoja prowizja (<span id="commission-rate-label">&mdash;</span>)</div>
      <div class="commission-value" id="value-preview">0 zl</div>
      <div class="commission-sub">od wartosci <span id="commission-source-value">&mdash;</span></div>
    </div>
  `;
}

// The partner in every fixture is Gold (7%), deliberately NOT 5% -- a preview
// that ignores the tier would read as "250 PLN" here and the assertions would
// catch it.
const GOLD_PARTNER = { tier_name: "Gold", base_rate: 7, total_percent: 7 };

// initNewLeadForm resolves the rate asynchronously; the preview is only
// meaningful once that has landed.
function whenRateResolved() {
  return vi.waitFor(() =>
    expect(document.getElementById("commission-rate-label").textContent).not.toBe("—"),
  );
}

async function chooseProduct(id = "7") {
  await vi.waitFor(() =>
    expect(document.querySelectorAll("#lead-product option")).toHaveLength(3),
  );
  document.getElementById("lead-product").value = id;
}

// initNewLeadForm registers exactly one App.onRerender callback for the
// whole realm (the module-level newLeadRerenderBound guard fires only on
// this file's first call) -- so the capturing stub has to live at file
// scope and every beforeEach has to install a stub that feeds the SAME
// array, or a later test's language switch would trigger nothing.
const rerenderHooks = [];
function appStubWithRerender(lang) {
  const stub = appStub(lang);
  stub.onRerender = (fn) => rerenderHooks.push(fn);
  return stub;
}
function triggerRerender() {
  rerenderHooks.forEach((fn) => fn());
}

beforeEach(() => {
  buildNewLeadFixture();

  globalThis.ApiClient = {
    post: vi.fn().mockResolvedValue({ lead: { id: "LD-010" } }),
    get: vi.fn().mockImplementation((path) => {
      if (path === "/api/products") {
        return Promise.resolve({
          products: [
            { id: 7, name: "Wdrożenie ERP" },
            { id: 12, name: "Licencja CRM" },
          ],
        });
      }
      return Promise.resolve({ partner: { ...GOLD_PARTNER } });
    }),
  };
  globalThis.App = appStubWithRerender("pl");
  globalThis.showToast = vi.fn();
  globalThis.handleApiError = vi.fn();
  globalThis.formatCurrency = vi.fn(
    (value) => new Intl.NumberFormat("pl-PL", { style: "currency", currency: "PLN", maximumFractionDigits: 0 }).format(value),
  );
  globalThis.formatDate = vi.fn((value) => String(value));
  globalThis.getInitials = vi.fn(() => "JK");

  initNewLeadForm({ id: 1, role: "partner" });
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe("Zglos lead — commission preview (single source of truth per fact)", () => {
  it("reads the partner's own tier rate rather than a flat house rate", async () => {
    await whenRateResolved();

    expect(ApiClient.get).toHaveBeenCalledWith("/api/partner-bonuses/1");
    expect(document.getElementById("commission-rate-label").textContent).toBe("7%");
  });

  it("sets the sidebar commission-value element to the partner's tier figure", async () => {
    await whenRateResolved();
    const input = document.getElementById("lead-value");
    input.value = "5000";
    input.dispatchEvent(new Event("input"));

    expect(document.getElementById("value-preview").textContent).toBe(formatCurrency(350));
  });

  it("sets the form hint under the input to the same figure, not the raw contract value", async () => {
    await whenRateResolved();
    const input = document.getElementById("lead-value");
    input.value = "5000";
    input.dispatchEvent(new Event("input"));

    expect(document.getElementById("commission-preview-hint").textContent).toBe(formatCurrency(350));
  });

  it("sets the sidebar 'od wartosci' element to the raw contract value", async () => {
    await whenRateResolved();
    const input = document.getElementById("lead-value");
    input.value = "5000";
    input.dispatchEvent(new Event("input"));

    expect(document.getElementById("commission-source-value").textContent).toBe(formatCurrency(5000));
  });

  it("matches the producer-side formula by applying the queue share of a duplicate", async () => {
    // api/leads.py pays value * total_percent% * queue_share%. A second
    // submission on a 50% share must be previewed at half, not at full rate.
    vi.useFakeTimers();
    await whenRateResolved();
    ApiClient.post.mockResolvedValue({ duplicate: true, queue_position: 2, share: 50, policy: "queue" });

    document.getElementById("lead-nip").dispatchEvent(new Event("blur"));
    await vi.advanceTimersByTimeAsync(400);

    const input = document.getElementById("lead-value");
    input.value = "5000";
    input.dispatchEvent(new Event("input"));

    expect(document.getElementById("value-preview").textContent).toBe(formatCurrency(175));
    expect(document.getElementById("commission-rate-label").textContent).toBe("7% × 50% udziału");
  });

  it("shows a dash instead of inventing a figure when the rate cannot be resolved", async () => {
    ApiClient.get.mockRejectedValue(new Error("Błąd serwera (500)"));
    initNewLeadForm({ id: 1, role: "partner" });

    const input = document.getElementById("lead-value");
    input.value = "5000";
    input.dispatchEvent(new Event("input"));

    await vi.waitFor(() =>
      expect(document.getElementById("value-preview").textContent).toBe("—"),
    );
    expect(document.getElementById("commission-rate-label").textContent).toBe("—");
  });

  it("addresses exactly one element per id used by the preview handler", () => {
    for (const id of ["value-preview", "commission-preview-hint", "commission-source-value", "commission-rate-label"]) {
      expect(document.querySelectorAll(`[id="${id}"]`).length).toBe(1);
    }
  });
});

describe("Zglos lead — submission payload", () => {
  it("fills the product select with names from the API", async () => {
    await vi.waitFor(() =>
      expect(document.querySelectorAll("#lead-product option")).toHaveLength(3),
    );

    const options = Array.from(document.querySelectorAll("#lead-product option"));
    expect(ApiClient.get).toHaveBeenCalledWith("/api/products");
    expect(options.map((option) => option.textContent)).toEqual([
      "Wybierz produkt...",
      "Wdrożenie ERP",
      "Licencja CRM",
    ]);
  });

  it("posts numeric product_id without the denormalized product name", async () => {
    await vi.waitFor(() =>
      expect(document.querySelectorAll("#lead-product option")).toHaveLength(3),
    );
    document.getElementById("lead-product").value = "12";
    const form = document.getElementById("new-lead-form");
    form.dispatchEvent(new Event("submit", { cancelable: true }));

    await vi.waitFor(() =>
      expect(ApiClient.post).toHaveBeenCalledWith("/api/leads", expect.any(Object)),
    );

    const [, body] = ApiClient.post.mock.calls.find(([path]) => path === "/api/leads");
    expect(body).toMatchObject({
      nip: "1111111111",
      website: "nova-sklep.pl",
      product_id: 12,
      quantity: 2,
    });
    expect(body).not.toHaveProperty("product");
  });

  it("blocks submission and explains an empty product catalogue", async () => {
    buildNewLeadFixture();
    ApiClient.get.mockImplementation((path) => {
      if (path === "/api/products") return Promise.resolve({ products: [] });
      return Promise.resolve({ partner: { ...GOLD_PARTNER } });
    });
    ApiClient.post.mockClear();
    initNewLeadForm({ id: 1, role: "partner" });

    await vi.waitFor(() =>
      expect(document.getElementById("product-catalog-message").textContent).toBe(
        "Producent nie udostępnił jeszcze żadnych produktów",
      ),
    );
    expect(document.getElementById("product-catalog-message").hidden).toBe(false);
    expect(document.getElementById("submit-lead-btn").disabled).toBe(true);

    document.getElementById("new-lead-form").dispatchEvent(new Event("submit", { cancelable: true }));
    expect(ApiClient.post).not.toHaveBeenCalled();
  });

  it("marks an unselected product inline and sends no request", async () => {
    await vi.waitFor(() =>
      expect(document.querySelectorAll("#lead-product option")).toHaveLength(3),
    );

    document.getElementById("new-lead-form").dispatchEvent(new Event("submit", { cancelable: true }));

    expect(document.getElementById("lead-product").classList.contains("error")).toBe(true);
    expect(ApiClient.post.mock.calls.some(([path]) => path === "/api/leads")).toBe(false);
  });
});

// A language switch must only redraw the product select's
// LABELS. The submitted product_id, the quantity already typed, and the
// rest of a half-filled form must survive it untouched -- the same
// contract the producer's own custom-field captions (LeadFields.relabel)
// already keep.
describe("Zglos lead — language switch relabels the product select only", () => {
  // The catalogue fetch already fired inside the outer beforeEach's
  // initNewLeadForm() call, against the default (no name_en) mock -- so
  // every test here re-fetches with its own product list the same way
  // "blocks submission and explains an empty product catalogue" above does.
  function reloadWithProducts(products) {
    buildNewLeadFixture();
    globalThis.ApiClient.get.mockImplementation((path) => {
      if (path === "/api/products") return Promise.resolve({ products });
      return Promise.resolve({ partner: { ...GOLD_PARTNER } });
    });
    initNewLeadForm({ id: 1, role: "partner" });
  }

  it("relabels the chosen product to English and keeps its id and the rest of the form", async () => {
    reloadWithProducts([
      { id: 7, name: "Wdrożenie ERP", name_en: "ERP rollout" },
      { id: 12, name: "Licencja CRM", name_en: "CRM licence" },
    ]);
    await chooseProduct("7");
    document.getElementById("lead-quantity").value = "9";

    App.lang = "en";
    triggerRerender();

    const select = document.getElementById("lead-product");
    expect(select.value).toBe("7");
    expect(select.options[1].textContent).toBe("ERP rollout");
    expect(select.options[2].textContent).toBe("CRM licence");
    // Untouched by the relabel: still the answer the partner already typed.
    expect(document.getElementById("lead-quantity").value).toBe("9");
  });

  it("falls back to the Polish name for a product with no English translation", async () => {
    reloadWithProducts([
      { id: 7, name: "Wdrożenie ERP", name_en: "" },
      { id: 12, name: "Licencja CRM" },
    ]);
    await chooseProduct("7");

    App.lang = "en";
    triggerRerender();

    const select = document.getElementById("lead-product");
    expect(select.options[1].textContent).toBe("Wdrożenie ERP");
  });

  it("switches back to the Polish label on a second language switch, id still unchanged", async () => {
    reloadWithProducts([{ id: 7, name: "Wdrożenie ERP", name_en: "ERP rollout" }]);
    await vi.waitFor(() =>
      expect(document.querySelectorAll("#lead-product option")).toHaveLength(2),
    );
    document.getElementById("lead-product").value = "7";

    App.lang = "en";
    triggerRerender();
    App.lang = "pl";
    triggerRerender();

    const select = document.getElementById("lead-product");
    expect(select.value).toBe("7");
    expect(select.options[1].textContent).toBe("Wdrożenie ERP");
  });
});

describe("Zglos lead — duplicate pre-check", () => {
  it("issues exactly one pre-check request once both identity fields hold valid values", async () => {
    vi.useFakeTimers();
    ApiClient.post.mockResolvedValue({ duplicate: false, queue_position: 1, share: 100, policy: "queue" });

    document.getElementById("lead-nip").dispatchEvent(new Event("blur"));
    await vi.advanceTimersByTimeAsync(400);

    expect(ApiClient.post).toHaveBeenCalledTimes(1);
    expect(ApiClient.post).toHaveBeenCalledWith("/api/leads/precheck", { nip: "1111111111", website: "nova-sklep.pl" });
  });

  it("leaves the form clean with no warning shown when no duplicate is reported", async () => {
    vi.useFakeTimers();
    ApiClient.post.mockResolvedValue({ duplicate: false, queue_position: 1, share: 100, policy: "queue" });

    document.getElementById("lead-nip").dispatchEvent(new Event("blur"));
    await vi.advanceTimersByTimeAsync(400);

    expect(document.getElementById("duplicate-warning").hidden).toBe(true);
  });

  it("shows the Copywriting Contract duplicate message and a position/share badge, with no other partner's details", async () => {
    vi.useFakeTimers();
    ApiClient.post.mockResolvedValue({ duplicate: true, queue_position: 2, share: 50, policy: "queue" });

    document.getElementById("lead-nip").dispatchEvent(new Event("blur"));
    await vi.advanceTimersByTimeAsync(400);

    const warning = document.getElementById("duplicate-warning");
    expect(warning.hidden).toBe(false);
    expect(document.getElementById("duplicate-warning-text").textContent).toBe(
      "Ten klient został już zgłoszony (ten sam NIP lub domena). Trafiasz do kolejki — Twoja prowizja zależy od kolejności zgłoszenia.",
    );
    const badge = document.getElementById("duplicate-warning-badge");
    expect(badge.textContent).toBe("2. zgłoszenie — 50% prowizji");
    expect(badge.textContent).not.toMatch(/@|partner|email/i);
  });

  it("renders a complete badge label at the deepest configured queue position", async () => {
    vi.useFakeTimers();
    ApiClient.post.mockResolvedValue({ duplicate: true, queue_position: 5, share: 0, policy: "queue" });

    document.getElementById("lead-nip").dispatchEvent(new Event("blur"));
    await vi.advanceTimersByTimeAsync(400);

    expect(document.getElementById("duplicate-warning-badge").textContent).toBe("5. zgłoszenie — 0% prowizji");
  });

  it("does not block submission under the queue policy", async () => {
    vi.useFakeTimers();
    ApiClient.post.mockResolvedValue({ duplicate: true, queue_position: 2, share: 50, policy: "queue" });

    document.getElementById("lead-nip").dispatchEvent(new Event("blur"));
    await vi.advanceTimersByTimeAsync(400);

    expect(document.getElementById("submit-lead-btn").disabled).toBe(false);
    expect(document.getElementById("submit-blocked-reason").hidden).toBe(true);
  });

  it("blocks submission under the reject policy and states the reason inline", async () => {
    vi.useFakeTimers();
    ApiClient.post.mockResolvedValue({ duplicate: true, queue_position: 2, share: 0, policy: "reject" });

    document.getElementById("lead-nip").dispatchEvent(new Event("blur"));
    await vi.advanceTimersByTimeAsync(400);

    const btn = document.getElementById("submit-lead-btn");
    const reason = document.getElementById("submit-blocked-reason");
    expect(btn.disabled).toBe(true);
    expect(reason.hidden).toBe(false);
    expect(reason.textContent.length).toBeGreaterThan(0);
  });

  it("shows the inline NIP format hint rather than a raw server message on a malformed identity", async () => {
    vi.useFakeTimers();
    ApiClient.post.mockRejectedValue(new Error("Pole 'NIP' musi składać się z 10 cyfr."));

    const nipEl = document.getElementById("lead-nip");
    nipEl.value = "1234567890";
    document.getElementById("lead-website").value = "nova-sklep.pl";
    nipEl.dispatchEvent(new Event("blur"));
    await vi.advanceTimersByTimeAsync(400);

    expect(nipEl.classList.contains("error")).toBe(true);
    expect(document.getElementById("duplicate-warning").hidden).toBe(true);
    expect(document.body.textContent).not.toContain("Pole 'NIP' musi składać się z 10 cyfr.");
  });
});

describe("Zglos lead — data preservation on duplicate and failed submission", () => {
  it("keeps every entered value after a duplicate warning is shown", async () => {
    vi.useFakeTimers();
    ApiClient.post.mockResolvedValue({ duplicate: true, queue_position: 2, share: 50, policy: "queue" });

    document.getElementById("lead-nip").dispatchEvent(new Event("blur"));
    await vi.advanceTimersByTimeAsync(400);

    expect(document.getElementById("lead-client-name").value).toBe("Jan Kowalski");
    expect(document.getElementById("lead-nip").value).toBe("1111111111");
    expect(document.getElementById("lead-website").value).toBe("nova-sklep.pl");
  });

  it("keeps every entered value after a failed submission and says data was not lost", async () => {
    await chooseProduct();
    ApiClient.post.mockRejectedValue(new Error("Błąd serwera (500)"));

    document.getElementById("new-lead-form").dispatchEvent(new Event("submit", { cancelable: true }));
    await vi.waitFor(() => expect(showToast).toHaveBeenCalledWith(
      "error", expect.any(String), "Dane nie zostały utracone — spróbuj ponownie.",
    ));

    expect(document.getElementById("lead-client-name").value).toBe("Jan Kowalski");
    expect(document.getElementById("lead-nip").value).toBe("1111111111");
    expect(document.getElementById("lead-website").value).toBe("nova-sklep.pl");
  });
});

describe("Zglos lead — submit control double-click and timeout protection", () => {
  it("disables the control and shows a spinner immediately on click", async () => {
    await chooseProduct();
    let resolvePost;
    ApiClient.post.mockReturnValue(new Promise((resolve) => { resolvePost = resolve; }));

    const form = document.getElementById("new-lead-form");
    const btn = document.getElementById("submit-lead-btn");
    form.dispatchEvent(new Event("submit", { cancelable: true }));

    expect(btn.disabled).toBe(true);
    expect(btn.innerHTML).toContain("spinner");

    resolvePost({ lead: { id: "LD-011" } });
    await vi.waitFor(() => expect(btn.disabled).toBe(false));
  });

  it("issues no second request when the submit control is clicked twice", async () => {
    await chooseProduct();
    let resolvePost;
    ApiClient.post.mockImplementation(() => new Promise((resolve) => { resolvePost = resolve; }));

    const form = document.getElementById("new-lead-form");
    form.dispatchEvent(new Event("submit", { cancelable: true }));
    form.dispatchEvent(new Event("submit", { cancelable: true }));

    expect(ApiClient.post.mock.calls.filter(([path]) => path === "/api/leads").length).toBe(1);
    resolvePost({ lead: { id: "LD-012" } });
  });

  it("re-enables the control after a successful server response", async () => {
    await chooseProduct();
    ApiClient.post.mockResolvedValue({ lead: { id: "LD-013" } });
    const form = document.getElementById("new-lead-form");

    form.dispatchEvent(new Event("submit", { cancelable: true }));
    await vi.waitFor(() => expect(showToast).toHaveBeenCalledWith("success", expect.any(String), expect.any(String)));
  });

  it("re-enables the control after a failed server response", async () => {
    await chooseProduct();
    ApiClient.post.mockRejectedValue(new Error("Błąd serwera (500)"));
    const form = document.getElementById("new-lead-form");
    const btn = document.getElementById("submit-lead-btn");

    form.dispatchEvent(new Event("submit", { cancelable: true }));
    await vi.waitFor(() => expect(btn.disabled).toBe(false));
  });

  it("re-enables the control after a timeout when the server never responds", async () => {
    await chooseProduct();
    vi.useFakeTimers();
    ApiClient.post.mockReturnValue(new Promise(() => {})); // never resolves

    const form = document.getElementById("new-lead-form");
    const btn = document.getElementById("submit-lead-btn");
    form.dispatchEvent(new Event("submit", { cancelable: true }));

    expect(btn.disabled).toBe(true);
    await vi.advanceTimersByTimeAsync(20000);

    expect(btn.disabled).toBe(false);
  });
});
