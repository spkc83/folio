/** Value formatting with the exact rules of the server's `format_value`. */

export const NO_DATA = "No data";

/**
 * Fixed decimals with Python's rounding. `toFixed` rounds an exact tie up; Python rounds it
 * to the even digit. `toFixed(100)` is the exact decimal expansion, so a tie shows there.
 */
function fixed(value: number, digits: number): string {
  const exact = Math.abs(value).toFixed(100);
  const cut = exact.indexOf(".") + 1 + digits;
  if (!/^50*$/.test(exact.slice(cut))) return value.toFixed(digits);
  const kept = exact.slice(0, digits ? cut : cut - 1);
  const sign = value < 0 ? "-" : "";
  if (Number(kept[kept.length - 1]) % 2 === 0) return sign + kept;
  return sign + (Number(kept) + 10 ** -digits).toFixed(digits);
}

export function formatValue(value: number | null | undefined, fmt: string): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return NO_DATA;
  if (fmt === "percent") return `${fixed(value * 100, 2)}%`;
  if (fmt === "count") return Math.trunc(value).toLocaleString("en-US");
  if (fmt === "currency") {
    const size = Math.abs(value);
    const sign = value < 0 ? "-" : "";
    if (size >= 1_000_000) return `${sign}$${fixed(size / 1_000_000, 1)}M`;
    if (size >= 1_000) return `${sign}$${fixed(size / 1_000, 1)}K`;
    return `${sign}$${fixed(size, 2)}`;
  }
  return String(value);
}

const signed = (v: number, digits: number) => `${v < 0 ? "-" : "+"}${Math.abs(v).toFixed(digits)}`;

/** The change from `prev` to `cur`: points for a rate, else a relative change. */
export function deltaText(cur: number | null, prev: number | null, fmt: string): string | null {
  if (cur === null || prev === null || !Number.isFinite(cur) || !Number.isFinite(prev)) return null;
  if (fmt === "percent") return `${signed((cur - prev) * 100, 2)} pts`;
  if (prev === 0) return null;
  return `${signed(((cur - prev) / Math.abs(prev)) * 100, 1)}%`;
}

/** The format that a formatted value shows, read the way `tickFormat` reads it. */
export function formatOf(sample: string): "currency" | "percent" | "count" {
  if (sample.startsWith("$") || sample.startsWith("-$")) return "currency";
  if (sample.endsWith("%")) return "percent";
  return "count";
}
