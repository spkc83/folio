import { describeSelection, selectionOf } from "./selection";

const labels = { "orders.sales_channel": "Sales channel" };

test("selectionOf: nothing selected is null", () => {
  expect(selectionOf({ range: null, filters: [] }, null, null)).toBeNull();
});

test("selectionOf: filters, a null member, a range, a section, and a fact", () => {
  const scope = { range: ["2025-07-01", "2025-11-01"] as [string, string], filters: [
    { dimension: "orders.sales_channel", value: "Telesales" }, { dimension: "orders.fraud_type", value: null }] };
  expect(selectionOf(scope, "s2", "q1.fraud_loss.App")).toEqual({
    filters: [
      { member: "orders.sales_channel", op: "eq", values: ["Telesales"] },
      { member: "orders.fraud_type", op: "is_null", values: [] },
    ],
    range: ["2025-07-01", "2025-11-01"], section_id: "s2", fact_id: "q1.fraud_loss.App",
  });
});

test("describeSelection: the parts in order", () => {
  const scope = { range: ["2025-07-01", "2025-11-01"] as [string, string], filters: [{ dimension: "orders.sales_channel", value: "Telesales" }] };
  expect(describeSelection(scope, labels, "Channels", "Fraud loss · App")).toBe("Fraud loss · App · Sales channel is Telesales · Jul 2025 – Nov 2025 · Channels");
  expect(describeSelection({ range: null, filters: [] }, labels, null, null)).toBe("");
});
