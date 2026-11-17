import type { AskResult, QueryDef } from "../lib/api";
import { QueryChips } from "./QueryChips";

type Props = {
  result: AskResult | null;
  onChoose: (query: QueryDef) => void;
  onExplore: () => void;
};

/** Shows every non-report outcome of a question. A clarifying question shows each reading as a chip row. */
export function AskOutcome({ result, onChoose, onExplore }: Props) {
  if (!result) return null;
  if (result.kind === "clarify" && result.clarify) {
    return (
      <div className="outcome" role="status">
        <p>{result.clarify.question}</p>
        {result.clarify.options.map((o) => (
          <div className="clarify-option" key={o.label}>
            <QueryChips query={o.query} readOnly />
            <button type="button" className="chip" onClick={() => onChoose(o.query)}>{o.label}</button>
          </div>
        ))}
      </div>
    );
  }
  if (result.kind === "out_of_scope" || result.kind === "failed") {
    return (
      <div className="outcome" role="status">
        <p>{result.message}</p>
        {result.can_explore && (
          <button type="button" className="chip" onClick={onExplore}>Explore with unverified SQL</button>
        )}
      </div>
    );
  }
  return null;
}
