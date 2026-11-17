import { useState } from "react";
import type { Block, QueryView } from "../lib/api";
import { Bars } from "./charts/Bars";
import { ChartCard } from "./charts/ChartCard";
import { DataTable } from "./charts/DataTable";
import { DeltaBars } from "./charts/DeltaBars";
import { LineChart, type Compare, type Emphasis } from "./charts/LineChart";
import { Segmented } from "./charts/Segmented";
import { SmallMultiples } from "./charts/SmallMultiples";
import { derive, withPrevious } from "./cube";
import { Drill } from "./Drill";
import { valueText, withMember, withoutMember, withRange, type ScopeFilter } from "./scope";
import { useDerived, useScope } from "./ScopeContext";

type ChartBlock = Extract<Block, { query_ref: string }>;

/** A chart whose data cannot show under the current scope. The reason replaces the marks. */
function Unavailable({ title, reason }: { title: string; reason: string }) {
  const text = <p className="empty">{reason}</p>;
  return <ChartCard title={title} table={text}>{text}</ChartCard>;
}

/** The raw member value behind a point label: points and rows share one order. */
function memberAt(view: QueryView, label: string, column: number): string | null | undefined {
  const i = view.points.findIndex((p) => p.label === label);
  if (i < 0) return undefined;
  const raw = view.rows[i]?.[column];
  return raw === null || raw === undefined ? null : String(raw);
}

function labelOf(view: QueryView, value: string | null, column: number): string | null {
  const i = view.rows.findIndex((r) => (r[column] ?? null) === value);
  return i < 0 ? null : view.points[i]?.label ?? null;
}

export function Chart({ block }: { block: ChartBlock }) {
  const { scope, setScope, cube, defs, comparing, setComparing, drill, pin, hosted } = useScope();
  const def = defs[block.query_ref];
  // Re-filtering needs the cube. Selecting a member does not: with no cube, a click still
  // selects it for the next question, and the reader can always choose another member.
  const live = Boolean(cube && def);
  const dims = def?.dimensions ?? [];
  // The categorical dimension that a click on a mark filters.
  const monthly = def?.time?.grain === "month";
  const own = def ? dims[0] ?? null : null;
  const derived = useDerived(block.query_ref, own);
  const against = useDerived(block.type === "delta_bars" ? block.against_ref : "");
  const [reason, setReason] = useState<string | null>(null);
  if (!derived) return null;
  if ("reason" in derived) return <Unavailable title={block.title} reason={derived.reason} />;
  const query = derived.view;
  const note = "note" in derived ? derived.note ?? null : null;

  const current = own ? scope.filters.find((f) => f.dimension === own) : undefined;
  const column = own ? query.columns.indexOf(own) : -1;
  const selectedLabel = current && column >= 0 ? labelOf(query, current.value, column) : null;
  const onSelect = own && column >= 0
    ? (label: string | null) => {
      if (label === null) { setScope(withoutMember(scope, own)); return; }
      const value = memberAt(query, label, column);
      if (value !== undefined) setScope(withMember(withoutMember(scope, own), own, value));
    }
    : undefined;
  // Drill one level on the selected member: hosted pages ask the server, the export the cube.
  let footer = null;
  if (own && current && drill && cube && ["bars", "segmented", "table"].includes(block.type)) {
    const pool = hosted ? Object.keys(cube.vocabulary) : cube.dimensions;
    const names = { ...cube.vocabulary, ...cube.labels };
    const candidates = pool.filter((d) => d !== "month" && !dims.includes(d)).map((d) => ({ id: d, label: names[d] ?? d }));
    footer = candidates.length === 0 ? null : (
      <Drill key={`${own}=${current.value}`} queryRef={block.query_ref} scope={withoutMember(scope, own)}
        member={{ [own]: current.value }} candidates={candidates} load={drill} onPin={pin} />
    );
  }
  const label = own && cube ? (cube.labels[own] ?? own).toLowerCase() : "";
  const withHint = (text: string | null) => (text || footer || note
    ? <>{note && <p className="hint">Not filtered. <span>{note}</span></p>}{text && <p className="hint">{text}</p>}{footer}</>
    : undefined);
  const onBrush = live && monthly ? (a: string, b: string) => setScope(withRange(scope, a, b)) : undefined;

  if (block.type === "delta_bars") {
    if (!against) return null;
    if ("reason" in against) return <Unavailable title={block.title} reason={against.reason} />;
    return <DeltaBars before={against.view} after={query} title={block.title} />;
  }
  if (block.type === "line") {
    const single = live && dims.length === 0 && monthly;
    const pressed = comparing.includes(block.query_ref);
    const previous = single && pressed ? withPrevious(cube!, def, scope) : null;
    const compare: Compare | undefined = single ? {
      pressed: pressed && previous !== null && !("reason" in previous),
      reason: previous && "reason" in previous ? previous.reason : reason,
      onToggle: () => {
        if (pressed) { setComparing(block.query_ref, false); return; }
        const tried = withPrevious(cube!, def, scope);
        if ("reason" in tried) { setReason(tried.reason); return; }
        setReason(null);
        setComparing(block.query_ref, true);
      },
    } : undefined;
    if (previous && "view" in previous) {
      return <LineChart query={previous.view} title={block.title} compare={compare} />;
    }
    // A member that the line does not group by: show it against the whole.
    const outside: ScopeFilter[] = single ? scope.filters.filter((f) => !dims.includes(f.dimension)) : [];
    let emphasis: Emphasis | undefined;
    if (outside.length > 0) {
      const all = derive(cube!, def, { ...scope, filters: scope.filters.filter((f) => dims.includes(f.dimension)) });
      if ("view" in all) emphasis = { subject: query, whole: all.view, name: outside.map((f) => valueText(f.value, f.dimension)).join(", ") };
    }
    return <LineChart query={query} title={block.title} compare={compare} emphasis={emphasis} onBrush={onBrush}
      footer={withHint(onBrush ? "Drag to select a range." : null)} />;
  }
  if (block.type === "table") {
    const onCell = def ? (dimension: string, value: string | null) => setScope(withMember(scope, dimension, value)) : undefined;
    return <DataTable query={query} title={block.title} onSelect={onCell} dimensions={dims}
      footer={withHint(onCell ? "Click a value to explore it across the report." : null)} />;
  }
  if (block.type === "small_multiples") {
    const pick = own ? (name: string | null) => setScope(name === null ? withoutMember(scope, own) : withMember(withoutMember(scope, own), own, name)) : undefined;
    return <SmallMultiples query={query} title={block.title} onSelect={pick} selected={current?.value ?? null} onBrush={onBrush}
      footer={withHint(onBrush ? "Drag to select a range." : null)} />;
  }
  if (block.type === "segmented") {
    return <Segmented query={query} title={block.title} onSelect={onSelect} selected={selectedLabel}
      footer={withHint(onSelect ? `Click a ${label} to explore it across the report.` : null)} />;
  }
  return <Bars query={query} title={block.title} onSelect={onSelect} selected={selectedLabel}
    footer={withHint(onSelect ? `Click a ${label} to explore it across the report.` : null)} />;
}

export const isChart = (b: Block): b is ChartBlock => "query_ref" in b;
