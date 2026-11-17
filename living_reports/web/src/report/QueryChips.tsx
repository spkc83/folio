import { useEffect, useRef, useState, type FormEvent } from "react";
import type { QueryDef, QueryFilter, Vocabulary } from "../lib/api";
import { chipsOf, flatFilters, removeFilter, setDimension, setFilter, setGrain, setMetric, setTime, type Chip } from "./chips";
import { lastMonth, nextMonth } from "./scope";
import { useVocabulary } from "./VocabularyContext";

type Props = {
  query: QueryDef;
  onChange?: (q: QueryDef) => void;
  readOnly?: boolean;
  /** Known members of a dimension, for the filter value list. */
  members?: (member: string) => string[];
  /** The latest month with data (`YYYY-MM-01`), for "Last 12 months". */
  latest?: string | null;
};

/** The routed question as chips. With `onChange`, a chip opens an editor and a change re-runs the section. */
export function QueryChips({ query, onChange, readOnly, members, latest }: Props) {
  const vocabulary = useVocabulary();
  const [open, setOpen] = useState<number | null>(null);
  const chipRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const chips = chipsOf(query, vocabulary);
  const editable = Boolean(onChange) && !readOnly;
  const close = (i: number) => { setOpen(null); chipRefs.current[i]?.focus(); };
  return (
    <div className="query" role="group" aria-label="The routed question">
      {chips.map((chip, i) => (
        <span className="q-wrap" key={`${chip.kind}:${chip.key}`}>
          {editable && !chip.fixed ? (
            <button type="button" className="q" ref={(el) => { chipRefs.current[i] = el; }}
              aria-expanded={open === i} onClick={() => setOpen(open === i ? null : i)}>
              <span>{chip.label}</span>{" "}{chip.value}
            </button>
          ) : <span className="q"><span>{chip.label}</span>{" "}{chip.value}</span>}
          {editable && open === i && (
            <ChipEditor chip={chip} query={query} vocabulary={vocabulary} members={members?.(chip.key) ?? []} latest={latest ?? null}
              onApply={(q) => { onChange!(q); close(i); }} onCancel={() => close(i)} />
          )}
        </span>
      ))}
      {editable && <span className="meta">The question as the system read it. Edit a chip to change it.</span>}
    </div>
  );
}

type EditorProps = {
  chip: Chip; query: QueryDef; vocabulary: Vocabulary | null; members: string[]; latest: string | null;
  onApply: (q: QueryDef) => void; onCancel: () => void;
};

// Same order as EDITABLE_OPS in chips.ts.
const OPS: [string, string][] = [["eq", "="], ["ne", "≠"], ["is_null", "is empty"]];
const month = (iso: string | null | undefined) => (iso ? iso.slice(0, 7) : "");

function ChipEditor({ chip, query, vocabulary, members, latest, onApply, onCancel }: EditorProps) {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => { box.current?.querySelector<HTMLElement>("select, input")?.focus(); }, []);
  const filter = flatFilters(query).find((f) => f.member === chip.key);
  const [choice, setChoice] = useState(() => initial(chip, query));
  const [op, setOp] = useState(filter && OPS.some(([o]) => o === filter.op) ? filter.op : "eq");
  const [value, setValue] = useState(filter ? filter.values.map(String).join(", ") : "");
  const [from, setFrom] = useState(month(query.time?.start));
  const [to, setTo] = useState(query.time?.end ? month(lastMonth(query.time.end)) : "");

  const apply = (e: FormEvent) => {
    e.preventDefault();
    onApply(edited());
  };
  const edited = (): QueryDef => {
    if (chip.kind === "metric") return setMetric(query, choice);
    if (chip.kind === "by" && chip.key === "grain") return setGrain(query, choice || null);
    if (chip.kind === "by") return setDimension(query, choice || null);
    if (chip.kind === "filter") {
      const values: QueryFilter["values"] = op === "is_null" ? [] : [value.trim()];
      return setFilter(query, chip.key, op, values);
    }
    if (choice === "all") return setTime(query, null);
    if (choice === "last12") {
      const end = nextMonth(latest ?? new Date().toISOString().slice(0, 7) + "-01");
      return setTime(query, { start: nextMonth(end, -12), end });
    }
    return setTime(query, { start: from ? `${from}-01` : null, end: to ? nextMonth(`${to}-01`) : null });
  };

  const dims = (vocabulary?.dimensions ?? []).filter((d) => !d.is_time);
  const listId = `members-${chip.key.replace(/\W/g, "-")}`;
  return (
    <div className="chip-editor" role="dialog" aria-label={`Edit ${chip.label}`} ref={box}
      onKeyDown={(e) => { if (e.key === "Escape") { e.stopPropagation(); onCancel(); } }}>
      <form onSubmit={apply}>
        {chip.kind === "metric" && (
          <label>Metric{" "}
            <select aria-label="Metric" value={choice} onChange={(e) => setChoice(e.target.value)}>
              {(vocabulary?.metrics ?? []).map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
            </select>
          </label>
        )}
        {chip.kind === "by" && (
          <label>{chip.key === "grain" ? "Time grain" : "By"}{" "}
            <select aria-label={chip.key === "grain" ? "Time grain" : "By"} value={choice} onChange={(e) => setChoice(e.target.value)}>
              <option value="">None</option>
              {chip.key === "grain"
                ? (vocabulary?.grains ?? ["month"]).map((g) => <option key={g} value={g}>{g.charAt(0).toUpperCase() + g.slice(1)}</option>)
                : dims.map((d) => <option key={d.id} value={d.id}>{d.label}</option>)}
            </select>
          </label>
        )}
        {chip.kind === "filter" && (
          <>
            <label>Operator{" "}
              <select aria-label="Operator" value={op} onChange={(e) => setOp(e.target.value)}>
                {OPS.map(([o, text]) => <option key={o} value={o}>{text}</option>)}
              </select>
            </label>
            {op !== "is_null" && (
              <label>Value{" "}
                <input aria-label="Value" value={value} onChange={(e) => setValue(e.target.value)} list={members.length ? listId : undefined} required />
                {members.length > 0 && <datalist id={listId}>{members.map((m) => <option key={m} value={m} />)}</datalist>}
              </label>
            )}
            <button type="button" onClick={() => onApply(removeFilter(query, chip.key))}>Remove</button>
          </>
        )}
        {chip.kind === "time" && (
          <>
            <label>Dates{" "}
              <select aria-label="Dates" value={choice} onChange={(e) => setChoice(e.target.value)}>
                <option value="all">All dates</option>
                <option value="last12">Last 12 months</option>
                <option value="custom">Custom…</option>
              </select>
            </label>
            {choice === "custom" && (
              <>
                <label>From <input aria-label="From" type="month" value={from} onChange={(e) => setFrom(e.target.value)} /></label>
                <label>To <input aria-label="To" type="month" value={to} onChange={(e) => setTo(e.target.value)} /></label>
              </>
            )}
          </>
        )}
        <div className="chip-editor-actions">
          <button type="submit">Apply</button>
          <button type="button" onClick={onCancel}>Cancel</button>
        </div>
      </form>
    </div>
  );
}

function initial(chip: Chip, query: QueryDef): string {
  if (chip.kind === "metric") return chip.key;
  if (chip.kind === "by") return chip.key === "grain" ? query.time?.grain ?? "" : chip.key;
  if (chip.kind === "time") return query.time?.start || query.time?.end ? "custom" : "all";
  return "";
}
