import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, vi } from "vitest";
import { posts, show, state, stubFetch } from "./askingFixture";
import { claimsPayload } from "./claimsFixture";

beforeEach(() => {
  stubFetch();
  const data = claimsPayload();
  data.spec.sections[1] = { ...data.spec.sections[1], stale_claims: ["q2.change.West"] };
  state.payload = data;
});
afterEach(() => vi.unstubAllGlobals());

test("Rewrite this section sends the regenerate command for that section", async () => {
  const user = userEvent.setup();
  show("/r/r9");
  await user.click(await screen.findByRole("button", { name: "Rewrite this section" }));
  const sent = posts("/sections/s2/command");
  expect(sent).toHaveLength(1);
  expect(JSON.parse(sent[0].body!)).toEqual({ command: "regenerate" });
});

test("a published edition marks stale claims but offers no rewrite", async () => {
  show("/r/r9/e/1");
  expect(await screen.findByText("No longer true with the new data")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Rewrite this section" })).toBeNull();
});

test("the Evidence tab shows Claims and proof for a report with claims", async () => {
  const user = userEvent.setup();
  show("/r/r9");
  await user.click(await screen.findByRole("tab", { name: "Evidence" }));
  expect(screen.getByRole("heading", { name: "Claims and proof" })).toBeTruthy();
});
