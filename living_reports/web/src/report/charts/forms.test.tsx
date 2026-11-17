import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, vi } from "vitest";

// jsdom has no layout. The segmented bar measures its width through ResizeObserver.
let barWidth = 600;
class FakeObserver {
  constructor(private cb: ResizeObserverCallback) {}
  observe() { this.cb([{ contentRect: { width: barWidth } } as ResizeObserverEntry], this as unknown as ResizeObserver); }
  disconnect() {}
  unobserve() {}
}
beforeEach(() => { barWidth = 600; vi.stubGlobal("ResizeObserver", FakeObserver); });
afterEach(() => vi.unstubAllGlobals());
import { DataTable } from "./DataTable";
import type { ReactNode } from "react";
import type { Block, FactT, QueryView } from "../../lib/api";
import { FactsContext, QueriesContext } from "../context";
import { Chart } from "../Chart";
import { Tile } from "../Tile";
import { DeltaBars } from "./DeltaBars";
import { MONTHLY, month, pts, view } from "./fixtures";
import { formatValue } from "./format";
import { Segmented } from "./Segmented";
import { SmallMultiples } from "./SmallMultiples";

type TileBlock = Extract<Block, { type: "tile" }>;

const fact = (id: string, value: number, formatted: string): FactT => ({
  id, label: "Fraud loss", value, formatted, query_hash: "h", metric: "fraud_loss", metric_version: 1,
  source_id: "duckdb_demo", data_time: "2025-12-31T10:00:00", state: "ok",
});
const one = (value: number | null) => view(pts([["", value]]), { columns: ["fraud_loss"] });
const FACTS = { "q1_total.fraud_loss": fact("q1_total.fraud_loss", 52_590_000, "$52.6M") };
const QUERIES: Record<string, QueryView> = {
  q1_cur: one(31_163_000),
  q1_prev: one(18_463_000),
  q1_months: view(pts(MONTHLY.map((v, i) => [month(i), v])), { columns: ["month", "fraud_loss"] }),
};
const TILE: TileBlock = {
  type: "tile", fact_id: "q1_total.fraud_loss", label: "Fraud loss",
  delta: "previous_period", delta_label: "2025 vs 2024", spark_ref: "q1_months",
};

function withData(children: ReactNode, queries = QUERIES) {
  return (
    <FactsContext.Provider value={FACTS}>
      <QueriesContext.Provider value={queries}>{children}</QueriesContext.Provider>
    </FactsContext.Provider>
  );
}

test("tile: shows the delta against the previous period", () => {
  const { container } = render(withData(<Tile block={TILE} />));
  expect(screen.getByText("$52.6M")).toBeTruthy();
  const delta = container.querySelector(".delta")!;
  expect(delta.textContent).toContain("+68.8%");
  expect(delta.textContent).toContain("2025 vs 2024");
});

test("tile: a null previous value says so", () => {
  render(withData(<Tile block={TILE} />, { ...QUERIES, q1_prev: one(null) }));
  expect(screen.getByText("No previous period")).toBeTruthy();
});

test("tile: the sparkline carries 12 points and a text alternative", () => {
  const { container } = render(withData(<Tile block={TILE} />));
  const line = container.querySelector(".spark polyline")!;
  expect(line.getAttribute("points")!.trim().split(/\s+/)).toHaveLength(12);
  expect(container.querySelector(".spark")!.getAttribute("aria-hidden")).toBe("true");
  expect(container.querySelector(".sr-only")!.textContent).toContain("highest $4.3M in Nov 2025");
});

test("tile: no delta field renders no delta line", () => {
  const plain: TileBlock = { type: "tile", fact_id: "q1_total.fraud_loss", label: "Fraud loss" };
  const { container } = render(withData(<Tile block={plain} />));
  expect(container.querySelector(".delta")).toBeNull();
  expect(container.querySelector(".spark")).toBeNull();
});

test("tile: markup inside the tile is phrasing content", () => {
  const { container } = render(withData(<Tile block={TILE} />));
  expect(container.querySelectorAll(".tile div, .tile p, .tile ul, .tile section")).toHaveLength(0);
});

const BEFORE = view(pts([["App", 10_000_000], ["Web", 9_000_000], ["Telesales", 6_000_000], ["Marketplace", null]]));
const AFTER = view(pts([["App", 15_609_548], ["Web", 10_053_341], ["Telesales", 10_044_517], ["Marketplace", 319_143]]));

test("delta bars: rows are ordered by change and signed", () => {
  render(<DeltaBars before={BEFORE} after={AFTER} title="Change in fraud loss" />);
  const rows = within(screen.getByRole("group")).getAllByRole("button");
  expect(rows[0].textContent).toContain("App");
  expect(rows[0].textContent).toContain("+$5.6M");
  expect(rows.map((r) => r.textContent).join("|")).toMatch(/App.*Telesales.*Web.*Marketplace/);
  expect(rows[3].textContent).toContain("No data");
  expect(rows[0].getAttribute("aria-label")).toBe("App, $15.6M from $10.0M, change +$5.6M");
  expect(screen.getByRole("group").getAttribute("aria-label")).toBe(
    "Change in fraud loss, 4 members, largest increase App at +$5.6M",
  );
});

test("delta bars: a decrease draws to the left of zero", () => {
  const after = view(pts([["App", 15_609_548], ["Web", 8_000_000], ["Telesales", 10_044_517]]));
  const { container } = render(<DeltaBars before={BEFORE} after={after} title="t" />);
  const web = screen.getByRole("button", { name: /^Web/ });
  const fill = web.querySelector<HTMLElement>(".delta-fill")!;
  expect(fill.dataset.sign).toBe("neg");
  expect(web.textContent).toContain("-$1.0M");
  const zero = container.querySelector<HTMLElement>(".delta-zero")!;
  expect(parseFloat(fill.style.left) + parseFloat(fill.style.width)).toBeCloseTo(parseFloat(zero.style.left), 5);
});

test("delta bars: focus shows after, before, and change", () => {
  render(<DeltaBars before={BEFORE} after={AFTER} title="t" />);
  fireEvent.focus(screen.getByRole("button", { name: /^Telesales/ }));
  const tip = screen.getByRole("status");
  expect(tip.textContent).toContain("$10.0M");
  expect(tip.textContent).toContain("$6.0M");
  expect(tip.textContent).toContain("+$4.0M");
});

test("delta bars: the axis is signed with a zero tick", () => {
  const after = view(pts([["App", 15_609_548], ["Web", 8_000_000], ["Telesales", 10_044_517]]));
  const { container } = render(<DeltaBars before={BEFORE} after={after} title="t" />);
  const ticks = [...container.querySelectorAll(".delta-axis span")].map((t) => t.textContent ?? "");
  expect(ticks).toContain("0");
  expect(ticks.some((t) => t.startsWith("-"))).toBe(true);
});

test("delta bars: nulls never produce NaN", () => {
  const before = view(pts([["App", null], ["Web", null]]));
  const after = view(pts([["App", null], ["Web", 3]]));
  const { container } = render(<DeltaBars before={before} after={after} title="t" />);
  for (const el of container.querySelectorAll("*")) {
    for (const attr of el.getAttributeNames()) expect(el.getAttribute(attr)).not.toMatch(/NaN|Infinity/);
  }
});

test("delta bars: the twin has four columns", async () => {
  render(<DeltaBars before={BEFORE} after={AFTER} title="t" />);
  fireEvent.click(screen.getByRole("button", { name: "Table" }));
  const heads = screen.getAllByRole("columnheader").map((h) => h.textContent);
  expect(heads).toEqual(["sales_channel", "Before", "After", "Change"]);
});

const cash = (v: number) => formatValue(v, "currency");
const PARTS = view(pts([
  ["App", 15_609_548], ["Web", 10_053_341], ["Telesales", 10_044_517],
  ["Partner", 8_292_885], ["Retail", 8_268_225], ["Marketplace", 319_143],
], cash));

test("segmented: parts carry share and value", () => {
  render(<Segmented query={PARTS} title="Fraud loss by sales channel" />);
  expect(screen.getByRole("button", { name: "App, $15.6M, 30%" })).toBeTruthy();
  expect(screen.getByRole("group").getAttribute("aria-label")).toBe(
    "Fraud loss by sales channel, 6 parts, App largest at 30%",
  );
});

test("segmented: a small part prints no inner label", () => {
  render(<Segmented query={PARTS} title="t" />);
  const small = screen.getByRole("button", { name: /^Marketplace/ });
  expect(small.textContent).toBe("");
  expect(screen.getByRole("button", { name: /^App/ }).textContent).toBe("30%");
  fireEvent.focus(small);
  expect(screen.getByRole("status").textContent).toContain("$319.1K");
});

test("segmented: seven or more parts fold into Other", () => {
  const eight = view(pts(["a", "b", "c", "d", "e", "f", "g", "h"].map((k, i) => [k, 100 - i]), cash));
  render(<Segmented query={eight} title="t" />);
  const parts = within(screen.getByRole("group")).getAllByRole("button");
  expect(parts).toHaveLength(7);
  expect(parts[6].getAttribute("aria-label")).toMatch(/^Other, \$187\.00, /);
});

test("segmented: zero and null parts draw nothing but stay in the twin", () => {
  const some = view(pts([["App", 5], ["Web", 0], ["Retail", null]], cash));
  render(<Segmented query={some} title="t" />);
  expect(within(screen.getByRole("group")).getAllByRole("button")).toHaveLength(1);
  fireEvent.click(screen.getByRole("button", { name: "Table" }));
  expect(screen.getAllByRole("row")).toHaveLength(4);
});

test("segmented: the legend has one entry per part and no numbers", () => {
  const { container } = render(<Segmented query={PARTS} title="t" />);
  const items = [...container.querySelectorAll(".legend li")].map((li) => li.textContent);
  expect(items).toEqual(["App", "Web", "Telesales", "Partner", "Retail", "Marketplace"]);
});

function grid(names: string[], months = 3): QueryView {
  const rows: [string, number][] = [];
  for (let m = 0; m < months; m += 1) names.forEach((n, k) => rows.push([`${month(m)} · ${n}`, 1_000_000 * (k + 1) + m * 100_000]));
  return view(pts(rows), { columns: ["month", "sales_channel", "fraud_loss"], series: "sales_channel" });
}

test("multiples: one panel per member with a shared axis", () => {
  const { container } = render(<SmallMultiples query={grid(["App", "Web", "Telesales"])} title="Loss by channel" />);
  const panels = [...container.querySelectorAll(".panel")];
  expect(panels.map((p) => p.querySelector(".panel-name")!.textContent)).toEqual(["App", "Web", "Telesales"]);
  const ticks = panels.map((p) => [...p.querySelectorAll("text.tick")].map((t) => t.textContent).join("|"));
  expect(new Set(ticks).size).toBe(1);
  expect(ticks[0]).not.toBe("");
  expect(container.querySelector(".legend")).toBeNull();
});

test("multiples: one crosshair moves every panel", () => {
  render(<SmallMultiples query={grid(["App", "Web", "Telesales"])} title="Loss by channel" />);
  const group = screen.getByRole("img");
  expect(group.getAttribute("aria-label")).toBe(
    "Loss by channel. 3 panels, 3 points each. Use the arrow keys to read each point.",
  );
  fireEvent.focus(group);
  fireEvent.keyDown(group, { key: "ArrowLeft" });
  const tip = screen.getByRole("status");
  expect(tip.children).toHaveLength(4);
  expect(tip.children[0].textContent).toBe("Feb 2024");
  expect(document.querySelectorAll(".panel .crosshair")).toHaveLength(3);
});

test("multiples: seven members fold into Other", () => {
  const { container } = render(<SmallMultiples query={grid(["a", "b", "c", "d", "e", "f", "g"])} title="t" />);
  const names = [...container.querySelectorAll(".panel-name")].map((n) => n.textContent);
  expect(names).toHaveLength(6);
  expect(names[5]).toBe("Other");
});

test("multiples: the twin has a column per panel", () => {
  render(<SmallMultiples query={grid(["App", "Web", "Telesales"])} title="t" />);
  fireEvent.click(screen.getByRole("button", { name: "Table" }));
  expect(screen.getAllByRole("columnheader").map((h) => h.textContent)).toEqual(["month", "App", "Web", "Telesales"]);
});

test("chart: dispatches the three forms", () => {
  const queries = { q1: PARTS, q1_prev_members: BEFORE, q2: grid(["App", "Web", "Telesales"]) };
  const one = (block: Parameters<typeof Chart>[0]["block"]) => render(withData(<Chart block={block} />, queries));
  let r = one({ type: "segmented", query_ref: "q1", title: "Seg" });
  expect(r.container.querySelector(".segbar")).toBeTruthy();
  r.unmount();
  r = one({ type: "delta_bars", query_ref: "q1", against_ref: "q1_prev_members", title: "Delta" });
  expect(r.container.querySelector(".delta-track")).toBeTruthy();
  r.unmount();
  r = one({ type: "small_multiples", query_ref: "q2", title: "Multi" });
  expect(r.container.querySelector(".multiples")).toBeTruthy();
  r.unmount();
  r = one({ type: "delta_bars", query_ref: "q1", against_ref: "missing", title: "Delta" });
  expect(r.container.innerHTML).toBe("");
});

test("segmented: a click selects the part and grays the others", async () => {
  const onSelect = vi.fn();
  render(<Segmented query={PARTS} title="t" onSelect={onSelect} selected="Web" />);
  const web = screen.getByRole("button", { name: /^Web/ });
  expect(web.getAttribute("aria-pressed")).toBe("true");
  const app = screen.getByRole("button", { name: /^App/ });
  expect(app.style.background).toBe("var(--gray)");
  // A dimmed part prints no share, so no light text sits on the gray.
  expect(app.textContent).toBe("");
  expect(web.textContent).toBe("19%");
  await userEvent.click(app);
  expect(onSelect).toHaveBeenCalledWith("App");
  await userEvent.click(web);
  expect(onSelect).toHaveBeenCalledWith(null);
});

test("table: a click on a dimension cell selects the member", async () => {
  const onSelect = vi.fn();
  const q = view(pts([["App", 1], ["Web", null]]));
  render(<DataTable query={{ ...q, rows: [["App", 1], [null, 2]] }} title="t" onSelect={onSelect} />);
  await userEvent.click(screen.getByRole("button", { name: "App" }));
  expect(onSelect).toHaveBeenCalledWith("sales_channel", "App");
  await userEvent.click(screen.getByRole("button", { name: "No value" }));
  expect(onSelect).toHaveBeenCalledWith("sales_channel", null);
});

test("multiples: a panel click selects its member", async () => {
  const onSelect = vi.fn();
  render(<SmallMultiples query={grid(["App", "Web", "Telesales"])} title="t" onSelect={onSelect} />);
  await userEvent.click(screen.getByRole("button", { name: "Select Web" }));
  expect(onSelect).toHaveBeenCalledWith("Web");
});

test("segmented: the inner share shows only when it fits the measured block", () => {
  render(<Segmented query={PARTS} title="t" />);
  expect(screen.getByRole("button", { name: /^App/ }).textContent).toBe("30%");
  expect(screen.getByRole("button", { name: /^Marketplace/ }).textContent).toBe("");
  cleanup();
  barWidth = 40;
  render(<Segmented query={PARTS} title="t" />);
  // 30% of a 40px bar is 12px, too narrow for the label and its padding.
  expect(screen.getByRole("button", { name: /^App/ }).textContent).toBe("");
});

test("new forms: the twins show column labels, not member ids", () => {
  const labels = { "orders.sales_channel": "Sales channel", fraud_loss: "Fraud loss" };
  const after = { ...AFTER, columns: ["orders.sales_channel", "fraud_loss"], labels };
  render(<DeltaBars before={BEFORE} after={after} title="t" />);
  fireEvent.click(screen.getByRole("button", { name: "Table" }));
  expect(screen.getAllByRole("columnheader")[0].textContent).toBe("Sales channel");
  cleanup();
  render(<Segmented query={{ ...PARTS, columns: ["orders.sales_channel", "fraud_loss"], labels }} title="t" />);
  fireEvent.click(screen.getByRole("button", { name: "Table" }));
  expect(screen.getAllByRole("columnheader").map((h) => h.textContent)).toEqual(["Sales channel", "Fraud loss", "Share"]);
});
