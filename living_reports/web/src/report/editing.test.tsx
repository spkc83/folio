import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, vi } from "vitest";
import { posts, show, state, stubFetch } from "./askingFixture";

beforeEach(stubFetch);
afterEach(() => { vi.unstubAllGlobals(); localStorage.clear(); });

const dots = async (n = 0) => (await screen.findAllByRole("button", { name: "Section commands" }))[n];
const reportReads = () => state.calls.filter((c) => c.url === "/api/reports/r1" && c.method === "GET").length;

test("menu: opens with the keyboard and lists the commands", async () => {
  const user = userEvent.setup();
  show();
  const button = await dots();
  expect(button.getAttribute("aria-haspopup")).toBe("menu");
  button.focus();
  await user.keyboard("{Enter}");
  const menu = screen.getByRole("menu");
  expect(button.getAttribute("aria-expanded")).toBe("true");
  const items = within(menu).getAllByRole("menuitem");
  expect(items.map((i) => i.getAttribute("aria-label"))).toEqual(["Shorter", "Expand", "Swap chart", "Regenerate wording", "Edit wording", "Remove section"]);
  expect(document.activeElement).toBe(items[0]);
  await user.keyboard("{ArrowDown}");
  expect(document.activeElement).toBe(items[1]);
  await user.keyboard("{End}");
  expect(document.activeElement).toBe(items[5]);
  await user.keyboard("{ArrowDown}");
  expect(document.activeElement).toBe(items[0]);
  await user.keyboard("{Escape}");
  expect(screen.queryByRole("menu")).toBeNull();
  expect(document.activeElement).toBe(button);
});

test("menu: swap chart lists the forms from the server", async () => {
  const user = userEvent.setup();
  show();
  await user.click(await dots());
  await user.click(screen.getByRole("menuitem", { name: "Swap chart" }));
  const forms = await screen.findByRole("menu", { name: "Chart forms" });
  expect(within(forms).getAllByRole("menuitem").map((i) => i.getAttribute("aria-label"))).toEqual(["Ranked bars", "Table", "Back"]);
  await waitFor(() => expect(document.activeElement).toBe(within(forms).getAllByRole("menuitem")[0]));
  await user.keyboard("{ArrowDown}{Enter}");
  await waitFor(() => expect(posts("/sections/s1/chart")).toHaveLength(1));
  expect(JSON.parse(posts("/sections/s1/chart")[0].body!)).toEqual({ form: "table" });
  expect(screen.queryByRole("menu")).toBeNull();
});

test("menu: a non-author sees the wording items disabled", async () => {
  localStorage.setItem("lr-demo-user", "omar");
  const user = userEvent.setup();
  show();
  await user.click(await dots());
  const item = (name: string) => screen.getByRole("menuitem", { name });
  for (const name of ["Shorter", "Expand", "Regenerate wording", "Edit wording", "Remove section"]) {
    expect(item(name).getAttribute("aria-disabled")).toBe("true");
    expect(item(name).getAttribute("title")).toBe("Only an author of this report can change its words");
  }
  expect(item("Swap chart").getAttribute("aria-disabled")).toBeNull();
  await user.click(item("Shorter"));
  expect(posts("/command")).toHaveLength(0);
});

test("menu: remove posts the command and refreshes", async () => {
  const user = userEvent.setup();
  show();
  await user.click(await dots(1));
  const before = reportReads();
  await user.click(screen.getByRole("menuitem", { name: "Remove section" }));
  // One confirm step: the menu asks first, and Cancel posts nothing.
  const confirm = screen.getByRole("menu", { name: "Remove this section?" });
  expect(posts("/command")).toHaveLength(0);
  await waitFor(() => expect(document.activeElement).toBe(within(confirm).getByRole("menuitem", { name: "Cancel" })));
  await user.keyboard("{ArrowUp}{Enter}");
  await waitFor(() => expect(posts("/sections/s2/command")).toHaveLength(1));
  expect(JSON.parse(posts("/sections/s2/command")[0].body!)).toEqual({ command: "remove" });
  await waitFor(() => expect(reportReads()).toBeGreaterThan(before));
});

test("menu: a failed rewrite shows its message", async () => {
  const { json } = await import("./askingFixture");
  state.answers["/sections/s1/command"] = () => json({ kind: "failed", message: "The model did not give a valid answer.", cost: { calls: 2 } });
  const user = userEvent.setup();
  show();
  await user.click(await dots());
  await user.click(screen.getByRole("menuitem", { name: "Shorter" }));
  expect(await screen.findByText("The model did not give a valid answer.")).toBeTruthy();
});

test("feedback: pressing Helpful posts and marks the button", async () => {
  const user = userEvent.setup();
  show();
  const row = (await screen.findAllByRole("group", { name: "Feedback on this section" }))[0];
  const helpful = within(row).getByRole("button", { name: "Helpful" });
  expect(helpful.getAttribute("aria-pressed")).toBe("false");
  expect(within(row).getByRole("button", { name: "Not helpful" }).getAttribute("aria-pressed")).toBe("false");
  await user.click(helpful);
  await waitFor(() => expect(posts("/sections/s1/feedback")).toHaveLength(1));
  expect(JSON.parse(posts("/sections/s1/feedback")[0].body!)).toEqual({ signal: "up" });
  await waitFor(() => expect(helpful.getAttribute("aria-pressed")).toBe("true"));
  expect(row.textContent).toContain("It does not change the report.");
});

test("feedback: the saved signal shows as pressed", async () => {
  const { json } = await import("./askingFixture");
  state.answers["/sections/s2/feedback"] = () => json({ signal: "down" });
  show();
  const row = (await screen.findAllByRole("group", { name: "Feedback on this section" }))[1];
  await waitFor(() => expect(within(row).getByRole("button", { name: "Not helpful" }).getAttribute("aria-pressed")).toBe("true"));
});

const puts = () => state.calls.filter((c) => c.method === "PUT");

async function openEditor(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await dots());
  await user.click(screen.getByRole("menuitem", { name: "Edit wording" }));
  return screen.getByLabelText("Section wording") as HTMLTextAreaElement;
}

test("editor: opens from the menu with the tokens as text", async () => {
  const user = userEvent.setup();
  show();
  const area = await openEditor(user);
  expect(area.value).toBe("App leads at {fact:q1.fraud_loss.App}.\n\nThe total is {fact:q1_total.fraud_loss}.");
  expect(document.activeElement).toBe(area);
  expect(screen.queryByText("App leads at")).toBeNull();
});

test("editor: a token chip inserts at the caret", async () => {
  const user = userEvent.setup();
  show();
  const area = await openEditor(user);
  await user.clear(area);
  await user.type(area, "Total: .");
  area.setSelectionRange(7, 7);
  await user.click(screen.getByRole("button", { name: "Fraud loss: $1.55K" }));
  expect(area.value).toBe("Total: {fact:q1_total.fraud_loss}.");
});

test("editor: save sends paragraphs split on blank lines", async () => {
  const user = userEvent.setup();
  show();
  const area = await openEditor(user);
  await user.clear(area);
  await user.type(area, "First line.{Enter}{Enter}{Enter}Second {{fact:q1_total.fraud_loss}.");
  await user.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(puts()).toHaveLength(1));
  expect(puts()[0].url).toBe("/api/reports/r1/sections/s1/prose");
  expect(JSON.parse(puts()[0].body!)).toEqual({ paragraphs: ["First line.", "Second {fact:q1_total.fraud_loss}."] });
  await waitFor(() => expect(screen.queryByLabelText("Section wording")).toBeNull());
});

test("editor: a 422 shows the message and keeps the text", async () => {
  const { json } = await import("./askingFixture");
  state.answers["/sections/s1/prose"] = () => json({ detail: "raw_number: Use a fact token", issues: [] }, 422);
  const user = userEvent.setup();
  show();
  const area = await openEditor(user);
  await user.clear(area);
  await user.type(area, "Loss rose 38%.");
  await user.click(screen.getByRole("button", { name: "Save" }));
  expect((await screen.findByRole("alert")).textContent).toBe("raw_number: Use a fact token");
  expect((screen.getByLabelText("Section wording") as HTMLTextAreaElement).value).toBe("Loss rose 38%.");
});

test("editor: Escape cancels", async () => {
  const user = userEvent.setup();
  show();
  await openEditor(user);
  await user.keyboard("{Escape}");
  expect(screen.queryByLabelText("Section wording")).toBeNull();
  expect(puts()).toHaveLength(0);
  expect(screen.getByText(/App leads at/)).toBeTruthy();
});

test("editor: a non-author has no editor", async () => {
  localStorage.setItem("lr-demo-user", "omar");
  const user = userEvent.setup();
  show();
  await user.click(await dots());
  await user.click(screen.getByRole("menuitem", { name: "Edit wording" }));
  expect(screen.queryByLabelText("Section wording")).toBeNull();
});

test("blocks: move down posts the new order", async () => {
  const { makePayload } = await import("./askingFixture");
  const payload = makePayload();
  payload.spec.sections[0].blocks.push({ type: "line", query_ref: "q2", title: "Fraud loss by month" });
  payload.spec.sections = [payload.spec.sections[0]];
  state.payload = payload;
  const user = userEvent.setup();
  show();
  await user.click(await screen.findByRole("button", { name: "Move down: Fraud loss by sales channel" }));
  await waitFor(() => expect(posts("/sections/s1/blocks")).toHaveLength(1));
  expect(JSON.parse(posts("/sections/s1/blocks")[0].body!)).toEqual({ order: [0, 1, 2, 4, 3] });
  expect(screen.getByRole("button", { name: "Move up: Fraud loss by sales channel" }).hasAttribute("disabled")).toBe(true);
});

test("blocks: remove posts the block index", async () => {
  const user = userEvent.setup();
  show();
  await user.click(await screen.findByRole("button", { name: "Remove block: Fraud loss by month" }));
  const ask = screen.getByRole("group", { name: "Remove this block?" });
  await user.click(within(ask).getByRole("button", { name: "Cancel" }));
  expect(posts("/blocks")).toHaveLength(0);
  await user.click(screen.getByRole("button", { name: "Remove block: Fraud loss by month" }));
  await user.click(within(screen.getByRole("group", { name: "Remove this block?" })).getByRole("button", { name: "Remove" }));
  await waitFor(() => expect(posts("/sections/s2/blocks")).toHaveLength(1));
  expect(JSON.parse(posts("/sections/s2/blocks")[0].body!)).toEqual({ remove: 0 });
});


test("rights: a contributor without the author role cannot change the words", async () => {
  localStorage.setItem("lr-demo-user", "omar");
  state.payload = { ...state.payload, contributors: ["ana", "omar"] };
  const user = userEvent.setup();
  show();
  await user.click(await dots());
  expect(screen.getByRole("menuitem", { name: "Shorter" }).getAttribute("aria-disabled")).toBe("true");
  expect(screen.queryByRole("button", { name: /^Remove block/ })).toBeNull();
});

test("rights: a signer is warned once before a first content change", async () => {
  localStorage.setItem("lr-demo-user", "omar");
  const user = userEvent.setup();
  show();
  const row = (await screen.findAllByRole("group", { name: "The routed question" }))[0];
  await user.click(await within(row).findByRole("button", { name: "By Sales channel" }));
  await user.selectOptions(screen.getByRole("combobox", { name: "By" }), "Region");
  await user.click(screen.getByRole("button", { name: "Apply" }));
  const warning = screen.getByRole("alertdialog");
  expect(warning.textContent).toContain("Editing this means you cannot sign this report.");
  await user.click(within(warning).getByRole("button", { name: "Cancel" }));
  expect(posts("/query")).toHaveLength(0);
  await user.click(within(row).getByRole("button", { name: "By Sales channel" }));
  await user.selectOptions(screen.getByRole("combobox", { name: "By" }), "Region");
  await user.click(screen.getByRole("button", { name: "Apply" }));
  await user.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Continue" }));
  await waitFor(() => expect(posts("/sections/s1/query")).toHaveLength(1));
  // Once is enough for this report.
  await user.click(screen.getAllByRole("group", { name: "Follow up" })[0].querySelector("button")!);
  expect(screen.queryByRole("alertdialog")).toBeNull();
  await waitFor(() => expect(posts("/api/ask")).toHaveLength(1));
});

test("rights: an author sees no warning", async () => {
  const user = userEvent.setup();
  show();
  const row = (await screen.findAllByRole("group", { name: "The routed question" }))[0];
  await user.click(await within(row).findByRole("button", { name: "By Sales channel" }));
  await user.click(screen.getByRole("button", { name: "Apply" }));
  expect(screen.queryByRole("alertdialog")).toBeNull();
  await waitFor(() => expect(posts("/query")).toHaveLength(1));
});

test("editor: a FastAPI list detail shows its message", async () => {
  const { json } = await import("./askingFixture");
  state.answers["/sections/s1/prose"] = () => json({ detail: [{ type: "value_error", loc: ["body"], msg: "Value error, A paragraph has at most 4,000 characters." }] }, 422);
  const user = userEvent.setup();
  show();
  const area = await openEditor(user);
  await user.type(area, " More.");
  await user.click(screen.getByRole("button", { name: "Save" }));
  expect((await screen.findByRole("alert")).textContent).toBe("Value error, A paragraph has at most 4,000 characters.");
});

test("editor: the limits are checked before a request", async () => {
  const user = userEvent.setup();
  show();
  const area = await openEditor(user);
  await user.clear(area);
  area.focus();
  await user.paste("x".repeat(4001));
  await user.click(screen.getByRole("button", { name: "Save" }));
  expect((await screen.findByRole("alert")).textContent).toBe("A paragraph has at most 4,000 characters.");
  expect(puts()).toHaveLength(0);
});
