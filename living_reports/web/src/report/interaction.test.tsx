import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, vi } from "vitest";
import type { FactT, Payload, QueryView } from "../lib/api";
import { formatValue } from "./charts/format";
import { CUBE } from "./cubeFixture";
import { ReportPage } from "./ReportPage";

const rateFact: FactT = {
  id: "q1_total.fraud_rate", label: "Fraud rate", value: 0.0264, formatted: "2.64%", query_hash: "abcd1234",
  metric: "fraud_rate", metric_version: 1, source_id: "duckdb_demo", data_time: "2025-12-31T10:00:00", state: "ok",
  binding: { ref: "q1", metric: "fraud_rate", keys: {}, share: false },
};

const view = (columns: string[], rows: [string, number][], fmt: string): QueryView => ({
  columns, rows, query_hash: "abcd1234",
  points: rows.map(([label, value], i) => ({ label, value, formatted: formatValue(value, fmt), fact_id: `q.${i}` })),
});

const BY_CHANNEL = view(["orders.sales_channel", "fraud_rate"],
  [["Telesales", 0.0352], ["Web", 0.033], ["App", 0.02], ["Marketplace", 0.02]], "percent");
const BY_MONTH = view(["month", "fraud_loss"], [["2024-12-01", 1300], ["2025-01-01", 1570]], "currency");

const BY_TYPE = view(["orders.fraud_type", "fraud_loss"],
  [["none", 600], ["identity_theft", 200], ["friendly_fraud", 80], ["account_takeover", 20]], "currency");

function makePayload(extra: Partial<Payload> = {}): Payload {
  return {
    id: "r1", edition: null, state: "draft", tier: "team", view: "dashboard",
    facts: { [rateFact.id]: rateFact },
    queries: { q1: BY_CHANNEL, q2: BY_MONTH },
    spec: {
      title: "Loss overview", kicker: "Fraud rate, all dates", objective: "status", assumptions: [],
      sections: [{
        id: "s1", heading: "Channels", intent: "Where?", decisions: [],
        blocks: [
          { type: "tile", fact_id: "q1_total.fraud_rate", label: "Fraud rate" },
          { type: "bars", query_ref: "q1", title: "Fraud rate by sales channel" },
          { type: "line", query_ref: "q2", title: "Fraud loss by month" },
        ],
      }],
    },
    cube: CUBE,
    query_defs: {
      q1: { metrics: ["fraud_rate"], dimensions: ["orders.sales_channel"] },
      q2: { metrics: ["fraud_loss"], time: { grain: "month" } },
    },
    ...extra,
  };
}

let payload = makePayload();
let calls: { url: string; body?: string }[] = [];
const json = (data: unknown) => new Response(JSON.stringify(data), { status: 200, headers: { "Content-Type": "application/json" } });

beforeEach(() => {
  payload = makePayload();
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, body: init?.body as string | undefined });
    if (url.endsWith("/threads")) return json([]);
    if (url.endsWith("/drill")) return json(BY_TYPE);
    if (url.endsWith("/pin")) return json({ kind: "report", report_id: "r1", view: "dashboard", cost: {} });
    return json(payload);
  }));
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

const tile = () => document.querySelector(".tile")!;
const exploring = () => document.querySelector(".exploring");

test("filter row: one select per cube dimension plus dates", async () => {
  show();
  const row = await screen.findByRole("group", { name: "Report filters" });
  expect(within(row).getAllByRole("combobox").map((s) => s.getAttribute("aria-label"))).toEqual(["Dates", "Sales channel"]);
  expect(within(row).getByText("Changing a scope re-runs the queries. No model call.")).toBeTruthy();
});

test("filter row: choosing a member re-runs the tile and the line", async () => {
  const user = userEvent.setup();
  show();
  const row = await screen.findByRole("group", { name: "Report filters" });
  await user.selectOptions(within(row).getByRole("combobox", { name: "Sales channel" }), "Telesales");
  expect(tile().textContent).toContain("3.52%");
  const line = screen.getByText(/^Fraud loss by month/).closest(".card")!;
  await user.click(within(line as HTMLElement).getByRole("button", { name: "Table" }));
  expect(within(line as HTMLElement).getByText("$400.00")).toBeTruthy();
  expect(within(line as HTMLElement).getByText("$500.00")).toBeTruthy();
  expect(exploring()!.textContent).toContain("Sales channel is Telesales");
});

test("filter row: the date presets set a range", async () => {
  const user = userEvent.setup();
  show();
  const row = await screen.findByRole("group", { name: "Report filters" });
  await user.selectOptions(within(row).getByRole("combobox", { name: "Dates" }), "Custom…");
  await user.selectOptions(within(row).getByRole("combobox", { name: "From" }), "2025-01-01");
  expect(exploring()!.textContent).toContain("Jan 2025");
  expect(tile().textContent).toContain("2.74%");
});

test("exploring: reset returns to the baseline", async () => {
  const user = userEvent.setup();
  show();
  const row = await screen.findByRole("group", { name: "Report filters" });
  await user.selectOptions(within(row).getByRole("combobox", { name: "Sales channel" }), "Telesales");
  await user.click(screen.getByRole("button", { name: "Reset" }));
  expect(exploring()).toBeNull();
  expect(tile().querySelector(".fact-btn")).toBeTruthy();
  expect((within(row).getByRole("combobox", { name: "Sales channel" }) as HTMLSelectElement).value).toBe("");
});

test("exploring: values under a scope are not traced", async () => {
  const user = userEvent.setup();
  show();
  const row = await screen.findByRole("group", { name: "Report filters" });
  await user.selectOptions(within(row).getByRole("combobox", { name: "Sales channel" }), "Telesales");
  expect(tile().querySelector(".fact-btn")).toBeNull();
  expect(tile().querySelector('.fact[data-verified="false"]')).toBeTruthy();
  expect(exploring()!.textContent).toContain("pin to trace them");
});

test("filter row: an older payload with no cube shows no row", async () => {
  payload = makePayload({ cube: null });
  show();
  await screen.findByText("Loss overview");
  expect(screen.queryByRole("group", { name: "Report filters" })).toBeNull();
  expect(screen.getByText("Fraud rate by sales channel")).toBeTruthy();
});

test("derived chart with an other-kind metric shows the reason", async () => {
  const user = userEvent.setup();
  payload = makePayload();
  payload.query_defs!.q1 = { metrics: ["dealers"], dimensions: ["orders.sales_channel"] };
  show();
  const row = await screen.findByRole("group", { name: "Report filters" });
  await user.selectOptions(within(row).getByRole("combobox", { name: "Dates" }), "Custom…");
  await waitFor(() => expect(screen.getAllByText("Dealers cannot be re-aggregated").length).toBeGreaterThan(0));
  fireEvent.click(screen.getByRole("button", { name: "Reset" }));
  expect(screen.queryByText("Dealers cannot be re-aggregated")).toBeNull();
});

test("interaction: clicking a bar filters the tile and shows the Exploring bar", async () => {
  const user = userEvent.setup();
  show();
  const bars = await screen.findByRole("group", { name: /Fraud rate by sales channel/ });
  await user.click(within(bars).getByRole("button", { name: /^Telesales/ }));
  expect(exploring()!.textContent).toContain("Sales channel is Telesales");
  expect(tile().textContent).toContain("3.52%");
  // The chart that set the filter keeps every member and marks the selection.
  const again = screen.getByRole("group", { name: /Fraud rate by sales channel/ });
  expect(within(again).getAllByRole("button")).toHaveLength(4);
  expect(within(again).getByRole("button", { name: /^Telesales/ }).getAttribute("aria-pressed")).toBe("true");
  const select = screen.getByRole("combobox", { name: "Sales channel" }) as HTMLSelectElement;
  expect(select.value).toBe("Telesales");
  await user.click(within(again).getByRole("button", { name: /^Telesales/ }));
  expect(exploring()).toBeNull();
});

test("interaction: a line that does not group by the member shows it against all", async () => {
  const user = userEvent.setup();
  show();
  const bars = await screen.findByRole("group", { name: /Fraud rate by sales channel/ });
  await user.click(within(bars).getByRole("button", { name: /^Web/ }));
  expect(screen.getByText("Fraud loss by month · Web against all")).toBeTruthy();
});

test("interaction: a null member reads No value", async () => {
  const user = userEvent.setup();
  const typed = {
    ...CUBE,
    dimensions: ["orders.fraud_type", "month"],
    labels: { "orders.fraud_type": "Fraud type", month: "Month" },
    columns: ["orders.fraud_type", ...CUBE.columns.slice(1)],
    rows: [["identity_theft", "2025-01-01", 50, 100, 5, 100, 1], [null, "2025-01-01", 0, 900, 0, 900, 1]],
  };
  const server: QueryView = {
    columns: ["orders.fraud_type", "fraud_rate"], rows: [["identity_theft", 0.05], [null, 0]], query_hash: "h",
    points: [{ label: "identity_theft", value: 0.05, formatted: "5.00%", fact_id: "a" }, { label: "None", value: 0, formatted: "0.00%", fact_id: "b" }],
  };
  payload = makePayload({ cube: typed, queries: { q1: server, q2: BY_MONTH }, query_defs: { q1: { metrics: ["fraud_rate"], dimensions: ["orders.fraud_type"] }, q2: { metrics: ["fraud_loss"], time: { grain: "month" } } } });
  show();
  const bars = await screen.findByRole("group", { name: /Fraud rate by sales channel/ });
  await user.click(within(bars).getByRole("button", { name: /^None/ }));
  expect(exploring()!.textContent).toContain("Fraud type is No value");
  expect(tile().textContent).toContain("0.00%");
});

test("interaction: a brush re-runs the tiles for the range", async () => {
  if (!("PointerEvent" in window)) {
    class PointerEventPolyfill extends MouseEvent { pointerId = 1; }
    Object.assign(window, { PointerEvent: PointerEventPolyfill });
  }
  show();
  const plot = await screen.findByRole("img", { name: /Fraud loss by month/ });
  vi.spyOn(plot, "getBoundingClientRect").mockReturnValue({ left: 0, top: 0, width: 660, height: 200, right: 660, bottom: 200, x: 0, y: 0, toJSON: () => ({}) });
  fireEvent.pointerDown(plot, { clientX: 600 });
  fireEvent.pointerUp(plot, { clientX: 600 });
  expect(exploring()!.textContent).toContain("Jan 2025");
  expect(tile().textContent).toContain("2.74%");
});

async function selectTelesales(user: ReturnType<typeof userEvent.setup>) {
  const bars = await screen.findByRole("group", { name: /Fraud rate by sales channel/ });
  await user.click(within(bars).getByRole("button", { name: /^Telesales/ }));
}

test("drill: choosing a dimension loads a nested panel", async () => {
  const user = userEvent.setup();
  show();
  await selectTelesales(user);
  await user.selectOptions(screen.getByRole("combobox", { name: "Break down by…" }), "orders.fraud_type");
  const panel = await screen.findByRole("region", { name: "Telesales · by Fraud type" });
  await waitFor(() => expect(within(panel).getAllByRole("button", { name: /\$/ })).toHaveLength(4));
  const call = calls.find((c) => c.url === "/api/reports/r1/drill")!;
  expect(JSON.parse(call.body!)).toEqual({ ref: "q1", filters: [], range: null, by: "orders.fraud_type", member: { "orders.sales_channel": "Telesales" } });
});

test("drill: the candidates leave out the chart's own dimensions", async () => {
  const user = userEvent.setup();
  show();
  await selectTelesales(user);
  const options = within(screen.getByRole("combobox", { name: "Break down by…" })).getAllByRole("option").map((o) => o.getAttribute("value"));
  expect(options).toEqual(["", "orders.fraud_type"]);
});

test("drill: pin posts the scoped query and resets the scope", async () => {
  const user = userEvent.setup();
  show();
  await selectTelesales(user);
  await user.selectOptions(screen.getByRole("combobox", { name: "Break down by…" }), "orders.fraud_type");
  const panel = await screen.findByRole("region", { name: "Telesales · by Fraud type" });
  const before = calls.filter((c) => c.url === "/api/reports/r1").length;
  await user.click(within(panel).getByRole("button", { name: "Pin" }));
  await waitFor(() => expect(calls.some((c) => c.url === "/api/reports/r1/pin")).toBe(true));
  expect(calls.find((c) => c.url === "/api/reports/r1/pin")!.body).toContain("Telesales");
  await waitFor(() => expect(exploring()).toBeNull());
  await waitFor(() => expect(calls.filter((c) => c.url === "/api/reports/r1").length).toBeGreaterThan(before));
});

test("exploring: Pin as section posts the scope", async () => {
  const user = userEvent.setup();
  show();
  await selectTelesales(user);
  await user.click(screen.getByRole("button", { name: "Pin 'Channels' with this scope" }));
  await waitFor(() => expect(calls.some((c) => c.url === "/api/reports/r1/pin")).toBe(true));
  expect(JSON.parse(calls.find((c) => c.url === "/api/reports/r1/pin")!.body!).filters)
    .toEqual([{ member: "orders.sales_channel", op: "eq", values: ["Telesales"] }]);
  await waitFor(() => expect(exploring()).toBeNull());
});

test("filter row: a report with no cube says why", async () => {
  payload = makePayload({ cube: null, cube_reason: "A query shows only its top members, so the report has no cells to explore." });
  show();
  const banner = await screen.findByText("Month-by-month exploration is not available for this report.");
  expect(banner.getAttribute("title")).toMatch(/^A query shows only its top members/);
  expect(screen.queryByRole("group", { name: "Report filters" })).toBeNull();
});

test("exploring: with several sections the reader picks the one to pin", async () => {
  const user = userEvent.setup();
  payload = makePayload();
  payload.spec.sections.push({ id: "s2", heading: "Months", intent: "When?", decisions: [], blocks: [] });
  show();
  await selectTelesales(user);
  const pick = screen.getByRole("combobox", { name: "Section to pin" });
  expect(within(pick).getAllByRole("option").map((o) => o.textContent)).toEqual(["Channels", "Months"]);
  await user.selectOptions(pick, "q2");
  await user.click(screen.getByRole("button", { name: "Pin 'Months' with this scope" }));
  await waitFor(() => expect(calls.some((c) => c.url === "/api/reports/r1/pin")).toBe(true));
  expect(JSON.parse(calls.find((c) => c.url === "/api/reports/r1/pin")!.body!).ref).toBe("q2");
});

test("hints: the charts say how to explore them", async () => {
  show();
  expect(await screen.findByText("Click a sales channel to explore it across the report.")).toBeTruthy();
  expect(screen.getByText("Drag to select a range.")).toBeTruthy();
});

test("filter row: Reset also clears a custom date choice", async () => {
  const user = userEvent.setup();
  show();
  const row = await screen.findByRole("group", { name: "Report filters" });
  await user.selectOptions(within(row).getByRole("combobox", { name: "Dates" }), "Custom…");
  await user.click(screen.getByRole("button", { name: "Reset" }));
  expect((within(row).getByRole("combobox", { name: "Dates" }) as HTMLSelectElement).value).toBe("all");
  expect(within(row).queryByRole("combobox", { name: "From" })).toBeNull();
});

test("interaction: a brush dragged right to left is normalized", async () => {
  if (!("PointerEvent" in window)) {
    class PointerEventPolyfill extends MouseEvent { pointerId = 1; }
    Object.assign(window, { PointerEvent: PointerEventPolyfill });
  }
  show();
  const plot = await screen.findByRole("img", { name: /Fraud loss by month/ });
  vi.spyOn(plot, "getBoundingClientRect").mockReturnValue({ left: 0, top: 0, width: 660, height: 200, right: 660, bottom: 200, x: 0, y: 0, toJSON: () => ({}) });
  fireEvent.pointerDown(plot, { clientX: 600 });
  fireEvent.pointerMove(plot, { clientX: 52 });
  fireEvent.pointerUp(plot, { clientX: 52 });
  expect(exploring()!.textContent).toContain("Dec 2024 – Jan 2025");
});

test("no cube: a bar still selects, and another bar replaces the selection", async () => {
  const user = userEvent.setup();
  payload = makePayload({ cube: null, cube_reason: "The report has more than 5,000 cells." });
  show();
  const bars = await screen.findByRole("group", { name: /Fraud rate by sales channel/ });
  await user.click(within(bars).getByRole("button", { name: /^Telesales/ }));
  expect(exploring()!.textContent).toContain("Sales channel is Telesales");
  expect(exploring()!.textContent).toContain("Your next question uses this selection");
  await user.click(within(screen.getByRole("group", { name: /Fraud rate by sales channel/ })).getByRole("button", { name: /^Web/ }));
  const again = screen.getByRole("group", { name: /Fraud rate by sales channel/ });
  expect(within(again).getByRole("button", { name: /^Web/ }).getAttribute("aria-pressed")).toBe("true");
  expect(within(again).getByRole("button", { name: /^Telesales/ }).getAttribute("aria-pressed")).toBe("false");
  expect(screen.getByLabelText("Ask about this report").getAttribute("placeholder")).toMatch(/Sales channel is Web/);
});

const BY_PAYMENT = view(["orders.payment_method", "fraud_rate"], [["Card", 0.03], ["PayPal", 0.02]], "percent");

test("a chart that cannot apply the scope keeps its bars and stays clickable", async () => {
  const user = userEvent.setup();
  payload = makePayload();
  payload.queries.q3 = BY_PAYMENT;
  payload.query_defs!.q3 = { metrics: ["fraud_rate"], dimensions: ["orders.payment_method"] };
  payload.spec.sections.push({ id: "s2", heading: "Payment", intent: "How?", decisions: [],
    blocks: [{ type: "bars", query_ref: "q3", title: "Fraud rate by payment method" }] });
  show();
  // The cube does not carry the payment method: a click on it cannot re-filter the channels.
  const payment = await screen.findByRole("group", { name: /Fraud rate by payment method/ });
  await user.click(within(payment).getByRole("button", { name: /^Card/ }));
  const channels = screen.getByRole("group", { name: /Fraud rate by sales channel/ });
  expect(within(channels).getAllByRole("button")).toHaveLength(4);
  expect(screen.getAllByText(/^Not filtered\./).length).toBeGreaterThan(0);
  expect(screen.getAllByText(/^The report's cells do not carry orders.payment_method/).length).toBeGreaterThan(0);
  // The earlier chart still takes a click, and the selection adds it.
  await user.click(within(channels).getByRole("button", { name: /^Web/ }));
  expect(exploring()!.textContent).toContain("Sales channel is Web");
  expect(within(screen.getByRole("group", { name: /Fraud rate by payment method/ })).getAllByRole("button")).toHaveLength(2);
});
