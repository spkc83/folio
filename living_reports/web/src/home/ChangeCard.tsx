import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { getJSON, postJSON, type ApiError, type ChangeItem, type Impact } from "../lib/api";

export const KIND_LABEL = {
  new_data: "New data", new_column: "New column", new_value: "New value", definition: "Definition change",
  question: "Certified question",
};

/** One change. A change of meaning needs two clicks, and it shows the effect first. */
export function ChangeCard({ item }: { item: ChangeItem }) {
  const client = useQueryClient();
  const [open, setOpen] = useState(false);
  const [armed, setArmed] = useState(false);
  const isDefinition = item.kind === "definition";
  const done = item.status === "done";
  const impact = useQuery({
    queryKey: ["impact", item.proposal_id],
    queryFn: () => getJSON<Impact>(`/api/graph/proposals/${item.proposal_id}/impact`),
    enabled: open && isDefinition,
  });
  const confirm = useMutation({
    mutationFn: () => postJSON(`/api/graph/proposals/${item.proposal_id}/confirm`, {}),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["changes"] });
      client.invalidateQueries({ queryKey: ["reports"] });
    },
  });
  return (
    <article className="card change">
      <span className="tag">{KIND_LABEL[item.kind]}</span>
      {item.is_new && <>{" "}<span className="tag" data-kind="new">New</span></>}
      <h3 className="change-title">{item.title}</h3>
      <p className="meta">{item.detail}</p>
      {item.affected_reports.length > 0 && <p className="meta">{item.affected_reports.length} report(s) affected</p>}
      {(item.attention?.length ?? 0) > 0 && <p className="finding">{item.attention?.length} report(s) could not update. Open each one and ask the question again.</p>}
      {done && <p className="meta">Resolved</p>}
      {!done && item.proposal_id && !open && <button type="button" className="chip" onClick={() => setOpen(true)}>Review</button>}
      {open && (
        <div className="review">
          {impact.data && (
            <p className="before-after"><span>{impact.data.before.formatted}</span> <span aria-hidden>→</span> <b>{impact.data.after.formatted}</b></p>
          )}
          {!armed && <button type="button" className="chip" onClick={() => setArmed(true)}>Approve</button>}
          {armed && <button type="button" className="primary" onClick={() => confirm.mutate()} disabled={confirm.isPending}>Confirm approval</button>}
          {confirm.isError && <p className="notice" role="alert">{(confirm.error as ApiError).message}</p>}
        </div>
      )}
    </article>
  );
}
