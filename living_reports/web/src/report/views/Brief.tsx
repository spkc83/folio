import type { Block, Section, Spec } from "../../lib/api";
import { claimsInOrder, ClaimsProvider, hasClaims, useClaims, useVerdict, VerdictLine } from "../claims";
import { CompareTable } from "../EvidenceDrawer";
import { RichText } from "../RichText";
import { Tile } from "../Tile";

type Action = Extract<Block, { type: "action" }>;

/** One claim as a card: its words with the citation, and its comparison table (top 4 rows and the claimed row). */
function ClaimCard({ id }: { id: string }) {
  const claim = useClaims().claims[id];
  if (!claim) return null;
  return (
    <article className="card claim-card">
      <VerdictLine id={id} />
      <p><RichText text={`{claim:${id}}`} /></p>
      <CompareTable claim={claim} limit={4} />
    </article>
  );
}

/** The decision first: each recommendation with a basis, its section, and why. Else the key points. */
function ClaimsBrief({ spec }: { spec: Spec }) {
  const known = spec.claims ?? {};
  const verdict = useVerdict();
  const actions: { action: Action; section: Section; basis: string[] }[] = spec.sections.flatMap((section) =>
    section.blocks.flatMap((b) => b.type === "action"
      ? [{ action: b, section, basis: [...new Set(b.basis ?? [])].filter((id) => id in known) }]
      : []),
  );
  const recs = actions.filter((a) => a.basis.length > 0);
  // An action with no known basis shows as before: bold, with no label.
  const plain = actions.filter((a) => a.basis.length === 0);
  const flag = (basis: string[]) => {
    const verdicts = basis.map(verdict);
    if (verdicts.includes("stale")) return "One of its reasons is no longer true with the new data";
    if (verdicts.includes("unchecked")) return "One of its reasons could not be checked";
    return null;
  };
  return (
    <>
      <div className="kicker">{actions.length ? "Decision needed" : "Key points"}</div>
      <h1 className="title"><RichText text={spec.title} /></h1>
      {recs.map(({ action, section, basis }, i) => (
        <section className="card rec-card" key={i} aria-labelledby={`rec-${i}`}>
          <span className="kicker" id={`rec-${i}`}>Recommendation</span>
          <p className="rec-text"><RichText text={action.text} /></p>
          {flag(basis) && <p className="rec-flag" role="status">{flag(basis)}</p>}
          <p className="meta">From: {section.heading}</p>
          <div className="kicker">Why</div>
          <div className="why-grid">{basis.map((id) => <ClaimCard key={id} id={id} />)}</div>
        </section>
      ))}
      {plain.map(({ action }, i) => <p className="prose" key={`plain-${i}`}><b><RichText text={action.text} /></b></p>)}
      {recs.length === 0 && (
        <div className="claim-list">{claimsInOrder(spec).slice(0, 3).map((id) => <ClaimCard key={id} id={id} />)}</div>
      )}
    </>
  );
}

export function Brief({ spec, onEvidence }: { spec: Spec; onEvidence: () => void }) {
  const blocks = spec.sections.flatMap((s) => s.blocks);
  const tiles = (
    <div className="tiles">
      {blocks.map((b, i) => b.type === "tile" && <Tile key={i} block={b} />)}
    </div>
  );
  const evidence = <button type="button" className="chip" onClick={onEvidence}>See the evidence</button>;
  if (hasClaims(spec)) {
    return (
      <div className="narrow">
        <ClaimsProvider spec={spec}>
          <ClaimsBrief spec={spec} />
          {tiles}
          {evidence}
        </ClaimsProvider>
      </div>
    );
  }
  const actions = blocks.filter((b) => b.type === "action");
  return (
    <div className="narrow">
      <div className="kicker">{actions.length ? "Decision needed" : "Key points"}</div>
      <h1 className="title"><RichText text={spec.title} /></h1>
      {actions.map((a, i) => <p className="prose" key={i}><b><RichText text={a.type === "action" ? a.text : ""} /></b></p>)}
      {tiles}
      {evidence}
    </div>
  );
}
