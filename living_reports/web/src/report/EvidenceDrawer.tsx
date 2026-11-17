import { useContext, useEffect, useId, useMemo, useRef, useState } from "react";
import type { Claim, EvidenceRow, FactT } from "../lib/api";
import { DataTable } from "./charts/DataTable";
import { linear } from "./charts/scale";
import { OPENER } from "./claims";
import { FactsContext } from "./context";
import { derive } from "./cube";
import { displayValue } from "./labels";
import { ClaimText } from "./RichText";
import { idWords, type Scope } from "./scope";
import { useScope } from "./ScopeContext";
import { labelOf, useVocabulary } from "./VocabularyContext";
import { verdictOf, VERDICT_TEXT } from "./verdict";
import "./evidence.css";

/** "Fraud loss" for a metric id: the vocabulary label, else the id in words. */
export function useMetricLabel(): (metric: string | null | undefined) => string {
  const vocabulary = useVocabulary();
  return (metric) => {
    if (!metric) return "Value";
    const label = labelOf(vocabulary, metric);
    return label !== metric ? label : idWords(metric);
  };
}


/** The label of a breakdown. The payload sends a label; a member id is a safety net. */
function useByLabel(): (by: string | null | undefined) => string {
  const vocabulary = useVocabulary();
  return (by) => {
    if (!by) return "Item";
    const label = labelOf(vocabulary, by);
    return label !== by || !by.includes(".") ? label : idWords(by);
  };
}

const bindingKey = (f: FactT) =>
  f.binding ? JSON.stringify([f.binding.ref, f.binding.metric, Object.entries(f.binding.keys).sort()]) : null;

/** The share fact of a value fact: same query, metric, and members, with `share` set. */
function useShares(): (factId: string) => FactT | undefined {
  const facts = useContext(FactsContext);
  const index = useMemo(() => {
    const out = new Map<string, FactT>();
    for (const f of Object.values(facts)) {
      const key = f.binding?.share ? bindingKey(f) : null;
      if (key) out.set(key, f);
    }
    return out;
  }, [facts]);
  return (factId) => {
    const f = facts[factId];
    const key = f && !f.binding?.share ? bindingKey(f) : null;
    return key ? index.get(key) : undefined;
  };
}

/** The rows of a mini table: the first `limit` rows, plus the highlighted row when it is further down. */
export function topRows(rows: EvidenceRow[] = [], limit?: number): EvidenceRow[] {
  if (limit === undefined || rows.length <= limit) return rows;
  const top = rows.slice(0, limit);
  const extra = rows.slice(limit).filter((r) => r.highlight);
  return [...top, ...extra];
}

/** The claim's comparison table: each row's value and share. The claimed row is highlighted. */
export function CompareTable({ claim, limit, label }: { claim: Claim; limit?: number; label?: string }) {
  const facts = useContext(FactsContext);
  const shareOf = useShares();
  const metricLabel = useMetricLabel();
  const byLabel = useByLabel();
  const rows = topRows(claim.evidence.rows, limit);
  if (rows.length === 0) return null;
  const shares = rows.map((r) => shareOf(r.fact));
  const withShare = shares.some(Boolean);
  const metric = facts[rows[0].fact]?.metric;
  return (
    <table className="data compare" aria-label={label}>
      <thead>
        <tr>
          <th>{byLabel(claim.evidence.by)}</th>
          <th className="num">{metricLabel(metric)}</th>
          {withShare && <th className="num">Share</th>}
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={`${r.label}-${i}`} aria-current={r.highlight || undefined} data-highlight={r.highlight || undefined}>
            <td>{r.label}</td>
            <td className="num">{facts[r.fact]?.formatted ?? "No data"}</td>
            {withShare && <td className="num">{shares[i]?.formatted ?? ""}</td>}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

const SW = 320, SH = 72, PAD = 8;

/** The claim's values over time as a small line. The periods the claim cites are marked. */
function SeriesChart({ claim }: { claim: Claim }) {
  const facts = useContext(FactsContext);
  const series = claim.evidence.series ?? [];
  const cited = new Set(claim.facts ?? []);
  const points = series.map((p) => {
    const f = facts[p.fact];
    const value = typeof f?.value === "number" && Number.isFinite(f.value) ? f.value : null;
    return { label: p.label, formatted: f?.formatted ?? "No data", value, marked: cited.has(p.fact) };
  });
  const values = points.map((p) => p.value).filter((v): v is number => v !== null);
  const x = linear([0, Math.max(points.length - 1, 1)], [PAD, SW - PAD]);
  const y = linear([Math.min(...values), Math.max(...values)], [SH - PAD, PAD]);
  const drawn = points.map((p, i) => ({ ...p, cx: x(i), cy: p.value === null ? null : y(p.value) }));
  return (
    <>
      {values.length >= 2 && (
        <svg className="ev-spark" viewBox={`0 0 ${SW} ${SH}`} aria-hidden="true" focusable="false">
          <polyline fill="none" stroke="var(--gray)" strokeWidth="1.5" strokeLinejoin="round"
            points={drawn.filter((p) => p.cy !== null).map((p) => `${p.cx},${p.cy}`).join(" ")} />
          {drawn.map((p, i) => p.cy !== null && (
            <circle key={i} className={p.marked ? "marked" : undefined} cx={p.cx} cy={p.cy} r={p.marked ? 4 : 2.5}
              fill={p.marked ? "var(--accent)" : "var(--gray)"} />
          ))}
        </svg>
      )}
      <ul className="ev-series" aria-label="Values over time">
        {points.map((p, i) => (
          <li key={i} data-marked={p.marked}><span>{p.label}</span><span className="num">{p.formatted}</span></li>
        ))}
      </ul>
    </>
  );
}

/** The month of an ISO day: "2025-11-30" -> "2025-11-01". */
const monthOf = (day: string) => `${day.slice(0, 7)}-01`;

/** The section's data re-aggregated from the cube for the claim's segment and period. Null when the cube cannot. */
function useSegmentTable(claim: Claim) {
  const { cube, defs } = useScope();
  const def = defs[claim.query_ref];
  if (!cube || !def) return null;
  const scope: Scope = {
    filters: Object.entries(claim.segment ?? {}).map(([dimension, value]) => ({ dimension, value })),
    range: claim.period && cube.columns.includes("month") ? [monthOf(claim.period[0]), monthOf(claim.period[1])] : null,
  };
  return () => derive(cube, def, scope);
}

type Props = {
  id: string; claim: Claim; number: number | null; stale: boolean; onClose: () => void;
  /** How the words around the claim were checked. The verdict covers the claim only. */
  around?: string;
};

/** The proof of one claim, in a side panel: what was counted, how it compares, how it changed, how it is defined. */
export function EvidenceDrawer({ id, claim, number, stale, onClose, around }: Props) {
  const facts = useContext(FactsContext);
  const box = useRef<HTMLElement>(null);
  const titleId = useId();
  const [asTable, setAsTable] = useState(false);
  const table = useSegmentTable(claim);
  useEffect(() => setAsTable(false), [claim.id]);
  useEffect(() => {
    box.current?.focus();
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    const onDown = (e: PointerEvent) => {
      const target = e.target as Element | null;
      if (!target || box.current?.contains(target) || target.closest?.(`[${OPENER}]`)) return;
      onClose();
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onDown);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onDown);
    };
  }, [onClose, claim.id]);

  const { evidence } = claim;
  const rows = evidence.rows ?? [];
  const verdict = verdictOf(claim, facts, stale);
  const changes = (claim.kind === "change" || claim.kind === "trend" || claim.kind === "peak") && (evidence.series?.length ?? 0) > 0;
  const segment = Object.entries(claim.segment ?? {}).map(([member, value]) => displayValue(member, value)).join(", ");
  const derived = asTable && table ? table() : null;
  return (
    <aside className="drawer" id={id} role="dialog" aria-labelledby={titleId} tabIndex={-1} ref={box}>
      <div className="drawer-head">
        <h2 className="kicker" id={titleId}>{number ? `Evidence ${number}` : "Evidence"}</h2>
        <button type="button" className="link" onClick={onClose}>Close</button>
      </div>
      <p className="drawer-claim"><ClaimText claim={claim} /></p>
      <p className="verdict" data-verdict={verdict}>{VERDICT_TEXT[verdict]}</p>
      {around && <p className="drawer-p wording-check">The words around it: {around}</p>}

      <h3 className="drawer-h">What was counted</h3>
      <p className="drawer-p">{evidence.counted}</p>

      {rows.length > 0 && (
        <>
          <h3 className="drawer-h">How it compares</h3>
          <CompareTable claim={claim} label="How it compares" />
        </>
      )}

      {changes && (
        <>
          <h3 className="drawer-h">How it changed</h3>
          <SeriesChart claim={claim} />
        </>
      )}

      {evidence.definition && (
        <>
          <h3 className="drawer-h">How it is defined</h3>
          <p className="drawer-p">{evidence.definition}</p>
        </>
      )}

      {table && (
        <div className="drawer-table">
          <button type="button" className="link" aria-expanded={asTable} onClick={() => setAsTable(!asTable)}>
            {asTable ? "Hide the table" : "Show this as a table"}
          </button>
          {derived && ("reason" in derived
            ? <p className="meta">{derived.reason}</p>
            : <DataTable query={derived.view} title={segment ? `The data for ${segment}` : "The data"} />)}
        </div>
      )}
    </aside>
  );
}
