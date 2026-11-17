import { useRef, useState } from "react";
import type { QueryView } from "../lib/api";
import { Bars } from "./charts/Bars";
import { scopedQuery, valueText, type Scope, type ScopedQuery } from "./scope";

type Props = {
  queryRef: string;
  scope: Scope;
  member: Record<string, string | null>;
  candidates: { id: string; label: string }[];
  load: (sq: ScopedQuery) => Promise<QueryView>;
  onPin: ((sq: ScopedQuery) => void) | null;
};

type Panel = { by: string; sq: ScopedQuery | null; view: QueryView | null; text: string | null };

/** One level of drill: the selected member broken down by one more dimension. */
export function Drill({ queryRef, scope, member, candidates, load, onPin }: Props) {
  const [panel, setPanel] = useState<Panel | null>(null);
  const [busy, setBusy] = useState(false);
  const request = useRef(0);
  const name = Object.entries(member).map(([dimension, value]) => valueText(value, dimension)).join(", ");

  const choose = (by: string) => {
    if (!by) { setPanel(null); return; }
    const sq = scopedQuery(queryRef, scope, { by, member });
    if ("reason" in sq) { setPanel({ by, sq: null, view: null, text: sq.reason }); return; }
    const mine = ++request.current;
    setBusy(true);
    setPanel((p) => ({ by, sq, view: p?.view ?? null, text: null }));
    load(sq)
      .then((view) => { if (mine === request.current) setPanel({ by, sq, view, text: null }); })
      .catch((e: unknown) => {
        if (mine === request.current) setPanel({ by, sq, view: null, text: e instanceof Error ? e.message : "The request failed." });
      })
      .finally(() => { if (mine === request.current) setBusy(false); });
  };

  const label = candidates.find((c) => c.id === panel?.by)?.label ?? panel?.by ?? "";
  const title = `${name} · by ${label}`;
  return (
    <div className="drill">
      <label className="filter">
        <span>Break down {name} by</span>
        <select aria-label="Break down by…" value={panel?.by ?? ""} onChange={(e) => choose(e.target.value)}>
          <option value="">Choose…</option>
          {candidates.map((c) => <option key={c.id} value={c.id}>{c.label}</option>)}
        </select>
      </label>
      {panel && (
        <section className={`drill-panel${busy ? " busy" : ""}`} role="region" aria-label={title} aria-busy={busy}>
          {panel.text && <p className="empty">{panel.text}</p>}
          {panel.view && <Bars query={panel.view} title={title} />}
          {onPin && panel.sq && panel.view && (
            <button type="button" className="secondary" disabled={busy} onClick={() => onPin(panel.sq!)}>Pin</button>
          )}
        </section>
      )}
    </div>
  );
}
