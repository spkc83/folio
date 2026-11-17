import type { QueryDef } from "../lib/api";
import type { FollowUp } from "./followups";

type Props = { items: FollowUp[]; onAsk: (q: QueryDef) => void; onCompare: () => void };

/** Follow-up questions under a section. Each adds a section with one narration call; compare saves nothing. */
export function FollowUps({ items, onAsk, onCompare }: Props) {
  if (items.length === 0) return null;
  return (
    <div className="follow" role="group" aria-label="Follow up">
      <span className="lbl">Follow up</span>
      {items.map((f) => (
        <button type="button" className="chip" key={f.label} onClick={() => (f.compare ? onCompare() : f.query && onAsk(f.query))}>
          {f.label}
        </button>
      ))}
      <span className="meta">Built from the vocabulary, not the model.</span>
    </div>
  );
}
