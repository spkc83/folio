import type { Point, QueryView } from "../../lib/api";

export const money = (v: number) => `$${(v / 1e6).toFixed(1)}M`;
export const pts = (rows: [string, number | null][], fmt: (v: number) => string = money): Point[] =>
  rows.map(([label, value], i) => ({
    label, value, formatted: value === null ? "No data" : fmt(value), fact_id: `q1.fraud_loss.${i}`,
  }));
export const view = (points: Point[], extra: Partial<QueryView> = {}): QueryView => ({
  columns: ["sales_channel", "fraud_loss"], rows: points.map((p) => [p.label, p.value]),
  query_hash: "h", points, ...extra,
});

export const CHANNELS = view(pts([
  ["App", 15_609_548], ["Web", 10_053_341], ["Telesales", 10_044_517],
  ["Partner", 8_292_885], ["Retail", 8_268_225], ["Marketplace", 319_143],
]));

export const MONTHLY = [1302985, 1445190, 1558723, 1481456, 1695875, 1685984, 1505746, 1559674, 1624144,
  1627782, 1986449, 2092738, 1736240, 1719400, 2449724, 2350234, 2168561, 2323449, 3083730,
  3577319, 2691581, 2887015, 4277772, 3755887];
export const month = (i: number) => `${2024 + Math.floor(i / 12)}-${String((i % 12) + 1).padStart(2, "0")}-01`;
export const LINE = view(pts(MONTHLY.map((v, i) => [month(i), v])), { columns: ["month", "fraud_loss"] });
