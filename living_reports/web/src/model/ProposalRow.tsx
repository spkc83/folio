import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { postJSON, type ConfirmResult, type Probe, type ProposalView } from "../lib/api";

const RENAMEABLE = ["Field", "MetricBinding"];

/** Pass or Fail as a word and a mark, never color alone. */
export function ProbeResult({ probe }: { probe: Probe | null }) {
  if (!probe) return <span className="meta">No probe</span>;
  const pass = probe.status === "pass";
  return (
    <span className={`probe ${pass ? "pass" : "fail"}`}>
      <span role="img" aria-label={pass ? "Pass" : "Fail"}>{pass ? "✓" : "✗"}</span>{" "}
      <strong>{pass ? "Pass" : "Fail"}</strong>{" "}
      <span>{probe.detail}</span>
    </span>
  );
}

type Mode = "idle" | "rename" | "reject";

export function ProposalRow({ view, owner }: { view: ProposalView; owner: boolean }) {
  const client = useQueryClient();
  const [mode, setMode] = useState<Mode>("idle");
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const id = view.proposal.id;
  const done = () => { setMode("idle"); setText(""); setError(null); client.invalidateQueries({ queryKey: ["model-proposals"] }); };
  const fail = (e: unknown) => setError(e instanceof Error ? e.message : "The request failed.");
  const confirm = useMutation({ mutationFn: () => postJSON<ConfirmResult>("/api/model/proposals/confirm", { ids: [id] }), onSuccess: done, onError: fail });
  const rename = useMutation({ mutationFn: () => postJSON(`/api/model/proposals/${id}/rename`, { label: text }), onSuccess: done, onError: fail });
  const reject = useMutation({ mutationFn: () => postJSON(`/api/model/proposals/${id}/reject`, { reason: text }), onSuccess: done, onError: fail });
  const busy = confirm.isPending || rename.isPending || reject.isPending;
  const off = !owner || busy;
  const cancel = () => { setMode("idle"); setText(""); };

  return (
    <tr>
      <td><code>{view.node_id}</code></td>
      <td>{view.summary}</td>
      <td><ProbeResult probe={view.probe} /></td>
      <td className="actions">
        {mode === "idle" && (
          <>
            <button type="button" className="chip" disabled={off} onClick={() => confirm.mutate()}>Confirm</button>
            {RENAMEABLE.includes(view.kind) && (
              <button type="button" className="chip" disabled={off} onClick={() => setMode("rename")}>Rename</button>
            )}
            <button type="button" className="chip" disabled={off} onClick={() => setMode("reject")}>Reject</button>
          </>
        )}
        {mode !== "idle" && (
          <form onSubmit={(e) => { e.preventDefault(); if (text.trim()) (mode === "rename" ? rename : reject).mutate(); }}>
            <input
              aria-label={mode === "rename" ? "New label" : "Reason"}
              value={text}
              autoFocus
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Escape") cancel(); }}
            />
            <button type="submit" className="chip" disabled={off || !text.trim()}>
              {mode === "rename" ? "Save" : "Send rejection"}
            </button>
            <button type="button" className="chip" onClick={cancel}>Cancel</button>
          </form>
        )}
        {error && <p role="alert" className="row-error">{error}</p>}
      </td>
    </tr>
  );
}
