type Props = { tier: "exploratory" | "team" | "business_critical"; state: "draft" | "published" };

const KIND = {
  certified: { word: "Certified", title: "Traced, checked, and signed in a published edition" },
  traced: { word: "Traced", title: "Every number is traced to a query. Not yet published." },
  unverified: { word: "Unverified", title: "Exploratory. The numbers come from model-written SQL." },
};

/** The state of a report in one word, with an icon. Never color alone. */
export function Badge({ tier, state }: Props) {
  const kind = state === "published" ? "certified" : tier === "exploratory" ? "unverified" : "traced";
  const { word, title } = KIND[kind];
  return (
    <span className="badge" data-kind={kind} title={title}>
      <svg viewBox="0 0 12 12" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.6">
        {kind === "unverified" ? <path d="M6 3v4M6 9v.5" /> : <path d="M2 6.5l2.5 2.5L10 3.5" />}
      </svg>
      {word}
    </span>
  );
}
