import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import {
  DEMO_ROLES, currentUser, getJSON, postJSON,
  type ConfirmResult, type DiscoveryRun, type Dropped, type ImportResult, type ProposalView,
} from "../lib/api";
import { ProposalRow } from "./ProposalRow";

const GROUPS: [string, string][] = [
  ["Source", "Sources"], ["Dataset", "Datasets"], ["Field", "Fields"],
  ["Relationship", "Relationships"], ["MetricBinding", "Metrics"],
];
type SourcesState = { sources: { id: string; dialect: string }[]; demo_moment: number | null };

/** The model page: proposals from discovery or import, each with its probe, for a person to confirm. */
export function ModelPage() {
  const client = useQueryClient();
  const owner = DEMO_ROLES[currentUser()] === "owner";
  const proposals = useQuery({ queryKey: ["model-proposals"], queryFn: () => getJSON<ProposalView[]>("/api/model/proposals") });
  const sources = useQuery({ queryKey: ["sources"], queryFn: () => getJSON<SourcesState>("/api/sources") });
  const [chosen, setChosen] = useState("");
  const source = chosen || sources.data?.sources?.[0]?.id || "";
  const [document, setDocument] = useState("");
  const [dropped, setDropped] = useState<Dropped[] | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = () => client.invalidateQueries({ queryKey: ["model-proposals"] });
  const fail = (e: unknown) => { setMessage(null); setError(e instanceof Error ? e.message : "The request failed."); };
  const afterRun = (run: DiscoveryRun) => {
    setError(null);
    setDropped(run.dropped);
    setMessage(`${run.proposal_ids.length} proposed, ${run.skipped.length} already in the model, ${run.dropped.length} dropped.`);
    refresh();
  };
  const discover = useMutation({ mutationFn: () => postJSON<DiscoveryRun>("/api/model/discover", { source_id: source }), onSuccess: afterRun, onError: fail });
  const importDoc = useMutation({
    mutationFn: () => postJSON<ImportResult>("/api/model/import", { source_id: source, document }),
    onSuccess: (r) => afterRun(r.run), onError: fail,
  });
  const confirmAll = useMutation({
    mutationFn: () => postJSON<ConfirmResult>("/api/model/proposals/confirm", { ids: null }),
    onSuccess: (r) => {
      setError(null);
      setMessage(r.failed.length ? `${r.confirmed.length} confirmed. Not confirmed: ${r.failed.map(([, why]) => why).join("; ")}` : `${r.confirmed.length} confirmed.`);
      refresh();
    },
    onError: fail,
  });
  const busy = discover.isPending || importDoc.isPending || confirmAll.isPending;
  const views = proposals.data ?? [];

  return (
    <main className="page model-page">
      <p className="meta"><Link to="/">Home</Link></p>
      <h1 className="title">Model</h1>
      <div className="model-controls">
        <label className="tier-field">
          <span className="meta">Source</span>
          <select aria-label="Source" value={source} onChange={(e) => setChosen(e.target.value)}>
            {sources.data?.sources?.map((s) => <option key={s.id} value={s.id}>{s.id}</option>)}
          </select>
        </label>
        <button type="button" className="chip" disabled={!owner || busy || !source} onClick={() => discover.mutate()}>
          {discover.isPending ? "Running discovery…" : "Run discovery"}
        </button>
        <button type="button" className="chip" disabled={!owner || busy || views.length === 0} onClick={() => confirmAll.mutate()}>Confirm all</button>
        <a className="chip" href={`/api/model/export${source ? `?source_id=${encodeURIComponent(source)}` : ""}`}>Export</a>
      </div>
      {!owner && <p className="meta">Only an owner can change the model. Ask an owner to review these proposals.</p>}
      <details className="model-import">
        <summary>Import</summary>
        <form onSubmit={(e) => { e.preventDefault(); if (document.trim()) importDoc.mutate(); }}>
          <textarea aria-label="Ossie document" rows={8} value={document} onChange={(e) => setDocument(e.target.value)} />
          <button type="submit" className="chip" disabled={!owner || busy || !document.trim() || !source}>Import document</button>
        </form>
      </details>
      {message && <p className="meta" role="status">{message}</p>}
      {error && <p className="row-error" role="alert">{error}</p>}
      {proposals.isError && <p className="row-error" role="alert">The proposals cannot be read now. Reload the page to try again.</p>}
      {proposals.isSuccess && views.length === 0 && (
        <p className="meta">No proposals. Run discovery on a source, or import an Ossie document.</p>
      )}
      {GROUPS.map(([kind, heading]) => {
        const rows = views.filter((v) => v.kind === kind);
        if (rows.length === 0) return null;
        return (
          <section key={kind}>
            <h2 className="heading">{heading}</h2>
            <div className="table-scroll">
              <table className="data">
                <thead><tr><th scope="col">Name</th><th scope="col">Summary</th><th scope="col">Probe</th><th scope="col">Actions</th></tr></thead>
                <tbody>{rows.map((v) => <ProposalRow key={v.proposal.id} view={v} owner={owner} />)}</tbody>
              </table>
            </div>
          </section>
        );
      })}
      {dropped && dropped.length > 0 && (
        <section>
          <h2 className="heading">Dropped in the last run</h2>
          <div className="table-scroll">
            <table className="data">
              <thead><tr><th scope="col">Name</th><th scope="col">Kind</th><th scope="col">Reason</th></tr></thead>
              <tbody>
                {dropped.map((d) => (
                  <tr key={`${d.kind}:${d.name}`}>
                    <td><code>{d.name}</code></td><td>{d.kind}</td>
                    <td><span role="img" aria-label="Fail">✗</span> {d.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </main>
  );
}
