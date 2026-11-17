import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, vi } from "vitest";
import { setCurrentUser, type ProposalView } from "../lib/api";
import { ModelPage } from "./ModelPage";

const view = (id: string, kind: string, nodeId: string, summary: string, probe: ProposalView["probe"] = null): ProposalView => ({
  proposal: { id, status: "pending", proposed_by: "discovery", reason: "discovery:run1", change: { id: nodeId, kind, payload: {} } },
  probe, run_id: "run1", kind, node_id: nodeId, summary,
});
const PASS = { target: "fraud_loss", kind: "metric" as const, status: "pass" as const, detail: "Fraud loss: $7.5K", value: "$7.5K", sql: "SELECT 1" };
const VIEWS = [
  view("p3", "MetricBinding", "fraud_loss", "sum(orders.loss_amount)", PASS),
  view("p1", "Dataset", "orders", "main.orders, key order_id"),
  view("p2", "Field", "orders.sales_channel", "string, dimension"),
];

let proposals: ProposalView[] = VIEWS;
let answer: { status: number; body: unknown } = { status: 200, body: { confirmed: [], failed: [] } };
const posts: { url: string; body: unknown }[] = [];

beforeEach(() => {
  posts.length = 0;
  proposals = VIEWS;
  answer = { status: 200, body: { confirmed: [], failed: [] } };
  setCurrentUser("omar");
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const reply = (status: number, d: unknown) => new Response(JSON.stringify(d), { status });
    if (init?.method === "POST") {
      posts.push({ url, body: JSON.parse(init.body as string) });
      return reply(answer.status, answer.body);
    }
    if (url === "/api/model/proposals") return reply(200, proposals);
    if (url === "/api/sources") return reply(200, { sources: [{ id: "test", dialect: "duckdb" }], demo_moment: null });
    return reply(404, {});
  }));
});
afterEach(() => { vi.unstubAllGlobals(); setCurrentUser("ana"); });

function show() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><MemoryRouter><ModelPage /></MemoryRouter></QueryClientProvider>);
}

test("renders groups in kind order with summaries and probe text", async () => {
  show();
  await screen.findByText("Fraud loss: $7.5K");
  const headings = screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent);
  expect(headings).toEqual(["Datasets", "Fields", "Metrics"]);
  const row = screen.getByText("fraud_loss").closest("tr")!;
  expect(within(row).getByText("sum(orders.loss_amount)")).toBeTruthy();
  expect(within(row).getByText("Pass")).toBeTruthy();
  expect(within(row).getByRole("img", { name: "Pass" })).toBeTruthy();
});

test("confirm posts the proposal id and shows a conflict sentence", async () => {
  answer = { status: 409, body: { detail: "Confirm orders.loss_amount first" } };
  const user = userEvent.setup();
  show();
  const row = (await screen.findByText("fraud_loss")).closest("tr")!;
  await user.click(within(row).getByRole("button", { name: "Confirm" }));
  expect(posts[0]).toEqual({ url: "/api/model/proposals/confirm", body: { ids: ["p3"] } });
  expect((await within(row).findByRole("alert")).textContent).toBe("Confirm orders.loss_amount first");
});

test("rename opens an input and posts the label", async () => {
  const user = userEvent.setup();
  show();
  const row = (await screen.findByText("orders.sales_channel")).closest("tr")!;
  await user.click(within(row).getByRole("button", { name: "Rename" }));
  await user.type(within(row).getByLabelText("New label"), "Channel");
  await user.click(within(row).getByRole("button", { name: "Save" }));
  expect(posts[0]).toEqual({ url: "/api/model/proposals/p2/rename", body: { label: "Channel" } });
});

test("a dataset row has no rename", async () => {
  show();
  const row = (await screen.findByText("orders")).closest("tr")!;
  expect(within(row).queryByRole("button", { name: "Rename" })).toBeNull();
});

test("reject asks for a reason and posts it", async () => {
  const user = userEvent.setup();
  show();
  const row = (await screen.findByText("orders")).closest("tr")!;
  await user.click(within(row).getByRole("button", { name: "Reject" }));
  await user.type(within(row).getByLabelText("Reason"), "Not needed");
  await user.click(within(row).getByRole("button", { name: "Send rejection" }));
  expect(posts[0]).toEqual({ url: "/api/model/proposals/p1/reject", body: { reason: "Not needed" } });
});

test("confirm all posts ids null", async () => {
  const user = userEvent.setup();
  show();
  await screen.findByText("orders");
  await user.click(screen.getByRole("button", { name: "Confirm all" }));
  expect(posts[0]).toEqual({ url: "/api/model/proposals/confirm", body: { ids: null } });
});

test("run discovery posts the chosen source and lists what it dropped", async () => {
  answer = { status: 200, body: { id: "run2", source_id: "test", proposal_ids: [], skipped: [], dropped: [{ name: "orders_dealer", kind: "Relationship", reason: "dealers.dealer_id has 1 duplicate keys" }] } };
  const user = userEvent.setup();
  show();
  await screen.findByRole("option", { name: "test" });
  await user.click(screen.getByRole("button", { name: "Run discovery" }));
  expect(posts[0]).toEqual({ url: "/api/model/discover", body: { source_id: "test" } });
  expect(await screen.findByRole("heading", { name: "Dropped in the last run" })).toBeTruthy();
  expect(screen.getByText("dealers.dealer_id has 1 duplicate keys")).toBeTruthy();
});

test("actions are disabled for a non-owner", async () => {
  setCurrentUser("ana");
  show();
  const row = (await screen.findByText("orders.sales_channel")).closest("tr")!;
  for (const name of ["Confirm", "Rename", "Reject"]) {
    expect((within(row).getByRole("button", { name }) as HTMLButtonElement).disabled).toBe(true);
  }
  expect((screen.getByRole("button", { name: "Confirm all" }) as HTMLButtonElement).disabled).toBe(true);
  expect((screen.getByRole("button", { name: "Run discovery" }) as HTMLButtonElement).disabled).toBe(true);
});

test("import posts the document", async () => {
  answer = { status: 200, body: { run: { id: "r", source_id: "test", proposal_ids: [], skipped: [], dropped: [] }, dropped: [] } };
  const user = userEvent.setup();
  show();
  await screen.findByRole("option", { name: "test" });
  await user.click(screen.getByText("Import"));
  await user.type(screen.getByLabelText("Ossie document"), "version: 0.2.0.dev0");
  await user.click(screen.getByRole("button", { name: "Import document" }));
  expect(posts[0]).toEqual({ url: "/api/model/import", body: { source_id: "test", document: "version: 0.2.0.dev0" } });
});

test("empty state", async () => {
  proposals = [];
  show();
  expect(await screen.findByText("No proposals. Run discovery on a source, or import an Ossie document.")).toBeTruthy();
});

test("export links to the export endpoint", async () => {
  show();
  await screen.findByRole("option", { name: "test" });
  expect(screen.getByRole("link", { name: "Export" }).getAttribute("href")).toBe("/api/model/export?source_id=test");
});

test("a failed action shows its sentence as an alert", async () => {
  answer = { status: 503, body: { detail: "The language model is not available." } };
  const user = userEvent.setup();
  show();
  await screen.findByRole("option", { name: "test" });
  await user.click(screen.getByRole("button", { name: "Run discovery" }));
  expect((await screen.findByRole("alert")).textContent).toBe("The language model is not available.");
});

test("a failed proposal list shows a sentence", async () => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => new Response("{}", { status: url === "/api/model/proposals" ? 500 : 200 })));
  show();
  expect((await screen.findByRole("alert")).textContent).toBe("The proposals cannot be read now. Reload the page to try again.");
});

test("the owner hint names no demo user", async () => {
  setCurrentUser("ana");
  show();
  await screen.findByText("orders");
  expect(screen.getByText("Only an owner can change the model. Ask an owner to review these proposals.")).toBeTruthy();
  expect(screen.queryByText(/omar/)).toBeNull();
});

test("any owner can act on the model, not only omar", async () => {
  setCurrentUser("priya");
  render(<QueryClientProvider client={new QueryClient()}><MemoryRouter><ModelPage /></MemoryRouter></QueryClientProvider>);
  await screen.findByText("main.orders, key order_id");
  expect(screen.getByRole("button", { name: "Confirm all" })).toHaveProperty("disabled", false);
  expect(screen.queryByText(/Only an owner can change the model/)).toBeNull();
});
