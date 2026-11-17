import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { FactT } from "../lib/api";
import { Badge } from "./Badge";
import { AnchorContext, FactsContext } from "./context";
import { Fact } from "./Fact";

const loss: FactT = {
  id: "q1_total.fraud_loss", label: "Fraud loss", value: 43_100_000, formatted: "$43.1M",
  query_hash: "d3b74483aa", metric: "fraud_loss", metric_version: 2, source_id: "duckdb_demo", data_time: "2025-12-31T10:00:00", state: "ok",
  definition: "Total loss on orders confirmed as fraud, excluding friendly fraud.",
};

function show(anchor: ((id: string) => void) | null) {
  return render(
    <FactsContext.Provider value={{ [loss.id]: loss }}>
      <AnchorContext.Provider value={anchor}>
        <p>Loss was <Fact id={loss.id} />.</p>
      </AnchorContext.Provider>
    </FactsContext.Provider>,
  );
}

test("a number opens the card that says where it comes from", async () => {
  show(() => {});
  await userEvent.click(screen.getByRole("button", { name: "$43.1M" }));
  const card = screen.getByRole("dialog", { name: "Where this number comes from" });
  expect(card.textContent).toContain("fraud_loss · version 2");
  expect(card.textContent).toContain("excluding friendly fraud");
  expect(card.textContent).toContain("d3b74483");
  expect(card.textContent).toContain("Traced");
});

test("Escape closes the card and returns focus to the number", async () => {
  show(() => {});
  const number = screen.getByRole("button", { name: "$43.1M" });
  await userEvent.click(number);
  await userEvent.keyboard("{Escape}");
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(document.activeElement).toBe(number);
});

test("the ask button anchors a question on the number", async () => {
  const asked: string[] = [];
  show((id) => asked.push(id));
  await userEvent.click(screen.getByRole("button", { name: "$43.1M" }));
  await userEvent.click(screen.getByRole("button", { name: "Ask about this number" }));
  expect(asked).toEqual([loss.id]);
  expect(screen.queryByRole("dialog")).toBeNull();
});

test("without a place to ask, the card has no ask button", async () => {
  show(null);
  await userEvent.click(screen.getByRole("button", { name: "$43.1M" }));
  expect(screen.getByRole("dialog")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Ask about this number" })).toBeNull();
});

test("the badge names the state of the report in a word", () => {
  const { rerender } = render(<Badge tier="business_critical" state="published" />);
  expect(screen.getByText("Certified")).toBeTruthy();
  rerender(<Badge tier="team" state="draft" />);
  expect(screen.getByText("Traced")).toBeTruthy();
  rerender(<Badge tier="exploratory" state="draft" />);
  expect(screen.getByText("Unverified")).toBeTruthy();
});

test("the card is phrasing content, so it can sit inside a paragraph", async () => {
  show(() => {});
  await userEvent.click(screen.getByRole("button", { name: "$43.1M" }));
  const card = screen.getByRole("dialog");
  expect(card.tagName).toBe("SPAN");
  expect(card.querySelector("div, p, h4, dl, dt, dd")).toBeNull();
});

test("the card names the source and the data time", async () => {
  show(() => {});
  await userEvent.click(screen.getByRole("button", { name: "$43.1M" }));
  const card = screen.getByRole("dialog", { name: "Where this number comes from" });
  expect(card.textContent).toContain("Sourceduckdb_demo");
  expect(card.textContent).toContain("Data time2025-12-31");
});
