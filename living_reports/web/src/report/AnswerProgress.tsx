import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { getJSON, type ProgressView } from "../lib/api";

type Props = { progressId: string; question: string; pollMs?: number };

/** Shows that an answer is being built: each stage that is done, with its time, and the stage that runs now. */
export function AnswerProgress({ progressId, question, pollMs = 700 }: Props) {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    const started = Date.now();
    const timer = setInterval(() => setElapsed(Math.floor((Date.now() - started) / 1000)), 1000);
    return () => clearInterval(timer);
  }, [progressId]);
  // Before the backend starts the answer, the id is unknown (404): keep polling.
  const view = useQuery({
    queryKey: ["progress", progressId],
    queryFn: () => getJSON<ProgressView>(`/api/progress/${progressId}`),
    refetchInterval: pollMs,
    retry: false,
  });
  const steps = view.data?.steps ?? [];
  return (
    <div className="progress" role="status" aria-live="polite" aria-label="Answer progress">
      <div className="progress-head">
        <span className="spinner" aria-hidden="true" />
        <span className="progress-title">Building your answer</span>
        <span className="progress-time">{elapsed} s</span>
      </div>
      {question && <p className="progress-question">“{question}”</p>}
      <ol className="progress-steps">
        {steps.length === 0 && <li className="running">Start the answer…</li>}
        {steps.map((s, n) => (
          <li key={`${s.key}-${n}`} className={s.seconds === null ? "running" : "done"}>
            <span className="mark" aria-hidden="true">{s.seconds === null ? "" : "✓"}</span>
            <span className="label">{s.label}{s.seconds === null ? "…" : ""}</span>
            {s.seconds !== null && <span className="seconds">{s.seconds.toFixed(1)} s</span>}
          </li>
        ))}
      </ol>
      <p className="meta">This can take a minute or more.</p>
    </div>
  );
}
