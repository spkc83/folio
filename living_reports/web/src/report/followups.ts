/** Follow-up questions built from the vocabulary, never from the model. */
import type { QueryDef, QueryView, Vocabulary } from "../lib/api";
import { flatFilters, setDimension, setFilter, setGrain } from "./chips";
import { displayParts } from "./labels";
import { labelOf } from "./VocabularyContext";

export type FollowUp = { label: string; query?: QueryDef; compare?: true };

/** ``stored``: the section's follow-ups, ranked on the server (code makes them, a decision model
 * orders them). Without them, the fixed rule below applies. */
export function followUps(def: QueryDef, v: Vocabulary, view: QueryView | undefined, hasLine: boolean,
  stored?: { label: string; query: QueryDef }[]): FollowUp[] {
  if (stored?.length) {
    const ranked: FollowUp[] = stored.map((f) => ({ label: f.label, query: f.query }));
    return (hasLine ? [...ranked, { label: "Compare with previous period", compare: true as const }] : ranked).slice(0, 4);
  }
  const used = new Set([...(def.dimensions ?? []), ...flatFilters(def).map((f) => f.member)]);
  const unused = (v.dimensions ?? []).filter((d) => !d.is_time && !used.has(d.id));
  const out: FollowUp[] = [];
  if (unused[0]) out.push({ label: `By ${unused[0].label}`, query: setDimension(def, unused[0].id) });
  if (!def.time?.grain) out.push({ label: "Over time", query: setGrain(setDimension(def, null), "month") });
  if (hasLine) out.push({ label: "Compare with previous period", compare: true });
  const dim = def.dimensions?.[0];
  const top = view?.points[0];
  if (dim && top && unused[0]) {
    const column = view.columns.indexOf(dim);
    const raw = column >= 0 ? view.rows[0]?.[column] : top.label;
    // A member with no value is the NULL member: it filters with is_null, not with its label.
    const filtered = raw === null || raw === undefined
      ? setFilter(def, dim, "is_null", [])
      : setFilter(def, dim, "eq", [raw as string | number | boolean]);
    out.push({ label: `${displayParts(top.label, view.columns)} by ${labelOf(v, unused[0].id)}`, query: setDimension(filtered, unused[0].id) });
  }
  return out.slice(0, 4);
}
