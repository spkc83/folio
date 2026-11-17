import { useEffect, useRef } from "react";
import type { Thread } from "../lib/api";
import { RichText } from "./RichText";

type Props = {
  thread: Thread;
  draft: string;
  onDraft: (text: string) => void;
  onSend: () => void;
  onDismiss: () => void;
  onAddToReport: () => void;
  busy: boolean;
};

/** A thread that stays on one number. Escape or a click outside closes it. */
export function ThreadPanel({ thread, draft, onDraft, onSend, onDismiss, onAddToReport, busy }: Props) {
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onDismiss(); };
    const onDown = (e: PointerEvent) => {
      const target = e.target as Element;
      if (!box.current?.contains(target) && !target.closest?.(".fact-btn")) onDismiss();
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onDown);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onDown);
    };
  }, [onDismiss]);

  const lastQuestion = [...thread.messages].reverse().find((m) => m.role === "user");
  return (
    <div className="card thread" ref={box}>
      {thread.messages.map((m, i) => (
        <p key={i} className={m.role === "user" ? "thread-q" : "thread-a"}><RichText text={m.text} /></p>
      ))}
      <form onSubmit={(e) => { e.preventDefault(); if (draft.trim()) onSend(); }}>
        <input
          aria-label="Ask about this number"
          className="ask-input"
          placeholder="Ask about this number…"
          value={draft}
          autoFocus
          onChange={(e) => onDraft(e.target.value)}
        />
      </form>
      {busy && <p className="meta">Working…</p>}
      {lastQuestion && <button type="button" className="chip" onClick={onAddToReport}>Add to report</button>}
    </div>
  );
}
