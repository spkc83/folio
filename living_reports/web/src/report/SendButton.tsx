/** The send button inside an ask box. It is gray and does nothing while an answer runs or the box is empty. */
export function SendButton({ busy, empty }: { busy: boolean; empty: boolean }) {
  return (
    <button type="submit" className="send" disabled={busy || empty}
      aria-label={busy ? "Building your answer" : "Send the question"}
      title={busy ? "Building your answer" : "Send (Enter)"}>
      <svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true">
        <path d="M10 16V4M4.5 9.5 10 4l5.5 5.5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    </button>
  );
}
