import type { Verified } from "../lib/api";

export const verifiedLine = (by: string, at: string) => `by ${by} on ${at.slice(0, 10)}`;

/** Who verified a certified answer, as an icon and a word. Never color alone. */
export function VerifiedBadge({ by, at, current }: Verified) {
  const title = `Verified by ${by} on ${at.slice(0, 10)}${current ? "" : "; definitions changed since"}`;
  return (
    <span className="badge" data-kind="verified" data-current={current} title={title}>
      <svg viewBox="0 0 12 12" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.6">
        {current ? <path d="M2 6.5l2.5 2.5L10 3.5" /> : <path d="M6 2.5v4M6 8.5v.5" />}
      </svg>
      {current ? "Verified" : "Verified; definitions changed since"}
    </span>
  );
}
