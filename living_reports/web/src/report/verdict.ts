import type { Claim, FactT } from "../lib/api";

export const CHECKED = "✓ Checked against the data";
export const STALE = "No longer true with the new data";
export const UNCHECKED = "Not checked: a value is missing";

export type Verdict = "checked" | "stale" | "unchecked";

/** A claim is checked only when the data still supports it and every value it cites is there. */
export function verdictOf(claim: Claim, facts: Record<string, FactT>, stale: boolean): Verdict {
  if (stale) return "stale";
  // A claim that cites no values has nothing to check.
  const ids = claim.facts ?? [];
  return ids.length > 0 && ids.every((f) => facts[f]?.state === "ok") ? "checked" : "unchecked";
}

export const VERDICT_TEXT: Record<Verdict, string> = { checked: CHECKED, stale: STALE, unchecked: UNCHECKED };
