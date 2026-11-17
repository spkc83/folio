import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, vi } from "vitest";
import type { Payload } from "../lib/api";
import { ReportPage } from "./ReportPage";

const fact = (id: string, formatted: string) => ({ id, label: id, value: 1, formatted, query_hash: "abcd1234", metric: "fraud_loss", metric_version: 1, source_id: "duckdb_demo", data_time: "2025-12-31T10:00:00", state: "ok" as const });

const payload: Payload = {
  id: "r1", edition: null, state: "draft", tier: "team", view: "narrative",
  facts: { "q1_total.fraud_loss": fact("q1_total.fraud_loss", "$52.6M") },
  queries: { q1: { columns: ["a", "b"], rows: [["App", 1]], query_hash: "abcd1234", points: [{ label: "App", value: 1, formatted: "$1.00", fact_id: "q1_total.fraud_loss" }] } },
  spec: {
    title: "Loss overview", kicker: "Fraud loss, all dates", objective: "why",
    assumptions: [{ id: "a1", text: "All dates in the data." }],
    sections: [{
      id: "s1", heading: "Channels", intent: "Where is the loss?", decisions: [],
      blocks: [
        { type: "prose", text: "Loss reached {fact:q1_total.fraud_loss}." },
        { type: "tile", fact_id: "q1_total.fraud_loss", label: "Fraud loss" },
        { type: "bars", query_ref: "q1", title: "Loss by channel" },
        { type: "action", text: "Review the App channel." },
      ],
    }],
  },
};

let calls: { url: string; body?: string }[] = [];

function json(data: unknown) {
  return new Response(JSON.stringify(data), { status: 200, headers: { "Content-Type": "application/json" } });
}

beforeEach(() => {
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, body: init?.body as string | undefined });
    if (url.endsWith("/threads") && !init) return json([]);
    if (url.endsWith("/threads")) return json({ id: "t1", report_id: "r1", section_id: "s1", fact_id: "q1_total.fraud_loss", messages: [], status: "open", decision: null });
    if (url === "/api/ask") return json({ kind: "report", report_id: "r2", view: "narrative", cost: {} });
    return json(payload);
  }));
  vi.stubGlobal("open", vi.fn());
});
afterEach(() => vi.unstubAllGlobals());

function show() {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter initialEntries={["/r/r1"]}>
        <Routes><Route path="/r/:id" element={<ReportPage />} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const reportCalls = () => calls.filter((c) => c.url === "/api/reports/r1").length;

test("changing the view makes no new request", async () => {
  const user = userEvent.setup();
  show();
  await screen.findByText("Loss overview");
  const before = reportCalls();
  await user.click(screen.getByRole("tab", { name: "Dashboard" }));
  await user.click(screen.getByRole("tab", { name: "Decision brief" }));
  await user.click(screen.getByRole("tab", { name: "Evidence" }));
  expect(reportCalls()).toBe(before);
});

test("the same fact shows the same text in all four views", async () => {
  const user = userEvent.setup();
  show();
  await screen.findByText("Loss overview");
  for (const name of ["Narrative", "Dashboard", "Decision brief", "Evidence"]) {
    await user.click(screen.getByRole("tab", { name }));
    expect(screen.getAllByText("$52.6M").length).toBeGreaterThan(0);
  }
});

test("evidence shows the metric version and query for each fact", async () => {
  const user = userEvent.setup();
  show();
  await screen.findByText("Loss overview");
  await user.click(screen.getByRole("tab", { name: "Evidence" }));
  const row = screen.getByRole("row", { name: /q1_total.fraud_loss/ });
  expect(within(row).getByText("fraud_loss v1")).toBeTruthy();
  expect(within(row).getByText("abcd1234")).toBeTruthy();
  expect(within(row).getByText("Traced")).toBeTruthy();
});

test("the decision brief shows the action", async () => {
  const user = userEvent.setup();
  show();
  await screen.findByText("Loss overview");
  await user.click(screen.getByRole("tab", { name: "Decision brief" }));
  expect(screen.getByText("Review the App channel.")).toBeTruthy();
});

test("selecting a number opens a thread on it, and Escape closes it", async () => {
  const user = userEvent.setup();
  show();
  await user.click(await screen.findByRole("button", { name: "$52.6M" }));
  await user.click(screen.getByRole("button", { name: "Ask about this number" }));
  expect(await screen.findByLabelText("Ask about this number")).toBeTruthy();
  await user.keyboard("{Escape}");
  await waitFor(() => expect(screen.queryByLabelText("Ask about this number")).toBeNull());
});

test("clicking outside closes a thread", async () => {
  const user = userEvent.setup();
  show();
  await user.click(await screen.findByRole("button", { name: "$52.6M" }));
  await user.click(screen.getByRole("button", { name: "Ask about this number" }));
  await screen.findByLabelText("Ask about this number");
  await user.click(screen.getByRole("heading", { name: "Loss overview" }));
  await waitFor(() => expect(screen.queryByLabelText("Ask about this number")).toBeNull());
});

test("a draft survives a change of view", async () => {
  const user = userEvent.setup();
  show();
  await user.type(await screen.findByLabelText("Ask about this report"), "My unsent question");
  await user.click(screen.getByRole("tab", { name: "Dashboard" }));
  await user.click(screen.getByRole("tab", { name: "Narrative" }));
  expect((screen.getByLabelText("Ask about this report") as HTMLInputElement).value).toBe("My unsent question");
});

test("changing an assumption sends a new question", async () => {
  const user = userEvent.setup();
  show();
  await user.click(await screen.findByRole("button", { name: "Change" }));
  const input = screen.getByLabelText("New assumption");
  await user.clear(input);
  await user.type(input, "Only the App channel{Enter}");
  await waitFor(() => expect(calls.some((c) => c.url === "/api/ask")).toBe(true));
  const body = calls.find((c) => c.url === "/api/ask")!.body!;
  expect(body).toContain("Where is the loss?");
  expect(body).toContain("Only the App channel");
});

test("an error page says the report is not available", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response("", { status: 404 })));
  show();
  expect(await screen.findByText("This report is not available.")).toBeTruthy();
});

test("a failed question shows its error on the page", async () => {
  const user = userEvent.setup();
  const base = fetch;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) =>
    url === "/api/ask" ? new Response(JSON.stringify({ detail: "This report changed while you worked." }), { status: 409 }) : base(url, init)));
  show();
  await user.type(await screen.findByLabelText("Ask about this report"), "More{Enter}");
  expect(await screen.findByText("This report changed while you worked.")).toBeTruthy();
});
