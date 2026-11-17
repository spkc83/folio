/** The reader's scope: a month range and member filters. One state for the whole report. */
import { displayValue } from "./labels";

export type ScopeFilter = { dimension: string; value: string | null };
export type Scope = { range: [string, string] | null; filters: ScopeFilter[] };

export const BASELINE: Scope = { range: null, filters: [] };
export const NO_VALUE = "No value";

/** Selects a member. The same member again removes the filter. */
export function withMember(scope: Scope, dimension: string, value: string | null): Scope {
  const current = scope.filters.find((f) => f.dimension === dimension);
  const others = scope.filters.filter((f) => f.dimension !== dimension);
  if (current && current.value === value) return { ...scope, filters: others };
  return { ...scope, filters: [...others, { dimension, value }] };
}

/** Removes the filter on one dimension. */
export function withoutMember(scope: Scope, dimension: string): Scope {
  return { ...scope, filters: scope.filters.filter((f) => f.dimension !== dimension) };
}

/** Sets an inclusive month range. The earlier month comes first. */
export function withRange(scope: Scope, a: string, b: string): Scope {
  return { ...scope, range: a <= b ? [a, b] : [b, a] };
}

export const reset = (): Scope => BASELINE;
export const isBaseline = (scope: Scope) => scope.range === null && scope.filters.length === 0;

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** The month after an ISO month (`YYYY-MM-01`). */
export function nextMonth(month: string, step = 1): string {
  const [y, m] = month.split("-").map(Number);
  const index = y * 12 + (m - 1) + step;
  return `${Math.floor(index / 12)}-${String((index % 12) + 1).padStart(2, "0")}-01`;
}

/** "Dec 2024" for an ISO month. */
export function monthLabel(month: string): string {
  const [y, m] = month.split("-").map(Number);
  return `${MONTHS[m - 1]} ${y}`;
}

/**
 * The month (`YYYY-MM-01`) of the last day before an exclusive `end`. An old stored end on a
 * month's last day (`2025-11-30`) meant "through that day": its month is its own.
 */
export function lastMonth(end: string): string {
  return end.endsWith("-01") ? nextMonth(end, -1) : `${end.slice(0, 7)}-01`;
}

/** "2025" for a calendar year, else "Jul 2025 – Nov 2025". `end` is exclusive. */
export function spanLabel(start: string, end: string): string {
  const [y, m] = start.split("-").map(Number);
  if (m === 1 && end === `${y + 1}-01-01`) return String(y);
  const last = lastMonth(end);
  if (last === `${start.slice(0, 7)}-01`) return monthLabel(start);
  return `${monthLabel(start)} – ${monthLabel(last)}`;
}

/** A member value as a key (raw), or in words when the member is given ("Bust-out"). */
export const valueText = (value: string | null, member?: string) => {
  if (value === null) return NO_VALUE;
  return member === undefined ? value : displayValue(member, value);
};

/** "Region" for "dealers.region": the last part of an id in words. */
export function idWords(id: string): string {
  const words = (id.split(".").pop() ?? id).replace(/_/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** The scope in words: "Sales channel is Telesales · Jul 2025 – Nov 2025". */
export function describe(scope: Scope, labels: Record<string, string>): string {
  const parts = scope.filters.map((f) => `${labels[f.dimension] ?? idWords(f.dimension)} is ${valueText(f.value, f.dimension)}`);
  if (scope.range) parts.push(spanLabel(scope.range[0], nextMonth(scope.range[1])));
  return parts.join(" · ");
}

/** The body of the drill and pin routes. Same shape as the server's `ScopedQuery`. */
export type ScopedQuery = {
  ref: string;
  filters: { member: string; op: "eq"; values: string[] }[];
  range: [string, string] | null;
  by?: string | null;
  member?: Record<string, string>;
};

const NULL_MEMBER = "A member with no value cannot be drilled or pinned";

export function scopedQuery(
  ref: string, scope: Scope, opts: { by?: string; member?: Record<string, string | null> } = {},
): ScopedQuery | { reason: string } {
  const member = opts.member ?? {};
  if (scope.filters.some((f) => f.value === null) || Object.values(member).some((v) => v === null)) {
    return { reason: NULL_MEMBER };
  }
  return {
    ref,
    filters: scope.filters.map((f) => ({ member: f.dimension, op: "eq", values: [f.value as string] })),
    range: scope.range,
    by: opts.by ?? null,
    member: member as Record<string, string>,
  };
}
