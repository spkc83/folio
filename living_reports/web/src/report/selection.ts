/** What the reader pointed at, as the ask request's selection and as words for the ask bar. */
import type { Selection } from "../lib/api";
import { describe, isBaseline, type Scope } from "./scope";

export function selectionOf(scope: Scope, sectionId: string | null, factId: string | null): Selection | null {
  if (isBaseline(scope) && !sectionId && !factId) return null;
  return {
    filters: scope.filters.map((f) => (f.value === null
      ? { member: f.dimension, op: "is_null", values: [] }
      : { member: f.dimension, op: "eq", values: [f.value] })),
    range: scope.range,
    section_id: sectionId,
    fact_id: factId,
  };
}

export function describeSelection(scope: Scope, labels: Record<string, string>, sectionHeading: string | null, factLabel: string | null): string {
  return [factLabel, describe(scope, labels), sectionHeading].filter((part) => part).join(" · ");
}
