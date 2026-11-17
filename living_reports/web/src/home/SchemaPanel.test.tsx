import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, vi } from "vitest";
import type { SourceSchema } from "../lib/api";
import { SchemaPanel } from "./SchemaPanel";

export const SCHEMA: SourceSchema = {
  source_id: "duckdb_demo", profiled: true,
  metrics: [{ id: "fraud_loss", label: "Fraud loss", definition: "Total loss on orders confirmed as fraud.", format: "currency" }],
  dimensions: [
    { id: "orders.sales_channel", label: "Sales channel", dataset: "orders", is_time: false, synonyms: ["channel"] },
    { id: "orders.order_created_date", label: "Order date", dataset: "orders", is_time: true },
  ],
  datasets: [{
    name: "orders", table: "main.orders", row_count: 1_000_000, primary_key: ["order_id"],
    columns: [
      { field_id: "orders.sales_channel", name: "sales_channel", label: "Sales channel", type: "string", dimension: true, is_time: false,
        synonyms: [], distinct_count: 6, null_rate: 0, samples: ["App", "Retail", "Web"], min: null, max: null },
      { field_id: "orders.order_created_date", name: "order_created_date", label: "Order date", type: "time", dimension: true, is_time: true,
        synonyms: [], distinct_count: 900, null_rate: 0.02, samples: [], min: "2024-01-01 00:00:00", max: "2026-10-31 23:00:00" },
    ],
  }],
};

const asked: string[] = [];
beforeEach(() => {
  asked.length = 0;
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    asked.push(url);
    const body = (d: unknown) => new Response(JSON.stringify(d), { status: 200 });
    if (url === "/api/sources") return body({ sources: [{ id: "duckdb_demo", dialect: "duckdb" }, { id: "card_fraud", dialect: "duckdb" }], demo_moment: 1 });
    if (url === "/api/sources/duckdb_demo/schema") return body(SCHEMA);
    if (url === "/api/sources/card_fraud/schema") return body({ ...SCHEMA, source_id: "card_fraud", metrics: [], dimensions: [], datasets: [] });
    return body({});
  }));
});
afterEach(() => vi.unstubAllGlobals());

function show(onPick = (_: string) => {}, onSource = (_: string) => {}) {
  return render(<QueryClientProvider client={new QueryClient()}><SchemaPanel onPick={onPick} onSource={onSource} /></QueryClientProvider>);
}

test("the panel names what you can ask about on the selected dataset", async () => {
  show();
  expect(await screen.findByRole("button", { name: "Fraud loss" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Sales channel" })).toBeTruthy();
  expect(screen.getByText(/1,000,000 rows/)).toBeTruthy();
  expect(screen.getByRole("button", { name: "Order date" }).parentElement?.textContent).toContain("2024-01-01 → 2026-10-31");
});

test("a term adds its label to the question", async () => {
  const picked: string[] = [];
  show((label) => picked.push(label));
  await userEvent.click(await screen.findByRole("button", { name: "Fraud loss" }));
  expect(picked).toEqual(["Fraud loss"]);
});

test("the columns show a type and a profile", async () => {
  show();
  await screen.findByRole("button", { name: "Fraud loss" });
  await userEvent.click(screen.getByText(/^orders/));
  const row = screen.getByRole("row", { name: /sales_channel/ });
  expect(row.textContent).toContain("string");
  expect(row.textContent).toContain("6 values");
  expect(row.textContent).toContain("App, Retail, Web");
  expect(screen.getByRole("row", { name: /order_created_date/ }).textContent).toContain("2% null");
});

test("choosing another dataset loads its schema and tells the page", async () => {
  const sources: string[] = [];
  show(() => {}, (id) => sources.push(id));
  await screen.findByRole("button", { name: "Fraud loss" });
  await userEvent.selectOptions(screen.getByLabelText("Dataset"), "card_fraud");
  expect(await screen.findByText(/No metrics on this dataset yet/)).toBeTruthy();
  expect(asked).toContain("/api/sources/card_fraud/schema");
  expect(sources).toContain("card_fraud");
});
