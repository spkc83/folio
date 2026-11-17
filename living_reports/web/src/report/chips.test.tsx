import type { QueryDef, QueryView, Vocabulary } from "../lib/api";
import { chipsOf, describeQuery, setDimension, setFilter, timeText } from "./chips";
import { followUps } from "./followups";
import { labelOf } from "./VocabularyContext";

export const VOCAB: Vocabulary = {
  metrics: [{ id: "fraud_loss", label: "Fraud loss", format: "currency" }, { id: "order_count", label: "Orders", format: "count" }],
  dimensions: [
    { id: "orders.sales_channel", label: "Sales channel" },
    { id: "dealers.region", label: "Region" },
    { id: "orders.fraud_type", label: "Fraud type" },
  ],
  time_fields: ["orders.order_created_date"],
  grains: ["day", "week", "month", "quarter", "year"],
  model_version: 1,
};

const SPEC: QueryDef = {
  metrics: ["fraud_loss"], dimensions: ["orders.sales_channel"],
  filters: [{ member: "dealers.region", op: "eq", values: ["East"] }],
  time: { start: "2025-01-01", end: "2026-01-01" },
};

const bars = (labels: string[]): QueryView => ({
  columns: ["orders.sales_channel", "fraud_loss"], rows: labels.map((l) => [l, 1]), query_hash: "h",
  points: labels.map((label) => ({ label, value: 1, formatted: "$1", fact_id: `q1.fraud_loss.${label}` })),
});

test("chips: the spec example reads in words", () => {
  expect(describeQuery(SPEC, VOCAB)).toBe("Fraud loss · by Sales channel · Region = East · 2025");
});

test("chips: a monthly query gets a Month chip and all dates", () => {
  const chips = chipsOf({ metrics: ["fraud_loss"], time: { grain: "month" } }, VOCAB);
  expect(chips.find((c) => c.kind === "by")).toMatchObject({ key: "grain", value: "Month" });
  expect(chips.find((c) => c.kind === "time")).toMatchObject({ label: "Dates", value: "All dates" });
});

test("chips: timeText covers the five shapes, with the inclusive last month", () => {
  expect(timeText(null)).toBe("All dates");
  expect(timeText({ start: "2025-01-01", end: "2026-01-01" })).toBe("2025");
  expect(timeText({ start: "2025-07-01", end: "2025-12-01" })).toBe("Jul 2025 – Nov 2025");
  expect(timeText({ start: "2025-07-01" })).toBe("From Jul 2025");
  expect(timeText({ end: "2025-12-01" })).toBe("Through Nov 2025");
});

test("chips: filter values read in words", () => {
  const q: QueryDef = { metrics: ["fraud_loss"], filters: [
    { member: "orders.sales_channel", op: "ne", values: ["East"] },
    { member: "dealers.region", op: "in", values: ["East", "West"] },
    { member: "orders.fraud_type", op: "is_null", values: [] },
  ] };
  expect(chipsOf(q, VOCAB).filter((c) => c.kind === "filter").map((c) => c.value)).toEqual(["≠ East", "in East, West", "is empty"]);
});

test("chips: edits are pure and drop limit on a dimension change", () => {
  const q: QueryDef = { ...SPEC, limit: 3 };
  const edited = setDimension(q, "dealers.region");
  expect(edited.limit).toBeUndefined();
  expect(edited.dimensions).toEqual(["dealers.region"]);
  expect(q.limit).toBe(3);
  expect(q.dimensions).toEqual(["orders.sales_channel"]);
});

test("chips: setFilter replaces a filter on the same member", () => {
  const edited = setFilter(SPEC, "dealers.region", "eq", ["West"]);
  expect(edited.filters).toEqual([{ member: "dealers.region", op: "eq", values: ["West"] }]);
  const added = setFilter(SPEC, "orders.fraud_type", "is_null", []);
  expect(added.filters).toHaveLength(2);
});

test("followups: the four rules in order", () => {
  const items = followUps(SPEC, VOCAB, bars(["App", "Web"]), false);
  expect(items.map((f) => f.label)).toEqual(["By Fraud type", "Over time", "App by Fraud type"]);
  expect(items[0].query?.dimensions).toEqual(["orders.fraud_type"]);
  expect(items[1].query?.time?.grain).toBe("month");
  expect(items[1].query?.dimensions).toEqual([]);
  expect(items[2].query?.filters).toContainEqual({ member: "orders.sales_channel", op: "eq", values: ["App"] });
});

test("followups: a line section offers compare", () => {
  const items = followUps(SPEC, VOCAB, bars(["App"]), true);
  expect(items[2].label).toBe("Compare with previous period");
  expect(items[2].compare).toBe(true);
});

test("labelOf: unknown ids fall back to the id and the time alias reads Month", () => {
  expect(labelOf(VOCAB, "orders.order_created_date:month")).toBe("Month");
  expect(labelOf(VOCAB, "month")).toBe("Month");
  expect(labelOf(VOCAB, "nope")).toBe("nope");
  expect(labelOf(null, "fraud_loss")).toBe("fraud_loss");
});

test("followups: a NULL top member filters with is_null", () => {
  const view: QueryView = { columns: ["orders.sales_channel", "fraud_loss"], rows: [[null, 5]], query_hash: "h",
    points: [{ label: "None", value: 5, formatted: "$5", fact_id: "x" }] };
  const top = followUps(SPEC, VOCAB, view, false).find((f) => f.label.endsWith("by Fraud type") && f.label !== "By Fraud type")!;
  expect(top.query?.filters).toContainEqual({ member: "orders.sales_channel", op: "is_null", values: [] });
});

test("chips: a between filter shows both values in words with a dash", async () => {
  const { filterText } = await import("./chips");
  const f = { member: "orders.order_created_date", op: "between", values: ["2024-01-01", "2024-03-01"] } as never;
  expect(filterText(f)).toBe("Jan 2024 – Mar 2024");
});

test("followups: a section's ranked follow-ups replace the fixed rule", () => {
  const stored = [{ label: "By Region", query: { ...SPEC, dimensions: ["dealers.region"] } },
    { label: "Over time", query: { ...SPEC, dimensions: [], time: { grain: "month" } } }];
  const items = followUps(SPEC, VOCAB, bars(["App"]), true, stored);
  expect(items.map((f) => f.label)).toEqual(["By Region", "Over time", "Compare with previous period"]);
  expect(items[0].query?.dimensions).toEqual(["dealers.region"]);
});
