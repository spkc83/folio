/**
 * Values in the words people read. The mirror of the server's `display_value`
 * (`living_reports/semantic/labels.py`): keep the two the same.
 *
 * `bust_out` is "Bust-out", `none` of the fraud type is "No fraud", the month `2024-01-01`
 * is "Jan 2024". Filters, ids, and scope keep the raw value.
 */

export const NO_VALUE = "No value";
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** Labels that the general rule cannot make, by field name (the member id after the dot). */
export const VALUE_LABELS: Record<string, Record<string, string>> = {
  fraud_type: { none: "No fraud", bust_out: "Bust-out" },
};

const CODE = /^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$/;
const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})(?:[ T]00:00(?::00(?:\.0+)?)?)?$/;

const monthText = (y: number, m: number) => `${MONTHS[m - 1]} ${y}`;

function dateText(y: number, m: number, d: number, grain: string): string {
  if (grain === "year") return String(y);
  if (grain === "quarter") return `Q${Math.floor((m - 1) / 3) + 1} ${y}`;
  if (grain === "week") return `Week of ${d} ${monthText(y, m)}`;
  if (grain === "day" || (grain === "" && d !== 1)) return `${d} ${monthText(y, m)}`;
  return monthText(y, m);
}

/**
 * The label of `value`, one value of `member`: a member id (`orders.fraud_type`), a time
 * column (`orders.order_created_date:month`), `month`, or "" when the member is not known.
 * `labels` maps a raw value to the semantic model's label; a label there wins.
 */
export function displayValue(member: string, value: unknown, labels?: Record<string, string>): string {
  if (value === null || value === undefined) return NO_VALUE;
  const raw = String(value);
  if (labels && Object.hasOwn(labels, raw)) return labels[raw];
  const [column, grain = ""] = member.split(":");
  const name = column.split(".").pop() ?? "";
  const known = VALUE_LABELS[name];
  if (known && Object.hasOwn(known, raw)) return known[raw];
  const iso = typeof value === "string" ? ISO_DATE.exec(value) : null;
  if (iso) {
    const [y, m, d] = iso.slice(1).map(Number);
    return dateText(y, m, d, grain || (name === "month" ? "month" : ""));
  }
  if (typeof value === "string" && CODE.test(value)) {
    const text = value.replaceAll("_", " ");
    return text.charAt(0).toUpperCase() + text.slice(1);
  }
  return raw;
}

/** Each part of a joined label (`identity_theft · 2024-01-01`) in words. `members` names the parts in order. */
export function displayParts(label: string, members: string[] = []): string {
  return label.split(" · ").map((part, i) => displayValue(members[i] ?? "", part)).join(" · ");
}
