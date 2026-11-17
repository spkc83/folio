/** The query ref that a fact comes from: `q1_total.fraud_loss` -> `q1`. Same rule as Python `ref_of`. */
export function refOf(factId: string): string {
  return factId.split(".")[0].replace(/_total$/, "");
}

/** The query ref of a section: `s2` -> `q2`. Same rule as Python `ref_for`. */
export const sectionRef = (sectionId: string): string => `q${sectionId.slice(1)}`;

const TOKEN = /\{fact:([\w.\-+]+)\}/g;
const MAX_FACTS = 30;

/**
 * The facts a section can cite: tokens in its words, its tiles, then the facts of its charts' queries.
 * Same rule as Python `harness.context._fact_ids`.
 */
export function sectionFacts<F>(
  section: { blocks: ({ type: string; text?: string; fact_id?: string; query_ref?: string })[] },
  facts: Record<string, F>,
): string[] {
  const ids: string[] = [];
  const refs: string[] = [];
  for (const block of section.blocks) {
    if (block.type === "prose" && block.text) ids.push(...[...block.text.matchAll(TOKEN)].map((m) => m[1]));
    else if (block.type === "tile" && block.fact_id) ids.push(block.fact_id);
    else if (block.query_ref) refs.push(block.query_ref);
  }
  for (const ref of refs) ids.push(...Object.keys(facts).filter((f) => f.startsWith(`${ref}.`) || f.startsWith(`${ref}_total.`)));
  return [...new Set(ids)].filter((id) => id in facts).slice(0, MAX_FACTS);
}
