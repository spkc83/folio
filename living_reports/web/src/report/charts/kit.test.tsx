import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ChartCard } from "./ChartCard";
import { slotColor } from "./palette";
import { linear, niceTicks, tickFormat } from "./scale";
import { TableTwin } from "./TableTwin";
import { Tooltip } from "./Tooltip";

const point = (formatted: string) => ({ label: "x", value: 1, formatted, fact_id: "f" });

test("nice ticks use a nice step and stop at the first multiple above the maximum", () => {
  expect(niceTicks(4_277_772)).toEqual([0, 2_000_000, 4_000_000, 6_000_000]);
  expect(niceTicks(15_609_548)).toEqual([0, 5_000_000, 10_000_000, 15_000_000, 20_000_000]);
  expect(niceTicks(0.0352)).toEqual([0, 0.01, 0.02, 0.03, 0.04]);
  expect(niceTicks(4_000_000)).toEqual([0, 1_000_000, 2_000_000, 3_000_000, 4_000_000]);
});

test("nice ticks survive empty and broken input", () => {
  expect(niceTicks(0)).toEqual([0, 1]);
  expect(niceTicks(-3)).toEqual([0, 1]);
  expect(niceTicks(NaN)).toEqual([0, 1]);
});

test("linear maps the domain onto the range", () => {
  const y = linear([0, 5_000_000], [146, 14]);
  expect(y(0)).toBe(146);
  expect(y(5_000_000)).toBe(14);
  expect(linear([0, 0], [0, 10])(0)).toBe(0);
});

test("tick format follows the formatted values", () => {
  expect(tickFormat([point("$15.6M")])(5_000_000)).toBe("$5M");
  expect(tickFormat([point("$15.6M")])(1_250_000)).toBe("$1.25M");
  expect(tickFormat([point("$9.5K")])(250_000)).toBe("$250K");
  expect(tickFormat([point("3.52%")])(0.025)).toBe("2.5%");
  expect(tickFormat([point("1,200")])(1200)).toBe("1,200");
  expect(tickFormat([])(0)).toBe("0");
});

test("a member keeps its slot when others are filtered out", () => {
  const all = ["App", "Web", "Telesales"];
  expect(slotColor("Telesales", all)).toBe("var(--c3)");
  expect(slotColor("Telesales", ["Telesales"])).toBe("var(--c1)");
  expect(slotColor("Seventh", ["a", "b", "c", "d", "e", "f", "Seventh"])).toBe("var(--gray)");
});

test("the card switches between chart and table", async () => {
  render(<ChartCard title="Loss" table={<p>the table</p>}><p>the chart</p></ChartCard>);
  const toggle = screen.getByRole("button", { name: "Table" });
  expect(toggle.getAttribute("aria-pressed")).toBe("false");
  expect(screen.getByText("the chart")).toBeTruthy();
  await userEvent.click(toggle);
  expect(toggle.getAttribute("aria-pressed")).toBe("true");
  expect(screen.getByText("the table")).toBeTruthy();
  expect(screen.queryByText("the chart")).toBeNull();
  await userEvent.click(toggle);
  expect(screen.getByText("the chart")).toBeTruthy();
});

test("the tooltip shows labels as text, never as markup", () => {
  const { container } = render(<Tooltip x={50} heading="Jun 25" rows={[{ key: "<b>x</b>", value: "$1.0M" }]} />);
  expect(screen.getByText("<b>x</b>")).toBeTruthy();
  expect(container.querySelector("b")).toBeNull();
});

test("the table twin right-aligns numbers", () => {
  render(<TableTwin columns={["Channel", "Loss"]} rows={[["App", "$15.6M"]]} numeric={[1]} />);
  expect(screen.getByText("$15.6M").className).toContain("num");
  expect(screen.getByText("App").className).not.toContain("num");
});

test("signed ticks share one step across zero", async () => {
  const { signedTicks } = await import("./scale");
  expect(signedTicks(-1_300_000, 2_400_000)).toEqual([-2_000_000, -1_000_000, 0, 1_000_000, 2_000_000, 3_000_000]);
  expect(signedTicks(0, 5)).toEqual(niceTicks(5));
  expect(signedTicks(NaN, 1)).toEqual([0, 1]);
  expect(signedTicks(-3, -1)).toContain(0);
});

test("values format exactly as the server formats them", async () => {
  const { formatValue } = await import("./format");
  expect(formatValue(15_609_548, "currency")).toBe("$15.6M");
  expect(formatValue(319_143, "currency")).toBe("$319.1K");
  expect(formatValue(42, "currency")).toBe("$42.00");
  expect(formatValue(2_250, "currency")).toBe("$2.2K");
  expect(formatValue(3_250, "currency")).toBe("$3.2K");
  expect(formatValue(3_350, "currency")).toBe("$3.4K");
  expect(formatValue(-1_200_000, "currency")).toBe("-$1.2M");
  expect(formatValue(0.0264, "percent")).toBe("2.64%");
  expect(formatValue(1_200_000, "count")).toBe("1,200,000");
  expect(formatValue(null, "currency")).toBe("No data");
  expect(formatValue(Infinity, "percent")).toBe("No data");
});

test("delta text gives points for rates and a relative change otherwise", async () => {
  const { deltaText } = await import("./format");
  expect(deltaText(0.0352, 0.0264, "percent")).toBe("+0.88 pts");
  expect(deltaText(52.6e6, 31.16e6, "currency")).toBe("+68.8%");
  expect(deltaText(80, 100, "count")).toBe("-20.0%");
  expect(deltaText(5, 0, "count")).toBeNull();
  expect(deltaText(null, 3, "count")).toBeNull();
});

test("a fact id names its query", async () => {
  const { refOf } = await import("../refs");
  expect(refOf("q1_total.fraud_loss")).toBe("q1");
  expect(refOf("q1.fraud_loss.share.telesales")).toBe("q1");
});

test("shape keeps the largest series and folds the rest into Other", async () => {
  const { shape } = await import("./series");
  const names = ["a", "b", "c", "d", "e"];
  const query = {
    columns: ["month", "k", "v"], rows: [], query_hash: "h", series: "k",
    points: names.map((n, i) => ({ label: `2025-01-01 · ${n}`, value: i, formatted: String(i), fact_id: n })),
  };
  // The values are 0..4, so c, d, and e have the largest totals.
  expect(shape(query, 4).series.map((s) => s.name)).toEqual(["c", "d", "e", "Other"]);
  expect(shape(query, 6).series.map((s) => s.name)).toEqual(names);
});
