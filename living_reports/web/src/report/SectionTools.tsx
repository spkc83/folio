/** The asking and editing controls of a hosted draft, placed into the Narrative view's slots. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useContext, useState } from "react";
import { getAsUser, postJSON, type CubeView, type QueryDef, type Section } from "../lib/api";
import { Feedback } from "./Feedback";
import { ProseEditor } from "./ProseEditor";
import { FactsContext, QueriesContext } from "./context";
import { isChart } from "./Chart";
import { followUps } from "./followups";
import { FollowUps } from "./FollowUps";
import { QueryChips } from "./QueryChips";
import { SectionMenu, type Command } from "./SectionMenu";
import { sectionFacts, sectionRef } from "./refs";
import { useScope } from "./ScopeContext";
import { useVocabulary } from "./VocabularyContext";
import type { SectionSlots } from "./views/Narrative";

/** The distinct members of a cube dimension, for a filter value list. */
export function membersOf(cube: CubeView | null, member: string): string[] {
  const column = cube ? cube.columns.indexOf(member) : -1;
  if (!cube || column < 0) return [];
  return [...new Set(cube.rows.map((r) => r[column]).filter((v) => v !== null && v !== undefined).map(String))].sort();
}

/** The latest month in the cube, or null. */
export function latestMonth(cube: CubeView | null): string | null {
  const column = cube ? cube.columns.indexOf("month") : -1;
  if (!cube || column < 0) return null;
  return cube.rows.reduce<string | null>((max, r) => {
    const m = r[column] as string | null;
    return m && (!max || m > max) ? m : max;
  }, null);
}

type Signal = "up" | "down";

/** The current user's feedback on one section, read and written through the section route. */
function SectionFeedback({ reportId, sectionId }: { reportId: string; sectionId: string }) {
  const client = useQueryClient();
  const key = ["feedback", reportId, sectionId];
  const url = `/api/reports/${reportId}/sections/${sectionId}/feedback`;
  const saved = useQuery({ queryKey: key, queryFn: () => getAsUser<{ signal: Signal | null }>(url), retry: false });
  const send = useMutation({
    mutationFn: (signal: Signal) => postJSON<{ ok: boolean }>(url, { signal }),
    onSuccess: (_, signal) => client.setQueryData(key, { signal }),
  });
  return <Feedback value={send.isPending ? send.variables ?? null : saved.data?.signal ?? null} onChange={(s) => send.mutate(s)} />;
}

export type Tools = {
  reportId: string;
  defs: Record<string, QueryDef>;
  cube: CubeView | null;
  rerun: (sectionId: string, query: QueryDef) => void;
  /** The selected section, toggled by its heading button. */
  selected: string | null;
  select: (sectionId: string) => void;
  /** Adds a section from a follow-up query (one narration call, no router call). */
  followUp: (query: QueryDef) => void;
  /** The current user may change the words (the server checks again). */
  canEdit: boolean;
  command: (sectionId: string, c: Command) => void;
  swap: (sectionId: string, form: string) => void;
  forms: (sectionId: string) => Promise<string[]>;
  /** The section whose words are open in the editor. */
  editing: string | null;
  edit: (sectionId: string | null) => void;
  saveProse: (sectionId: string, paragraphs: string[]) => void;
  saving: boolean;
  editError: string | null;
  blocks: (sectionId: string, body: { order: number[] } | { remove: number }) => void;
};

function SectionEditor({ section, t }: { section: Section; t: Tools }) {
  const facts = useContext(FactsContext);
  const ids = sectionFacts(section, facts);
  const paragraphs = section.blocks.flatMap((b) => (b.type === "prose" ? [b.text] : []));
  return <ProseEditor paragraphs={paragraphs} busy={t.saving} error={t.editError}
    facts={ids.map((id) => ({ id, label: facts[id].label, formatted: facts[id].formatted }))}
    onSave={(p) => t.saveProse(section.id, p)} onCancel={() => t.edit(null)} />;
}

/** Move up, Move down, and Remove block (behind one confirm step) under one chart. */
function BlockTools({ title, up, down, onOrder, onRemove }: {
  title: string; up: number[] | null; down: number[] | null; onOrder: (order: number[]) => void; onRemove: () => void;
}) {
  const [confirming, setConfirming] = useState(false);
  if (confirming) {
    return (
      <div className="block-tools" role="group" aria-label="Remove this block?">
        <span className="meta">Remove this block?</span>
        <button type="button" onClick={() => { setConfirming(false); onRemove(); }}>Remove</button>
        <button type="button" autoFocus onClick={() => setConfirming(false)}>Cancel</button>
      </div>
    );
  }
  return (
    <div className="block-tools" role="group" aria-label={`Arrange ${title}`}>
      <button type="button" aria-label={`Move up: ${title}`} disabled={!up} onClick={() => up && onOrder(up)}>Move up</button>
      <button type="button" aria-label={`Move down: ${title}`} disabled={!down} onClick={() => down && onOrder(down)}>Move down</button>
      <button type="button" aria-label={`Remove block: ${title}`} onClick={() => setConfirming(true)}>Remove block</button>
    </div>
  );
}

/** Move a chart past the chart before or after it, in the order of the section's blocks. */
function moved(section: Section, index: number, step: -1 | 1): number[] | null {
  const charts = section.blocks.flatMap((b, i) => (isChart(b) ? [i] : []));
  const at = charts.indexOf(index);
  const other = charts[at + step];
  if (other === undefined) return null;
  const order = section.blocks.map((_, i) => i);
  [order[index], order[other]] = [order[other], order[index]];
  return order;
}

/** The follow-up row of one section. "Compare" presses the compare tool of the section's line. */
function SectionFollowUps({ section, def, onAsk }: { section: Section; def: QueryDef; onAsk: (q: QueryDef) => void }) {
  const vocabulary = useVocabulary();
  const queries = useContext(QueriesContext);
  const { setComparing } = useScope();
  if (!vocabulary) return null;
  const line = section.blocks.find((b) => b.type === "line");
  const ref = sectionRef(section.id);
  const items = followUps(def, vocabulary, queries[ref], Boolean(line), section.follow_ups);
  return <FollowUps items={items} onAsk={onAsk}
    onCompare={() => { if (line && "query_ref" in line) setComparing(line.query_ref, true); }} />;
}

export function sectionSlots(t: Tools): SectionSlots {
  const latest = latestMonth(t.cube);
  return {
    head: (section) => (
      <div className="section-head">
        <h2 className="heading">
          <button type="button" className={`heading-btn${t.selected === section.id ? " selected" : ""}`}
            aria-pressed={t.selected === section.id} onClick={() => t.select(section.id)}>{section.heading}</button>
        </h2>
        <SectionMenu sectionId={section.id} canEdit={t.canEdit} onCommand={(c) => t.command(section.id, c)}
          onSwap={(form) => t.swap(section.id, form)} forms={() => t.forms(section.id)}
          onEdit={() => { if (t.canEdit) t.edit(section.id); }} />
      </div>
    ),
    prose: (section) => (t.editing === section.id && t.canEdit ? <SectionEditor section={section} t={t} /> : null),
    block: (section, index, title) => {
      if (!t.canEdit) return null;
      return <BlockTools title={title} up={moved(section, index, -1)} down={moved(section, index, 1)}
        onOrder={(order) => t.blocks(section.id, { order })} onRemove={() => t.blocks(section.id, { remove: index })} />;
    },
    after: (section) => {
      const def = t.defs[sectionRef(section.id)];
      return (
        <>
          {def && <SectionFollowUps section={section} def={def} onAsk={t.followUp} />}
          <SectionFeedback reportId={t.reportId} sectionId={section.id} />
        </>
      );
    },
    chips: (section) => {
      const def = t.defs[sectionRef(section.id)];
      if (!def) return null;
      return <QueryChips query={def} onChange={(q) => t.rerun(section.id, q)}
        members={(m) => membersOf(t.cube, m)} latest={latest} />;
    },
  };
}
