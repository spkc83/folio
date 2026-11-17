import { useContext } from "react";
import type { FactT, Section, Spec } from "../../lib/api";
import { claimsInOrder, ClaimsProvider, hasClaims, OPENER, sectionClaims, useClaims, useVerdict, VerdictLine } from "../claims";
import { FactsContext } from "../context";
import { useMetricLabel } from "../EvidenceDrawer";
import { omissionText, wordingText } from "../review";
import { RichText } from "../RichText";

const traced = (f: FactT) => f.state === "ok" && Boolean(f.query_hash);

/** Every value with its metric version, data, and query. */
function FactTable() {
  const facts = Object.values(useContext(FactsContext));
  return (
    <>
      <div className="kicker">Evidence</div>
      <h1 className="title">Where each number comes from</h1>
      <div className="card table-scroll">
        <table className="data">
          <thead>
            <tr><th>Claim</th><th>Value</th><th>Metric</th><th>Source</th><th>Query</th><th>Check</th></tr>
          </thead>
          <tbody>
            {facts.map((f) => (
              <tr key={f.id}>
                <td>{f.label}</td>
                <td className="num">{f.formatted}</td>
                <td>{f.metric ? `${f.metric} v${f.metric_version}` : "None"}</td>
                <td>{f.source_id}</td>
                <td><code>{f.query_hash.slice(0, 8)}</code></td>
                <td>{traced(f) ? "Traced" : "No data"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

/** "30 Nov 2025" for an ISO date or time. */
const dayText = (iso: string) =>
  new Date(`${iso.slice(0, 10)}T00:00:00Z`).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });

/** One claim: verdict, sentence, the numbers it cites, and the way to its evidence. */
function ClaimProof({ id }: { id: string }) {
  const { claims, toggle } = useClaims();
  const facts = useContext(FactsContext);
  const claim = claims[id];
  return (
    <article className="card claim-card">
      <VerdictLine id={id} />
      <p><RichText text={`{claim:${id}}`} /></p>
      <ul className="cited" aria-label="Numbers cited">
        {(claim.facts ?? []).map((f) => <li key={f}>{facts[f] ? `${facts[f].label}: ${facts[f].formatted}` : "No data"}</li>)}
      </ul>
      <button type="button" className="chip" {...{ [OPENER]: "" }} onClick={(e) => toggle(id, e.currentTarget)}>Open the evidence</button>
    </article>
  );
}

/** One section's three levels: the numbers (proven by code), the words, and what is left out. */
function SectionCheck({ section, spec }: { section: Section; spec: Spec }) {
  const verdict = useVerdict();
  const ids = sectionClaims(section, spec);
  const checked = ids.filter((id) => verdict(id) === "checked").length;
  const headingId = `check-${section.id}`;
  return (
    <div className="card section-check" role="group" aria-labelledby={headingId}>
      <h3 className="drawer-h" id={headingId}>{section.heading}</h3>
      <ul>
        <li><strong>The numbers:</strong> Checked against the data ({checked} of {ids.length} claims).</li>
        <li><strong>The words between them:</strong> {wordingText(section)}</li>
        <li><strong>What the text leaves out:</strong> {omissionText(section)}</li>
      </ul>
    </div>
  );
}

function SectionChecks({ spec }: { spec: Spec }) {
  return (
    <section className="section-checks narrow" aria-labelledby="section-checks">
      <h2 className="kicker" id="section-checks">How each section was checked</h2>
      {spec.sections.map((s) => <SectionCheck key={s.id} section={s} spec={spec} />)}
    </section>
  );
}

/** The lineage of each cited number, in plain words, for an auditor. */
function Auditors({ ids, sections }: { ids: string[]; sections: Section[] }) {
  const { claims } = useClaims();
  const facts = useContext(FactsContext);
  const metricLabel = useMetricLabel();
  const cited = [...new Set(ids.flatMap((id) => claims[id]?.facts ?? []))].map((f) => facts[f]).filter(Boolean);
  const lineage = (f: FactT) => [
    `Definition: ${metricLabel(f.metric)} v${f.metric_version ?? "?"}`,
    `Data: ${f.source_id}${f.data_time ? `, refreshed ${dayText(f.data_time)}` : ""}`,
    `Query ${f.query_hash.slice(0, 8) || "none"}`,
    traced(f) ? "Traced" : "No data",
  ].join(" · ");
  return (
    <details className="auditors">
      <summary>For auditors</summary>
      <div className="card table-scroll">
        <table className="data">
          <thead><tr><th>Number</th><th>Value</th><th>Lineage</th><th>How it was derived</th></tr></thead>
          <tbody>
            {cited.map((f) => (
              <tr key={f.id}>
                <td>{f.label}</td>
                <td className="num">{f.formatted}</td>
                <td>{lineage(f)}</td>
                <td>{f.basis ?? "Read from the data"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {sections.filter((s) => s.review?.model).map((s) => (
        <p className="meta" key={s.id}>{`${s.heading}: words reviewed by ${s.review!.model}`}</p>
      ))}
    </details>
  );
}

/** Each claim of the report with its proof. An old report with no claims shows the fact table. */
export function Evidence({ spec }: { spec?: Spec }) {
  if (!spec || !hasClaims(spec)) return <FactTable />;
  const ids = claimsInOrder(spec);
  return (
    <ClaimsProvider spec={spec}>
      <div className="kicker">Evidence</div>
      <h1 className="title">Claims and proof</h1>
      <SectionChecks spec={spec} />
      <div className="claim-list narrow">{ids.map((id) => <ClaimProof key={id} id={id} />)}</div>
      <Auditors ids={ids} sections={spec.sections} />
    </ClaimsProvider>
  );
}
