import { useEffect, useState } from "react";
import { getJSON, reportUrl, type SearchHit } from "../lib/api";

const TIER_WORD: Record<string, string> = { exploratory: "Exploratory", team: "Team", business_critical: "Business critical" };
const DEBOUNCE_MS = 250;

/** Find a report by its title or by the label of a number in it. */
export function Search({ onOpen }: { onOpen: (hit: SearchHit) => void }) {
  const [term, setTerm] = useState("");
  const [hits, setHits] = useState<SearchHit[] | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const q = term.trim();
    if (!q) { setHits(null); setFailed(false); return; }
    let live = true;
    const timer = setTimeout(() => {
      getJSON<SearchHit[]>(`/api/search?q=${encodeURIComponent(q)}`)
        .then((found) => { if (live) { setHits(found); setFailed(false); } })
        .catch(() => { if (live) { setHits(null); setFailed(true); } });
    }, DEBOUNCE_MS);
    return () => { live = false; clearTimeout(timer); };
  }, [term]);

  return (
    <div className="search">
      <label>
        <span className="meta">Find a report</span>
        <input
          type="search"
          aria-label="Find a report"
          className="ask-input"
          placeholder="Search titles and numbers"
          maxLength={200}
          value={term}
          onChange={(e) => setTerm(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && hits?.length) { e.preventDefault(); onOpen(hits[0]); } }}
        />
      </label>
      {/* The live region stays mounted, so a screen reader hears each new count. */}
      <p role="status" className="meta">
        {failed ? "Search is unavailable" : hits === null ? "" : hits.length ? `${hits.length} report(s) match` : "No report matches"}
      </p>
      {hits !== null && (
        <>
          <ul className="results">
            {hits.map((h) => (
              <li key={h.report_id}>
                <a href={reportUrl(h.report_id)} target="_blank" rel="noreferrer">
                  {h.title} <span className="meta">{TIER_WORD[h.tier] ?? h.tier}{h.labels.length > 0 && ` · ${h.labels.join(" · ")}`}</span>
                </a>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
