import { useMemo, useState } from "react";
import type { Payload, View } from "../lib/api";
import { Badge } from "./Badge";
import { FactsContext, QueriesContext } from "./context";
import { cubeDrill } from "./cube";
import { Exploring } from "./Exploring";
import { FilterRow } from "./FilterRow";
import { ScopeProvider } from "./ScopeContext";
import { VIEWS } from "./views";
import { Brief } from "./views/Brief";
import { Dashboard } from "./views/Dashboard";
import { Evidence } from "./views/Evidence";
import { Narrative } from "./views/Narrative";

/**
 * The same views as a hosted page, with no server. Filters, cross-filter, brush, compare, and
 * drill run over the cube that the file carries. Questions and pins link back to the hosted page.
 */
export function ExportPage({ data }: { data: Payload & { hosted_url: string } }) {
  const [view, setView] = useState<View>(data.view);
  const cube = data.cube ?? null;
  const defs = useMemo(() => data.query_defs ?? {}, [data.query_defs]);
  const drill = useMemo(() => (cube ? cubeDrill(cube, defs) : null), [cube, defs]);
  return (
    <FactsContext.Provider value={data.facts}>
      <QueriesContext.Provider value={data.queries}>
      <ScopeProvider cube={cube} defs={defs} reason={cube ? null : data.cube_reason ?? null} drill={drill} pin={null} hosted={false}>
        <header className="topbar">
          <div className="tabs" role="tablist">
            {VIEWS.map((v) => (
              <button key={v.id} role="tab" aria-selected={v.id === view} onClick={() => setView(v.id)}>{v.label}</button>
            ))}
          </div>
          <span className="meta">Edition {data.edition} · Published <Badge tier={data.tier} state={data.state} /></span>
        </header>
        <main className="page">
          <FilterRow />
          <Exploring />
          {view === "narrative" && <Narrative spec={data.spec} panelFor={() => null} />}
          {view === "dashboard" && <Dashboard spec={data.spec} panel={null} />}
          {view === "brief" && <Brief spec={data.spec} onEvidence={() => setView("evidence")} />}
          {view === "evidence" && <Evidence spec={data.spec} />}
          {data.spec.assumptions.length > 0 && (
            <div className="assumptions">
              <span className="meta">Assumptions</span>
              {data.spec.assumptions.map((a) => <span className="assumption" key={a.id}>{a.text}</span>)}
            </div>
          )}
          <p className="outcome"><a href={data.hosted_url}>Ask a question about this report</a></p>
        </main>
      </ScopeProvider>
      </QueriesContext.Provider>
    </FactsContext.Provider>
  );
}
