import type { ReactNode } from "react";
import type { Section, Spec } from "../../lib/api";
import { Chart, isChart } from "../Chart";
import { ClaimsProvider } from "../claims";
import { Cite, RichText } from "../RichText";

/** Places where the hosted draft adds its asking and editing controls. The export passes none. */
export type SectionSlots = {
  /** The section heading row. Without it, sections after the first show a plain heading. */
  head?: (section: Section, n: number) => ReactNode;
  /** The routed question as chips: under the title for the first section, under the heading after. */
  chips?: (section: Section) => ReactNode;
  /** Replaces the section's paragraphs when it returns a node (the wording editor). */
  prose?: (section: Section) => ReactNode | null;
  /** After the charts: follow-ups and feedback. */
  after?: (section: Section) => ReactNode;
  /** Under each chart: its block commands. `index` is the block's place in `section.blocks`. */
  block?: (section: Section, index: number, title: string) => ReactNode;
};

export const CODE_NOTE = "Written by code: the model's draft did not pass the checks.";

/** Who wrote the words, and whether the new data still supports them. */
function SectionNotes({ section, onRewrite }: { section: Section; onRewrite?: (sectionId: string) => void }) {
  return (
    <>
      {section.narration === "code" && <p className="meta narration-note">{CODE_NOTE}</p>}
      {(section.stale_claims?.length ?? 0) > 0 && (
        <div className="warn stale-banner" role="status">
          <p>Some sentences in this section are no longer true with the new data.</p>
          {onRewrite && <button type="button" className="chip" onClick={() => onRewrite(section.id)}>Rewrite this section</button>}
        </div>
      )}
    </>
  );
}

/** An action that cites the claims it follows from. An action with no basis is not shown, as before. */
function Recommendations({ section }: { section: Section }) {
  return (
    <>
      {section.blocks.map((b, i) => b.type === "action" && (b.basis?.length ?? 0) > 0 && (
        <div className="recommendation" role="note" aria-labelledby={`rec-${section.id}-${i}`} key={i}>
          <span className="kicker" id={`rec-${section.id}-${i}`}>Recommendation</span>
          <p className="prose"><RichText text={b.text} />{[...new Set(b.basis)].map((id) => <Cite key={id} id={id} />)}</p>
        </div>
      ))}
    </>
  );
}

type Props = {
  spec: Spec;
  panelFor: (sectionId: string) => ReactNode;
  slots?: SectionSlots;
  /** Writes a section's words again. Offered when the new data no longer supports a claim. */
  onRewrite?: (sectionId: string) => void;
};

export function Narrative({ spec, panelFor, slots = {}, onRewrite }: Props) {
  return (
    <ClaimsProvider spec={spec}>
    <div className="narrow">
      <div className="kicker">{spec.kicker}</div>
      <h1 className="title"><RichText text={spec.title} /></h1>
      {spec.sections.map((section, n) => (
        <section key={section.id}>
          {n === 0 && slots.chips?.(section)}
          {slots.head ? slots.head(section, n) : n > 0 && <h2 className="heading">{section.heading}</h2>}
          {n > 0 && slots.chips?.(section)}
          <SectionNotes section={section} onRewrite={onRewrite} />
          {slots.prose?.(section) ?? section.blocks.filter((b) => b.type === "prose").map((b, i) => (
            <p className="prose" key={i}><RichText text={b.type === "prose" ? b.text : ""} /></p>
          ))}
          {panelFor(section.id)}
          {section.blocks.map((b, i) => (isChart(b) ? (
            <div className="block" key={`${b.type}:${b.query_ref}`}>
              <Chart block={b} />
              {slots.block?.(section, i, b.title)}
            </div>
          ) : null))}
          <Recommendations section={section} />
          {section.decisions.map((d) => <p className="decision" key={d}>{d}</p>)}
          {slots.after?.(section)}
        </section>
      ))}
    </div>
    </ClaimsProvider>
  );
}
