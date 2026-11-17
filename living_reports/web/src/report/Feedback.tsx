type Signal = "up" | "down";

/** A per-section signal for the usage ledger. It has no visible effect on the report. */
export function Feedback({ value, onChange }: { value: Signal | null; onChange: (s: Signal) => void }) {
  return (
    <div className="feedback" role="group" aria-label="Feedback on this section">
      <button type="button" aria-pressed={value === "up"} onClick={() => onChange("up")}>
        <span aria-hidden="true">▲</span> Helpful
      </button>
      <button type="button" aria-pressed={value === "down"} onClick={() => onChange("down")}>
        <span aria-hidden="true">▼</span> Not helpful
      </button>
      {value && <span className="meta">Your feedback is recorded. It does not change the report.</span>}
    </div>
  );
}
