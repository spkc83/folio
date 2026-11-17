import type { Point, QueryView } from "../../lib/api";
import { displayValue } from "../labels";
import { SLOTS, slotColor } from "./palette";

const ISO = /^\d{4}-\d{2}-\d{2}$/;

/** An x value in words: an ISO date is "Jan 2024" (or "30 Nov 2025" off day 1). */
export function periodLabel(label: string): string {
  return ISO.test(label) ? displayValue("", label) : label;
}

/** `name` is the raw key that a scope filters on; `label` is the name in words. */
export type Series = { name: string; label: string; color: string; points: (Point | undefined)[] };

export const num = (p?: Point) => (p && typeof p.value === "number" && Number.isFinite(p.value) ? p.value : null);

const OTHER = "Other";

/**
 * Where the series name sits in a point label (`West · bust_out · 2024-01-01`). The server
 * names one series column for a date and one member. With more members, every member part
 * that varies names the series, and the date is the x axis. A part with one value (a
 * filtered member, such as West) names nothing.
 */
function seriesParts(query: QueryView, parts: string[][]): { at: number[]; x: number[] } {
  const width = parts[0]?.length ?? 0;
  const all = Array.from({ length: width }, (_, i) => i);
  if (query.series) {
    const at = query.columns.indexOf(query.series);
    return at < 0 ? { at: [], x: all } : { at: [at], x: all.filter((i) => i !== at) };
  }
  const dated = all.filter((i) => parts.every((p) => ISO.test(p[i] ?? "")));
  if (width < 2 || dated.length !== 1) return { at: [], x: all };
  const varies = all.filter((i) => i !== dated[0] && new Set(parts.map((p) => p[i])).size > 1);
  return { at: varies, x: dated };
}

/**
 * Splits points into an x axis and series. At most `max` series show, and at most one per
 * color slot: past that, the series with the largest totals stay (in their order) and the
 * rest fold into Other.
 */
export function shape(query: QueryView, max = 4): { xs: string[]; series: Series[] } {
  const parts = query.points.map((p) => p.label.split(" · "));
  const { at, x } = seriesParts(query, parts);
  const xOf = (i: number) => x.map((k) => parts[i][k]).join(" · ");
  const nameOf = (i: number) => at.map((k) => parts[i][k] ?? "").join(" · ");
  const labelOf = (i: number) => at.map((k) => displayValue(query.columns[k] ?? "", parts[i][k] ?? "")).join(" · ");
  let xs = [...new Set(query.points.map((_, i) => xOf(i)))];
  if (xs.every((v) => ISO.test(v))) xs = xs.sort();
  const names = [...new Set(query.points.map((_, i) => nameOf(i)))];
  const room = Math.min(names.length > max ? max - 1 : names.length, SLOTS);
  let kept = names;
  if (room < names.length) {
    const total = new Map<string, number>();
    query.points.forEach((p, i) => total.set(nameOf(i), (total.get(nameOf(i)) ?? 0) + Math.abs(num(p) ?? 0)));
    const top = new Set([...names].sort((a, b) => (total.get(b) ?? 0) - (total.get(a) ?? 0)).slice(0, room));
    kept = names.filter((n) => top.has(n));
  }
  const labels = new Map<string, string>();
  const groups = new Map<string, Point[]>(kept.map((n) => [n, []]));
  const xsOf = new Map<Point, string>();
  query.points.forEach((p, i) => {
    const name = kept.includes(nameOf(i)) ? nameOf(i) : OTHER;
    if (!groups.has(name)) groups.set(name, []);
    groups.get(name)!.push(p);
    xsOf.set(p, xOf(i));
    if (!labels.has(name)) labels.set(name, name === OTHER ? OTHER : labelOf(i));
  });
  const series = [...groups].map(([name, ps]) => {
    const byX = new Map<string, Point>();
    for (const p of ps) {
      const key = xsOf.get(p)!;
      const prev = byX.get(key);
      if (prev && name === OTHER) {
        byX.set(key, { ...p, value: (num(prev) ?? 0) + (num(p) ?? 0), formatted: "" });
      } else {
        byX.set(key, p);
      }
    }
    const color = name === OTHER ? "var(--gray)" : slotColor(name, kept);
    return { name, label: labels.get(name) ?? name, color, points: xs.map((v) => byX.get(v)) };
  });
  return { xs, series };
}

/**
 * The x labels to draw so that no two overlap: every k-th label, with the first and the
 * last. `width` is the plot width in px; a character is about `charPx` wide.
 */
export function tickIndexes(labels: string[], width: number, charPx = 6.6, gap = 8): number[] {
  const n = labels.length;
  if (n <= 1) return n === 1 ? [0] : [];
  const step = width / (n - 1);
  const widest = Math.max(...labels.map((l) => l.length)) * charPx + gap;
  const every = Math.max(1, Math.ceil(widest / step));
  const out: number[] = [];
  for (let i = 0; i < n; i += every) out.push(i);
  const tail = out[out.length - 1];
  if (tail !== n - 1) {
    if ((n - 1 - tail) * step >= widest) out.push(n - 1);
    else if (out.length > 1) out[out.length - 1] = n - 1;
  }
  return out;
}
