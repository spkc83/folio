import { render, screen } from "@testing-library/react";
import { Fact } from "./Fact";
import { FactsContext } from "./context";
import type { FactT } from "../lib/api";

const ok: FactT = { id: "a", label: "Loss", value: 1, formatted: "$52.6M", query_hash: "abc", metric: "m", metric_version: 1, source_id: "duckdb_demo", data_time: "2025-12-31T10:00:00", state: "ok" };
const empty: FactT = { ...ok, id: "b", value: null, formatted: "No data", state: "empty" };
const untraced: FactT = { ...ok, id: "c", query_hash: "" };

function show(id: string) {
  return render(
    <FactsContext.Provider value={{ a: ok, b: empty, c: untraced }}>
      <Fact id={id} />
    </FactsContext.Provider>,
  );
}

test("a traced fact shows its value and a verified mark", () => {
  show("a");
  const el = screen.getByText("$52.6M");
  expect(el.getAttribute("data-verified")).toBe("true");
  expect(el.getAttribute("title")).toContain("Loss");
});

test("an empty fact says No data with no verified mark", () => {
  const { container } = show("b");
  expect(screen.getByText("No data").getAttribute("data-verified")).toBe("false");
  expect(container.textContent).not.toMatch(/NaN|null|undefined|0%/);
});

test("an untraced fact gets no verified mark", () => {
  show("c");
  expect(screen.getByText("$52.6M").getAttribute("data-verified")).toBe("false");
});

test("an unknown fact id never shows a number", () => {
  const { container } = show("zz");
  expect(container.textContent).toBe("No data");
});
