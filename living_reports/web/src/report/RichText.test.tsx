import { render } from "@testing-library/react";
import { RichText } from "./RichText";
import { FactsContext } from "./context";
import type { FactT } from "../lib/api";

const ok: FactT = { id: "a.b-1", label: "L", value: 1, formatted: "3.52%", query_hash: "h", metric: "m", metric_version: 1, source_id: "duckdb_demo", data_time: "2025-12-31T10:00:00", state: "ok" };

test("tokens become fact values inside plain text", () => {
  const { container } = render(
    <FactsContext.Provider value={{ "a.b-1": ok }}>
      <RichText text="Rate is {fact:a.b-1} today." />
    </FactsContext.Provider>,
  );
  expect(container.textContent).toBe("Rate is 3.52% today.");
});

test("text is never treated as HTML", () => {
  const { container } = render(<RichText text="<b>x</b>" />);
  expect(container.querySelector("b")).toBeNull();
});
