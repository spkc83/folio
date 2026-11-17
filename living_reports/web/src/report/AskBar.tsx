import type { Selection } from "../lib/api";
import { describeSelection, selectionOf } from "./selection";
import { useScope } from "./ScopeContext";
import { SendButton } from "./SendButton";

type Props = {
  draft: string;
  onDraft: (text: string) => void;
  onAsk: (question: string, selection: Selection | null) => void;
  section: { id: string; heading: string } | null;
  fact: { id: string; label: string } | null;
  /** An answer is running: the bar keeps its text and does not send a second question. */
  busy?: boolean;
};

const NOTHING = "Select a number, a bar, or a range, or ask about this report…";

/** The ask bar. Its placeholder names the selection, and a question carries it. */
export function AskBar({ draft, onDraft, onAsk, section, fact, busy = false }: Props) {
  const { scope, cube } = useScope();
  const labels = { ...(cube?.vocabulary ?? {}), ...(cube?.labels ?? {}) };
  const text = describeSelection(scope, labels, section?.heading ?? null, fact?.label ?? null);
  return (
    <form className="ask-bar ask-form" onSubmit={(e) => {
      e.preventDefault();
      if (draft.trim() && !busy) onAsk(draft, selectionOf(scope, section?.id ?? null, fact?.id ?? null));
    }}>
      <input
        aria-label="Ask about this report"
        aria-busy={busy}
        readOnly={busy}
        className="ask-input"
        placeholder={text ? `Ask about ${text}…` : NOTHING}
        value={draft}
        onChange={(e) => onDraft(e.target.value)}
      />
      <SendButton busy={busy} empty={!draft.trim()} />
    </form>
  );
}
