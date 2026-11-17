/**
 * Deterministic re-aggregation over the report's cube. No server call, no model call.
 *
 * The cube carries each metric as additive components. A sum or a count adds its `num`
 * column. A ratio is `sum(num) / sum(den)`, so a fraud rate under a filter is the rate of
 * the filtered rows, never the mean of member rates. Other metrics give a reason instead.
 */
import type { Binding, CubeView, MetricAgg, Point, QueryDef, QueryFilter, QueryFilterGroup, QueryView } from "../lib/api";
import { deltaText, formatValue, NO_DATA } from "./charts/format";
import { lastMonth, monthLabel, nextMonth, spanLabel, valueText, type Scope, type ScopedQuery, type ScopeFilter } from "./scope";

export type Derived = { view: QueryView } | { reason: string };
type Opts = { groupBy?: string[]; extra?: ScopeFilter[]; metric?: string };

const notAggregable = (agg: MetricAgg) => `${agg.label} cannot be re-aggregated`;

type Item = QueryFilter | QueryFilterGroup;
const isGroup = (f: Item): f is QueryFilterGroup => !("member" in f);

/** One stored filter on one cube row, with SQL semantics: a NULL matches only is_null. */
function filterMatches(f: QueryFilter, value: unknown): boolean {
  if (f.op === "is_null") return value === null || value === undefined;
  if (value === null || value === undefined) return false;
  const [a, b] = f.values;
  const v = value as string | number;
  switch (f.op) {
    case "eq": return v === a;
    case "ne": return v !== a;
    case "in": return f.values.includes(v);
    case "not_in": return !f.values.includes(v);
    case "gt": return v > (a as string | number);
    case "gte": return v >= (a as string | number);
    case "lt": return v < (a as string | number);
    case "lte": return v <= (a as string | number);
    case "between": return v >= (a as string | number) && v <= (b as string | number);
    case "contains": return String(v).includes(String(a));
    default: return false;
  }
}

function itemMatches(f: Item, row: unknown[], col: (name: string) => number): boolean {
  if (!isGroup(f)) return filterMatches(f, row[col(f.member)]);
  if (f.and) return f.and.every((x) => itemMatches(x, row, col));
  return (f.or ?? []).some((x) => itemMatches(x, row, col));
}

function membersOf(items: Item[]): string[] {
  return items.flatMap((f) => (isGroup(f) ? membersOf([...(f.and ?? []), ...(f.or ?? [])]) : [f.member]));
}

/** The def's grouping in the cube: its member dimensions, then month for a monthly series. */
export function groupOf(def: QueryDef): string[] {
  return [...(def.dimensions ?? []), ...(def.time?.grain === "month" ? ["month"] : [])];
}

function compute(agg: MetricAgg, num: number | null, den: number | null, rows: number): number | null {
  if (agg.kind === "ratio") return den === null || den === 0 || num === null ? null : num / den;
  if (agg.kind === "count") return num ?? (rows === 0 ? 0 : null);
  return num;
}

const addNullable = (a: number | null, b: unknown) =>
  typeof b === "number" && Number.isFinite(b) ? (a ?? 0) + b : a;

function compare(a: unknown, b: unknown): number {
  if (a === b) return 0;
  if (a === null || a === undefined) return 1; // NULLS LAST, as DuckDB orders
  if (b === null || b === undefined) return -1;
  return String(a) < String(b) ? -1 : 1;
}

/** The def's query rebuilt from the cube under the scope. */
export function derive(cube: CubeView, def: QueryDef, scope: Scope, opts: Opts = {}): Derived {
  const metrics = opts.metric ? [opts.metric] : def.metrics;
  const aggs = metrics.map((m) => cube.metrics[m]);
  if (aggs.some((a) => a === undefined)) return { reason: "The report's cells do not carry this metric." };
  const other = aggs.find((a) => a.kind === "other");
  if (other) return { reason: notAggregable(other) };
  if (def.time?.grain && def.time.grain !== "month") return { reason: "The report's cells are monthly." };
  if (def.having?.length) return { reason: "A query that tests metric values cannot be re-aggregated." };
  const groupBy = opts.groupBy ?? groupOf(def);
  const col = (name: string) => cube.columns.indexOf(name);
  const missing = [...groupBy, ...membersOf(def.filters ?? []), ...scope.filters.map((f) => f.dimension)]
    .filter((d) => col(d) < 0);
  if (missing.length) return { reason: `The report's cells do not carry ${missing.join(", ")}.` };

  const month = col("month");
  const start = def.time?.start ?? null, end = def.time?.end ?? null;
  const scoped = [...scope.filters, ...(opts.extra ?? [])].map((f) => [col(f.dimension), f.value] as const);
  const low = scope.range?.[0] ?? null;
  const high = scope.range ? nextMonth(scope.range[1]) : null;
  const keyCols = groupBy.map(col);
  const parts = aggs.map((a) => [col(a.num), a.den ? col(a.den) : -1] as const);

  type Group = { keys: unknown[]; num: (number | null)[]; den: (number | null)[]; rows: number };
  const groups = new Map<string, Group>();
  for (const row of cube.rows) {
    const m = row[month] as string;
    if (start && m < start) continue;
    if (end && m >= end) continue;
    if (low && (m < low || m >= high!)) continue;
    if (!(def.filters ?? []).every((f) => itemMatches(f, row, col))) continue;
    if (!scoped.every(([i, v]) => (row[i] ?? null) === v)) continue;
    const keys = keyCols.map((i) => row[i] ?? null);
    const id = JSON.stringify(keys);
    let g = groups.get(id);
    if (!g) {
      g = { keys, num: aggs.map(() => null), den: aggs.map(() => null), rows: 0 };
      groups.set(id, g);
    }
    g.rows += 1;
    parts.forEach(([n, d], k) => {
      g!.num[k] = addNullable(g!.num[k], row[n]);
      if (d >= 0) g!.den[k] = addNullable(g!.den[k], row[d]);
    });
  }
  // With no grouping, SQL returns one row even when no row matches.
  if (groupBy.length === 0 && groups.size === 0) {
    groups.set("[]", { keys: [], num: aggs.map(() => null), den: aggs.map(() => null), rows: 0 });
  }

  let out = [...groups.values()].map((g) => ({
    keys: g.keys,
    values: aggs.map((a, k) => compute(a, g.num[k], g.den[k], g.rows)),
  }));
  const byKeys = (x: typeof out[number], y: typeof out[number]) => {
    for (let i = 0; i < x.keys.length; i += 1) {
      const c = compare(x.keys[i], y.keys[i]);
      if (c) return c;
    }
    return 0;
  };
  if (groupBy.includes("month")) {
    // The engine orders a series by month first, then by the other members.
    const at = groupBy.indexOf("month");
    out.sort((x, y) => compare(x.keys[at], y.keys[at]) || byKeys(x, y));
  } else {
    out.sort((x, y) => {
      const a = x.values[0], b = y.values[0];
      if (a !== b) {
        if (a === null) return 1;
        if (b === null) return -1;
        return b - a;
      }
      return byKeys(x, y);
    });
  }
  if (def.limit) out = out.slice(0, def.limit);

  const others = groupBy.filter((d) => d !== "month");
  const series = groupBy.length === 2 && groupBy.includes("month") && out.length > 0 ? others[0] : null;
  const ref = "derived";
  const points: Point[] = out.map((r, i) => ({
    label: r.keys.map((k) => valueText(k as string | null)).join(" · "),
    value: r.values[0],
    formatted: formatValue(r.values[0], aggs[0].format),
    fact_id: `${ref}.${i}`,
  }));
  return {
    view: {
      columns: [...groupBy, ...metrics],
      rows: out.map((r) => [...r.keys, ...r.values]),
      query_hash: cube.query_hash,
      points,
      series,
      labels: { ...cube.labels, ...Object.fromEntries(metrics.map((m) => [m, cube.metrics[m].label])) },
    },
  };
}

/** The value of one fact under the scope: its member keys become filters. */
export function tileValue(
  cube: CubeView, def: QueryDef, binding: Binding, scope: Scope,
): { value: number | null; formatted: string } | { reason: string } {
  const extra = Object.entries(binding.keys).map(([dimension, value]) => ({ dimension, value }));
  const part = derive(cube, def, scope, { groupBy: [], extra, metric: binding.metric });
  if ("reason" in part) return part;
  const value = part.view.points[0]?.value as number | null ?? null;
  const agg = cube.metrics[binding.metric];
  if (!binding.share) return { value, formatted: formatValue(value, agg.format) };
  const whole = derive(cube, def, scope, { groupBy: [], metric: binding.metric });
  if ("reason" in whole) return whole;
  const total = whole.view.points[0]?.value as number | null ?? null;
  const share = value === null || total === null || total === 0 ? null : value / total;
  return { value: share, formatted: formatValue(share, "percent") };
}

/** Distinct values of one dimension, in cube order. The full list keeps slot colors stable. */
export function members(cube: CubeView, dimension: string): (string | null)[] {
  const i = cube.columns.indexOf(dimension);
  return i < 0 ? [] : [...new Set(cube.rows.map((r) => (r[i] ?? null) as string | null))];
}

/** The cube's months, ascending. */
export function months(cube: CubeView): string[] {
  return members(cube, "month").filter((m): m is string => m !== null).sort();
}

type Periods = { cur: [string, string]; prev: [string, string]; n: number };

/** The scope's range, else the def's dates, else the last 12 months. Months inclusive. */
function periodsOf(cube: CubeView, def: QueryDef, scope: Scope): Periods | null {
  let first: string, last: string;
  if (scope.range) [first, last] = scope.range;
  else if (def.time?.start && def.time?.end) [first, last] = [def.time.start, lastMonth(def.time.end)];
  else {
    const all = months(cube);
    if (!all.length) return null;
    last = all[all.length - 1];
    first = nextMonth(last, -11);
  }
  const n = monthsBetween(first, last) + 1;
  return { cur: [first, last], prev: [nextMonth(first, -n), nextMonth(first, -1)], n };
}

function monthsBetween(a: string, b: string): number {
  const [ay, am] = a.split("-").map(Number);
  const [by, bm] = b.split("-").map(Number);
  return (by - ay) * 12 + (bm - am);
}

const unbounded = (def: QueryDef): QueryDef => ({ ...def, time: def.time?.grain ? { grain: def.time.grain } : null });

/** The scope's period against the period of the same length before it, as two series. */
export function withPrevious(cube: CubeView, def: QueryDef, scope: Scope): Derived {
  const p = periodsOf(cube, def, scope);
  const all = months(cube);
  if (!p || !all.length) return { reason: NO_DATA };
  if (p.prev[0] < all[0]) return { reason: `No data before ${monthLabel(all[0])}` };
  const metric = def.metrics[0];
  const flat = unbounded(def);
  const names = [spanLabel(p.cur[0], nextMonth(p.cur[1])), spanLabel(p.prev[0], nextMonth(p.prev[1]))];
  const points: Point[] = [];
  const rows: unknown[][] = [];
  for (const [k, [first, last]] of [p.cur, p.prev].entries()) {
    const d = derive(cube, flat, { ...scope, range: [first, last] }, { groupBy: ["month"], metric });
    if ("reason" in d) return d;
    const byMonth = new Map(d.view.points.map((pt) => [pt.label, pt]));
    for (let i = 0; i < p.n; i += 1) {
      const pt = byMonth.get(nextMonth(first, i));
      const x = `Month ${i + 1}`;
      const value = (pt?.value ?? null) as number | null;
      points.push({ label: `${x} · ${names[k]}`, value, formatted: pt?.formatted ?? NO_DATA, fact_id: `derived.${k}.${i}` });
      rows.push([x, names[k], value]);
    }
  }
  return { view: { columns: ["month", "period", metric], rows, query_hash: cube.query_hash, points, series: "period" } };
}

/** The change of one fact between the scope's period and the period before. */
export function deltaUnderScope(cube: CubeView, def: QueryDef, binding: Binding, scope: Scope, fmt: string): string | null {
  const p = periodsOf(cube, def, scope);
  const all = months(cube);
  if (!p || !all.length || p.prev[0] < all[0]) return null;
  const flat = unbounded(def);
  const at = (range: [string, string]) => {
    const v = tileValue(cube, flat, binding, { ...scope, range });
    return "reason" in v ? null : v.value;
  };
  return deltaText(at(p.cur), at(p.prev), fmt);
}

/** The drill loader of the exported file: the same breakdown, from the cube it carries. */
export function cubeDrill(cube: CubeView, defs: Record<string, QueryDef>) {
  return (sq: ScopedQuery): Promise<QueryView> => {
    const def = defs[sq.ref];
    if (!def || !sq.by) return Promise.reject(new Error("This breakdown is not available in the exported file."));
    const scope: Scope = { range: sq.range, filters: sq.filters.map((f) => ({ dimension: f.member, value: f.values[0] as string })) };
    const extra = Object.entries(sq.member ?? {}).map(([dimension, value]) => ({ dimension, value }));
    const got = derive(cube, def, scope, { groupBy: [sq.by], extra });
    return "reason" in got ? Promise.reject(new Error(got.reason)) : Promise.resolve(got.view);
  };
}
