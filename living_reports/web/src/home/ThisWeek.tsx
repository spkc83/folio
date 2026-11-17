import type { Digest, SuggestedQuestion } from "../lib/api";
import { KIND_LABEL } from "./ChangeCard";

type Props = { digest: Digest; onAsk: (q: SuggestedQuestion) => void; onAskCertified: (id: string) => void };

/** The weekly digest: the top changes of the last seven days and questions to ask next. */
export function ThisWeek({ digest, onAsk, onAskCertified }: Props) {
  if (digest.top_changes.length + digest.suggested_questions.length === 0) return null;
  return (
    <section className="week" aria-labelledby="week-h">
      <h2 id="week-h" className="heading">This week</h2>
      {digest.top_changes.length > 0 && (
        <ul className="week-list">
          {digest.top_changes.map((c) => <li key={c.id}>{KIND_LABEL[c.kind]} · {c.title}</li>)}
        </ul>
      )}
      {digest.suggested_questions.length > 0 && (
        <>
          <h3 className="week-sub">Suggested questions</h3>
          <div className="certified">
            {digest.suggested_questions.map((q) => (
              <button
                key={q.question}
                type="button"
                className="chip"
                onClick={() => (q.certified_id ? onAskCertified(q.certified_id) : onAsk(q))}
              >
                {q.question}
              </button>
            ))}
          </div>
        </>
      )}
    </section>
  );
}
