/**
 * A report with claims, in the payload shape of the provable-narration spec (section 6).
 * Real values from the demo data: fraud loss by region, and West by half-year.
 */
import type { Claim, CubeView, FactT, Payload, QueryView, Spec } from "../lib/api";

const DATA_TIME = "2025-11-30T00:00:00";

function fact(
  id: string, label: string, value: number, formatted: string,
  keys: Record<string, string | null>, opts: { ref?: string; share?: boolean; basis?: string; hash?: string } = {},
): FactT {
  const ref = opts.ref ?? "q1";
  return {
    id, label, value, formatted, query_hash: opts.hash ?? "502a7904c1d2", metric: "fraud_loss", metric_version: 1,
    source_id: "duckdb_demo", data_time: DATA_TIME, state: "ok",
    definition: "The order amount of orders confirmed as fraud.",
    binding: { ref, metric: "fraud_loss", keys, share: opts.share ?? false },
    ...(opts.basis ? { basis: opts.basis } : {}),
  };
}

const R = "dealers.region";
const REGIONS: [string, number, string, number, string][] = [
  // region, loss, formatted, share, formatted share
  ["South", 15_840_000, "$15.84M", 0.3244, "32.44%"],
  ["East", 13_300_000, "$13.30M", 0.2724, "27.24%"],
  ["North", 10_400_000, "$10.40M", 0.2130, "21.30%"],
  ["West", 9_290_000, "$9.29M", 0.1903, "19.03%"],
];
const HALVES: [string, string, number, string, string][] = [
  // key, label, loss, formatted, basis
  ["2024-H1", "2024 H1", 2_110_000, "$2.11M", "Sum of 6 monthly values"],
  ["2024-H2", "2024 H2", 2_340_000, "$2.34M", "Sum of 6 monthly values"],
  ["2025-H1", "2025 H1", 2_220_000, "$2.22M", "Sum of 6 monthly values"],
  ["2025-H2", "2025 Jul–Nov", 2_630_000, "$2.63M", "Sum of 5 monthly values"],
];

export const FACTS: Record<string, FactT> = {
  "q1_total.fraud_loss": fact("q1_total.fraud_loss", "Fraud loss", 48_830_000, "$48.83M", {}),
  ...Object.fromEntries(REGIONS.flatMap(([r, v, f, s, fs]) => [
    [`q1.fraud_loss.${r}`, fact(`q1.fraud_loss.${r}`, `Fraud loss · ${r}`, v, f, { [R]: r })],
    [`q1.fraud_loss.share.${r}`, fact(`q1.fraud_loss.share.${r}`, `Share of fraud loss · ${r}`, s, fs, { [R]: r }, { share: true })],
  ])),
  ...Object.fromEntries(HALVES.map(([k, label, v, f, basis]) => {
    const id = `q2.derived.half.West.${k}`;
    return [id, fact(id, `Fraud loss · West · ${label}`, v, f, { [R]: "West" }, { ref: "q2", basis, hash: "9b1e0f33aa70" })];
  })),
};

const COUNTED_REGION = "Fraud loss · by Region · Jan 2024 – Nov 2025 · 1,130,716 orders";
const COUNTED_WEST = "Fraud loss · Region is West · by half-year · Jan 2024 – Nov 2025 · 280,536 orders";
const DEFINITION = "Fraud loss: the order amount of orders confirmed as fraud.";
const regionRows = (highlight: string) =>
  REGIONS.map(([r]) => ({ label: r, fact: `q1.fraud_loss.${r}`, highlight: r === highlight }));
const halfSeries = HALVES.map(([k, label]) => ({ label, fact: `q2.derived.half.West.${k}` }));

export const CLAIMS: Record<string, Claim> = {
  "q1.rank.West": {
    id: "q1.rank.West", kind: "rank",
    text: "West had the lowest fraud loss of the 4 regions ({fact:q1.fraud_loss.West}, {fact:q1.fraud_loss.share.West} of the total)",
    facts: ["q1.fraud_loss.West", "q1.fraud_loss.share.West"], query_ref: "q1", segment: { [R]: "West" },
    period: ["2024-01-01", "2025-11-30"],
    evidence: { counted: COUNTED_REGION, by: "Region", rows: regionRows("West"), series: null, definition: DEFINITION },
  },
  "q1.extreme.South": {
    id: "q1.extreme.South", kind: "extreme",
    text: "South had the largest fraud loss of the 4 regions ({fact:q1.fraud_loss.South}, {fact:q1.fraud_loss.share.South} of the total)",
    facts: ["q1.fraud_loss.South", "q1.fraud_loss.share.South"], query_ref: "q1", segment: { [R]: "South" },
    period: ["2024-01-01", "2025-11-30"], word: "highest", about: ["South"],
    evidence: { counted: COUNTED_REGION, by: "Region", rows: regionRows("South"), series: null, definition: DEFINITION },
  },
  "q2.change.West": {
    id: "q2.change.West", kind: "change",
    text: "Fraud loss for West rose from {fact:q2.derived.half.West.2024-H1} in 2024 H1 to {fact:q2.derived.half.West.2025-H2} in 2025 Jul–Nov",
    facts: ["q2.derived.half.West.2024-H1", "q2.derived.half.West.2025-H2"], query_ref: "q2", segment: { [R]: "West" },
    period: ["2024-01-01", "2025-11-30"],
    evidence: { counted: COUNTED_WEST, by: "Half-year", rows: [], series: halfSeries, definition: DEFINITION },
  },
  "q2.trend.West.half": {
    id: "q2.trend.West.half", kind: "trend",
    text: "Fraud loss for West rose in 2 of 3 half-years: {fact:q2.derived.half.West.2024-H1}, {fact:q2.derived.half.West.2024-H2}, {fact:q2.derived.half.West.2025-H1}, {fact:q2.derived.half.West.2025-H2}",
    facts: HALVES.map(([k]) => `q2.derived.half.West.${k}`), query_ref: "q2", segment: { [R]: "West" },
    period: ["2024-01-01", "2025-11-30"],
    evidence: { counted: COUNTED_WEST, by: "Half-year", rows: [], series: halfSeries, definition: DEFINITION },
  },
};

const BY_REGION: QueryView = {
  columns: [R, "fraud_loss"], query_hash: "502a7904c1d2",
  rows: REGIONS.map(([r, v]) => [r, v]),
  points: REGIONS.map(([r, v, f]) => ({ label: r, value: v, formatted: f, fact_id: `q1.fraud_loss.${r}` })),
  labels: { [R]: "Region", fraud_loss: "Fraud loss" },
};
const WEST_BY_MONTH: QueryView = {
  columns: ["month", "fraud_loss"], query_hash: "9b1e0f33aa70",
  rows: [["2024-01-01", 352_000], ["2025-11-01", 98_200]],
  points: [
    { label: "2024-01-01", value: 352_000, formatted: "$352.0K", fact_id: "q2.fraud_loss.2024-01-01" },
    { label: "2025-11-01", value: 98_200, formatted: "$98.2K", fact_id: "q2.fraud_loss.2025-11-01" },
  ],
  labels: { month: "Month", fraud_loss: "Fraud loss" },
};

/** Region by month. One row per half-year start month stands in for that half-year. */
export const CLAIMS_CUBE: CubeView = {
  dimensions: [R, "month"],
  labels: { [R]: "Region", month: "Month" },
  vocabulary: { [R]: "Region", month: "Month" },
  metrics: { fraud_loss: { kind: "sum", num: "fraud_loss", den: null, format: "currency", label: "Fraud loss" } },
  columns: [R, "month", "fraud_loss"],
  rows: [
    ["West", "2024-01-01", 2_110_000], ["West", "2024-07-01", 2_340_000],
    ["West", "2025-01-01", 2_220_000], ["West", "2025-07-01", 2_630_000],
    ["South", "2024-01-01", 15_840_000], ["East", "2024-01-01", 13_300_000], ["North", "2024-01-01", 10_400_000],
  ],
  span: ["2024-01-01", "2025-12-01"],
  query_hash: "cube",
};

export const CLAIMS_SPEC: Spec = {
  title: "Fraud loss by region", kicker: "Fraud loss, Jan 2024 – Nov 2025", objective: "Where should we act?",
  assumptions: [],
  claims: CLAIMS,
  sections: [
    {
      id: "s1", heading: "Regions", intent: "Which region loses the most to fraud?", decisions: [], narration: "model",
      blocks: [
        { type: "prose", text: "{claim:q1.rank.West}. By contrast, {claim:q1.extreme.South}." },
        { type: "tile", fact_id: "q1_total.fraud_loss", label: "Fraud loss" },
        { type: "bars", query_ref: "q1", title: "Fraud loss by region" },
        { type: "action", text: "Review fraud controls in the South first.", basis: ["q1.extreme.South"] },
      ],
    },
    {
      id: "s2", heading: "West over time", intent: "How did West change?", decisions: [], narration: "code",
      blocks: [
        { type: "prose", text: "{claim:q2.change.West}." },
        { type: "prose", text: "{claim:q2.trend.West.half}." },
        { type: "line", query_ref: "q2", title: "West fraud loss by month" },
      ],
    },
  ],
};

/** A fresh copy each call, so a test can change it. */
export function claimsPayload(spec: Partial<Spec> = {}): Payload {
  return JSON.parse(JSON.stringify({
    id: "r9", edition: null, state: "draft", tier: "team", view: "narrative", author: "ana", contributors: ["ana"],
    facts: FACTS,
    queries: { q1: BY_REGION, q2: WEST_BY_MONTH },
    spec: { ...CLAIMS_SPEC, ...spec },
    cube: CLAIMS_CUBE,
    query_defs: {
      q1: { metrics: ["fraud_loss"], dimensions: [R] },
      q2: { metrics: ["fraud_loss"], filters: [{ member: R, op: "eq", values: ["West"] }], time: { grain: "month" } },
    },
  }));
}
