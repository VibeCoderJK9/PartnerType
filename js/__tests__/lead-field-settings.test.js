/*
 * Producer-configurable lead fields, client side.
 *
 * The module under test is js/lead-fields.js: one renderer and one serializer
 * shared by every screen that shows these questions. It is tested on its own
 * rather than through a page controller because the rules it protects -- that
 * `0` and `false` are answers, and that a label is text and never markup --
 * are the same on the submission form, the lead detail and the assignment
 * form, and each of those arrives in a later wave.
 */
import { describe, expect, it, beforeEach } from "vitest";
import { loadScript } from "./helpers/load-script.js";

loadScript("js/lead-fields.js");

const FIELDS = [
  { id: 1, label: "Termin wdrożenia", field_type: "text", required: false, partner_editable: false, active: true, sort_order: 0 },
  { id: 2, label: "Liczba stanowisk", field_type: "number", required: false, partner_editable: true, active: true, sort_order: 1 },
  { id: 3, label: "Zgoda marketingowa", field_type: "checkbox", required: false, partner_editable: false, active: true, sort_order: 2 },
];

function container() {
  document.body.innerHTML = '<div id="fields"></div>';
  return document.getElementById("fields");
}

describe("LeadFields.render", () => {
  let host;
  beforeEach(() => { host = container(); });

  it("renders one control per active field, in the producer's order", () => {
    LeadFields.render(host, FIELDS, {});

    const inputs = [...host.querySelectorAll("[data-field-id]")];
    expect(inputs.map(i => i.dataset.fieldId)).toEqual(["1", "2", "3"]);
    expect(inputs.map(i => i.dataset.fieldType)).toEqual(["text", "number", "checkbox"]);
  });

  it("skips archived fields, which exist only to keep old answers labelled", () => {
    LeadFields.render(host, [...FIELDS, {
      id: 4, label: "Wycofane", field_type: "text", required: false,
      partner_editable: false, active: false, sort_order: 3,
    }], {});

    expect(host.querySelector('[data-field-id="4"]')).toBeNull();
  });

  it("renders nothing at all when the producer defined no questions", () => {
    LeadFields.render(host, [], {});
    expect(host.children.length).toBe(0);
  });

  it("treats a producer's label as text, never as markup", () => {
    LeadFields.render(host, [{
      id: 9, label: '<img src=x onerror="window.__leadFieldsPwned=true">',
      field_type: "text", required: false, partner_editable: false,
      active: true, sort_order: 0,
    }], {});

    expect(host.querySelector("img")).toBeNull();
    expect(window.__leadFieldsPwned).toBeUndefined();
    expect(host.querySelector("label").textContent).toContain("<img");
  });

  it("marks a required field so the person can see which ones they owe", () => {
    LeadFields.render(host, [{ ...FIELDS[0], required: true }], {});
    expect(host.querySelector("label").textContent).toBe("Termin wdrożenia *");
    expect(host.querySelector("[data-field-id]").dataset.required).toBe("1");
  });

  it("shows an existing zero rather than an empty box", () => {
    // `existing || ''` would blank this out, which is the same bug as calling
    // a zero "unanswered".
    LeadFields.render(host, FIELDS, { 2: 0 });
    expect(host.querySelector('[data-field-id="2"]').value).toBe("0");
  });

  it("shows an existing false as an unticked box, not as missing", () => {
    LeadFields.render(host, FIELDS, { 3: false });
    expect(host.querySelector('[data-field-id="3"]').checked).toBe(false);
  });

  it("shows an existing true as ticked", () => {
    LeadFields.render(host, FIELDS, { 3: true });
    expect(host.querySelector('[data-field-id="3"]').checked).toBe(true);
  });

  it("leaves a control empty when there is genuinely no answer", () => {
    LeadFields.render(host, FIELDS, {});
    expect(host.querySelector('[data-field-id="2"]').value).toBe("");
  });
});

describe("LeadFields.serialize", () => {
  let host;
  beforeEach(() => { host = container(); });

  it("sends a number as a number and text as trimmed text", () => {
    LeadFields.render(host, FIELDS, {});
    host.querySelector('[data-field-id="1"]').value = "  Q1 2027  ";
    host.querySelector('[data-field-id="2"]').value = "12";

    const { values, missing } = LeadFields.serialize(host);

    expect(values["1"]).toBe("Q1 2027");
    expect(values["2"]).toBe(12);
    expect(missing).toEqual([]);
  });

  it("always sends a checkbox, because not ticking it is an answer", () => {
    LeadFields.render(host, FIELDS, {});
    const { values } = LeadFields.serialize(host);
    expect(values["3"]).toBe(false);
  });

  it("sends a typed zero rather than dropping it", () => {
    LeadFields.render(host, FIELDS, {});
    host.querySelector('[data-field-id="2"]').value = "0";

    const { values } = LeadFields.serialize(host);

    expect(values["2"]).toBe(0);
    expect(Object.keys(values)).toContain("2");
  });

  it("omits an untouched optional field instead of sending an empty string", () => {
    LeadFields.render(host, FIELDS, {});
    const { values, missing } = LeadFields.serialize(host);
    expect(values["1"]).toBeUndefined();
    expect(missing).toEqual([]);
  });

  it("reports an unanswered required field instead of sending a partial form", () => {
    LeadFields.render(host, [{ ...FIELDS[0], required: true }], {});

    const { values, missing } = LeadFields.serialize(host);

    expect(missing).toEqual(["1"]);
    expect(values["1"]).toBeUndefined();
  });

  it("counts a required number answered when it is zero", () => {
    LeadFields.render(host, [{ ...FIELDS[1], required: true }], {});
    host.querySelector('[data-field-id="2"]').value = "0";

    const { values, missing } = LeadFields.serialize(host);

    expect(missing).toEqual([]);
    expect(values["2"]).toBe(0);
  });

  it("reports a number that is not a number rather than sending NaN", () => {
    LeadFields.render(host, FIELDS, {});
    const input = host.querySelector('[data-field-id="2"]');
    // happy-dom keeps a rejected value out of a number input, so the hostile
    // value is set the way a tampered client would.
    Object.defineProperty(input, "value", { value: "nie-liczba", writable: true });

    const { values, missing } = LeadFields.serialize(host);

    expect(values["2"]).toBeUndefined();
    expect(missing).toContain("2");
  });
});

describe("LeadFields.highlightMissing", () => {
  it("marks the fields still owed and hands back the first", () => {
    const host = container();
    LeadFields.render(host, [{ ...FIELDS[0], required: true }, FIELDS[1]], {});

    const first = LeadFields.highlightMissing(host, ["1"]);

    expect(first.dataset.fieldId).toBe("1");
    expect(first.classList.contains("input-error")).toBe(true);
    expect(host.querySelector('[data-field-id="2"]').classList.contains("input-error"))
      .toBe(false);
  });

  it("clears previous marks so a fixed field stops looking wrong", () => {
    const host = container();
    LeadFields.render(host, [{ ...FIELDS[0], required: true }], {});

    LeadFields.highlightMissing(host, ["1"]);
    LeadFields.highlightMissing(host, []);

    expect(host.querySelector('[data-field-id="1"]').classList.contains("input-error"))
      .toBe(false);
  });
});
