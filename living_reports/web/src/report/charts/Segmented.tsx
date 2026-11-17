import { useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { columnLabel, type QueryView } from "../../lib/api";
import { pretty } from "./Bars";
import { ChartCard } from "./ChartCard";
import { formatOf, formatValue, NO_DATA } from "./format";
import { slotColor, SLOTS } from "./palette";
import { num } from "./series";
import { TableTwin } from "./TableTwin";
import { Tooltip } from "./Tooltip";

const LABEL_PAD = 16;
const GAP = 2;

type Part = { label: string; value: number; formatted: string; share: number; color: string };

const pct = (share: number) => `${Math.round(share)}%`;

type Props = {
  query: QueryView; title: string;
  onSelect?: (label: string | null) => void; selected?: string | null; footer?: ReactNode;
};

/** One tall bar split into parts of a whole. Parts past the sixth fold into Other. */
export function Segmented({ query, title, onSelect, selected, footer }: Props) {
  const [active, setActive] = useState<number | null>(null);
  const bar = useRef<HTMLDivElement>(null);
  const measure = useRef<HTMLSpanElement>(null);
  const [width, setWidth] = useState(0);
  const [labelWidths, setLabelWidths] = useState<number[]>([]);
  const all = query.points.map((p) => p.label);
  const fmt = formatOf(query.points.find((p) => num(p) !== null)?.formatted ?? "");
  const positive = query.points.filter((p) => (num(p) ?? 0) > 0);
  const total = positive.reduce((sum, p) => sum + num(p)!, 0);
  const share = (v: number) => (total > 0 ? (v / total) * 100 : 0);
  const kept = positive.length > SLOTS ? positive.slice(0, SLOTS) : positive;
  const parts: Part[] = kept.map((p) => ({
    label: p.label, value: num(p)!, formatted: p.formatted, share: share(num(p)!), color: slotColor(p.label, all),
  }));
  if (positive.length > SLOTS) {
    const rest = positive.slice(SLOTS).reduce((sum, p) => sum + num(p)!, 0);
    parts.push({ label: "Other", value: rest, formatted: formatValue(rest, fmt), share: share(rest), color: "var(--gray)" });
  }
  const top = parts.reduce<Part | null>((best, p) => (best === null || p.value > best.value ? p : best), null);
  const summary = top
    ? `${title}, ${parts.length} parts, ${pretty(top.label, query.columns)} largest at ${pct(top.share)}`
    : `${title}, no parts`;
  const twin = (
    <TableTwin columns={[...query.columns.slice(0, 2).map((c) => columnLabel(query, c)), "Share"]} numeric={[1, 2]}
      rows={query.points.map((p) => {
        const v = num(p);
        return [pretty(p.label, query.columns), p.formatted, v !== null && v > 0 ? pct(share(v)) : NO_DATA];
      })} />
  );
  const hovered = active === null ? null : parts[active];
  const shares = parts.map((p) => pct(p.share)).join("|");
  // The share prints inside a part only when the measured label fits the measured part.
  useLayoutEffect(() => {
    const el = bar.current;
    if (!el) return;
    setWidth(el.getBoundingClientRect().width);
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver((entries) => setWidth(entries[0]?.contentRect.width ?? 0));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  useLayoutEffect(() => {
    const spans = measure.current ? [...measure.current.children] as HTMLElement[] : [];
    setLabelWidths(spans.map((n) => n.getBoundingClientRect().width));
  }, [shares]);
  const usable = Math.max(width - GAP * Math.max(parts.length - 1, 0), 0);
  const fits = (i: number) => (labelWidths[i] ?? 0) + LABEL_PAD <= (usable * parts[i].share) / 100;
  let start = 0;
  const lefts = parts.map((p) => { const at = start; start += p.share; return at; });

  return (
    <ChartCard title={title} table={twin} footer={footer}>
      {parts.length === 0 && <p className="empty">No data for this selection.</p>}
      {parts.length > 0 && (
        <ul className="legend">
          {parts.map((p) => (
            <li key={p.label}><span className="legend-key" style={{ background: p.color }} />{pretty(p.label, query.columns)}</li>
          ))}
        </ul>
      )}
      <div className="segwrap">
        {hovered && (
          <Tooltip x={lefts[active!] + hovered.share / 2} heading={pretty(hovered.label, query.columns)}
            rows={[{ key: "", value: hovered.formatted }, { key: "of the total", value: pct(hovered.share) }]} />
        )}
        <span className="seg-measure" ref={measure} aria-hidden="true">
          {parts.map((p) => <span key={p.label}>{pct(p.share)}</span>)}
        </span>
        <div className="segbar" ref={bar} role="group" aria-label={summary} onPointerLeave={() => setActive(null)}>
          {parts.map((p, i) => (
            <button type="button" className="seg" key={p.label}
              style={{ flexGrow: p.value, background: selected != null && p.label !== selected ? "var(--gray)" : p.color }}
              aria-label={`${pretty(p.label, query.columns)}, ${p.formatted}, ${pct(p.share)}`}
              aria-pressed={onSelect ? p.label === selected : undefined}
              onClick={onSelect && p.label !== "Other" ? () => onSelect(p.label === selected ? null : p.label) : undefined}
              onPointerEnter={() => setActive(i)} onFocus={() => setActive(i)} onBlur={() => setActive(null)}>
              {fits(i) && !(selected != null && p.label !== selected) ? pct(p.share) : ""}
            </button>
          ))}
        </div>
      </div>
    </ChartCard>
  );
}
