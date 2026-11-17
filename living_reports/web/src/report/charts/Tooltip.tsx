export type TipRow = { key: string; color?: string; value: string };

/** The readout for one position. Values lead; labels are text, never markup. */
export function Tooltip({ x, heading, rows }: { x: number; heading?: string; rows: TipRow[] }) {
  return (
    <div className="tip" role="status" style={{ left: `${Math.min(Math.max(x, 12), 88)}%` }}>
      {heading && <div>{heading}</div>}
      {rows.map((r, i) => (
        <div key={i}>
          {r.color && <span className="tip-key" style={{ background: r.color }} />}
          <strong>{r.value}</strong> <span className="tip-label">{r.key}</span>
        </div>
      ))}
    </div>
  );
}
