import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, vi } from "vitest";
import { VOCAB } from "../report/askingFixture";
import { VocabularyProvider } from "../report/VocabularyContext";
import { Home } from "./Home";

let ask: unknown;
let changes: () => unknown[] = () => [];
let modelProposals: unknown[] = [];
const item = (id: string, kind: string, title: string) => ({ id, kind, title, detail: "Detail text.", affected_reports: ["r1"], status: "open", proposal_id: kind === "definition" ? "p1" : null });
const sent: string[] = [];
const DIGEST = { since: "2025-07-13T00:00:00", until: "2025-07-20T00:00:00", top_changes: [], suggested_questions: [] };

beforeEach(() => {
  sent.length = 0;
  changes = () => [];
  modelProposals = [];
  vi.stubGlobal("open", vi.fn());
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const body = (d: unknown) => new Response(JSON.stringify(d), { status: 200 });
    if (init) { sent.push(init.body as string); return body(ask); }
    if (url === "/api/changes") return body(changes());
    if (url === "/api/model/proposals") return body(modelProposals);
    if (url === "/api/config") return body({ demo: true });
    if (url === "/api/vocabulary") return body(VOCAB);
    if (url === "/api/certified" || url.startsWith("/api/search")) return body([]);
    if (url === "/api/digest") return body(DIGEST);
    if (url === "/api/sources") return body({ sources: [{ id: "duckdb_demo", dialect: "duckdb" }], demo_moment: 1 });
    if (url === "/api/sources/duckdb_demo/schema") return body({ source_id: "duckdb_demo", profiled: true, metrics: [], dimensions: [], datasets: [] });
    if (url.endsWith("/impact")) return body({ reports: ["r1"], before: { label: "Fraud loss", value: 1, formatted: "$52.6M" }, after: { label: "Fraud loss", value: 1, formatted: "$43.1M" } });
    return body([{ id: "r1", title: "Earlier report", tier: "team", author: "ana" }]);
  }));
});
afterEach(() => vi.unstubAllGlobals());

function show(pollMs?: number) {
  return render(<QueryClientProvider client={new QueryClient()}><VocabularyProvider><MemoryRouter><Home pollMs={pollMs} /></MemoryRouter></VocabularyProvider></QueryClientProvider>);
}

async function question(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText("Ask a question"), "How big?{Enter}");
}

test("a report answer opens a new tab and leaves a link", async () => {
  ask = { kind: "report", report_id: "r9", view: "dashboard", cost: { usd: 0.004 } };
  const user = userEvent.setup();
  show();
  await question(user);
  const link = await screen.findByRole("link", { name: /Open the report/ });
  expect(link.getAttribute("href")).toBe("/r/r9?view=dashboard");
  expect(window.open).toHaveBeenCalledWith("/r/r9?view=dashboard", "_blank");
});

test("a clarifying question shows chips and a chip sends the choice", async () => {
  ask = { kind: "clarify", clarify: { question: "Value or count?", options: [{ label: "Value", query: { metrics: ["fraud_loss"] } }] }, cost: {} };
  const user = userEvent.setup();
  show();
  await question(user);
  ask = { kind: "report", report_id: "r9", view: "narrative", cost: {} };
  const row = await screen.findByRole("group", { name: "The routed question" });
  expect(row.textContent).toContain("Metric Fraud loss");
  await user.click(await screen.findByRole("button", { name: "Value" }));
  expect(sent[1]).toContain("fraud_loss");
});

test("out of scope offers exploration", async () => {
  ask = { kind: "out_of_scope", message: "No entry for profit.", can_explore: true, cost: {} };
  const user = userEvent.setup();
  show();
  await question(user);
  expect(await screen.findByText("No entry for profit.")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Explore with unverified SQL" })).toBeTruthy();
});

test("a failure shows the message and earlier reports stay", async () => {
  ask = { kind: "failed", message: "The language model is not available.", cost: {} };
  const user = userEvent.setup();
  show();
  await question(user);
  expect(await screen.findByText("The language model is not available.")).toBeTruthy();
  expect(screen.getByRole("link", { name: "Earlier report" })).toBeTruthy();
});

test("the empty briefing explains what to do next", async () => {
  show();
  expect(await screen.findByText(/No changes yet/)).toBeTruthy();
});

test("a definition change shows before and after, and approval needs a second step", async () => {
  changes = () => [item("c1", "definition", "Fraud loss: exclude friendly fraud")];
  ask = {};
  const user = userEvent.setup();
  show();
  await user.click(await screen.findByRole("button", { name: "Review" }));
  expect(await screen.findByText("$43.1M")).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "Approve" }));
  expect(sent).toEqual([]);
  await user.click(screen.getByRole("button", { name: "Confirm approval" }));
  await waitFor(() => expect(sent.length).toBe(1));
});

test("the list keeps its order while the pointer is over it", async () => {
  const a = item("a", "new_data", "First card"), b = item("b", "new_value", "Second card");
  let flipped = false;
  changes = () => (flipped ? [b, a] : [a, b]);
  const user = userEvent.setup();
  show(20);
  const first = await screen.findByText("First card");
  await user.hover(first);
  flipped = true;
  await new Promise((r) => setTimeout(r, 90));
  const titles = () => screen.getAllByRole("heading", { level: 3 }).map((h) => h.textContent);
  expect(titles()).toEqual(["First card", "Second card"]);
  await user.unhover(first);
  await waitFor(() => expect(titles()).toEqual(["Second card", "First card"]));
});

test("changes appear above recent reports", async () => {
  changes = () => [item("a", "new_data", "First card")];
  show();
  await screen.findByText("First card");
  const headings = screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent);
  expect(headings).toEqual(["What changed", "Recent reports"]);
});

test("a request error shows a message", async () => {
  const user = userEvent.setup();
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if (init) return new Response(JSON.stringify({ detail: "Server problem." }), { status: 500 });
    const reply = url === "/api/sources" ? { sources: [], demo_moment: 2 } : url === "/api/digest" ? DIGEST : [];
    return new Response(JSON.stringify(reply), { status: 200 });
  }));
  show();
  await question(user);
  expect(await screen.findByText("Server problem.")).toBeTruthy();
});

test("a change that needs attention says so and offers no second approval", async () => {
  changes = () => [{ ...item("c1", "definition", "Fraud loss: exclude friendly fraud"), proposal_id: null, attention: ["r1"] }];
  show();
  expect(await screen.findByText(/1 report\(s\) could not update/)).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Review" })).toBeNull();
});

test("home links to the model page when proposals exist", async () => {
  modelProposals = [{ proposal: { id: "p1" } }, { proposal: { id: "p2" } }];
  show();
  const link = await screen.findByRole("link", { name: "Review the model (2 proposals)" });
  expect(link.getAttribute("href")).toBe("/model");
});

test("home always has a model link and no review line without proposals", async () => {
  show();
  expect((await screen.findByRole("link", { name: "Model" })).getAttribute("href")).toBe("/model");
  await screen.findByText("Earlier report");
  expect(screen.queryByText(/Review the model/)).toBeNull();
});

test("a question goes to the selected dataset", async () => {
  ask = { kind: "report", report_id: "r9", view: "dashboard", cost: {} };
  const user = userEvent.setup();
  show();
  await question(user);
  await waitFor(() => expect(sent.length).toBe(1));
  expect(JSON.parse(sent[0]).source_id).toBe("duckdb_demo");
});

test("while an answer runs, the page shows its progress and does not send the question again", async () => {
  let finish: (r: Response) => void = () => {};
  const base = (globalThis.fetch as ReturnType<typeof vi.fn>).getMockImplementation()!;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if (init) { sent.push(init.body as string); return new Promise<Response>((resolve) => { finish = resolve; }); }
    if (url.startsWith("/api/progress/")) {
      return new Response(JSON.stringify({ steps: [{ key: "write", label: "Write the report and check each statement", seconds: null }], seconds: 4, done: false }));
    }
    return base(url, init);
  }));
  const user = userEvent.setup();
  show();
  await question(user);
  const panel = await screen.findByRole("status", { name: "Answer progress" });
  expect(await screen.findByText("Write the report and check each statement…")).toBeTruthy();
  expect(panel.textContent).toContain("“How big?”");
  await user.type(screen.getByLabelText("Ask a question"), "{Enter}");
  expect(sent).toHaveLength(1);
  expect(JSON.parse(sent[0]).progress_id).toMatch(/^p-/);
  finish(new Response(JSON.stringify({ kind: "out_of_scope", message: "Not in the data.", cost: {} })));
  await waitFor(() => expect(screen.queryByRole("status", { name: "Answer progress" })).toBeNull());
});
