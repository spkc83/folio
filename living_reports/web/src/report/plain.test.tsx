import { render } from "@testing-library/react";
import type { QueryView, Vocabulary } from "../lib/api";
import { LineChart } from "./charts/LineChart";
import { shape, tickIndexes } from "./charts/series";
import { chipsOf, timeText } from "./chips";
import { followUps } from "./followups";
import { displayParts, displayValue } from "./labels";
import { describe as describeScope, lastMonth, spanLabel } from "./scope";
import { describeSelection } from "./selection";
import { tileLabel } from "./Tile";

const VOCAB: Vocabulary = {
  metrics: [{ id: "fraud_loss", label: "Fraud loss", format: "currency" }],
  dimensions: [
    { id: "orders.sales_channel", label: "Sales channel" },
    { id: "orders.fraud_type", label: "Fraud type" },
  ],
  time_fields: ["orders.order_created_date"],
  grains: ["month"],
  model_version: 1,
};

test.each([
  ["orders.fraud_type", "bust_out", "Bust-out"],
  ["orders.fraud_type", "dealer_collusion", "Dealer collusion"],
  ["orders.fraud_type", "none", "No fraud"],
  ["dealers.region", "West", "West"],
  ["orders.tenure_band", "3Y+", "3Y+"],
  ["orders.order_created_date:month", "2024-01-01", "Jan 2024"],
  ["month", "2025-11-01", "Nov 2025"],
  ["orders.order_created_date:quarter", "2024-04-01", "Q2 2024"],
  ["orders.order_created_date:year", "2024-01-01", "2024"],
  ["orders.order_created_date:day", "2025-11-30", "30 Nov 2025"],
  ["", "2024-01-01", "Jan 2024"],
  ["orders.sales_channel", null, "No value"],
  ["orders.num_items", 3, "3"],
])("displayValue(%s, %s) is %s", (member, value, text) => {
  expect(displayValue(member, value)).toBe(text);
});

test("displayValue: a model label wins", () => {
  expect(displayValue("orders.fraud_type", "none", { none: "Not fraud" })).toBe("Not fraud");
});

test("displayParts: each part of a point label", () => {
  expect(displayParts("bust_out · 2024-01-01", ["orders.fraud_type", "month"])).toBe("Bust-out · Jan 2024");
});

test("dates: an exclusive end shows the month before; an old inclusive end shows its own month", () => {
  expect(spanLabel("2024-01-01", "2025-12-01")).toBe("Jan 2024 – Nov 2025");
  expect(spanLabel("2024-01-01", "2025-11-30")).toBe("Jan 2024 – Nov 2025");
  expect(lastMonth("2025-12-01")).toBe("2025-11-01");
  expect(lastMonth("2025-11-30")).toBe("2025-11-01");
  expect(timeText({ start: "2024-01-01", end: "2025-11-30" })).toBe("Jan 2024 – Nov 2025");
  expect(timeText({ end: "2025-12-01" })).toBe("Through Nov 2025");
});

test("scope words use 'is' and display values", () => {
  const scope = { range: null, filters: [{ dimension: "orders.fraud_type", value: "bust_out" }] };
  expect(describeScope(scope, { "orders.fraud_type": "Fraud type" })).toBe("Fraud type is Bust-out");
  const west = { range: null, filters: [{ dimension: "dealers.region", value: "West" }] };
  expect(describeSelection(west, { "dealers.region": "Region" }, null, null)).toBe("Region is West");
});

test("chips: filter values are display values", () => {
  const chips = chipsOf({ metrics: ["fraud_loss"], filters: [{ member: "orders.fraud_type", op: "eq", values: ["bust_out"] }] }, VOCAB);
  expect(chips.find((c) => c.kind === "filter")?.value).toBe("= Bust-out");
});

const WEST_TYPES: QueryView = {
  columns: ["dealers.region", "orders.fraud_type", "orders.order_created_date:month", "fraud_loss"],
  rows: [], query_hash: "h", series: null,
  points: ["bust_out", "identity_theft"].flatMap((t, k) => ["2024-01-01", "2024-02-01", "2024-03-01"].map((m, i) => ({
    label: `West · ${t} · ${m}`, value: (k + 1) * (i + 1), formatted: `$${(k + 1) * (i + 1)}`, fact_id: `q2.fraud_loss.West.${t}.${m}`,
  }))),
};

test("followups: the top member reads in words", () => {
  const def = { metrics: ["fraud_loss"], dimensions: ["orders.fraud_type"] };
  const view: QueryView = { columns: ["orders.fraud_type", "fraud_loss"], rows: [["bust_out", 5]], query_hash: "h",
    points: [{ label: "bust_out", value: 5, formatted: "$5", fact_id: "x" }] };
  const items = followUps(def, VOCAB, view, false);
  expect(items.map((f) => f.label)).toContain("Bust-out by Sales channel");
  expect(items.find((f) => f.label === "Bust-out by Sales channel")?.query?.filters)
    .toContainEqual({ member: "orders.fraud_type", op: "eq", values: ["bust_out"] });
});

test("shape: a line over two dimensions has one series per member, not one zigzag", () => {
  const { xs, series } = shape(WEST_TYPES, 7);
  expect(xs).toEqual(["2024-01-01", "2024-02-01", "2024-03-01"]);
  // West is the same in every point, so it does not name a series.
  expect(series.map((s) => s.label)).toEqual(["Bust-out", "Identity theft"]);
  expect(series.map((s) => s.color)).toEqual(["var(--c1)", "var(--c2)"]);
});

test("shape: past six series, the smallest fold into Other", () => {
  const names = ["a", "b", "c", "d", "e", "f", "g", "h"];
  const query: QueryView = {
    columns: ["month", "k", "v"], rows: [], query_hash: "h", series: "k",
    points: names.map((n, i) => ({ label: `2025-01-01 · ${n}`, value: 10 - i, formatted: String(10 - i), fact_id: n })),
  };
  const got = shape(query, 7).series;
  expect(got.map((s) => s.name)).toEqual(["a", "b", "c", "d", "e", "f", "Other"]);
  expect(got[6].color).toBe("var(--gray)");
  const seven = { ...query, points: query.points.slice(0, 7) };
  expect(shape(seven, 7).series.map((s) => s.name)).toEqual(["a", "b", "c", "d", "e", "f", "Other"]);
});

test("tickIndexes: labels never overlap and the ends show", () => {
  const months = Array.from({ length: 23 }, (_, i) => `Mmm ${2024 + Math.floor(i / 12)}`);
  const at = tickIndexes(months, 544);
  expect(at[0]).toBe(0);
  expect(at[at.length - 1]).toBe(22);
  const step = 544 / 22;
  for (let i = 1; i < at.length; i += 1) {
    expect((at[i] - at[i - 1]) * step).toBeGreaterThanOrEqual(8 * 6.6 + 8);
  }
  expect(tickIndexes(["a", "b"], 544)).toEqual([0, 1]);
});

test("line: two dimensions draw a legend and one line per series with month ticks", () => {
  const { container } = render(<LineChart query={WEST_TYPES} title="t" />);
  expect(container.querySelectorAll("polyline")).toHaveLength(2);
  const legend = [...container.querySelectorAll(".legend li")].map((li) => li.textContent);
  expect(legend).toEqual(["Bust-out", "Identity theft"]);
  const ticks = [...container.querySelectorAll(".x-tick")].map((t) => t.textContent);
  expect(ticks).toEqual(["Jan 2024", "Feb 2024", "Mar 2024"]);
});

test("tile labels show raw parts in words", () => {
  expect(tileLabel("Top: identity_theft")).toBe("Top: Identity theft");
  expect(tileLabel("Fraud loss · West · 2024-01-01")).toBe("Fraud loss · West · Jan 2024");
  expect(tileLabel("Fraud loss")).toBe("Fraud loss");
});

test("labels: a value that names an object property is not looked up on the prototype", () => {
  expect(displayValue("orders.fraud_type", "constructor")).toBe("Constructor");
  expect(typeof displayValue("dealers.region", "toString", { West: "West" })).toBe("string");
  expect(displayValue("dealers.region", "toString", { West: "West" })).toBe("toString");
});
