import { useQuery } from "@tanstack/react-query";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { getJSON, type EditionDiff, type FactChange } from "../lib/api";

function Rows({ title, rows }: { title: string; rows: FactChange[] }) {
  if (rows.length === 0) return null;
  return (
    <section className="card table-scroll">
      <h3 className="card-title">{title}</h3>
      <table className="data">
        <thead><tr><th>Value</th><th>Before</th><th>After</th></tr></thead>
        <tbody>
          {rows.map((r) => <tr key={r.id}><td>{r.label}</td><td className="num">{r.before}</td><td className="num">{r.after}</td></tr>)}
        </tbody>
      </table>
    </section>
  );
}

export function DiffView() {
  const { id } = useParams();
  const [params] = useSearchParams();
  const from = params.get("from"), to = params.get("to");
  const diff = useQuery({
    queryKey: ["diff", id, from, to],
    queryFn: () => getJSON<EditionDiff>(`/api/reports/${id}/diff?from=${from}&to=${to}`),
    retry: false,
  });
  if (diff.isPending) return <p className="state">Loading the differences…</p>;
  if (diff.isError) return <p className="state">These editions are not available.</p>;
  const d = diff.data;
  return (
    <main className="page">
      <div className="kicker">Edition {from} to edition {to}</div>
      <h1 className="title">What changed</h1>
      {d.definition_changes.map((c) => (
        <p className="prose" key={c.metric}>{c.metric} changed from version {c.from_version} to version {c.to_version}. {c.definition}</p>
      ))}
      <Rows title="Changed values" rows={d.changed_facts} />
      <Rows title="New values" rows={d.added_facts} />
      <Rows title="Removed values" rows={d.removed_facts} />
      {d.changed_facts.length + d.added_facts.length + d.removed_facts.length === 0 && <p className="meta">No values changed.</p>}
      <p><Link className="link" to={`/r/${id}/e/${to}`}>Open edition {to}</Link></p>
    </main>
  );
}
