import { useEffect, useRef, type ReactNode } from "react";
import type { FactT } from "../lib/api";

type Props = { fact: FactT; onAsk?: () => void; onClose: () => void };

/**
 * Where one number comes from: metric version, definition, data, query, and check.
 * Built from inline elements only, because a number sits inside a paragraph.
 */
export function Provenance({ fact, onAsk, onClose }: Props) {
  const box = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    box.current?.focus();
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    const onDown = (e: PointerEvent) => {
      const target = e.target as Element;
      if (!box.current?.parentElement?.contains(target)) onClose();
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onDown);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onDown);
    };
  }, [onClose]);

  const traced = fact.state === "ok" && Boolean(fact.query_hash);
  const rows: [string, ReactNode][] = [
    ["Value", fact.formatted],
    ["Source", fact.source_id],
    ["Data time", fact.data_time ? fact.data_time.slice(0, 10) : "Unknown"],
    ["Query", <code key="q">{fact.query_hash.slice(0, 8) || "None"}</code>],
    ["Check", <span key="c" className="st" data-ok={traced}>{traced ? "✓ Traced" : "No data"}</span>],
  ];
  return (
    <span className="pop" role="dialog" aria-label="Where this number comes from" tabIndex={-1} ref={box}>
      <strong className="pop-title">{fact.label}</strong>
      {fact.metric && <span className="pop-metric">{fact.metric} · version {fact.metric_version}</span>}
      {fact.definition && <span className="def">{fact.definition}</span>}
      <span className="pop-grid">
        {rows.map(([name, value]) => (
          <span className="pop-row" key={name}><span className="pop-key">{name}</span><span>{value}</span></span>
        ))}
      </span>
      {onAsk && traced && (
        <span className="acts"><button type="button" className="chip" onClick={onAsk}>Ask about this number</button></span>
      )}
    </span>
  );
}
