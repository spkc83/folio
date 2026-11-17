import { useState } from "react";
import { columnLabel, type Point, type QueryView } from "../../lib/api";
import { pretty } from "./Bars";
import { ChartCard } from "./ChartCard";
import { formatOf, formatValue, NO_DATA } from "./format";
import { signedTicks, tickFormat } from "./scale";
import { num } from "./series";
import { TableTwin } from "./TableTwin";
import { Tooltip } from "./Tooltip";

type Row = { label: string; before?: Point; after?: Point; change: number | null };

/** The change for each member on a signed axis with a zero line. The sign is always in the text. */
export function DeltaBars({ before, after, title }: { before: QueryView; after: QueryView; title: string }) {
  const [active, setActive] = useState<number | null>(null);
  const fmt = formatOf(after.points.find((p) => num(p) !== null)?.formatted ?? before.points[0]?.formatted ?? "");
  const signed = (v: number | null) => (v === null ? NO_DATA : `${v >= 0 ? "+" : ""}${formatValue(v, fmt)}`);
  const byLabel = (q: QueryView) => new Map(q.points.map((p) => [p.label, p]));
  const was = byLabel(before), now = byLabel(after);
  const labels = [...new Set([...now.keys(), ...was.keys()])];
  const rows: Row[] = labels.map((label) => {
    const b = num(was.get(label)), a = num(now.get(label));
    return { label, before: was.get(label), after: now.get(label), change: a === null || b === null ? null : a - b };
  });
  rows.sort((x, y) => (x.change === null ? 1 : 0) - (y.change === null ? 1 : 0) || (y.change ?? 0) - (x.change ?? 0));

  const changes = rows.map((r) => r.change).filter((v): v is number => v !== null);
  const ticks = signedTicks(Math.min(...changes, 0), Math.max(...changes, 0));
  const lo = ticks[0], hi = ticks[ticks.length - 1];
  const at = (v: number) => ((v - lo) / (hi - lo)) * 100;
  const zero = at(0);
  const axis = tickFormat(after.points);
  const top = rows[0];
  const summary = top && top.change !== null && top.change > 0
    ? `${title}, ${rows.length} members, largest increase ${pretty(top.label, after.columns)} at ${signed(top.change)}`
    : `${title}, ${rows.length} members, no increase`;
  const text = (p?: Point) => p?.formatted ?? NO_DATA;
  const twin = (
    <TableTwin columns={[columnLabel(after, after.columns[0] ?? ""), "Before", "After", "Change"]} numeric={[1, 2, 3]}
      rows={rows.map((r) => [pretty(r.label, after.columns), text(r.before), text(r.after), signed(r.change)])} />
  );
  const hovered = active === null ? null : rows[active];

  return (
    <ChartCard title={title} table={twin}>
      {rows.length === 0 && <p className="empty">No data for this selection.</p>}
      <div className="barlist" role="group" aria-label={summary} onPointerLeave={() => setActive(null)}>
        {hovered && (
          <Tooltip x={hovered.change === null ? zero : at(hovered.change)} heading={pretty(hovered.label, after.columns)}
            rows={[
              { key: "After", value: text(hovered.after) },
              { key: "Before", value: text(hovered.before) },
              { key: "Change", value: signed(hovered.change) },
            ]} />
        )}
        {rows.map((r, i) => {
          const c = r.change;
          const left = c === null ? zero : Math.min(at(c), zero);
          const width = c === null ? 0 : Math.abs(at(c) - zero);
          return (
            <button type="button" className="bar-row delta-row" key={r.label}
              aria-label={`${pretty(r.label, after.columns)}, ${text(r.after)} from ${text(r.before)}, change ${signed(c)}`}
              onPointerEnter={() => setActive(i)} onFocus={() => setActive(i)} onBlur={() => setActive(null)}>
              <span className="bar-label" title={r.label}>{pretty(r.label, after.columns)}</span>
              <span className="delta-track">
                <span className="delta-zero" style={{ left: `${zero}%` }} />
                {c !== null && (
                  <span className="delta-fill" data-sign={c < 0 ? "neg" : "pos"}
                    style={{ left: `${left}%`, width: `${width}%` }} />
                )}
              </span>
              <span className="bar-value">{signed(c)}</span>
            </button>
          );
        })}
        {rows.length > 0 && (
          <div className="delta-row delta-axis-row" aria-hidden="true">
            <span />
            <span className="delta-axis">
              {ticks.map((t) => (
                <span key={t} style={{ left: `${at(t)}%` }}>{t === 0 ? "0" : axis(t)}</span>
              ))}
            </span>
            <span />
          </div>
        )}
      </div>
    </ChartCard>
  );
}
