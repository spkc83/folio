import { useRef, useState, type KeyboardEvent, type PointerEvent, type ReactNode } from "react";
import { columnLabel, type QueryView } from "../../lib/api";
import { ChartCard } from "./ChartCard";
import { linear, niceTicks, tickFormat } from "./scale";
import { num, periodLabel, shape, tickIndexes, type Series } from "./series";
import { TableTwin } from "./TableTwin";
import { Tooltip } from "./Tooltip";

const W = 660, H = 200, L = 52, R = 64, T = 14, B = 28;
/** Six named series, one per color slot, then Other. */
const MAX_SERIES = 7;
/** Past two lines, the end labels collide; the legend and the readout name the values. */
const END_LABELS = 2;

export type Compare = { pressed: boolean; onToggle: () => void; reason?: string | null };
export type Emphasis = { subject: QueryView; whole: QueryView; name: string };

type Props = {
  query: QueryView; title: string;
  /** The tool that compares the period with the period before it. */
  compare?: Compare;
  /** One member against the whole: the member in the accent, the whole in gray. */
  emphasis?: Emphasis;
  /** A drag, or Shift and an arrow key then Enter, selects a range of x values. */
  onBrush?: (a: string, b: string) => void;
  footer?: ReactNode;
};

function emphasized(e: Emphasis): { xs: string[]; series: Series[] } {
  const whole = shape(e.whole, 1);
  const subject = shape(e.subject, 1);
  const byX = new Map(subject.xs.map((x, i) => [x, subject.series[0]?.points[i]]));
  return {
    xs: whole.xs,
    series: [
      { name: "All", label: "All", color: "var(--gray)", points: whole.series[0]?.points ?? [] },
      { name: e.name, label: e.name, color: "var(--c1)", points: whole.xs.map((x) => byX.get(x)) },
    ],
  };
}

/** A line per series on a zero baseline, with an axis, a crosshair, and keyboard steps. */
export function LineChart({ query, title, compare, emphasis, onBrush, footer }: Props) {
  const [at, setAt] = useState<number | null>(null);
  const [brush, setBrush] = useState<[number, number] | null>(null);
  const dragging = useRef(false);
  const { xs, series } = emphasis ? emphasized(emphasis) : shape(query, MAX_SERIES);
  const n = xs.length;
  const legend = emphasis ? [...series].reverse() : series;
  const heading = emphasis ? `${title} · ${emphasis.name} against all` : title;
  const values = series.flatMap((s) => s.points.map(num)).filter((v): v is number => v !== null);
  const ticks = niceTicks(Math.max(...values, 0));
  const fmt = tickFormat(query.points);
  const x = linear([0, Math.max(n - 1, 1)], [L, W - R]);
  const y = linear([0, ticks[ticks.length - 1]], [H - B, T]);
  const labelAt = tickIndexes(xs.map(periodLabel), W - L - R);
  const multi = series.length > 1;
  const valueColumn = columnLabel(query, query.columns[query.columns.length - 1] ?? "");

  const twin = (
    <TableTwin
      columns={[columnLabel(query, query.columns.find((c) => c !== query.series) ?? ""), ...series.map((s) => (multi ? s.label : valueColumn))]}
      numeric={series.map((_, i) => i + 1)}
      rows={xs.map((label, i) => [periodLabel(label), ...series.map((s) => s.points[i]?.formatted ?? "No data")])}
    />
  );

  const clamp = (i: number) => Math.max(0, Math.min(n - 1, i));
  const indexOf = (e: PointerEvent<SVGSVGElement>): number | null => {
    const box = e.currentTarget.getBoundingClientRect();
    if (!box.width || n === 0) return null;
    const px = ((e.clientX - box.left) / box.width) * W;
    if (!Number.isFinite(px)) return null;
    return clamp(Math.round(((px - L) / (W - L - R)) * (n - 1)));
  };
  // A range is low index first; a click with no drag is one x value.
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
    try { e.currentTarget.setPointerCapture(e.pointerId); } catch { /* No capture in some test hosts. */ }
  };
  const onUp = (e: PointerEvent<SVGSVGElement>) => {
    if (!dragging.current || !brush) return;
    dragging.current = false;
    apply(brush[0], indexOf(e) ?? brush[1]);
  };
  const onKey = (e: KeyboardEvent<SVGSVGElement>) => {
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
    <ChartCard title={heading} table={twin} footer={footer}
      tools={compare && (
        <button type="button" aria-pressed={compare.pressed} onClick={compare.onToggle}>Compare with previous period</button>
      )}>
      {compare?.reason && <p className="meta compare-reason">{compare.reason}</p>}
      {n === 0 && <p className="empty">No data for this selection.</p>}
      {n > 0 && (
        <div className="line-wrap">
          {multi && (
            <ul className="legend">
              {legend.map((s) => (
                <li key={s.name}><span className="tip-key" style={{ background: s.color }} />{s.label}</li>
              ))}
            </ul>
          )}
          <svg viewBox={`0 0 ${W} ${H}`} role="img" tabIndex={0}
            aria-label={`${heading}. ${n} points. Use the arrow keys to read each point.${onBrush ? " Drag or press Shift and an arrow key to select a range." : ""}`}
            className={onBrush ? "brushable" : undefined}
            onPointerDown={onDown} onPointerUp={onUp}
            onPointerMove={onMove} onPointerLeave={() => setAt(null)}
            onFocus={() => setAt(at ?? n - 1)} onBlur={() => setAt(null)} onKeyDown={onKey}>
            {brush && (
              <rect className="brush" x={x(Math.min(...brush)) - 4} y={T}
                width={x(Math.max(...brush)) - x(Math.min(...brush)) + 8} height={H - B - T} />
            )}
            {ticks.map((t) => (
              <g key={t}>
                <line x1={L} x2={W - R} y1={y(t)} y2={y(t)} stroke="var(--grid)" />
                <text className="tick" x={L - 8} y={y(t) + 4} fontSize="11" fill="var(--mute)" textAnchor="end">
                  {t === 0 ? "0" : fmt(t)}
                </text>
              </g>
            ))}
            {labelAt.map((i) => (
              <text key={i} className="x-tick" x={x(i)} y={H - 8} fontSize="11" fill="var(--mute)" textAnchor="middle">{periodLabel(xs[i])}</text>
            ))}
            {series.map((s) => {
              const line = s.points.flatMap((p, i) => (num(p) === null ? [] : [`${x(i)},${y(num(p)!)}`]));
              let last = s.points.length - 1;
              while (last >= 0 && num(s.points[last]) === null) last -= 1;
              return (
                <g key={s.name}>
                  <polyline fill="none" stroke={s.color} strokeWidth="2" strokeLinejoin="round" points={line.join(" ")} />
                  {last >= 0 && series.length <= END_LABELS && (
                    <text x={x(last) + 6} y={y(num(s.points[last])!) + 4} fontSize="12" fontWeight="600" fill="var(--ink)">
                      {s.points[last]!.formatted}
                    </text>
                  )}
                </g>
              );
            })}
            {at !== null && (
              <g>
                <line x1={x(at)} x2={x(at)} y1={T} y2={H - B} stroke="var(--soft)" />
                {series.map((s) => {
                  const v = num(s.points[at]);
                  return v === null ? null : (
                    <circle key={s.name} cx={x(at)} cy={y(v)} r="4.5" fill={s.color} stroke="var(--card)" strokeWidth="2" />
                  );
                })}
              </g>
            )}
          </svg>
          {at !== null && (
            <Tooltip x={(x(at) / W) * 100} heading={periodLabel(xs[at])}
              rows={legend.map((s) => ({
                key: multi ? s.label : "",
                color: multi ? s.color : undefined,
                value: s.points[at]?.formatted ?? "No data",
              }))} />
          )}
        </div>
      )}
    </ChartCard>
  );
}
