import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useState } from "react";
import { Link } from "react-router-dom";
import {
  answerUrl, getJSON, newProgressId, postJSON, reportUrl, requestFailed,
  type AskResult, type CertifiedQuestion, type ChangeItem, type Digest, type ProposalView, type ReportSummary,
} from "../lib/api";
import { AnswerProgress } from "../report/AnswerProgress";
import { AskOutcome } from "../report/AskOutcome";
import { SendButton } from "../report/SendButton";
import { Certified } from "./Certified";
import { ChangeCard } from "./ChangeCard";
import { DemoControls } from "./DemoControls";
import { useConfig } from "../lib/config";
import { SchemaPanel } from "./SchemaPanel";
import { Search } from "./Search";
import { ThisWeek } from "./ThisWeek";

export function Home({ pollMs = 15_000 }: { pollMs?: number }) {
  const [question, setQuestion] = useState("");
  const [result, setResult] = useState<AskResult | null>(null);
  const [source, setSource] = useState<string | null>(null);
  const onSource = useCallback((id: string) => setSource(id), []);
  const addTerm = (label: string) => setQuestion((q) => (q.trim() ? `${q.trim()} ${label.toLowerCase()}` : label));
  const reports = useQuery({ queryKey: ["reports"], queryFn: () => getJSON<ReportSummary[]>("/api/reports") });
  // Polling pauses under the pointer, so a card never moves under a click.
  const [hovering, setHovering] = useState(false);
  const client = useQueryClient();
  const changes = useQuery({
    queryKey: ["changes"],
    queryFn: () => getJSON<ChangeItem[]>("/api/changes"),
    refetchInterval: hovering ? false : pollMs,
  });
  const certified = useQuery({ queryKey: ["certified"], queryFn: () => getJSON<CertifiedQuestion[]>("/api/certified") });
  const digest = useQuery({ queryKey: ["digest"], queryFn: () => getJSON<Digest>("/api/digest") });
  const modelProposals = useQuery({ queryKey: ["model-proposals"], queryFn: () => getJSON<ProposalView[]>("/api/model/proposals") });
  const pending = modelProposals.data?.length ?? 0;
  const { placeholder, starters = [] } = useConfig();

  const onResult = (r: AskResult) => {
    setResult(r);
    if (r.kind === "report" && r.report_id) window.open(answerUrl(r), "_blank");
    client.invalidateQueries({ queryKey: ["reports"] });
  };
  const onError = (e: unknown) => setResult(requestFailed(e));
  type Sent = { question?: string; progress_id: string } & Record<string, unknown>;
  const ask = useMutation({ mutationFn: (body: Sent) => postJSON<AskResult>("/api/ask", body), onSuccess: onResult, onError });
  const explore = useMutation({ mutationFn: (body: Sent) => postJSON<AskResult>("/api/explore", body), onSuccess: onResult, onError });
  const busy = ask.isPending || explore.isPending;
  const running = ask.isPending ? ask.variables : explore.isPending ? explore.variables : undefined;
  // One question at a time: a second one waits until the first answer ends.
  const send = (body: Record<string, unknown>) => {
    if (busy) return;
    setResult(null);
    ask.mutate({ ...body, progress_id: newProgressId() });
  };
  // A typed question goes to the selected dataset. Certified and suggested questions keep their own.
  const askTyped = (body: Record<string, unknown>) => send(source ? { source_id: source, ...body } : body);
  const askCertified = (id: string) => send({ certified_id: id });

  return (
    <main className="page home">
      <nav className="home-nav meta"><Link to="/model">Model</Link></nav>
      <h1 className="title">What do you want to know?</h1>
      {pending > 0 && (
        <p className="outcome">
          <Link to="/model">Review the model ({pending} {pending === 1 ? "proposal" : "proposals"})</Link>
        </p>
      )}
      <form className="ask-form" onSubmit={(e) => { e.preventDefault(); if (question.trim()) askTyped({ question }); }}>
        <input
          aria-label="Ask a question"
          aria-busy={busy}
          readOnly={busy}
          className="ask-input big-ask"
          placeholder={placeholder ?? "Ask a question about your data"}
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
        />
        <SendButton busy={busy} empty={!question.trim()} />
      </form>
      {running && <AnswerProgress progressId={running.progress_id} question={running.question ?? ""} />}
      <SchemaPanel onPick={addTerm} onSource={onSource} />
      <Certified items={certified.data ?? []} onAsk={askCertified} />
      {certified.data?.length === 0 && starters.length > 0 && (
        <div className="certified">
          {starters.map((q) => (
            <button key={q} type="button" className="chip" onClick={() => { setQuestion(q); askTyped({ question: q }); }}>{q}</button>
          ))}
        </div>
      )}
      <Search onOpen={(hit) => window.open(reportUrl(hit.report_id), "_blank")} />
      <AskOutcome
        result={result}
        onChoose={(query) => askTyped({ question, choice: query })}
        onExplore={() => { if (!busy) explore.mutate({ question, progress_id: newProgressId() }); }}
      />
      {result?.kind === "report" && result.report_id && (
        <p className="outcome">
          <a href={answerUrl(result)} target="_blank" rel="noreferrer">Open the report</a>
        </p>
      )}
      {digest.data && (
        <ThisWeek
          digest={digest.data}
          onAsk={(q) => send({ question: q.question, choice: q.query })}
          onAskCertified={askCertified}
        />
      )}
      <h2 className="heading">What changed</h2>
      {changes.data?.length === 0 && <p className="meta">No changes yet. New data and new definitions appear here.</p>}
      <div className="changes" onPointerEnter={() => setHovering(true)} onPointerLeave={() => setHovering(false)}>
        {changes.data?.map((c) => <ChangeCard key={c.id} item={c} />)}
      </div>
      <h2 className="heading">Recent reports</h2>
      {reports.data?.length === 0 && <p className="meta">Your reports appear here. Ask a question to create the first one.</p>}
      <ul className="report-list">
        {reports.data?.map((r) => (
          <li key={r.id}><a href={reportUrl(r.id)} target="_blank" rel="noreferrer">{r.title}</a></li>
        ))}
      </ul>
      <DemoControls />
    </main>
  );
}
