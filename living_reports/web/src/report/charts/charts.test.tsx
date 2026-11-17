import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import type { Point, QueryView } from "../../lib/api";
import { Bars } from "./Bars";
import { DataTable } from "./DataTable";
import { LineChart } from "./LineChart";

const money = (v: number) => `$${(v / 1e6).toFixed(1)}M`;
const pts = (rows: [string, number | null][]): Point[] =>
  rows.map(([label, value], i) => ({
    label, value, formatted: value === null ? "No data" : money(value), fact_id: `q1.fraud_loss.${i}`,
  }));
const view = (points: Point[], extra: Partial<QueryView> = {}): QueryView => ({
  columns: ["sales_channel", "fraud_loss"], rows: points.map((p) => [p.label, p.value]),
  query_hash: "h", points, ...extra,
});

const CHANNELS = view(pts([
  ["App", 15_609_548], ["Web", 10_053_341], ["Telesales", 10_044_517],
  ["Partner", 8_292_885], ["Retail", 8_268_225], ["Marketplace", 319_143],
]));

const MONTHLY = [1302985, 1445190, 1558723, 1481456, 1695875, 1685984, 1505746, 1559674, 1624144,
  1627782, 1986449, 2092738, 1736240, 1719400, 2449724, 2350234, 2168561, 2323449, 3083730,
  3577319, 2691581, 2887015, 4277772, 3755887];
const month = (i: number) => `${2024 + Math.floor(i / 12)}-${String((i % 12) + 1).padStart(2, "0")}-01`;
const LINE = view(pts(MONTHLY.map((v, i) => [month(i), v])), { columns: ["month", "fraud_loss"] });

test("bars: focus shows the tooltip with the formatted value", () => {
  render(<Bars query={CHANNELS} title="Fraud loss by sales channel" />);
  fireEvent.focus(screen.getByRole("button", { name: "Telesales, $10.0M" }));
  expect(within(screen.getByRole("status")).getByText("$10.0M")).toBeTruthy();
});

test("bars: the group label summarizes the chart", () => {
  render(<Bars query={CHANNELS} title="Fraud loss by sales channel" />);
  expect(screen.getByRole("group").getAttribute("aria-label")).toBe(
    "Fraud loss by sales channel, 6 values, App highest at $15.6M",
  );
});

test("bars: emphasis grays the others", () => {
  const { container } = render(<Bars query={CHANNELS} title="t" emphasis="Telesales" />);
  const fills = [...container.querySelectorAll<HTMLElement>(".bar-fill")].map((f) => f.style.background);
  expect(fills[2]).toBe("var(--c1)");
  expect(fills.filter((f) => f === "var(--gray)")).toHaveLength(5);
});

test("line: focus shows the last month and the arrow keys move the readout", async () => {
  render(<LineChart query={LINE} title="Fraud loss by month" />);
  const plot = screen.getByRole("img", { name: /Fraud loss by month/ });
  fireEvent.focus(plot);
  expect(within(screen.getByRole("status")).getByText("$3.8M")).toBeTruthy();
  expect(screen.getByRole("status").textContent).toContain("Dec 2025");
  fireEvent.keyDown(plot, { key: "ArrowLeft" });
  expect(screen.getByRole("status").textContent).toContain("Nov 2025");
  expect(screen.getByRole("status").textContent).toContain("$4.3M");
});

test("line: the axis has nice ticks", () => {
  const { container } = render(<LineChart query={LINE} title="t" />);
  const ticks = [...container.querySelectorAll(".tick")].map((t) => t.textContent);
  expect(ticks).toEqual(["0", "$2M", "$4M", "$6M"]);
});

test("line: one series has no legend, two series have one", () => {
  const { container, unmount } = render(<LineChart query={LINE} title="t" />);
  expect(container.querySelector(".legend")).toBeNull();
  unmount();
  const two = view(
    [...pts([[`${month(0)} · App`, 1e6], [`${month(1)} · App`, 2e6]]),
     ...pts([[`${month(0)} · Web`, 3e6], [`${month(1)} · Web`, 4e6]])],
    { columns: ["month", "sales_channel", "fraud_loss"], series: "sales_channel" },
  );
  const { container: c2 } = render(<LineChart query={two} title="t" />);
  expect(within(c2.querySelector(".legend") as HTMLElement).getByText("Web")).toBeTruthy();
  expect(c2.querySelectorAll("polyline")).toHaveLength(2);
});

test("an empty query shows the empty message and an empty twin", async () => {
  const empty = view([]);
  render(<><Bars query={empty} title="b" /><LineChart query={empty} title="l" /></>);
  expect(screen.getAllByText("No data for this selection.")).toHaveLength(2);
  await userEvent.click(screen.getAllByRole("button", { name: "Table" })[0]);
  expect(screen.getAllByRole("row")).toHaveLength(1);
});

test("one point renders with no NaN", () => {
  const { container } = render(<LineChart query={view(pts([[month(0), 5]]))} title="t" />);
  const attrs = [...container.querySelectorAll("svg *")].flatMap((el) => [...el.attributes].map((a) => a.value));
  expect(attrs.some((a) => a.includes("NaN"))).toBe(false);
});

test("null values are skipped in the line and shown as No data in the twin", async () => {
  const gap = view(pts([[month(0), 1e6], [month(1), null], [month(2), 3e6]]));
  const { container } = render(<LineChart query={gap} title="t" />);
  expect(container.querySelector("polyline")!.getAttribute("points")!.split(" ")).toHaveLength(2);
  await userEvent.click(screen.getByRole("button", { name: "Table" }));
  expect(screen.getByText("No data")).toBeTruthy();
});

test("a label with markup stays text everywhere", async () => {
  const q = view(pts([["<b>Retail</b>", 8_268_225]]));
  const { container } = render(<Bars query={q} title="t" />);
  fireEvent.focus(screen.getByRole("button", { name: "<b>Retail</b>, $8.3M" }));
  expect(screen.getAllByText("<b>Retail</b>").length).toBeGreaterThanOrEqual(2);
  await userEvent.click(screen.getByRole("button", { name: "Table" }));
  expect(screen.getByText("<b>Retail</b>")).toBeTruthy();
  expect(container.querySelector("b")).toBeNull();
});

test("table: a header click sorts the rows", async () => {
  render(<DataTable query={CHANNELS} title="t" />);
  await userEvent.click(screen.getByRole("button", { name: "fraud_loss" }));
  const first = () => screen.getAllByRole("row")[1].textContent;
  expect(first()).toContain("Marketplace");
  await userEvent.click(screen.getByRole("button", { name: "fraud_loss" }));
  expect(first()).toContain("App");
});


test("bars: a click selects the member and marks it pressed", async () => {
  const onSelect = vi.fn();
  const { container } = render(<Bars query={CHANNELS} title="t" onSelect={onSelect} selected="Telesales" />);
  await userEvent.click(screen.getByRole("button", { name: /^Web/ }));
  expect(onSelect).toHaveBeenCalledWith("Web");
  expect(screen.getByRole("button", { name: /^Telesales/ }).getAttribute("aria-pressed")).toBe("true");
  expect(screen.getByRole("button", { name: /^Web/ }).getAttribute("aria-pressed")).toBe("false");
  const fills = [...container.querySelectorAll<HTMLElement>(".bar-fill")].map((f) => f.style.background);
  expect(fills[2]).toBe("var(--c1)");
  expect(fills.filter((f) => f === "var(--gray)")).toHaveLength(5);
});

test("bars: clicking the selected member again clears", async () => {
  const onSelect = vi.fn();
  render(<Bars query={CHANNELS} title="t" onSelect={onSelect} selected="Telesales" />);
  await userEvent.click(screen.getByRole("button", { name: /^Telesales/ }));
  expect(onSelect).toHaveBeenCalledWith(null);
});

test("bars: with no onSelect the bars are not toggles", () => {
  render(<Bars query={CHANNELS} title="t" />);
  expect(screen.getByRole("button", { name: /^Web/ }).getAttribute("aria-pressed")).toBeNull();
});

// jsdom has no PointerEvent, so pointer events would lose clientX.
if (!("PointerEvent" in window)) {
  class PointerEventPolyfill extends MouseEvent { pointerId = 1; }
  Object.assign(window, { PointerEvent: PointerEventPolyfill });
}

function plotOf(title: string) {
  const plot = screen.getByRole("img", { name: new RegExp(title) });
  vi.spyOn(plot, "getBoundingClientRect").mockReturnValue({ left: 0, top: 0, width: 660, height: 200, right: 660, bottom: 200, x: 0, y: 0, toJSON: () => ({}) });
  return plot;
}
// The x position of index i on the 24-month fixture: L + i * (W - L - R) / (n - 1).
const at = (i: number) => 52 + (i * (660 - 52 - 64)) / 23;

test("line: a drag selects a normalized range", () => {
  const onBrush = vi.fn();
  const { container } = render(<LineChart query={LINE} title="Loss" onBrush={onBrush} />);
  const plot = plotOf("Loss");
  expect(plot.getAttribute("aria-label")).toMatch(/Drag or press Shift and an arrow key to select a range\.$/);
  fireEvent.pointerDown(plot, { clientX: at(10) });
  fireEvent.pointerMove(plot, { clientX: at(4) });
  expect(container.querySelector("rect.brush")).toBeTruthy();
  fireEvent.pointerUp(plot, { clientX: at(4) });
  expect(onBrush).toHaveBeenCalledWith("2024-05-01", "2024-11-01");
  expect(container.querySelector("rect.brush")).toBeNull();
});

test("line: a click with no drag selects one month", () => {
  const onBrush = vi.fn();
  render(<LineChart query={LINE} title="Loss" onBrush={onBrush} />);
  const plot = plotOf("Loss");
  fireEvent.pointerDown(plot, { clientX: at(7) });
  fireEvent.pointerUp(plot, { clientX: at(7) });
  expect(onBrush).toHaveBeenCalledWith("2024-08-01", "2024-08-01");
});

test("line: shift and arrow then Enter brushes from the keyboard", () => {
  const onBrush = vi.fn();
  const { container } = render(<LineChart query={LINE} title="Loss" onBrush={onBrush} />);
  const plot = plotOf("Loss");
  fireEvent.focus(plot);
  fireEvent.keyDown(plot, { key: "ArrowLeft", shiftKey: true });
  fireEvent.keyDown(plot, { key: "ArrowLeft", shiftKey: true });
  expect(container.querySelector("rect.brush")).toBeTruthy();
  fireEvent.keyDown(plot, { key: "Escape" });
  expect(container.querySelector("rect.brush")).toBeNull();
  fireEvent.keyDown(plot, { key: "ArrowLeft", shiftKey: true });
  fireEvent.keyDown(plot, { key: "Enter" });
  expect(onBrush).toHaveBeenCalledWith("2025-09-01", "2025-10-01");
});

test("line: compare shows two series with a legend", async () => {
  const onToggle = vi.fn();
  const two = view(
    [...pts([["Month 1 · 2025", 2e6], ["Month 2 · 2025", 3e6]]), ...pts([["Month 1 · 2024", 1e6], ["Month 2 · 2024", 1.5e6]])],
    { columns: ["month", "period", "fraud_loss"], series: "period" },
  );
  const { container } = render(<LineChart query={two} title="Loss" compare={{ pressed: true, onToggle }} />);
  const button = screen.getByRole("button", { name: "Compare with previous period" });
  expect(button.getAttribute("aria-pressed")).toBe("true");
  expect([...container.querySelectorAll(".legend li")].map((l) => l.textContent)).toEqual(["2025", "2024"]);
  expect(container.querySelectorAll("polyline")).toHaveLength(2);
  expect([...container.querySelectorAll("polyline")].map((p) => p.getAttribute("stroke"))).toEqual(["var(--c1)", "var(--c2)"]);
  await userEvent.click(button);
  expect(onToggle).toHaveBeenCalled();
});

test("line: a compare reason shows under the title and the button stays unpressed", () => {
  render(<LineChart query={LINE} title="Loss" compare={{ pressed: false, onToggle: () => undefined, reason: "No data before Jan 2024" }} />);
  expect(screen.getByText("No data before Jan 2024")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Compare with previous period" }).getAttribute("aria-pressed")).toBe("false");
});

test("line: emphasis draws the subject over the whole in gray", () => {
  const subject = view(pts([[month(0), 1e6], [month(1), 2e6]]));
  const whole = view(pts([[month(0), 3e6], [month(1), 4e6]]));
  const { container } = render(<LineChart query={subject} title="Loss" emphasis={{ subject, whole, name: "Telesales" }} />);
  expect(screen.getByRole("heading").textContent).toBe("Loss · Telesales against all");
  const strokes = [...container.querySelectorAll("polyline")].map((p) => p.getAttribute("stroke"));
  expect(strokes).toEqual(["var(--gray)", "var(--c1)"]);
  expect([...container.querySelectorAll(".legend li")].map((l) => l.textContent)).toEqual(["Telesales", "All"]);
});

test("table: headers show the column labels from the query", () => {
  const labelled = view(pts([["App", 1_000_000]]), {
    columns: ["orders.sales_channel", "fraud_loss"],
    labels: { "orders.sales_channel": "Sales channel", fraud_loss: "Fraud loss" },
  });
  render(<DataTable query={labelled} title="Fraud loss by Sales channel" />);
  expect(screen.getByRole("button", { name: "Sales channel" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Fraud loss" })).toBeTruthy();
});

test("table: a column without a label shows its id", () => {
  render(<DataTable query={view(pts([["App", 1]]))} title="t" />);
  expect(screen.getByRole("button", { name: "sales_channel" })).toBeTruthy();
});
