import type { CertifiedQuestion } from "../lib/api";
import { VerifiedBadge, verifiedLine } from "../report/VerifiedBadge";

/** Certified questions. A current one opens its report at no model cost. */
export function Certified({ items, onAsk }: { items: CertifiedQuestion[]; onAsk: (id: string) => void }) {
  if (items.length === 0) return null;
  return (
    <div className="certified">
      {items.map((q) => (
        <button key={q.id} type="button" className="chip certified" onClick={() => onAsk(q.id)}>
          <span className="certified-q">{q.question}</span>{" "}
          <VerifiedBadge by={q.verified_by} at={q.verified_at} current={q.current} />{" "}
          <span className="meta">{verifiedLine(q.verified_by, q.verified_at)}</span>
          {!q.current && <span className="meta"> Asking runs the question again.</span>}
        </button>
      ))}
    </div>
  );
}
