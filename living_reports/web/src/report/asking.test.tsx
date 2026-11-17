import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, vi } from "vitest";
import { posts, show, stubFetch } from "./askingFixture";

beforeEach(stubFetch);
afterEach(() => vi.unstubAllGlobals());

const firstChips = async () => (await screen.findAllByRole("group", { name: "The routed question" }))[0];

test("chips: the routed question shows under the title", async () => {
  show();
  const row = await firstChips();
  await within(row).findByRole("button", { name: "Metric Fraud loss" });
  expect(within(row).getByRole("button", { name: "By Sales channel" })).toBeTruthy();
  expect(within(row).getByRole("button", { name: "Region = East" })).toBeTruthy();
  expect(within(row).getByRole("button", { name: "Dates All dates" })).toBeTruthy();
});

test("chips: editing the dimension posts the new query", async () => {
  const user = userEvent.setup();
  show();
  const row = await firstChips();
  const chip = await within(row).findByRole("button", { name: "By Sales channel" });
  await user.click(chip);
  const dialog = screen.getByRole("dialog", { name: "Edit By" });
  await user.selectOptions(within(dialog).getByRole("combobox", { name: "By" }), "Region");
  await user.click(within(dialog).getByRole("button", { name: "Apply" }));
  await waitFor(() => expect(posts("/api/reports/r1/sections/s1/query")).toHaveLength(1));
  const body = JSON.parse(posts("/sections/s1/query")[0].body!);
  expect(body.dimensions).toEqual(["dealers.region"]);
  expect(body.filters).toEqual([{ member: "dealers.region", op: "eq", values: ["East"] }]);
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(document.activeElement).toBe(chip);
});

test("chips: a filter chip edits its value", async () => {
  const user = userEvent.setup();
  show();
  const row = await firstChips();
  await user.click(await within(row).findByRole("button", { name: "Region = East" }));
  const dialog = screen.getByRole("dialog", { name: "Edit Region" });
  const value = within(dialog).getByLabelText("Value");
  await user.clear(value);
  await user.type(value, "West");
  await user.click(within(dialog).getByRole("button", { name: "Apply" }));
  await waitFor(() => expect(posts("/sections/s1/query")).toHaveLength(1));
  expect(JSON.parse(posts("/sections/s1/query")[0].body!).filters).toEqual([{ member: "dealers.region", op: "eq", values: ["West"] }]);
});

test("chips: Escape closes the editor without a request", async () => {
  const user = userEvent.setup();
  show();
  const row = await firstChips();
  const chip = await within(row).findByRole("button", { name: "Metric Fraud loss" });
  await user.click(chip);
  expect(screen.getByRole("dialog")).toBeTruthy();
  await user.keyboard("{Escape}");
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(document.activeElement).toBe(chip);
  expect(posts("/query")).toHaveLength(0);
});

test("chips: dropped wording is reported", async () => {
  const user = userEvent.setup();
  const { state, json } = await import("./askingFixture");
  state.answers["/sections/s1/query"] = () => json({ kind: "ok", wording_dropped: true, cost: {} });
  show();
  const row = await firstChips();
  await user.click(await within(row).findByRole("button", { name: "By Sales channel" }));
  await user.selectOptions(screen.getByRole("combobox", { name: "By" }), "Region");
  await user.click(screen.getByRole("button", { name: "Apply" }));
  const note = await screen.findByText(/The words no longer matched the data/);
  expect(note.closest(".outcome")).toBeNull();
  expect(note.getAttribute("role")).toBe("status");
});

test("chips: a rejected edit shows the reason", async () => {
  const user = userEvent.setup();
  const { state, json } = await import("./askingFixture");
  state.answers["/sections/s1/query"] = () => json({ detail: "No rows match Region = Nope." }, 422);
  show();
  const row = await firstChips();
  await user.click(await within(row).findByRole("button", { name: "By Sales channel" }));
  await user.selectOptions(screen.getByRole("combobox", { name: "By" }), "Region");
  await user.click(screen.getByRole("button", { name: "Apply" }));
  expect(await screen.findByText("No rows match Region = Nope.")).toBeTruthy();
});

test("chips: an edition page shows no chips", async () => {
  show("/r/r1/e/1");
  await screen.findByText("Loss overview");
  expect(screen.queryByRole("group", { name: "The routed question" })).toBeNull();
});

const askInput = () => screen.getByLabelText("Ask about this report") as HTMLInputElement;

test("selection: a bar click names the member in the ask bar", async () => {
  const user = userEvent.setup();
  show();
  await user.click(await screen.findByRole("button", { name: "Telesales, $500.00" }));
  expect(askInput().placeholder).toBe("Ask about Sales channel is Telesales…");
});

test("selection: a heading click names the section and a second click clears it", async () => {
  const user = userEvent.setup();
  show();
  const heading = await screen.findByRole("button", { name: "Over time" });
  expect(askInput().placeholder).toBe("Select a number, a bar, or a range, or ask about this report…");
  await user.click(heading);
  expect(heading.getAttribute("aria-pressed")).toBe("true");
  expect(askInput().placeholder).toBe("Ask about Over time…");
  await user.click(heading);
  expect(heading.getAttribute("aria-pressed")).toBe("false");
  expect(askInput().placeholder).toBe("Select a number, a bar, or a range, or ask about this report…");
});

test("selection: the question carries the selection", async () => {
  const user = userEvent.setup();
  show();
  await user.click(await screen.findByRole("button", { name: "Telesales, $500.00" }));
  await user.click(screen.getByRole("button", { name: "Channels" }));
  await user.type(askInput(), "Why?{Enter}");
  await waitFor(() => expect(posts("/api/ask")).toHaveLength(1));
  const body = JSON.parse(posts("/api/ask")[0].body!);
  expect(body.question).toBe("Why?");
  expect(body.selection).toEqual({
    filters: [{ member: "orders.sales_channel", op: "eq", values: ["Telesales"] }], range: null, section_id: "s1", fact_id: null,
  });
});

test("selection: an unselected question sends no selection", async () => {
  const user = userEvent.setup();
  show();
  await user.type(await screen.findByLabelText("Ask about this report"), "Why?{Enter}");
  await waitFor(() => expect(posts("/api/ask")).toHaveLength(1));
  expect(JSON.parse(posts("/api/ask")[0].body!).selection).toBeNull();
});

test("selection: a click on a table's number selects the row's member", async () => {
  const { render } = await import("@testing-library/react");
  const { DataTable } = await import("./charts/DataTable");
  const picked: [string, string | null][] = [];
  const user = userEvent.setup();
  render(<DataTable title="t" dimensions={["orders.sales_channel"]} onSelect={(d, v) => picked.push([d, v])}
    query={{ columns: ["orders.sales_channel", "fraud_loss"], rows: [["App", 700]], query_hash: "h", points: [] }} />);
  await user.click(screen.getByRole("button", { name: "700" }));
  expect(picked).toEqual([["orders.sales_channel", "App"]]);
});

const followRow = (n: number) => screen.getAllByRole("group", { name: "Follow up" })[n];

test("follow-ups: chips from the vocabulary, per section", async () => {
  show();
  await screen.findByRole("button", { name: "Channels" });
  await waitFor(() => expect(within(followRow(0)).getAllByRole("button").map((b) => b.textContent))
    .toEqual(["By Fraud type", "Over time", "App by Fraud type"]));
  expect(within(followRow(1)).getAllByRole("button").map((b) => b.textContent))
    .toEqual(["By Sales channel", "Compare with previous period"]);
  expect(followRow(0).textContent).toContain("Built from the vocabulary, not the model.");
});

test("follow-ups: a chip posts the choice and the words", async () => {
  const user = userEvent.setup();
  show();
  await screen.findByRole("button", { name: "Channels" });
  await user.click(await within(followRow(0)).findByRole("button", { name: "By Fraud type" }));
  await waitFor(() => expect(posts("/api/ask")).toHaveLength(1));
  const body = JSON.parse(posts("/api/ask")[0].body!);
  expect(body.choice.dimensions).toEqual(["orders.fraud_type"]);
  expect(body.question).toBe("Fraud loss · by Fraud type · Region = East · All dates");
  expect(body.report_id).toBe("r1");
  expect(body.objective).toBe("why");
});

test("follow-ups: compare presses the line's compare", async () => {
  const user = userEvent.setup();
  show();
  const filters = await screen.findByRole("group", { name: "Report filters" });
  await user.selectOptions(within(filters).getByRole("combobox", { name: "Dates" }), "Custom…");
  await user.selectOptions(within(filters).getByRole("combobox", { name: "From" }), "2025-01-01");
  await user.click(within(followRow(1)).getByRole("button", { name: "Compare with previous period" }));
  const line = screen.getByText(/^Fraud loss by month/).closest(".card") as HTMLElement;
  await waitFor(() => expect(within(line).getByRole("button", { name: "Compare with previous period" }).getAttribute("aria-pressed")).toBe("true"));
  expect(line.querySelectorAll("polyline").length).toBe(2);
});

test("follow-ups: none on an edition page", async () => {
  show("/r/r1/e/1");
  await screen.findByText("Loss overview");
  expect(screen.queryByRole("group", { name: "Follow up" })).toBeNull();
});

test("a swapped table shows metric values in their format", async () => {
  const { render } = await import("@testing-library/react");
  const { DataTable } = await import("./charts/DataTable");
  render(<DataTable title="t" query={{
    columns: ["orders.fraud_type", "fraud_loss"], rows: [["identity_theft", 18887914.849999998], ["bust_out", 950.5]], query_hash: "h",
    points: [{ label: "identity_theft", value: 18887914.849999998, formatted: "$18.9M", fact_id: "a" }, { label: "bust_out", value: 950.5, formatted: "$950.50", fact_id: "b" }],
  }} />);
  expect(screen.getByText("$18.9M")).toBeTruthy();
  expect(screen.getByText("$950.50")).toBeTruthy();
  expect(screen.queryByText("18887914.849999998")).toBeNull();
});


test("chips: kept words after a data change get a neutral note", async () => {
  const user = userEvent.setup();
  const { state, json } = await import("./askingFixture");
  state.answers["/sections/s1/query"] = () => json({ kind: "ok", wording_dropped: false, wording_stale: true, cost: {} });
  show();
  const row = await firstChips();
  await user.click(await within(row).findByRole("button", { name: "Region = East" }));
  await user.click(screen.getByRole("button", { name: "Apply" }));
  const note = await screen.findByText("The data changed; these words were written for the earlier question. Use Regenerate wording.");
  expect(note.getAttribute("role")).toBe("status");
});

test("chips: a filter with another operator is read-only", async () => {
  const { state, makePayload } = await import("./askingFixture");
  const payload = makePayload();
  payload.query_defs!.q1 = { ...payload.query_defs!.q1, filters: [{ member: "dealers.region", op: "in", values: ["East", "West"] }] };
  state.payload = payload;
  show();
  const row = await firstChips();
  await within(row).findByRole("button", { name: "Metric Fraud loss" });
  expect(within(row).queryByRole("button", { name: "Region in East, West" })).toBeNull();
  expect(within(row).getByText("in East, West")).toBeTruthy();
});
