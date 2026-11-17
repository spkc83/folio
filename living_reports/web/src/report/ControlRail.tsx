import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { DemoUser } from "../DemoUser";
import { ApiError, currentUser, getJSON, postJSON, type Cost, type EditionState, type Publication } from "../lib/api";

const STEP_LABEL = { trace: "Numbers traced", totals: "Totals", rules: "Policy rules", wording: "Wording" };
const STATUS_LABEL = { pass: "Pass", fail: "Fail", open: "Open" };
const TIER_LABEL = { exploratory: "Exploratory", team: "Team report", business_critical: "Business critical" };
const ROLE_LABEL: Record<string, string> = { owner: "Owner", compliance: "Compliance" };

const RUNG_LABEL: Record<string, string> = {
  cache: "from cache", certified: "certified question", small_spec: "small model", code_mode: "code mode", frontier: "frontier model",
};

type Props = { reportId: string; edition: number | null; tier: keyof typeof TIER_LABEL; cost?: Cost; demo?: boolean };

export function costLine(cost?: Cost): string | null {
  if (!cost || cost.usd === undefined) return null;
  const rung = cost.rung ? ` · ${RUNG_LABEL[cost.rung] ?? cost.rung}` : "";
  if (cost.usd === null) return `The cost of this answer is unknown${rung}`;
  return `This answer cost $${cost.usd.toFixed(4)}${rung}`;
}

/** The state of a report: tier, checks, signatures, and the next action. */
export function ControlRail({ reportId, edition, tier, cost, demo }: Props) {
  const client = useQueryClient();
  const [error, setError] = useState<ApiError | null>(null);
  const [user, setUser] = useState(currentUser());
  const [note, setNote] = useState("");
  const key = ["publication", reportId];
  const publication = useQuery({ queryKey: key, queryFn: () => getJSON<Publication>(`/api/reports/${reportId}/publication`) });

  const act = useMutation({
    mutationFn: (v: { path: string; body?: unknown }) => postJSON<unknown>(`/api/reports/${reportId}/${v.path}`, v.body ?? {}),
    onSuccess: () => {
      setError(null);
      setNote("");
      client.invalidateQueries({ queryKey: key });
      client.invalidateQueries({ queryKey: ["edition-state", reportId] });
      client.invalidateQueries({ queryKey: ["report", reportId] });
    },
    onError: (e) => setError(e as ApiError),
  });

  // An edition page shows that edition. It never acts on a newer one.
  const pageState = useQuery({
    queryKey: ["edition-state", reportId, edition],
    queryFn: () => getJSON<EditionState>(`/api/reports/${reportId}/editions/${edition}/state`),
    enabled: edition !== null,
  });
  const state: EditionState | null | undefined = edition !== null ? pageState.data : publication.data?.edition;
  const stale = edition !== null && state && state.newest > edition;
  const canAct = !!state && !stale && state.status !== "published" && (edition !== null || !publication.data?.draft_changed);
  const wordingOpen = state?.checks.some((c) => c.step === "wording" && c.status === "open");
  const blocked = !!state && state.checks.some((c) => c.status === "fail");
  const signPath = state && `editions/${state.number}`;
  const line = costLine(cost);

  return (
    <aside className="rail">
      <div className="kicker">{TIER_LABEL[tier]}</div>
      {edition === null && tier !== "exploratory" && (
        <label className="tier-field">
          <span className="meta">Tier</span>
          <select value={tier} onChange={(e) => act.mutate({ path: "tier", body: { tier: e.target.value } })}>
            <option value="team">Team report</option>
            <option value="business_critical">Business critical</option>
          </select>
        </label>
      )}
      {stale && <p className="notice"><Link to={`/r/${reportId}/e/${state.newest}`}>A newer edition exists</Link></p>}

      {state && (
        <div className="card rail-card">
          {state.checks.map((c) => (
            <div key={c.step}>
              <div className="check"><span>{STEP_LABEL[c.step]}</span><span data-status={c.status}>{STATUS_LABEL[c.status]}</span></div>
              {c.status !== "pass" && c.findings.map((f, i) => <p className="finding" key={i}>{f.detail}</p>)}
            </div>
          ))}
          {state.signatures.map((s) => (
            <div className="check" key={s.role}><span>{ROLE_LABEL[s.role]}</span><span>Signed by {s.signer}</span></div>
          ))}
          {state.needs.map((r) => (
            <div className="check" key={r}><span>{ROLE_LABEL[r]}</span><span>Waiting</span></div>
          ))}
        </div>
      )}

      {tier !== "exploratory" && publication.data?.draft_changed && edition === null && (
        <button type="button" className="primary" onClick={() => act.mutate({ path: "editions" })} disabled={act.isPending}>
          {act.isPending ? "Running…" : "Run checks"}
        </button>
      )}
      {edition !== null && state && state.status !== "published" && <p className="notice">This edition is not published.</p>}
      {canAct && (
        <>
          {wordingOpen && user === "chidi" && (
            <form onSubmit={(e) => { e.preventDefault(); if (note.trim()) act.mutate({ path: `${signPath}/wording`, body: { note } }); }}>
              <input aria-label="Review note" className="ask-input" placeholder="Review note" value={note} onChange={(e) => setNote(e.target.value)} />
            </form>
          )}
          <button type="button" className="secondary" disabled={blocked || state.needs.length === 0} onClick={() => act.mutate({ path: `${signPath}/sign`, body: { content_hash: state.content_hash } })}>Sign</button>
          <button type="button" className="primary" disabled={!state.can_publish} onClick={() => act.mutate({ path: `${signPath}/publish` })}>Publish</button>
        </>
      )}
      {state?.status === "published" && (
        <p className="notice"><Link to={`/r/${reportId}/e/${state.number}`}>Edition {state.number} is published</Link></p>
      )}
      {state && state.number > 1 && (
        <Link className="link" to={`/r/${reportId}/diff?from=${state.number - 1}&to=${state.number}`}>See what changed</Link>
      )}
      {state?.status === "published" && <a className="link" href={`/api/reports/${reportId}/editions/${state.number}/export`}>Download as HTML</a>}

      {error && (
        <div className="notice" role="alert">
          <p>{error.message}</p>
          {error.currentEdition !== null && <Link to={`/r/${reportId}/e/${error.currentEdition}`}>Open edition {error.currentEdition}</Link>}
        </div>
      )}
      {line && <p className="meta">{line}</p>}
      {demo && <DemoUser onChange={setUser} />}
    </aside>
  );
}
