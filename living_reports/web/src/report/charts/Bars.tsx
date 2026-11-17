import { useState, type ReactNode } from "react";
import { columnLabel, type QueryView } from "../../lib/api";
import { displayParts } from "../labels";
import { ChartCard } from "./ChartCard";
import { TableTwin } from "./TableTwin";
import { Tooltip } from "./Tooltip";

/** A point label in words. `members` names its parts in order (a view's columns). */
export const pretty = (label: string, members: string[] = []) => displayParts(label, members);

/** Ranked horizontal bars on a zero baseline. Each bar is a button with its own readout. */
type Props = {
  query: QueryView; title: string; emphasis?: string;
  /** Cross-filter: a click selects the member, and a click on the selected member clears it. */
  onSelect?: (label: string | null) => void; selected?: string | null;
  footer?: ReactNode;
};

export function Bars({ query, title, emphasis, onSelect, selected, footer }: Props) {
  const [active, setActive] = useState<number | null>(null);
  const points = query.points;
  const values = points.map((p) => (typeof p.value === "number" && Number.isFinite(p.value) ? p.value : 0));
  const max = Math.max(...values, 0);
  const top = points[values.indexOf(max)];
  const summary = top
    ? `${title}, ${points.length} values, ${pretty(top.label, query.columns)} highest at ${top.formatted}`
    : `${title}, no values`;
  const twin = (
    <TableTwin columns={query.columns.slice(0, 2).map((c) => columnLabel(query, c))} numeric={[1]}
      rows={points.map((p) => [pretty(p.label, query.columns), p.formatted])} />
  );
  const hovered = active === null ? null : points[active];
  return (
    <ChartCard title={title} table={twin} footer={footer}>
      {points.length === 0 && <p className="empty">No data for this selection.</p>}
      <div className="barlist" role="group" aria-label={summary} onPointerLeave={() => setActive(null)}>
        {hovered && (
          <Tooltip x={max > 0 ? Math.min((values[active!] / max) * 100, 100) : 0}
            rows={[{ key: pretty(hovered.label, query.columns), value: hovered.formatted }]} />
        )}
        {points.map((p, i) => {
          const focus = selected ?? emphasis;
          const dim = focus !== undefined && focus !== null && p.label !== focus;
          const pressed = onSelect ? p.label === selected : undefined;
          return (
            <button type="button" className="bar-row" key={p.fact_id}
              aria-label={`${pretty(p.label, query.columns)}, ${p.formatted}`} aria-pressed={pressed}
              onClick={onSelect ? () => onSelect(p.label === selected ? null : p.label) : undefined}
              onPointerEnter={() => setActive(i)} onFocus={() => setActive(i)} onBlur={() => setActive(null)}>
              <span className="bar-label" title={p.label}>{pretty(p.label, query.columns)}</span>
              <span className="bar-track">
                <span className="bar-fill" style={{
                  width: max > 0 ? `${(values[i] / max) * 100}%` : 0,
                  background: dim ? "var(--gray)" : "var(--c1)",
                }} />
              </span>
              <span className="bar-value">{p.formatted}</span>
            </button>
          );
        })}
      </div>
    </ChartCard>
  );
}
