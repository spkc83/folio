import { useEffect, useRef, useState } from "react";

type Props = {
  paragraphs: string[];
  facts: { id: string; label: string; formatted: string }[];
  onSave: (paragraphs: string[]) => void;
  onCancel: () => void;
  busy: boolean;
  error: string | null;
};

/** Edits a section's words in place. Facts stay `{fact:id}` tokens; the server checks the text on save. */
// The server's bounds on a person's words (PUT /prose).
const MAX_PARAGRAPHS = 50;
const MAX_CHARS = 4000;

function limitProblem(paragraphs: string[]): string | null {
  if (paragraphs.length > MAX_PARAGRAPHS) return "A section has at most 50 paragraphs.";
  if (paragraphs.some((p) => p.length > MAX_CHARS)) return "A paragraph has at most 4,000 characters.";
  return null;
}

export function ProseEditor({ paragraphs, facts, onSave, onCancel, busy, error }: Props) {
  const [text, setText] = useState(paragraphs.join("\n\n"));
  const [limit, setLimit] = useState<string | null>(null);
  const area = useRef<HTMLTextAreaElement>(null);
  const caret = useRef<number | null>(null);
  useEffect(() => { area.current?.focus(); }, []);
  useEffect(() => {
    if (caret.current !== null && area.current) {
      area.current.focus();
      area.current.setSelectionRange(caret.current, caret.current);
      caret.current = null;
    }
  }, [text]);

  const insert = (id: string) => {
    const el = area.current;
    const token = `{fact:${id}}`;
    const start = el?.selectionStart ?? text.length;
    const end = el?.selectionEnd ?? start;
    caret.current = start + token.length;
    setText(text.slice(0, start) + token + text.slice(end));
  };

  return (
    <div className="editor" onKeyDown={(e) => { if (e.key === "Escape") { e.stopPropagation(); onCancel(); } }}>
      <textarea aria-label="Section wording" ref={area} value={text} rows={Math.max(4, text.split("\n").length + 1)}
        onChange={(e) => setText(e.target.value)} />
      <p className="meta">Separate paragraphs with a blank line. Write values only as tokens; select a value to add its token.</p>
      {facts.length > 0 && (
        <div className="tokens" role="group" aria-label="Values in this section">
          {facts.map((f) => (
            <button type="button" className="chip" key={f.id} title={`{fact:${f.id}}`} onClick={() => insert(f.id)}>
              {f.label}: {f.formatted}
            </button>
          ))}
        </div>
      )}
      {(limit ?? error) && <p className="notice" role="alert">{limit ?? error}</p>}
      <div className="editor-actions">
        <button type="button" className="chip" disabled={busy}
          onClick={() => {
            const parts = text.split(/\n\s*\n/).map((p) => p.trim()).filter((p) => p);
            const problem = limitProblem(parts);
            setLimit(problem);
            if (!problem) onSave(parts);
          }}>Save</button>
        <button type="button" className="link" onClick={onCancel}>Cancel</button>
        {busy && <span className="meta">Checking the words…</span>}
      </div>
    </div>
  );
}
