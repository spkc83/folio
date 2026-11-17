/** The routed question as chips, and the pure edits that a chip makes to it. */
import type { QueryDef, QueryFilter, QueryFilterGroup, Vocabulary } from "../lib/api";
import { displayValue } from "./labels";
import { lastMonth, monthLabel, spanLabel } from "./scope";
import { labelOf } from "./VocabularyContext";

/** `fixed`: a chip that the page can show but not edit (a filter operator with no editor). */
export type Chip = { kind: "metric" | "by" | "filter" | "time"; key: string; label: string; value: string; fixed?: boolean };
/** The filter operators that a chip editor can set. */
export const EDITABLE_OPS = ["eq", "ne", "is_null"];
type Time = QueryDef["time"];

export const isFlat = (f: QueryFilter | QueryFilterGroup): f is QueryFilter => "member" in f;
export const flatFilters = (q: QueryDef): QueryFilter[] => (q.filters ?? []).filter(isFlat);

const capitalized = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
const SIGN: Record<string, string> = { eq: "=", ne: "≠", gt: ">", gte: "≥", lt: "<", lte: "≤" };

/** A filter's operator and values in words: "= East", "in East, West", "is empty". */
export function filterText(f: QueryFilter): string {
  const list = (values: unknown[]) => values.map((v) => displayValue(f.member, v)).join(", ");
  if (f.op in SIGN) return `${SIGN[f.op]} ${list(f.values)}`;
  if (f.op === "in") return `in ${list(f.values)}`;
  if (f.op === "not_in") return `not in ${list(f.values)}`;
  if (f.op === "is_null") return "is empty";
  if (f.op === "between") return `${displayValue(f.member, f.values[0])} – ${displayValue(f.member, f.values[1])}`;
  if (f.op === "contains") return `contains ${f.values.map(String).join(", ")}`;
  return `${f.op} ${list(f.values)}`;
}

/** The time range in words. `end` is exclusive. */
export function timeText(t: Time): string {
  const start = t?.start ?? null;
  const end = t?.end ?? null;
  if (start && end) return spanLabel(start, end);
  if (start) return `From ${monthLabel(start)}`;
  if (end) return `Through ${monthLabel(lastMonth(end))}`;
  return "All dates";
}

export function chipsOf(q: QueryDef, v: Vocabulary | null): Chip[] {
  const chips: Chip[] = q.metrics.map((m) => ({ kind: "metric", key: m, label: "Metric", value: labelOf(v, m) }));
  const by: Chip[] = (q.dimensions ?? []).map((d) => ({ kind: "by", key: d, label: "By", value: labelOf(v, d) }));
  if (q.time?.grain) by.push({ kind: "by", key: "grain", label: "By", value: capitalized(q.time.grain) });
  by.forEach((c, i) => chips.push(i === 0 ? c : { ...c, label: "And by" }));
  for (const f of flatFilters(q)) {
    chips.push({ kind: "filter", key: f.member, label: labelOf(v, f.member), value: filterText(f), fixed: !EDITABLE_OPS.includes(f.op) });
  }
  chips.push({ kind: "time", key: "time", label: "Dates", value: timeText(q.time) });
  return chips;
}

/** "Fraud loss · by Sales channel · Region = East · 2025". */
export function describeQuery(q: QueryDef, v: Vocabulary | null): string {
  return chipsOf(q, v).map((c) => {
    if (c.kind === "by") return `by ${c.value}`;
    if (c.kind === "filter") return `${c.label} ${c.value}`;
    return c.value;
  }).join(" · ");
}

/** A breakdown change: a top N or an order of the old breakdown means nothing for the new one. */
function rebrokenDown(q: QueryDef, change: Partial<QueryDef>): QueryDef {
  const { limit: _limit, order: _order, ...rest } = q;
  return { ...rest, ...change };
}

export function setMetric(q: QueryDef, id: string): QueryDef {
  const { order: _order, having: _having, ...rest } = q;
  return { ...rest, metrics: [id, ...q.metrics.slice(1).filter((m) => m !== id)] };
}

export function setDimension(q: QueryDef, id: string | null): QueryDef {
  return rebrokenDown(q, { dimensions: id ? [id] : [] });
}

function withTime(q: QueryDef, time: NonNullable<Time>): QueryDef {
  const kept = Object.fromEntries(Object.entries(time).filter(([, value]) => value !== null && value !== undefined));
  const asks = Boolean(kept.start || kept.end || kept.grain);
  return { ...q, time: asks ? kept : null };
}

export function setGrain(q: QueryDef, grain: string | null): QueryDef {
  return withTime(rebrokenDown(q, {}), { ...(q.time ?? {}), grain });
}

export function setFilter(q: QueryDef, member: string, op: string, values: QueryFilter["values"]): QueryDef {
  const filters = q.filters ?? [];
  const next = { member, op, values };
  const at = filters.findIndex((f) => isFlat(f) && f.member === member);
  return { ...q, filters: at < 0 ? [...filters, next] : filters.map((f, i) => (i === at ? next : f)) };
}

export function removeFilter(q: QueryDef, member: string): QueryDef {
  return { ...q, filters: (q.filters ?? []).filter((f) => !(isFlat(f) && f.member === member)) };
}

export function setTime(q: QueryDef, range: { start: string | null; end: string | null } | null): QueryDef {
  return withTime(q, { ...(q.time ?? {}), start: range?.start ?? null, end: range?.end ?? null });
}
