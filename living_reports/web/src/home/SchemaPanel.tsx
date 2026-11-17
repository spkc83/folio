import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { getJSON, type SchemaColumn, type SourceSchema, type Term } from "../lib/api";

type Sources = { sources: { id: string; error?: string }[] };

const day = (v: string | null) => (v ?? "").slice(0, 10);

function profile(c: SchemaColumn): string {
  const parts: string[] = [];
  if (c.is_time && c.min && c.max) parts.push(`${day(c.min)} → ${day(c.max)}`);
  else if (c.distinct_count != null) parts.push(`${c.distinct_count.toLocaleString("en-US")} values`);
  if (c.null_rate) parts.push(`${Math.round(c.null_rate * 100)}% null`);
  return parts.join(" · ");
}

function Terms({ name, terms, onPick }: { name: string; terms: Term[]; onPick: (label: string) => void }) {
  if (terms.length === 0) return null;
  return (
    <p className="schema-terms">
      <span className="meta schema-key">{name}</span>
      {terms.map((t) => (
        <button key={t.id} type="button" className="chip" title={t.definition || t.synonyms?.join(", ") || t.id}
          onClick={() => onPick(t.label)}>{t.label}</button>
      ))}
    </p>
  );
}

/** The dataset selector and what a person can ask about on it: terms first, then the columns. */
export function SchemaPanel({ onPick, onSource }: { onPick: (label: string) => void; onSource: (id: string) => void }) {
  const sources = useQuery({ queryKey: ["sources"], queryFn: () => getJSON<Sources>("/api/sources") });
  const ids = (Array.isArray(sources.data?.sources) ? sources.data.sources : []).filter((s) => !s.error).map((s) => s.id);
  const [chosen, setChosen] = useState<string | null>(null);
  const source = chosen ?? ids[0] ?? null;
  useEffect(() => { if (source) onSource(source); }, [source, onSource]);
  const schema = useQuery({
    queryKey: ["schema", source],
    queryFn: () => getJSON<SourceSchema>(`/api/sources/${encodeURIComponent(source!)}/schema`),
    enabled: source !== null,
  });
  if (!source) return null;
  const s = Array.isArray(schema.data?.datasets) ? schema.data : undefined;
  const time = s?.datasets.flatMap((d) => d.columns).filter((c) => c.is_time) ?? [];
  const range = (id: string) => {
    const c = time.find((x) => x.field_id === id);
    return c?.min && c?.max ? ` (${day(c.min)} → ${day(c.max)})` : "";
  };
  return (
    <section className="schema-panel" aria-label="What you can ask about">
      <label className="schema-source">
        <span className="meta">Dataset</span>
        <select aria-label="Dataset" value={source} onChange={(e) => setChosen(e.target.value)}>
          {ids.map((id) => <option key={id} value={id}>{id}</option>)}
        </select>
        {s && <span className="meta">{s.datasets.length} {s.datasets.length === 1 ? "table" : "tables"}</span>}
      </label>
      {schema.isError && <p className="meta">The schema of {source} is not available.</p>}
      {s && (
        <>
          {s.metrics.length === 0 && <p className="meta">No metrics on this dataset yet. An owner can run discovery on the Model page.</p>}
          <Terms name="Metrics" terms={s.metrics} onPick={onPick} />
          <Terms name="By" terms={s.dimensions.filter((d) => !d.is_time)} onPick={onPick} />
          {s.dimensions.filter((d) => d.is_time).map((d) => (
            <p key={d.id} className="schema-terms">
              <span className="meta schema-key">Time</span>
              <button type="button" className="chip" onClick={() => onPick(d.label)}>{d.label}</button>
              <span className="meta">{range(d.id)}</span>
            </p>
          ))}
          {!s.profiled && <p className="meta">Column statistics are not available now.</p>}
          {s.datasets.map((d) => (
            <details key={d.name} className="schema-dataset">
              <summary>{d.name}{d.row_count != null && <span className="meta"> · {d.row_count.toLocaleString("en-US")} rows</span>}</summary>
              <table className="schema-columns">
                <thead><tr><th>Column</th><th>Type</th><th>Profile</th><th>Examples</th></tr></thead>
                <tbody>
                  {d.columns.map((c) => (
                    <tr key={c.field_id}>
                      <td title={c.label}><code>{c.name}</code>{c.dimension && <span className="meta"> · {c.is_time ? "time" : "group by"}</span>}</td>
                      <td>{c.type}</td>
                      <td>{profile(c)}</td>
                      <td>{c.samples.join(", ")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </details>
          ))}
        </>
      )}
    </section>
  );
}
