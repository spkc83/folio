import { useRef, useState, type KeyboardEvent, type PointerEvent, type ReactNode } from "react";
import { columnLabel, type QueryView } from "../../lib/api";
import { ChartCard } from "./ChartCard";
import { linear, niceTicks, tickFormat } from "./scale";
import { num, periodLabel, shape } from "./series";
import { TableTwin } from "./TableTwin";
import { Tooltip } from "./Tooltip";

const W = 330, H = 130, L = 52, R = 64, T = 10, B = 16;
const MAX_PANELS = 6;

/** One small line per member on one shared y axis. The panel names are the legend. */
type Props = {
  query: QueryView; title: string;
  onSelect?: (name: string | null) => void; selected?: string | null;
  onBrush?: (a: string, b: string) => void;
  footer?: ReactNode;
};

export function SmallMultiples({ query, title, onSelect, selected, onBrush, footer }: Props) {
  const [at, setAt] = useState<number | null>(null);
  const [brush, setBrush] = useState<[number, number] | null>(null);
  const dragging = useRef(false);
  const { xs, series } = shape(query, MAX_PANELS);
  const n = xs.length;
  const values = series.flatMap((s) => s.points.map(num)).filter((v): v is number => v !== null);
  const ticks = niceTicks(Math.max(...values, 0));
  const fmt = tickFormat(query.points);
  const x = linear([0, Math.max(n - 1, 1)], [L, W - R]);
  const y = linear([0, ticks[ticks.length - 1]], [H - B, T]);
  const clamp = (i: number) => Math.max(0, Math.min(n - 1, i));
  const xColumn = query.columns.find((c) => c !== query.series) ?? "";

  const twin = (
    <TableTwin columns={[columnLabel(query, xColumn), ...series.map((s) => s.label)]} numeric={series.map((_, i) => i + 1)}
      rows={xs.map((label, i) => [periodLabel(label), ...series.map((s) => s.points[i]?.formatted ?? "No data")])} />
  );
  const indexOf = (e: PointerEvent<SVGSVGElement>): number | null => {
    const box = e.currentTarget.getBoundingClientRect();
    if (!box.width || n === 0) return null;
    const px = ((e.clientX - box.left) / box.width) * W;
    return Number.isFinite(px) ? clamp(Math.round(((px - L) / (W - L - R)) * (n - 1))) : null;
  };
  // The same brush as the line chart: low index first, a click is one x value.
  const apply = (a: number, b: number) => {
    setBrush(null);
    onBrush?.(xs[Math.min(a, b)], xs[Math.max(a, b)]);
  };
  const onMove = (e: PointerEvent<SVGSVGElement>) => {
    const i = indexOf(e);
    if (i === null) return;
    setAt(i);
    if (dragging.current && brush) setBrush([brush[0], i]);
  };
  const onDown = (e: PointerEvent<SVGSVGElement>) => {
    const i = indexOf(e);
    if (!onBrush || i === null) return;
    dragging.current = true;
    setBrush([i, i]);
  };
  const onUp = (e: PointerEvent<SVGSVGElement>) => {
    if (!dragging.current || !brush) return;
    dragging.current = false;
    apply(brush[0], indexOf(e) ?? brush[1]);
  };
  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    if (onBrush && e.key === "Enter" && brush) { e.preventDefault(); apply(brush[0], brush[1]); return; }
    if (e.key === "Escape" && brush) { e.preventDefault(); setBrush(null); return; }
    const step = ({ ArrowLeft: -1, ArrowRight: 1, Home: -n, End: n } as Record<string, number>)[e.key];
    if (step === undefined) return;
    e.preventDefault();
    const from = at ?? n - 1;
    const to = clamp(from + step);
    setAt(to);
    if (onBrush && e.shiftKey) setBrush([brush ? brush[0] : from, to]);
  };

  return (
    <ChartCard title={title} table={twin} footer={footer}>
      {n === 0 && <p className="empty">No data for this selection.</p>}
      {n > 0 && (
        <div className="line-wrap">
          {onSelect && (
            <div className="panel-picks" role="group" aria-label="Filter by a panel">
              {series.filter((s) => s.name !== "Other").map((s) => (
                <button type="button" key={s.name} className="chip" aria-label={`Select ${s.label}`}
                  aria-pressed={s.name === selected} onClick={() => onSelect(s.name === selected ? null : s.name)}>
                  {s.label}
                </button>
              ))}
            </div>
          )}
          <div className="multiples" role="img" tabIndex={0}
            aria-label={`${title}. ${series.length} panels, ${n} points each. Use the arrow keys to read each point.${onBrush ? " Drag or press Shift and an arrow key to select a range." : ""}`}
            onFocus={() => setAt(at ?? n - 1)} onBlur={() => setAt(null)} onKeyDown={onKey}
            onPointerLeave={() => setAt(null)}>
            {series.map((s) => {
              const line = s.points.flatMap((p, i) => (num(p) === null ? [] : [`${x(i)},${y(num(p)!)}`]));
              let last = s.points.length - 1;
              while (last >= 0 && num(s.points[last]) === null) last -= 1;
              const v = at === null ? null : num(s.points[at]);
              return (
                <div className="panel" key={s.name}>
                  <span className="panel-name">{s.label}</span>
                  <svg viewBox={`0 0 ${W} ${H}`} onPointerMove={onMove} onPointerDown={onDown} onPointerUp={onUp}>
                    {brush && (
                      <rect className="brush" x={x(Math.min(...brush)) - 3} y={T}
                        width={x(Math.max(...brush)) - x(Math.min(...brush)) + 6} height={H - B - T} />
                    )}
                    {ticks.map((t) => (
                      <g key={t}>
                        <line x1={L} x2={W - R} y1={y(t)} y2={y(t)} stroke="var(--grid)" />
                        <text className="tick" x={L - 6} y={y(t) + 4} fontSize="11" fill="var(--mute)" textAnchor="end">
                          {t === 0 ? "0" : fmt(t)}
                        </text>
                      </g>
                    ))}
                    <polyline fill="none" stroke={s.color} strokeWidth="2" strokeLinejoin="round" points={line.join(" ")} />
                    {last >= 0 && (
                      <text x={x(last) + 5} y={y(num(s.points[last])!) + 3} fontSize="12" fontWeight="600" fill="var(--ink)">
                        {s.points[last]!.formatted}
                      </text>
                    )}
                    {at !== null && (
                      <g className="crosshair">
                        <line x1={x(at)} x2={x(at)} y1={T} y2={H - B} stroke="var(--soft)" />
                        {v !== null && <circle cx={x(at)} cy={y(v)} r="4" fill={s.color} stroke="var(--card)" strokeWidth="2" />}
                      </g>
                    )}
                  </svg>
                </div>
              );
            })}
          </div>
          {at !== null && (
            <Tooltip x={50} heading={periodLabel(xs[at])}
              rows={series.map((s) => ({ key: s.label, color: s.color, value: s.points[at]?.formatted ?? "No data" }))} />
          )}
        </div>
      )}
    </ChartCard>
  );
}
