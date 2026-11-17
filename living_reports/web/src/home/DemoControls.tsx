import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { DemoUser } from "../DemoUser";
import { getJSON, postJSON } from "../lib/api";
import { useConfig } from "../lib/config";

/** Demo-only controls, behind the server's demo flag and one disclosure. */
export function DemoControls() {
  const { demo, dataset, datasets = [] } = useConfig();
  const client = useQueryClient();
  const sources = useQuery({
    queryKey: ["sources"],
    queryFn: () => getJSON<{ demo_moment: number | null }>("/api/sources"),
    enabled: demo,
  });
  const load = useMutation({
    mutationFn: () => postJSON("/api/sources/duckdb_demo/advance", {}),
    onSuccess: () => { client.invalidateQueries(); },
  });
  if (!demo) return null;
  return (
    <details className="demo">
      <summary>Demo controls</summary>
      {datasets.length > 0 && <DemoData current={dataset} datasets={datasets} />}
      <DemoUser />
      {sources.data?.demo_moment === 1 && (
        <button type="button" className="chip" onClick={() => load.mutate()} disabled={load.isPending}>
          {load.isPending ? "Loading…" : "Load the next data snapshot"}
        </button>
      )}
    </details>
  );
}

/** Selects the demo dataset for the whole server. Each dataset keeps its own reports. */
function DemoData({ current, datasets }: { current?: string; datasets: { name: string; title: string }[] }) {
  const client = useQueryClient();
  const navigate = useNavigate();
  const select = useMutation({
    mutationFn: (name: string) => postJSON("/api/demo", { name }),
    onSuccess: () => { navigate("/"); client.invalidateQueries(); },
  });
  return (
    <label className="tier-field">
      <span className="meta">Demo data</span>
      <select aria-label="Demo data" value={select.isPending ? select.variables : current}
        disabled={select.isPending} onChange={(e) => select.mutate(e.target.value)}>
        {datasets.map((d) => <option key={d.name} value={d.name}>{d.title}</option>)}
      </select>
      {select.isPending && <span className="meta" role="status">Preparing the data…</span>}
      {select.isError && <span className="meta" role="alert">{(select.error as Error).message}</span>}
    </label>
  );
}
