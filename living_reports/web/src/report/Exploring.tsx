import { useState } from "react";
import { describe, isBaseline, reset, scopedQuery } from "./scope";
import { useScope } from "./ScopeContext";

/** Says that the page shows an exploration, not the report. Offers Reset and Pin. */
export type PinTarget = { ref: string; title: string };

export function Exploring({ sections = [] }: { sections?: PinTarget[] }) {
  const { scope, setScope, cube, defs, pin, comparing } = useScope();
  const targets = sections.filter((s) => s.ref in defs);
  const [picked, setPicked] = useState<string | null>(null);
  if (isBaseline(scope)) return null;
  const target = targets.find((t) => t.ref === picked) ?? targets[0];
  const sq = target ? scopedQuery(target.ref, scope) : { reason: "The report has no query to pin." };
  return (
    <div className="exploring" role="status">
      <span className="tag">Exploring</span>
      <span className="exploring-scope">{describe(scope, { ...(cube?.vocabulary ?? {}), ...(cube?.labels ?? {}) })}</span>
      <button type="button" className="secondary" onClick={() => setScope(reset())}>Reset</button>
      {pin && targets.length > 1 && (
        <select aria-label="Section to pin" value={target?.ref} onChange={(e) => setPicked(e.target.value)}>
          {targets.map((t) => <option key={t.ref} value={t.ref}>{t.title}</option>)}
        </select>
      )}
      {pin && target && (
        <button type="button" className="primary" disabled={"reason" in sq}
          title={"reason" in sq ? sq.reason : undefined}
          onClick={() => { if (!("reason" in sq)) pin(sq); }}>
          {`Pin '${target.title}' with this scope`}
        </button>
      )}
      <span className="meta">
        {cube
          ? "Not saved. Values are re-aggregated from the report's cells; pin to trace them."
          : "The report is too large to re-filter in the page. Your next question uses this selection."}
        {comparing.length > 0 && " Compare is not saved."}
      </span>
    </div>
  );
}
