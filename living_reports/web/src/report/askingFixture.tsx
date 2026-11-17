/** A narrative report payload and a recording fetch stub for the asking and editing tests. */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { vi } from "vitest";
import type { FactT, Payload, QueryView, Vocabulary } from "../lib/api";
import { CUBE } from "./cubeFixture";
import { ReportPage } from "./ReportPage";
import { VocabularyProvider } from "./VocabularyContext";

export const VOCAB: Vocabulary = {
  metrics: [{ id: "fraud_loss", label: "Fraud loss", format: "currency" }, { id: "order_count", label: "Orders", format: "count" }],
  dimensions: [
    { id: "orders.sales_channel", label: "Sales channel" },
    { id: "dealers.region", label: "Region" },
    { id: "orders.fraud_type", label: "Fraud type" },
  ],
  time_fields: ["orders.order_created_date"],
  grains: ["day", "week", "month", "quarter", "year"],
  model_version: 1,
};

const fact = (id: string, label: string, formatted: string, keys: Record<string, string | null> = {}): FactT => ({
  id, label, value: 1, formatted, query_hash: "abcd1234", metric: "fraud_loss", metric_version: 1, source_id: "duckdb_demo", data_time: "2025-12-31T00:00:00", state: "ok",
  binding: { ref: "q1", metric: "fraud_loss", keys, share: false },
});

const BY_CHANNEL: QueryView = {
  columns: ["orders.sales_channel", "fraud_loss"], query_hash: "abcd1234",
  rows: [["App", 700], ["Telesales", 500], ["Web", 350]],
  points: [
    { label: "App", value: 700, formatted: "$700.00", fact_id: "q1.fraud_loss.App" },
    { label: "Telesales", value: 500, formatted: "$500.00", fact_id: "q1.fraud_loss.Telesales" },
    { label: "Web", value: 350, formatted: "$350.00", fact_id: "q1.fraud_loss.Web" },
  ],
  labels: { "orders.sales_channel": "Sales channel", fraud_loss: "Fraud loss" },
};
const BY_MONTH: QueryView = {
  columns: ["month", "fraud_loss"], query_hash: "abcd1234", rows: [["2024-12-01", 1300], ["2025-01-01", 1570]],
  points: [
    { label: "2024-12-01", value: 1300, formatted: "$1.30K", fact_id: "q2.fraud_loss.2024-12-01" },
    { label: "2025-01-01", value: 1570, formatted: "$1.57K", fact_id: "q2.fraud_loss.2025-01-01" },
  ],
};

export function makePayload(extra: Partial<Payload> = {}): Payload {
  return {
    id: "r1", edition: null, state: "draft", tier: "team", view: "narrative", author: "ana", contributors: ["ana"],
    facts: {
      "q1_total.fraud_loss": fact("q1_total.fraud_loss", "Fraud loss", "$1.55K"),
      "q1.fraud_loss.App": fact("q1.fraud_loss.App", "Fraud loss · App", "$700.00", { "orders.sales_channel": "App" }),
      "q1.fraud_loss.Telesales": fact("q1.fraud_loss.Telesales", "Fraud loss · Telesales", "$500.00", { "orders.sales_channel": "Telesales" }),
    },
    queries: { q1: BY_CHANNEL, q2: BY_MONTH },
    spec: {
      title: "Loss overview", kicker: "Fraud loss, all dates", objective: "why", assumptions: [],
      sections: [
        {
          id: "s1", heading: "Channels", intent: "Where is the loss?", decisions: [],
          blocks: [
            { type: "prose", text: "App leads at {fact:q1.fraud_loss.App}." },
            { type: "prose", text: "The total is {fact:q1_total.fraud_loss}." },
            { type: "tile", fact_id: "q1_total.fraud_loss", label: "Fraud loss" },
            { type: "bars", query_ref: "q1", title: "Fraud loss by sales channel" },
          ],
        },
        {
          id: "s2", heading: "Over time", intent: "Trend?", decisions: [],
          blocks: [{ type: "line", query_ref: "q2", title: "Fraud loss by month" }],
        },
      ],
    },
    cube: CUBE,
    query_defs: {
      q1: { metrics: ["fraud_loss"], dimensions: ["orders.sales_channel"], filters: [{ member: "dealers.region", op: "eq", values: ["East"] }] },
      q2: { metrics: ["fraud_loss"], time: { grain: "month" } },
    },
    ...extra,
  };
}

export type Call = { url: string; method: string; body?: string };
export const state: { payload: Payload; calls: Call[]; answers: Record<string, () => Response> } = { payload: makePayload(), calls: [], answers: {} };

export const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });

export function stubFetch() {
  state.payload = makePayload();
  state.calls = [];
  state.answers = {};
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    state.calls.push({ url, method, body: init?.body as string | undefined });
    for (const [suffix, answer] of Object.entries(state.answers)) if (url.endsWith(suffix)) return answer();
    if (url === "/api/vocabulary") return json(VOCAB);
    if (url.endsWith("/threads") && method === "GET") return json([]);
    if (url.endsWith("/publication")) return json({ edition: null, draft_changed: true });
    if (url.endsWith("/state")) return json({ number: 1, content_hash: "h", tier: "team", status: "published", author: "ana", checks: [], signatures: [], needs: [], can_publish: false, newest: 1 });
    if (url === "/api/ask") return json({ kind: "report", report_id: "r1", view: "narrative", cost: {} });
    if (url.endsWith("/forms")) return json({ forms: ["bars", "table"] });
    if (url.endsWith("/feedback") && method === "GET") return json({ signal: null });
    if (url.endsWith("/feedback")) return json({ ok: true });
    if (url.includes("/sections/")) return json({ kind: "ok", wording_dropped: false, cost: {} });
    return json(state.payload);
  }));
}

export const posts = (suffix: string) => state.calls.filter((c) => c.method !== "GET" && c.url.endsWith(suffix));

export function show(path = "/r/r1") {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <VocabularyProvider>
        <MemoryRouter initialEntries={[path]}>
          <Routes>
            <Route path="/r/:id" element={<ReportPage />} />
            <Route path="/r/:id/e/:edition" element={<ReportPage />} />
          </Routes>
        </MemoryRouter>
      </VocabularyProvider>
    </QueryClientProvider>,
  );
}
