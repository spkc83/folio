import { useContext } from "react";
import type { Block, QueryView } from "../lib/api";
import { deltaText, formatOf } from "./charts/format";
import { linear } from "./charts/scale";
import { num, periodLabel } from "./charts/series";
import { FactsContext, QueriesContext } from "./context";
import { deltaUnderScope, derive, tileValue } from "./cube";
import { Fact } from "./Fact";
import { displayValue } from "./labels";
import { refOf } from "./refs";
import { isBaseline } from "./scope";
import { useScope } from "./ScopeContext";

type TileBlock = Extract<Block, { type: "tile" }>;

const SW = 120, SH = 28, PAD = 3;
const NO_PREVIOUS = "No previous period";

const first = (q?: QueryView) => num(q?.points[0]);

/**
 * A tile label in words. The server writes it ("Fraud loss · West · Jan 2024 – Nov 2025");
 * an older report can hold raw values ("Top: identity_theft", "2024-01-01"), so each part
 * after a colon or a dot separator shows as a display value.
 */
export function tileLabel(label: string): string {
  return label.split(" · ").map((part) => {
    const at = part.indexOf(": ");
    return at < 0 ? displayValue("", part) : `${part.slice(0, at + 2)}${displayValue("", part.slice(at + 2))}`;
  }).join(" · ");
}

/** The change against the previous period. Ink, not color: the graph does not say if up is good. */
export function DeltaLine({ text, label }: { text: string | null; label?: string | null }) {
  if (text === null) return <span className="delta">{NO_PREVIOUS}</span>;
  return <span className="delta"><b>{text}</b> {label}</span>;
}

/** The last twelve points as a small line, with a text alternative for screen readers. */
export function Sparkline({ query }: { query?: QueryView }) {
  const points = (query?.points ?? []).filter((p) => num(p) !== null).slice(-12);
  if (points.length < 2) return null;
  const values = points.map((p) => num(p)!);
  const lo = Math.min(...values), hi = Math.max(...values);
  const x = linear([0, points.length - 1], [PAD, SW - PAD]);
  const y = linear([lo, hi], [SH - PAD, PAD]);
  const low = points[values.indexOf(lo)], high = points[values.indexOf(hi)];
  const last = points.length - 1;
  return (
    <span className="spark-wrap">
      <svg className="spark" viewBox={`0 0 ${SW} ${SH}`} width={SW} height={SH} aria-hidden="true" focusable="false">
        <polyline fill="none" stroke="var(--gray)" strokeWidth="1.5" strokeLinejoin="round"
          points={values.map((v, i) => `${x(i)},${y(v)}`).join(" ")} />
        <circle cx={x(last)} cy={y(values[last])} r="2" fill="var(--accent)" />
      </svg>
      <span className="sr-only">
        {`Last ${points.length} months, lowest ${low.formatted} in ${periodLabel(low.label)}, highest ${high.formatted} in ${periodLabel(high.label)}`}
      </span>
    </span>
  );
}

/**
 * The tile under a scope: a value re-aggregated from the cube. It is plain text, not a traced
 * fact, because no stored query produced it.
 */
function ScopedTile({ block }: { block: TileBlock }) {
  const facts = useContext(FactsContext);
  const { scope, cube, defs } = useScope();
  const binding = facts[block.fact_id]?.binding;
  const def = binding ? defs[binding.ref] : undefined;
  if (!cube || !binding || !def) {
    return (
      <div className="card tile">
        <span className="label" title={tileLabel(block.label)}>{tileLabel(block.label)}</span>
        <span className="value"><span className="fact" data-verified="false">No data</span></span>
        <span className="delta">Not re-run under a scope</span>
      </div>
    );
  }
  const got = tileValue(cube, def, binding, scope);
  const fmt = cube.metrics[binding.metric]?.format ?? "count";
  const keys = Object.entries(binding.keys).map(([dimension, value]) => ({ dimension, value }));
  const spark = block.spark_ref
    ? derive(cube, { ...def, time: { grain: "month" } }, scope, { groupBy: ["month"], extra: keys, metric: binding.metric })
    : null;
  return (
    <div className="card tile">
      <span className="label" title={tileLabel(block.label)}>{tileLabel(block.label)}</span>
      <span className="value">
        <span className="fact" data-verified="false">{"reason" in got ? "No data" : got.formatted}</span>
      </span>
      {"reason" in got && <span className="delta">{got.reason}</span>}
      {!("reason" in got) && block.delta && (
        <DeltaLine text={binding.share ? null : deltaUnderScope(cube, def, binding, scope, fmt)} label="against the period before" />
      )}
      {spark && "view" in spark && <Sparkline query={spark.view} />}
    </div>
  );
}

/** A headline number with its change against the previous period and a sparkline. */
export function Tile({ block }: { block: TileBlock }) {
  const queries = useContext(QueriesContext);
  const { scope, cube } = useScope();
  if (cube && !isBaseline(scope)) return <ScopedTile block={block} />;
  const ref = refOf(block.fact_id);
  const cur = queries[`${ref}_cur`];
  let delta: string | null = null;
  if (block.delta && cur) {
    delta = deltaText(first(cur), first(queries[`${ref}_prev`]), formatOf(cur.points[0]?.formatted ?? ""));
  }
  return (
    <div className="card tile">
      <span className="label" title={tileLabel(block.label)}>{tileLabel(block.label)}</span>
      <span className="value"><Fact id={block.fact_id} /></span>
      {block.delta && <DeltaLine text={delta} label={block.delta_label} />}
      {block.spark_ref && <Sparkline query={queries[block.spark_ref]} />}
    </div>
  );
}
