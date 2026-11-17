import { createContext, useCallback, useContext, useId, useMemo, useRef, useState, type ReactNode } from "react";
import type { Claim, Section, Spec } from "../lib/api";
import { FactsContext } from "./context";
import { EvidenceDrawer } from "./EvidenceDrawer";
import { wordingText } from "./review";
import { verdictOf, VERDICT_TEXT, type Verdict } from "./verdict";

/**
 * `{claim:id}` (the claim's whole sentence) or `{word:id}` (the claim's finding word in the
 * writer's sentence) in report text. A claim id can hold any character but braces and spaces.
 */
export const CLAIM_TOKEN = /\{(?:claim|word):([^{}\s]+)\}/g;

const tokens = (text: string) => [...text.matchAll(CLAIM_TOKEN)].map((m) => m[1]);

/**
 * The claims the report cites, in reading order: the title, then each section's blocks in
 * order (paragraph tokens, then an action's basis). The index plus one is the citation number.
 */
export function citationOrder(spec: Spec): string[] {
  const ids = [...tokens(spec.title), ...spec.sections.flatMap(sectionTokens)];
  const known = spec.claims ?? {};
  return [...new Set(ids)].filter((id) => id in known);
}

function sectionTokens(section: Section): string[] {
  return section.blocks.flatMap((block) =>
    block.type === "prose" ? tokens(block.text)
      : block.type === "action" ? [...tokens(block.text), ...(block.basis ?? [])] : []);
}

/** The known claims that one section cites, in reading order. */
export function sectionClaims(section: Section, spec: Spec): string[] {
  const known = spec.claims ?? {};
  return [...new Set(sectionTokens(section))].filter((id) => id in known);
}

type ClaimsState = {
  claims: Record<string, Claim>;
  /** Citation number by claim id. */
  numbers: Record<string, number>;
  stale: Set<string>;
  openId: string | null;
  /** The id of the drawer element, for `aria-controls`. */
  drawerId: string;
  /** Opens the drawer for a claim. Focus returns to `opener` when it closes. The same claim again closes it. */
  toggle: (id: string, opener: HTMLElement) => void;
};

const ClaimsContext = createContext<ClaimsState>({
  claims: {}, numbers: {}, stale: new Set(), openId: null, drawerId: "", toggle: () => undefined,
});

export const useClaims = () => useContext(ClaimsContext);

/** Marks an element that opens the drawer, so a press on it is not a click outside. */
export const OPENER = "data-evidence-opener";

/** The claims of one report, their citation numbers, and the one evidence drawer. */
export function ClaimsProvider({ spec, children }: { spec: Spec; children: ReactNode }) {
  const [openId, setOpenId] = useState<string | null>(null);
  const opener = useRef<HTMLElement | null>(null);
  const drawerId = useId();
  const close = useCallback(() => {
    setOpenId(null);
    const el = opener.current;
    if (el?.isConnected) el.focus();
  }, []);
  const toggle = useCallback((id: string, el: HTMLElement) => {
    if (opener.current === el && openId === id) { close(); return; }
    opener.current = el;
    setOpenId(id);
  }, [openId, close]);
  const value = useMemo(() => {
    const claims = spec.claims ?? {};
    const numbers = Object.fromEntries(citationOrder(spec).map((id, i) => [id, i + 1]));
    const stale = new Set(spec.sections.flatMap((s) => s.stale_claims ?? []));
    return { claims, numbers, stale, openId, drawerId, toggle };
  }, [spec, openId, drawerId, toggle]);
  const claim = openId ? value.claims[openId] : undefined;
  const home = claim && spec.sections.find((s) => sectionClaims(s, spec).includes(claim.id));
  return (
    <ClaimsContext.Provider value={value}>
      {children}
      {claim && <EvidenceDrawer id={drawerId} claim={claim} number={value.numbers[claim.id] ?? null} stale={value.stale.has(claim.id)} around={home ? wordingText(home) : undefined} onClose={close} />}
    </ClaimsContext.Provider>
  );
}

/** Claim ids in reading order, then any other claim of the report. */
export function claimsInOrder(spec: Spec): string[] {
  const order = citationOrder(spec);
  return [...order, ...Object.keys(spec.claims ?? {}).filter((id) => !order.includes(id))];
}

/** The verdict of a claim in this report: stale, checked, or not checked. */
export function useVerdict(): (id: string) => Verdict {
  const { claims, stale } = useClaims();
  const facts = useContext(FactsContext);
  return (id) => (claims[id] ? verdictOf(claims[id], facts, stale.has(id)) : "unchecked");
}

/** The verdict line of one claim. */
export function VerdictLine({ id }: { id: string }) {
  const verdict = useVerdict()(id);
  return <p className="verdict" data-verdict={verdict}>{VERDICT_TEXT[verdict]}</p>;
}

export const hasClaims = (spec: Spec) => Object.keys(spec.claims ?? {}).length > 0;
