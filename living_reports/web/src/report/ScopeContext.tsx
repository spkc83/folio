import { createContext, useContext, useMemo, useState, type ReactNode } from "react";
import type { CubeView, QueryDef, QueryView } from "../lib/api";
import { QueriesContext } from "./context";
import { derive } from "./cube";
import { BASELINE, isBaseline, withoutMember, type Scope, type ScopedQuery } from "./scope";

export type ScopeState = {
  scope: Scope;
  setScope: (s: Scope) => void;
  cube: CubeView | null;
  defs: Record<string, QueryDef>;
  reason: string | null;
  drill: ((sq: ScopedQuery) => Promise<QueryView>) | null;
  pin: ((sq: ScopedQuery) => void) | null;
  /** The hosted page drills by any dimension in the graph; the export only by cube dimensions. */
  hosted: boolean;
  /** Refs whose chart shows the compare tool pressed. Compare is not saved by a pin. */
  comparing: string[];
  setComparing: (ref: string, on: boolean) => void;
};

const ScopeContext = createContext<ScopeState>({
  scope: BASELINE, setScope: () => undefined, cube: null, defs: {}, reason: null, drill: null, pin: null,
  hosted: false, comparing: [], setComparing: () => undefined,
});

type Props = Omit<ScopeState, "scope" | "setScope" | "comparing" | "setComparing"> & { children: ReactNode };

/** Owns the one scope of the report. Every view, chart, and tile below reads it. */
export function ScopeProvider({ cube, defs, reason, drill, pin, hosted, children }: Props) {
  const [scope, setScope] = useState<Scope>(BASELINE);
  const [comparing, setRefs] = useState<string[]>([]);
  const value = useMemo(() => {
    const setComparing = (ref: string, on: boolean) =>
      setRefs((refs) => (on ? [...new Set([...refs, ref])] : refs.filter((r) => r !== ref)));
    return { scope, setScope, cube, defs, reason, drill, pin, hosted, comparing, setComparing };
  }, [scope, cube, defs, reason, drill, pin, hosted, comparing]);
  return <ScopeContext.Provider value={value}>{children}</ScopeContext.Provider>;
}

export const useScope = () => useContext(ScopeContext);

/**
 * The view for one query ref. At the baseline it is the server's view, so traced facts stay
 * traced. Under a scope it is re-aggregated from the cube.
 */
/** A derived view, with a note when the chart shows its whole data because the scope cannot
 * apply to it. The chart then stays clickable, so the reader can choose another member. */
export type Shown = { view: QueryView; note?: string } | { reason: string };

export function useDerived(ref: string, ignore?: string | null): Shown | null {
  const queries = useContext(QueriesContext);
  const { scope: full, cube, defs } = useScope();
  return useMemo(() => {
    // A chart that sets a filter on its own dimension keeps every member and marks the selection.
    const scope = ignore ? withoutMember(full, ignore) : full;
    if (!(ref in queries)) return null;
    if (!cube || isBaseline(scope)) return { view: queries[ref] };
    const def = defs[ref];
    if (!def) return { view: queries[ref], note: "This chart is not re-run under a scope." };
    const derived = derive(cube, def, scope);
    // The scope cannot apply here: show the chart's own data with the reason, and keep it
    // clickable, so the reader can still choose another member.
    return "reason" in derived ? { view: queries[ref], note: derived.reason } : derived;
  }, [ref, ignore, queries, full, cube, defs]);
}
