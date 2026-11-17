import { Fragment } from "react";
import type { Claim } from "../lib/api";
import { OPENER, useClaims } from "./claims";
import { useFact } from "./context";
import { Fact } from "./Fact";
import { STALE } from "./verdict";

/** A fact, claim, or word token. Split keeps the groups: [text, fact, claim, word, text, …]. */
const TOKEN = /\{(?:fact:([\w.\-+]+)|claim:([^{}\s]+)|word:([^{}\s]+))\}/g;
const FACT = /\{fact:([\w.\-+]+)\}/g;

/** These kinds start with the metric ("Revenue for North rose…"); the others with a member. */
const SUBJECT_FIRST = new Set(["value", "change", "trend", "target", "peak"]);

/** A claim's text for the middle of a sentence: "Over time, revenue for North rose…".
 * A member name ("North had…") and an acronym ("AOV was…") keep their capitals. */
export function inlineText(claim: Claim): string {
  const t = claim.text;
  if (!SUBJECT_FIRST.has(claim.kind) || t.length < 2 || t[1] === t[1].toUpperCase()) return t;
  return t[0].toLowerCase() + t.slice(1);
}

/** Plain text with fact and claim tokens. React escapes the text, so it cannot become markup. */
export function RichText({ text }: { text: string }) {
  const parts = text.split(TOKEN);
  const out = [];
  for (let i = 0; i < parts.length; i += 4) {
    if (parts[i]) out.push(<Fragment key={i}>{parts[i]}</Fragment>);
    // A token after words of the same sentence ("Over time, {claim:…}") is mid-sentence.
    const before = (i === 0 ? parts[0] : `x${parts[i]}`).trimEnd();
    const mid = before !== "" && !/[.!?]$/.test(before);
    if (parts[i + 1] !== undefined) out.push(<Fact key={`f${i}`} id={parts[i + 1]} />);
    else if (parts[i + 2] !== undefined) out.push(<ClaimRef key={`c${i}`} id={parts[i + 2]} mid={mid} />);
    else if (parts[i + 3] !== undefined) out.push(<WordRef key={`w${i}`} id={parts[i + 3]} />);
  }
  return <>{out}</>;
}

/** A value inside a claim. A press opens the claim's evidence; with no `onOpen` it is plain text. */
function ClaimFact({ id, onOpen }: { id: string; onOpen?: (el: HTMLElement) => void }) {
  const fact = useFact(id);
  if (!fact) return <span className="fact" data-verified="false">No data</span>;
  const verified = fact.state === "ok" && Boolean(fact.query_hash);
  if (!onOpen) return <span className="fact" data-verified={verified}>{fact.formatted}</span>;
  return (
    <button type="button" className="fact fact-btn" data-verified={verified} title={fact.label} {...{ [OPENER]: "" }}
      onClick={(e) => onOpen(e.currentTarget)}>
      {fact.formatted}
    </button>
  );
}

/** The words of a claim, which code wrote. Its values open the evidence when `onOpen` is given. */
export function ClaimText({ claim, text = claim.text, onOpen }: { claim: Claim; text?: string; onOpen?: (el: HTMLElement) => void }) {
  const parts = text.split(FACT);
  return (
    <>
      {parts.map((part, i) =>
        i % 2 === 1 ? <ClaimFact key={i} id={part} onOpen={onOpen} /> : <Fragment key={i}>{part}</Fragment>,
      )}
    </>
  );
}

/** The citation number of a claim. It opens and closes the evidence drawer. */
export function Cite({ id }: { id: string }) {
  const { numbers, openId, drawerId, toggle } = useClaims();
  const n = numbers[id];
  if (!n) return null;
  return (
    <sup className="cite-sup">
      <button type="button" className="cite" aria-label={`Evidence ${n}`} aria-expanded={openId === id}
        aria-controls={openId === id ? drawerId : undefined}
        {...{ [OPENER]: "" }} onClick={(e) => toggle(id, e.currentTarget)}>
        {n}
      </button>
    </sup>
  );
}

/** A word token: the claim's finding word that code wrote ("highest"), and the claim's citation. */
function WordRef({ id }: { id: string }) {
  const { claims, stale } = useClaims();
  const claim = claims[id];
  if (!claim?.word) return <span className="fact" data-verified="false">No data</span>;
  const isStale = stale.has(id);
  return (
    <span className="claim" data-stale={isStale || undefined}>
      <span className="nowrap">{claim.word}<Cite id={id} /></span>
      {isStale && <span className="stale-note">{STALE}</span>}
    </span>
  );
}

/** A claim token: the claim's words, its citation, and a mark when the data no longer supports it. */
function ClaimRef({ id, mid }: { id: string; mid: boolean }) {
  const { claims, stale, toggle } = useClaims();
  const claim = claims[id];
  if (!claim) return <span className="fact" data-verified="false">No data</span>;
  const isStale = stale.has(id);
  const open = (el: HTMLElement) => toggle(id, el);
  const text = mid ? inlineText(claim) : claim.text;
  // Tokens hold no spaces, so the text after the last space is whole words and tokens.
  const cut = text.search(/\s\S*$/) + 1;
  const [head, tail] = [text.slice(0, cut), text.slice(cut)];
  return (
    <span className="claim" data-stale={isStale || undefined}>
      <ClaimText claim={claim} text={head} onOpen={open} />
      {/* The last word and the citation stay on one line. */}
      <span className="nowrap"><ClaimText claim={claim} text={tail} onOpen={open} /><Cite id={id} /></span>
      {isStale && <span className="stale-note">{STALE}</span>}
    </span>
  );
}
