import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { ApiError, canEditWords, currentUser, isSigner, getJSON, newProgressId, postJSON, putJSON, reportUrl, requestFailed, type AskResult, type CommandResult, type Payload, type QueryDef, type QueryView, type Selection, type Thread, type View } from "../lib/api";
import { AnswerProgress } from "./AnswerProgress";
import { AskBar } from "./AskBar";
import { AskOutcome } from "./AskOutcome";
import { Badge } from "./Badge";
import { Assumptions } from "./Assumptions";
import { AnchorContext, FactsContext, QueriesContext } from "./context";
import { ControlRail } from "./ControlRail";
import { Exploring } from "./Exploring";
import { FilterRow } from "./FilterRow";
import type { ScopedQuery } from "./scope";
import { ScopeProvider } from "./ScopeContext";
import { sectionRef } from "./refs";
import { sectionSlots } from "./SectionTools";
import { describeQuery } from "./chips";
import { useVocabulary } from "./VocabularyContext";
import { VerifiedBadge, verifiedLine } from "./VerifiedBadge";
import { useConfig } from "../lib/config";
import { ThreadPanel } from "./ThreadPanel";
import { VIEWS } from "./views";
import { Brief } from "./views/Brief";
import { Dashboard } from "./views/Dashboard";
import { Evidence } from "./views/Evidence";
import { Narrative } from "./views/Narrative";

export function ReportPage() {
  const { id = "", edition } = useParams();
  const [params, setParams] = useSearchParams();
  const client = useQueryClient();
  const config = useConfig();
  const vocabulary = useVocabulary();
  const report = useQuery({
    queryKey: ["report", id, edition],
    queryFn: () => getJSON<Payload>(edition ? `/api/reports/${id}/editions/${edition}` : `/api/reports/${id}`),
    retry: false,
  });
  const threads = useQuery({ queryKey: ["threads", id], queryFn: () => getJSON<Thread[]>(`/api/reports/${id}/threads`), enabled: report.isSuccess });

  // Drafts live here, above every view, so a change of view keeps them.
  const [askDraft, setAskDraft] = useState("");
  const [threadDrafts, setThreadDrafts] = useState<Record<string, string>>({});
  const [outcome, setOutcome] = useState<AskResult | null>(null);
  // A neutral note about the words after a chip edit. Not an error.
  const [notice, setNotice] = useState<string | null>(null);
  // A signer is warned once per report before a first change that makes them a contributor.
  const [warned, setWarned] = useState(false);
  const [pending, setPending] = useState<{ run: () => void } | null>(null);
  const [active, setActive] = useState<Thread | null>(null);
  // One selected section at a time; its heading button toggles it.
  const [selectedSection, setSelectedSection] = useState<string | null>(null);
  const [lastSelection, setLastSelection] = useState<Selection | null>(null);
  const dismiss = useCallback(() => setActive(null), []);

  const refresh = () => client.invalidateQueries({ queryKey: ["report", id] });

  type Body = { question: string } & Record<string, unknown>;
  type Sent = Body & { progress_id: string };
  const ask = useMutation({
    mutationFn: (body: Sent) => postJSON<AskResult>("/api/ask", { report_id: id, ...body }),
    onSuccess: (result) => {
      setOutcome(result.kind === "report" ? null : result);
      if (result.kind === "report") { setAskDraft(""); setSelectedSection(null); refresh(); }
    },
    onError: (e) => setOutcome(requestFailed(e)),
  });
  const explore = useMutation({
    mutationFn: (body: Sent) => postJSON<AskResult>("/api/explore", body),
    onSuccess: (r) => { setOutcome(null); if (r.report_id) window.open(reportUrl(r.report_id), "_blank"); },
    onError: (e) => setOutcome(requestFailed(e)),
  });
  const newReport = useMutation({
    mutationFn: (body: Sent) => postJSON<AskResult>("/api/ask", body),
    onSuccess: (r) => {
      if (r.kind === "report" && r.report_id) window.open(reportUrl(r.report_id, r.view), "_blank");
      else setOutcome(r);
    },
    onError: (e) => setOutcome(requestFailed(e)),
  });
  const openThread = useMutation({
    mutationFn: (v: { section_id: string; fact_id: string }) => postJSON<Thread>(`/api/reports/${id}/threads`, v),
    onSuccess: setActive,
  });
  const send = useMutation({
    mutationFn: (v: { thread: string; text: string }) => postJSON<Thread>(`/api/threads/${v.thread}/messages`, { text: v.text }),
    onSuccess: (t) => { setActive(t); setThreadDrafts((d) => ({ ...d, [t.id]: "" })); },
  });
  // A pin writes a new draft section. A new key remounts the scope, so it returns to the baseline.
  const [scopeKey, setScopeKey] = useState(0);
  const pinMutation = useMutation({
    mutationFn: (sq: ScopedQuery) => postJSON<AskResult>(`/api/reports/${id}/pin`, sq),
    onSuccess: (result) => {
      if (result.kind === "report") { setOutcome(null); setScopeKey((k) => k + 1); refresh(); }
      else setOutcome(result);
    },
    onError: (e) => setOutcome(requestFailed(e)),
  });
  const pinScope = useCallback((sq: ScopedQuery) => pinMutation.mutate(sq), [pinMutation]);
  const drillQuery = useCallback((sq: ScopedQuery) => postJSON<QueryView>(`/api/reports/${id}/drill`, sq), [id]);

  // A chip edit re-runs one section with no model call.
  const rerun = useMutation({
    mutationFn: (v: { section: string; query: QueryDef }) =>
      postJSON<CommandResult>(`/api/reports/${id}/sections/${v.section}/query`, v.query),
    onSuccess: (result) => {
      setOutcome(null);
      setNotice(result.wording_dropped ? WORDING_DROPPED : result.wording_stale ? WORDING_STALE : null);
      refresh();
    },
    onError: (e) => { setNotice(null); setOutcome(requestFailed(e)); },
  });

  // Section commands: a chart swap (no model call), a wording command (one call), or a removal.
  const command = useMutation({
    mutationFn: (v: { section: string; path: "command" | "chart" | "blocks"; body: object }) =>
      postJSON<CommandResult>(`/api/reports/${id}/sections/${v.section}/${v.path}`, v.body),
    onSuccess: (result) => {
      setOutcome(result.kind === "failed" ? { kind: "failed", message: result.message, cost: result.cost } : null);
      refresh();
    },
    onError: (e) => setOutcome(requestFailed(e)),
  });
  // Editing words in place: one section at a time. A rejected save keeps the editor open.
  const [editing, setEditing] = useState<string | null>(null);
  const saveProse = useMutation({
    mutationFn: (v: { section: string; paragraphs: string[] }) =>
      putJSON<CommandResult>(`/api/reports/${id}/sections/${v.section}/prose`, { paragraphs: v.paragraphs }),
    onSuccess: () => { setEditing(null); refresh(); },
  });
  const editSection = (section: string | null) => { saveProse.reset(); setEditing(section); };
  const forms = useCallback((section: string) =>
    getJSON<{ forms: string[] }>(`/api/reports/${id}/sections/${section}/forms`).then((r) => r.forms), [id]);

  const promote = useMutation({
    mutationFn: (v: { thread: string; question: string }) => postJSON<AskResult>(`/api/threads/${v.thread}/promote`, { question: v.question }),
    onSuccess: () => { setActive(null); refresh(); },
  });

  const data = report.data;
  const anchor = useCallback((factId: string) => {
    if (active?.fact_id === factId) { setActive(null); return; }
    const existing = threads.data?.find((t) => t.fact_id === factId && t.status === "open");
    if (existing) { setActive(existing); return; }
    const section = data?.spec.sections.find((s) => sectionHasFact(s.id, factId)) ?? data?.spec.sections[0];
    if (section) openThread.mutate({ section_id: section.id, fact_id: factId });
  }, [active, threads.data, data, openThread]);

  if (report.isPending) return <p className="state">Loading the report…</p>;
  if (report.isError || !data) return <p className="state">This report is not available.</p>;
  const view = (params.get("view") as View | null) ?? data.view;
  const canAsk = !edition;
  const defs = data.query_defs ?? {};
  const cube = data.cube ?? null;
  const cubeReason = cube ? null : data.cube_reason ?? null;

  const asking = ask.isPending || explore.isPending || newReport.isPending;
  const running = [ask, explore, newReport].find((m) => m.isPending)?.variables;
  // One question at a time: a second one waits until the first answer ends.
  const sendAsk = (body: Body) => {
    if (asking) return;
    setOutcome(null);
    ask.mutate({ ...body, progress_id: newProgressId() });
  };
  const sendOther = (m: typeof explore, question: string) => {
    if (!asking) m.mutate({ question, progress_id: newProgressId() });
  };
  const guard = (run: () => void) => {
    const user = currentUser();
    if (isSigner(user) && !warned && !(data.contributors ?? []).includes(user)) setPending({ run });
    else run();
  };

  const panel = active && (
    <ThreadPanel
      thread={active}
      draft={threadDrafts[active.id] ?? ""}
      onDraft={(text) => setThreadDrafts((d) => ({ ...d, [active.id]: text }))}
      onSend={() => send.mutate({ thread: active.id, text: threadDrafts[active.id] ?? "" })}
      onDismiss={dismiss}
      onAddToReport={() => {
        const last = [...active.messages].reverse().find((m) => m.role === "user");
        if (last) promote.mutate({ thread: active.id, question: last.text });
      }}
      busy={send.isPending || promote.isPending}
    />
  );

  return (
    <FactsContext.Provider value={data.facts}>
      <QueriesContext.Provider value={data.queries}>
        <AnchorContext.Provider value={canAsk ? anchor : null}>
        <ScopeProvider key={scopeKey} cube={cube} defs={defs} reason={cubeReason} hosted
          drill={drillQuery} pin={canAsk ? pinScope : null}>
          <header className="topbar">
            <div className="tabs" role="tablist">
              {VIEWS.map((v) => (
                <button key={v.id} role="tab" aria-selected={v.id === view} onClick={() => setParams({ view: v.id }, { replace: true })}>
                  {v.label}
                </button>
              ))}
            </div>
            <span className="meta">
              {data.edition ? `Edition ${data.edition} · ${data.state === "published" ? "Published" : "Not published"}` : "Draft"}
              {" "}<Badge tier={data.tier} state={data.state} />
              {data.verified && <>{" "}<VerifiedBadge {...data.verified} /> <span>{verifiedLine(data.verified.by, data.verified.at)}</span></>}
            </span>
          </header>
          <div className="with-rail">
          <main className="page">
            <FilterRow />
            <Exploring sections={data.spec.sections.map((s) => ({ ref: sectionRef(s.id), title: s.heading }))} />
            {view === "dashboard" && <Dashboard spec={data.spec} panel={panel} />}
            {view === "brief" && <Brief spec={data.spec} onEvidence={() => setParams({ view: "evidence" }, { replace: true })} />}
            {view === "evidence" && <Evidence spec={data.spec} />}
            {view === "narrative" && <Narrative spec={data.spec} panelFor={(s) => (active?.section_id === s ? panel : null)}
              onRewrite={canAsk && canEditWords(currentUser(), data.author)
                ? (section) => command.mutate({ section, path: "command", body: { command: "regenerate" } }) : undefined}
              slots={canAsk ? sectionSlots({
                reportId: id, defs, cube, rerun: (section, query) => guard(() => rerun.mutate({ section, query })),
                followUp: (query) => guard(() => sendAsk({ question: describeQuery(query, vocabulary), choice: query, objective: data.spec.objective })),
                canEdit: canEditWords(currentUser(), data.author),
                command: (section, c) => command.mutate({ section, path: "command", body: { command: c } }),
                swap: (section, form) => command.mutate({ section, path: "chart", body: { form } }),
                forms,
                editing, edit: editSection,
                saveProse: (section, paragraphs) => saveProse.mutate({ section, paragraphs }),
                saving: saveProse.isPending,
                editError: saveProse.error ? (saveProse.error instanceof ApiError ? saveProse.error.message : "The words could not be saved.") : null,
                blocks: (section, body) => command.mutate({ section, path: "blocks", body }),
                selected: selectedSection, select: (sid) => setSelectedSection((current) => (current === sid ? null : sid)),
              }) : undefined} />}
            <Assumptions
              items={data.spec.assumptions}
              onChange={(text) => sendOther(newReport, `${data.spec.sections[0]?.intent} Assumption: ${text}`)}
            />
            {pending && (
              <div className="warn" role="alertdialog" aria-labelledby="warn-text">
                <p id="warn-text">Editing this means you cannot sign this report. A person who changes a report cannot sign it.</p>
                <button type="button" className="chip" onClick={() => { setWarned(true); const { run } = pending; setPending(null); run(); }}>Continue</button>
                <button type="button" className="link" autoFocus onClick={() => setPending(null)}>Cancel</button>
              </div>
            )}
            {notice && <p className="meta wording-note" role="status">{notice}</p>}
            <AskOutcome
              result={outcome}
              onChoose={(query) => guard(() => sendAsk({ question: askDraft, choice: query, objective: data.spec.objective, selection: lastSelection }))}
              onExplore={() => sendOther(explore, askDraft)}
            />
            {canAsk && (
              <AskBar draft={askDraft} onDraft={setAskDraft} busy={asking}
                onAsk={(question, selection) => guard(() => { setLastSelection(selection); sendAsk({ question, selection }); })}
                section={selectedSection ? data.spec.sections.find((s) => s.id === selectedSection) ?? null : null}
                fact={active?.fact_id ? { id: active.fact_id, label: data.facts[active.fact_id]?.label ?? active.fact_id } : null} />
            )}
            {running && <AnswerProgress progressId={running.progress_id} question={running.question} />}
            {(pinMutation.isPending || rerun.isPending || command.isPending) && <p className="meta">Working…</p>}
          </main>
          <ControlRail reportId={id} edition={edition ? Number(edition) : null} tier={data.tier} cost={data.cost} demo={config.demo} />
          </div>
        </ScopeProvider>
        </AnchorContext.Provider>
      </QueriesContext.Provider>
    </FactsContext.Provider>
  );
}

const WORDING_STALE = "The data changed; these words were written for the earlier question. Use Regenerate wording.";
const WORDING_DROPPED = "The words no longer matched the data, so they were removed. Use Regenerate wording.";

function sectionHasFact(sectionId: string, factId: string): boolean {
  const number = sectionId.replace(/^s/, "");
  return factId.startsWith(`q${number}.`) || factId.startsWith(`q${number}_total.`);
}
