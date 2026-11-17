import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, vi } from "vitest";
import type { Payload } from "../lib/api";
import { ReportPage } from "../report/ReportPage";
import { ChangeCard } from "./ChangeCard";
import { Home } from "./Home";

const CQ = { id: "c1", question: "Where is fraud loss concentrated?", report_id: "r1", verified_by: "omar", verified_at: "2025-07-20T10:00:00", current: true };
const EMPTY_DIGEST = { since: "2025-07-13T00:00:00", until: "2025-07-20T00:00:00", top_changes: [], suggested_questions: [] };
const change = (over: object = {}) => ({ id: "x1", kind: "new_value", title: "New sales channel: Marketplace", detail: "First seen on 2025-12-25.", affected_reports: [], status: "open", proposal_id: null, ...over });

let routes: Record<string, unknown>;
let posts: { url: string; body: string }[];
let gets: string[];
const posted: { edition: number | null } = { edition: null };

beforeEach(() => {
  posts = [];
  gets = [];
  posted.edition = null;
  routes = {
    "/api/config": { demo: true },
    "/api/certified": [],
    "/api/digest": EMPTY_DIGEST,
    "/api/changes": [],
    "/api/sources": { sources: [{ id: "duckdb_demo", dialect: "duckdb", supports_as_of: false, data_time: "2025-11-30T10:00:00" }], demo_moment: 1 },
    "/api/reports": [],
  };
  vi.stubGlobal("open", vi.fn());
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const body = (d: unknown) => new Response(JSON.stringify(d), { status: 200 });
    if (init?.method === "POST") {
      posts.push({ url, body: init.body as string });
      return body({ kind: "report", report_id: "r1", view: "narrative", edition: posted.edition, cost: { calls: 0, rung: "certified" } });
    }
    gets.push(url);
    if (url.startsWith("/api/search")) return body(routes["/api/search"] ?? []);
    return body(routes[url] ?? []);
  }));
});
afterEach(() => vi.unstubAllGlobals());

function show() {
  return render(<QueryClientProvider client={new QueryClient()}><MemoryRouter><Home pollMs={60_000} /></MemoryRouter></QueryClientProvider>);
}

test("certified: a chip shows the question with the verified badge and asks by id", async () => {
  routes["/api/certified"] = [CQ];
  posted.edition = 1;
  const user = userEvent.setup();
  show();
  const chip = await screen.findByRole("button", { name: /Where is fraud loss concentrated\?/ });
  expect(within(chip).getByText("Verified")).toBeTruthy();
  expect(within(chip).getByText("by omar on 2025-07-20")).toBeTruthy();
  await user.click(chip);
  await waitFor(() => expect(posts.map((p) => [p.url, JSON.parse(p.body).certified_id, JSON.parse(p.body).progress_id?.slice(0, 2)])).toEqual([["/api/ask", "c1", "p-"]]));
  // A certified answer is the published edition, not the live draft.
  expect(window.open).toHaveBeenCalledWith("/r/r1/e/1?view=narrative", "_blank");
  expect(screen.getByRole("link", { name: "Open the report" }).getAttribute("href")).toBe("/r/r1/e/1?view=narrative");
});

test("certified: a stale row says definitions changed", async () => {
  routes["/api/certified"] = [{ ...CQ, current: false }];
  show();
  const chip = await screen.findByRole("button", { name: /Where is fraud loss concentrated\?/ });
  expect(within(chip).getByText("Verified; definitions changed since")).toBeTruthy();
  expect(within(chip).getByText(/Asking runs the question again/)).toBeTruthy();
});

test("search: typing lists matching reports and Enter opens the first", async () => {
  routes["/api/search"] = [{ report_id: "r1", title: "Loss by channel", tier: "team", labels: ["Fraud loss · Telesales"] }];
  const user = userEvent.setup();
  show();
  const box = await screen.findByRole("searchbox", { name: "Find a report" });
  await user.type(box, "tele");
  await waitFor(() => expect(screen.getByRole("status").textContent).toBe("1 report(s) match"));
  const link = screen.getByRole("link", { name: /Loss by channel/ });
  expect(link.getAttribute("href")).toBe("/r/r1");
  expect(link.textContent).toContain("Fraud loss · Telesales");
  expect(gets.filter((g) => g.startsWith("/api/search"))).toEqual(["/api/search?q=tele"]);
  await user.type(box, "{Enter}");
  expect(window.open).toHaveBeenCalledWith("/r/r1", "_blank");
});

test("search: no match says so", async () => {
  const user = userEvent.setup();
  show();
  await user.type(await screen.findByRole("searchbox", { name: "Find a report" }), "zzz");
  expect(await screen.findByText("No report matches")).toBeTruthy();
});

test("search: a blank box makes no request", async () => {
  const user = userEvent.setup();
  show();
  await user.type(await screen.findByRole("searchbox", { name: "Find a report" }), "   ");
  await new Promise((r) => setTimeout(r, 350));
  expect(gets.filter((g) => g.startsWith("/api/search"))).toEqual([]);
  // The live region stays mounted, so a screen reader hears the first result count.
  expect(screen.getByRole("status").textContent).toBe("");
});

test("search: a network error says that search is unavailable", async () => {
  routes["/api/search"] = undefined;
  const user = userEvent.setup();
  show();
  const box = await screen.findByRole("searchbox", { name: "Find a report" });
  const realFetch = globalThis.fetch;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if (url.startsWith("/api/search")) throw new TypeError("Failed to fetch");
    return realFetch(url, init);
  }));
  await user.type(box, "loss");
  await waitFor(() => expect(screen.getByRole("status").textContent).toBe("Search is unavailable"));
  expect(screen.queryByText("No report matches")).toBeNull();
});

test("this week: top changes and suggested questions render, and a query chip posts the choice", async () => {
  const query = { metrics: ["fraud_loss"], filters: [{ member: "orders.sales_channel", op: "eq", values: ["Marketplace"] }] };
  routes["/api/digest"] = {
    ...EMPTY_DIGEST,
    top_changes: [change({ kind: "definition", title: "Fraud loss: exclude friendly fraud" })],
    suggested_questions: [{ question: "Where is fraud loss concentrated?", certified_id: "c1" }, { question: "Fraud loss for Marketplace", query }],
  };
  const user = userEvent.setup();
  show();
  const week = await screen.findByRole("region", { name: "This week" });
  expect(within(week).getByText("Definition change · Fraud loss: exclude friendly fraud")).toBeTruthy();
  await user.click(within(week).getByRole("button", { name: "Fraud loss for Marketplace" }));
  await waitFor(() => expect(posts.length).toBe(1));
  expect(posts[0].body).toContain('"choice"');
  expect(posts[0].body).toContain("Marketplace");
  await user.click(within(week).getByRole("button", { name: "Where is fraud loss concentrated?" }));
  await waitFor(() => expect(JSON.parse(posts[1]?.body ?? "{}").certified_id).toBe("c1"));
  const headings = screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent);
  expect(headings).toEqual(["This week", "What changed", "Recent reports"]);
});

test("this week: hidden when empty", async () => {
  show();
  await screen.findByText(/No changes yet/);
  expect(screen.queryByRole("region", { name: "This week" })).toBeNull();
});

test("demo controls: present behind a disclosure when the config says demo is on", async () => {
  const user = userEvent.setup();
  show();
  const summary = await screen.findByText("Demo controls");
  await user.click(summary);
  expect(screen.getByRole("combobox", { name: /Demo user/ })).toBeTruthy();
  // A deployment that cannot switch the dataset sends no datasets, and shows no picker.
  expect(screen.queryByRole("combobox", { name: "Demo data" })).toBeNull();
  await user.click(screen.getByRole("button", { name: "Load the next data snapshot" }));
  await waitFor(() => expect(posts.map((p) => p.url)).toEqual(["/api/sources/duckdb_demo/advance"]));
});

test("demo controls: a dataset choice switches the demo and reloads the home page", async () => {
  routes["/api/config"] = { demo: true, dataset: "retail", datasets: [{ name: "retail", title: "Retail sales" }, { name: "hr", title: "People and HR" }] };
  const user = userEvent.setup();
  show();
  await user.click(await screen.findByText("Demo controls"));
  const pick = screen.getByRole("combobox", { name: "Demo data" }) as HTMLSelectElement;
  expect(pick.value).toBe("retail");
  expect(within(pick).getByRole("option", { name: "People and HR" })).toBeTruthy();
  const configs = () => gets.filter((g) => g === "/api/config").length;
  const before = configs();
  await user.selectOptions(pick, "hr");
  await waitFor(() => expect(posts).toEqual([{ url: "/api/demo", body: JSON.stringify({ name: "hr" }) }]));
  await waitFor(() => expect(configs()).toBeGreaterThan(before));
});

test("demo controls: no load button after the last demo moment", async () => {
  routes["/api/sources"] = { sources: [], demo_moment: 2 };
  const user = userEvent.setup();
  show();
  await user.click(await screen.findByText("Demo controls"));
  await waitFor(() => expect(gets).toContain("/api/sources"));
  expect(screen.queryByRole("button", { name: "Load the next data snapshot" })).toBeNull();
});

test("demo controls: absent when the config says demo is off", async () => {
  routes["/api/config"] = { demo: false };
  show();
  await screen.findByText(/No changes yet/);
  await waitFor(() => expect(gets).toContain("/api/config"));
  expect(screen.queryByText("Demo controls")).toBeNull();
  expect(screen.queryByText("Demo user")).toBeNull();
  expect(screen.queryByRole("button", { name: "Load the next data snapshot" })).toBeNull();
});

function card(item: object) {
  return render(<QueryClientProvider client={new QueryClient()}><ChangeCard item={change(item) as never} /></QueryClientProvider>);
}

test("change card: a new item carries the New tag", () => {
  card({ is_new: true });
  expect(screen.getByText("New").className).toBe("tag");
});

test("change card: an old item has no New tag, and a done item says Resolved", () => {
  card({ is_new: false, kind: "definition", status: "done", proposal_id: "p1" });
  expect(screen.queryByText("New")).toBeNull();
  expect(screen.getByText("Resolved")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Review" })).toBeNull();
});

test("change card: a moved certified answer is labeled", () => {
  card({ kind: "question", title: "Certified answer changed: Where?" });
  expect(screen.getByText("Certified question")).toBeTruthy();
});

const fact = { id: "q1_total.fraud_loss", label: "Fraud loss", value: 1, formatted: "$52.6M", query_hash: "abcd1234", metric: "fraud_loss", metric_version: 1, source_id: "duckdb_demo", data_time: "2025-12-31T10:00:00", state: "ok" as const };
const payload: Payload = {
  id: "r1", edition: 1, state: "published", tier: "team", view: "narrative",
  facts: { [fact.id]: fact }, queries: {},
  spec: { title: "Loss", kicker: "", objective: "why", assumptions: [], sections: [{ id: "s1", heading: "H", intent: "Where?", decisions: [], blocks: [{ type: "prose", text: "Text." }] }] },
  verified: { by: "omar", at: "2025-07-20T10:00:00", current: true },
};

function showReport(data: Payload) {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    const reply = url === "/api/config" ? { demo: false } : url.endsWith("/threads") ? [] : data;
    return new Response(JSON.stringify(reply), { status: 200 });
  }));
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter initialEntries={["/r/r1"]}><Routes><Route path="/r/:id" element={<ReportPage />} /></Routes></MemoryRouter>
    </QueryClientProvider>,
  );
}

test("verified badge: the report top bar shows who verified", async () => {
  showReport(payload);
  const badge = (await screen.findByText("Verified")).closest(".badge") as HTMLElement;
  expect(badge.dataset.kind).toBe("verified");
  expect(badge.getAttribute("title")).toBe("Verified by omar on 2025-07-20");
  expect(screen.getByText("by omar on 2025-07-20")).toBeTruthy();
});

test("verified badge: after a definition change it says so in words", async () => {
  showReport({ ...payload, verified: { by: "omar", at: "2025-07-20T10:00:00", current: false } });
  const badge = (await screen.findByText("Verified; definitions changed since")).closest(".badge") as HTMLElement;
  expect(badge.getAttribute("title")).toBe("Verified by omar on 2025-07-20; definitions changed since");
});

test("the ask box shows the demo's example, and a starter question asks it", async () => {
  routes["/api/config"] = { demo: true, placeholder: "Which categories drive the most revenue?", starters: ["What is the gross margin by category?"] };
  show();
  const box = await screen.findByRole("textbox", { name: "Ask a question" });
  await waitFor(() => expect(box.getAttribute("placeholder")).toBe("Which categories drive the most revenue?"));
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "What is the gross margin by category?" }));
  await waitFor(() => expect(posts.map((p) => JSON.parse(p.body).question)).toContain("What is the gross margin by category?"));
});

test("a starter question is hidden when the demo has certified questions", async () => {
  routes["/api/config"] = { demo: true, placeholder: "Ask", starters: ["Starter?"] };
  routes["/api/certified"] = [CQ];
  show();
  await screen.findByRole("button", { name: /Where is fraud loss concentrated\?/ });
  expect(screen.queryByRole("button", { name: "Starter?" })).toBeNull();
});
