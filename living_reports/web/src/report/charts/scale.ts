import type { Point } from "../../lib/api";

const NICE = [1, 2, 2.5, 5, 10];
const clean = (v: number) => Number(v.toPrecision(12));

/** Maps a domain onto a range. A flat domain maps every value to the range start. */
export function linear(domain: [number, number], range: [number, number]) {
  const [d0, d1] = domain;
  const [r0, r1] = range;
  const span = d1 - d0;
  return (v: number) => (span === 0 ? r0 : r0 + ((v - d0) / span) * (r1 - r0));
}

/** Axis ticks from zero: a nice step, up to the first multiple at or above ``max``. */
export function niceTicks(max: number, count = 4): number[] {
  if (!Number.isFinite(max) || max <= 0) return [0, 1];
  const raw = max / count;
  const power = 10 ** Math.floor(Math.log10(raw));
  const step = clean(NICE.find((n) => n * power >= raw - 1e-12)! * power);
  const ticks = [0];
  while (ticks[ticks.length - 1] < max - 1e-12) ticks.push(clean(ticks.length * step));
  return ticks;
}

const trim = (v: number) => String(clean(Number(v.toFixed(2))));

/** Formats tick values the way the chart's own values are formatted. */
export function tickFormat(points: Point[]): (v: number) => string {
  const sample = points.find((p) => typeof p.value === "number")?.formatted ?? "";
  if (sample.startsWith("$") || sample.startsWith("-$")) {
    return (v) => {
      const size = Math.abs(v);
      const sign = v < 0 ? "-" : "";
      if (size >= 1e6) return `${sign}$${trim(size / 1e6)}M`;
      if (size >= 1e3) return `${sign}$${trim(size / 1e3)}K`;
      return `${sign}$${trim(size)}`;
    };
  }
  if (sample.endsWith("%")) return (v) => `${trim(v * 100)}%`;
  return (v) => clean(v).toLocaleString("en-US");
}

/** Ticks for values on both sides of zero: one nice step, always with a zero tick. */
export function signedTicks(min: number, max: number, count = 4): number[] {
  if (!Number.isFinite(min) || !Number.isFinite(max)) return [0, 1];
  if (min >= 0) return niceTicks(max, count);
  const base = niceTicks(Math.max(Math.abs(min), Math.abs(max)), count);
  const step = base[1];
  const ticks: number[] = [];
  for (let k = Math.floor(min / step); k <= Math.max(Math.ceil(max / step), 0); k += 1) ticks.push(clean(k * step));
  return ticks;
}
