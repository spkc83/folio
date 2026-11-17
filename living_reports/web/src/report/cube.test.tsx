import type { Binding, CubeView, QueryDef, QueryView } from "../lib/api";
import { CUBE } from "./cubeFixture";
import { cubeDrill, derive, members, tileValue, withPrevious, deltaUnderScope } from "./cube";
import { BASELINE, describe as describeScope, reset, scopedQuery, withMember, withRange, isBaseline, type Scope } from "./scope";

const LOSS: QueryDef = { metrics: ["fraud_loss"], dimensions: ["orders.sales_channel"] };
const RATE: QueryDef = { metrics: ["fraud_rate"], dimensions: ["orders.sales_channel"] };
const TOTAL_RATE: QueryDef = { metrics: ["fraud_rate"], dimensions: [] };

const viewOf = (d: ReturnType<typeof derive>): QueryView => {
  if ("reason" in d) throw new Error(d.reason);
  return d.view;
};
const scope = (filters: Scope["filters"], range: Scope["range"] = null): Scope => ({ filters, range });

test("derive: baseline by channel equals the server view", () => {
  const v = viewOf(derive(CUBE, LOSS, BASELINE));
  expect(v.columns).toEqual(["orders.sales_channel", "fraud_loss"]);
  expect(v.rows).toEqual([["App", 1300], ["Telesales", 900], ["Web", 650], ["Marketplace", 20]]);
  expect(v.points.map((p) => [p.label, p.value, p.formatted])).toEqual([
    ["App", 1300, "$1.3K"], ["Telesales", 900, "$900.00"], ["Web", 650, "$650.00"], ["Marketplace", 20, "$20.00"],
  ]);
  expect(v.series ?? null).toBeNull();
});

test("derive: a member filter re-aggregates a ratio from components", () => {
  const all = viewOf(derive(CUBE, TOTAL_RATE, BASELINE));
  expect(all.points[0].formatted).toBe("2.64%");
  const rates = viewOf(derive(CUBE, RATE, BASELINE)).points.map((p) => p.value as number);
  expect(rates.reduce((a, b) => a + b, 0) / rates.length).not.toBeCloseTo(0.0264, 4);
  const tele = viewOf(derive(CUBE, TOTAL_RATE, scope([{ dimension: "orders.sales_channel", value: "Telesales" }])));
  expect(tele.points[0].formatted).toBe("3.52%");
});

test("derive: a range keeps only the months inside it", () => {
  const v = viewOf(derive(CUBE, LOSS, scope([], ["2025-01-01", "2025-01-01"])));
  expect(v.rows).toEqual([["App", 700], ["Telesales", 500], ["Web", 350], ["Marketplace", 20]]);
  const bounded = viewOf(derive(CUBE, { ...LOSS, time: { start: "2024-12-01", end: "2025-01-01" } }, BASELINE));
  expect(bounded.rows[0]).toEqual(["App", 600]);
});

test("derive: top applies after ordering", () => {
  const v = viewOf(derive(CUBE, { ...LOSS, limit: 2 }, BASELINE));
  expect(v.points.map((p) => p.label)).toEqual(["App", "Telesales"]);
});

test("derive: a month grouping orders by month and splits series", () => {
  const v = viewOf(derive(CUBE, { metrics: ["fraud_loss"], dimensions: ["orders.sales_channel"], time: { grain: "month" } }, BASELINE));
  expect(v.points[0].label).toBe("App · 2024-12-01");
  expect(v.points[1].label).toBe("Marketplace · 2024-12-01");
  expect(v.series).toBe("orders.sales_channel");
});

test("derive: an \"other\" metric is disabled with a reason", () => {
  expect(derive(CUBE, { metrics: ["dealers"], dimensions: ["orders.sales_channel"] }, BASELINE)).toEqual({
    reason: "Dealers cannot be re-aggregated",
  });
});

test("derive: a scope filter that contradicts the def filter gives an empty view", () => {
  const def: QueryDef = { ...LOSS, filters: [{ member: "orders.sales_channel", op: "eq", values: ["App"] }] };
  const contra = scope([{ dimension: "orders.sales_channel", value: "Web" }]);
  expect(viewOf(derive(CUBE, def, contra)).points).toHaveLength(0);
  const binding: Binding = { ref: "q1", metric: "fraud_loss", keys: {}, share: false };
  expect(tileValue(CUBE, def, binding, contra)).toEqual({ value: null, formatted: "No data" });
});

test("derive: a zero denominator gives null, never Infinity", () => {
  const v = viewOf(derive(CUBE, TOTAL_RATE, scope([{ dimension: "orders.sales_channel", value: "Marketplace" }], ["2024-12-01", "2024-12-01"])));
  expect(v.points[0].value).toBeNull();
  expect(v.points[0].formatted).toBe("No data");
});

test("derive: a null member filters to null rows", () => {
  const cube: CubeView = {
    ...CUBE,
    dimensions: ["orders.fraud_type", "month"],
    labels: { "orders.fraud_type": "Fraud type", month: "Month" },
    columns: ["orders.fraud_type", "month", "fraud_loss", "order_count", "fraud_rate__num", "fraud_rate__den", "dealers"],
    rows: [["identity_theft", "2025-01-01", 50, 1, 1, 1, 1], [null, "2025-01-01", 7, 9, 0, 9, 1]],
  };
  const def: QueryDef = { metrics: ["order_count"], dimensions: ["orders.fraud_type"] };
  const v = viewOf(derive(cube, def, scope([{ dimension: "orders.fraud_type", value: null }])));
  expect(v.rows).toEqual([[null, 9]]);
  expect(v.points[0].label).toBe("No value");
  expect(members(cube, "orders.fraud_type")).toEqual(["identity_theft", null]);
  expect(describeScope(scope([{ dimension: "orders.fraud_type", value: null }]), cube.labels)).toBe("Fraud type is No value");
});

test("tileValue: a binding's keys select its member, and a share divides by the whole", () => {
  const keyed: Binding = { ref: "q1", metric: "fraud_loss", keys: { "orders.sales_channel": "Web" }, share: false };
  expect(tileValue(CUBE, LOSS, keyed, BASELINE)).toEqual({ value: 650, formatted: "$650.00" });
  const share: Binding = { ...keyed, share: true };
  expect(tileValue(CUBE, LOSS, share, scope([], ["2025-01-01", "2025-01-01"]))).toEqual({ value: 350 / 1570, formatted: "22.29%" });
});

// 24 months of one channel, for the previous period.
const LONG: CubeView = {
  ...CUBE,
  rows: Array.from({ length: 24 }, (_, i) => {
    const m = `${2024 + Math.floor(i / 12)}-${String((i % 12) + 1).padStart(2, "0")}-01`;
    return ["App", m, 100 + i, 10, 1, 10, 1];
  }),
};

test("withPrevious: splits the last twelve months against the twelve before", () => {
  const v = viewOf(withPrevious(LONG, { metrics: ["fraud_loss"], dimensions: [] }, BASELINE));
  expect(v.series).toBe("period");
  const names = [...new Set(v.points.map((p) => p.label.split(" · ")[1]))];
  expect(names).toEqual(["2025", "2024"]);
  expect(v.points.filter((p) => p.label.endsWith("2025"))).toHaveLength(12);
  expect(v.points[0]).toMatchObject({ label: "Month 1 · 2025", value: 112 });
  expect(v.points[12]).toMatchObject({ label: "Month 1 · 2024", value: 100 });
  const binding: Binding = { ref: "q1", metric: "fraud_loss", keys: {}, share: false };
  const sum = (a: number, n: number) => Array.from({ length: n }, (_, i) => a + i).reduce((x, y) => x + y, 0);
  const expected = `+${(((sum(112, 12) - sum(100, 12)) / sum(100, 12)) * 100).toFixed(1)}%`;
  expect(deltaUnderScope(LONG, { metrics: ["fraud_loss"], dimensions: [] }, binding, BASELINE, "currency")).toBe(expected);
});

test("withPrevious: too little history gives a reason", () => {
  expect(withPrevious(CUBE, LOSS, BASELINE)).toEqual({ reason: "No data before Dec 2024" });
});

test("scope: withMember toggles and withRange normalizes order", () => {
  const one = withMember(BASELINE, "orders.sales_channel", "Telesales");
  expect(one.filters).toEqual([{ dimension: "orders.sales_channel", value: "Telesales" }]);
  expect(withMember(one, "orders.sales_channel", "Web").filters).toEqual([{ dimension: "orders.sales_channel", value: "Web" }]);
  expect(isBaseline(withMember(one, "orders.sales_channel", "Telesales"))).toBe(true);
  expect(withRange(BASELINE, "2025-11-01", "2025-07-01").range).toEqual(["2025-07-01", "2025-11-01"]);
  expect(describeScope(withRange(one, "2025-07-01", "2025-11-01"), CUBE.labels)).toBe("Sales channel is Telesales · Jul 2025 – Nov 2025");
  expect(isBaseline(reset())).toBe(true);
  expect(describeScope(withRange(BASELINE, "2025-03-01", "2025-03-01"), {})).toBe("Mar 2025");
});

test("scope: the scoped query flattens the scope and refuses a null member", () => {
  const s = withRange(withMember(BASELINE, "orders.sales_channel", "Telesales"), "2025-07-01", "2025-11-01");
  expect(scopedQuery("q1", s, { by: "orders.fraud_type" })).toEqual({
    ref: "q1", filters: [{ member: "orders.sales_channel", op: "eq", values: ["Telesales"] }],
    range: ["2025-07-01", "2025-11-01"], by: "orders.fraud_type", member: {},
  });
  expect(scopedQuery("q1", BASELINE, { member: { "orders.fraud_type": null } })).toEqual({
    reason: "A member with no value cannot be drilled or pinned",
  });
});

test("cubeDrill: the export breaks a member down from the cube", async () => {
  const load = cubeDrill(CUBE, { q1: LOSS });
  const v = await load({ ref: "q1", filters: [], range: ["2025-01-01", "2025-01-01"], by: "month", member: { "orders.sales_channel": "Web" } });
  expect(v.rows).toEqual([["2025-01-01", 350]]);
  await expect(load({ ref: "q9", filters: [], range: null, by: "month" })).rejects.toThrow("not available");
});

test("withPrevious: a bounded report compares with the period the cube carries before it", () => {
  // The server cube for a def bounded to Jul..Sep 2025 carries Apr..Sep 2025.
  const months = ["2025-04-01", "2025-05-01", "2025-06-01", "2025-07-01", "2025-08-01", "2025-09-01"];
  const cube: CubeView = { ...CUBE, rows: months.map((m, i) => ["App", m, 100 * (i + 1), 10, 1, 10, 1]), span: ["2025-04-01", "2025-10-01"] };
  const def: QueryDef = { metrics: ["fraud_loss"], dimensions: [], time: { start: "2025-07-01", end: "2025-10-01" } };
  const v = viewOf(withPrevious(cube, def, BASELINE));
  expect([...new Set(v.points.map((p) => p.label.split(" · ")[1]))]).toEqual(["Jul 2025 – Sep 2025", "Apr 2025 – Jun 2025"]);
  const binding: Binding = { ref: "q1", metric: "fraud_loss", keys: {}, share: false };
  // (400 + 500 + 600) against (100 + 200 + 300): +150.0%.
  expect(deltaUnderScope(cube, def, binding, BASELINE, "currency")).toBe("+150.0%");
});

test("derive: stored engine filters (in, not_in, an or group, is_null) keep the matching rows", () => {
  const CH = "orders.sales_channel";
  const rows = (filters: QueryDef["filters"]) =>
    viewOf(derive(CUBE, { metrics: ["order_count"], dimensions: [CH], filters }, BASELINE)).rows.map((r) => r[0]).sort();
  expect(rows([{ member: CH, op: "in", values: ["App", "Web"] }])).toEqual(["App", "Web"]);
  expect(rows([{ member: CH, op: "not_in", values: ["App", "Web"] }])).toEqual(["Marketplace", "Telesales"]);
  expect(rows([{ or: [{ member: CH, op: "eq", values: ["App"] }, { member: CH, op: "eq", values: ["Telesales"] }] }]))
    .toEqual(["App", "Telesales"]);
  expect(rows([{ member: CH, op: "is_null", values: [] }])).toEqual([]);
});
