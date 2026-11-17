import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { Section } from "../lib/api";
import { ClaimsProvider } from "./claims";
import { claimsPayload } from "./claimsFixture";
import { renderWith } from "./claimsHarness";
import { omissionText, wordingText } from "./review";
import { RichText } from "./RichText";
import { Evidence } from "./views/Evidence";

const section = (over: Partial<Section>): Section =>
  ({ id: "s1", heading: "h", intent: "q", blocks: [], decisions: [], narration: "model", ...over });

test.each([
  [{ wording: "model", omissions: "model" }, "Reviewed by a second model. Probable, not proven."],
  [{ wording: "connectors", omissions: "none" }, "Only connecting words, such as “also”. Checked by code."],
  [{ wording: "code", omissions: "none" }, "Fixed sentences that code wrote."],
  [{ wording: "none", omissions: "none" }, "Not reviewed."],
  [{ wording: "model", omissions: "model", outdated: true }, "Reviewed by a second model before the data changed. Not reviewed since."],
] as const)("the words between the claims: %o", (review, text) => {
  expect(wordingText(section({ review }))).toBe(text);
});

test("an old section with no record is not reviewed, unless code wrote it", () => {
  expect(wordingText(section({}))).toBe("Not reviewed.");
  expect(wordingText(section({ narration: "code" }))).toBe("Fixed sentences that code wrote.");
});

test("what the text leaves out is never more than probable", () => {
  expect(omissionText(section({ review: { wording: "connectors", omissions: "model" } }))).toBe(
    "Reviewed by a second model. Probable, not proven.");
  expect(omissionText(section({ review: { wording: "code", omissions: "none" } }))).toBe("Not reviewed.");
  expect(omissionText(section({}))).toBe("Not reviewed.");
});

test("Claims and proof shows the three levels of each section apart", () => {
  const data = claimsPayload();
  data.spec.sections[0].review = { wording: "model", omissions: "model", model: "decider" };
  renderWith((d) => <Evidence spec={d.spec} />, data);
  const checks = screen.getByRole("region", { name: "How each section was checked" });
  const first = within(checks).getByRole("group", { name: "Regions" });
  const rows = within(first).getAllByRole("listitem").map((r) => r.textContent);
  expect(rows).toEqual([
    "The numbers: Checked against the data (2 of 2 claims).",
    "The words between them: Reviewed by a second model. Probable, not proven.",
    "What the text leaves out: Reviewed by a second model. Probable, not proven.",
  ]);
  const second = within(checks).getByRole("group", { name: "West over time" });
  expect(second.textContent).toContain("The words between them: Fixed sentences that code wrote.");
  expect(checks.textContent).not.toContain("decider"); // the model name is for auditors only
});

test("a stale claim lowers the count of checked numbers", () => {
  const data = claimsPayload();
  data.spec.sections[1].stale_claims = ["q2.trend.West.half"];
  renderWith((d) => <Evidence spec={d.spec} />, data);
  const second = screen.getByRole("group", { name: "West over time" });
  expect(second.textContent).toContain("The numbers: Checked against the data (1 of 2 claims).");
});

test("the auditors see which model reviewed the words", async () => {
  const user = userEvent.setup();
  const data = claimsPayload();
  data.spec.sections[0].review = { wording: "model", omissions: "model", model: "decider" };
  renderWith((d) => <Evidence spec={d.spec} />, data);
  await user.click(screen.getByText("For auditors"));
  expect(screen.getByText("Regions: words reviewed by decider")).toBeTruthy();
});

test("the evidence of a claim says how the words around it were checked", async () => {
  const user = userEvent.setup();
  const data = claimsPayload();
  data.spec.sections[0].review = { wording: "none", omissions: "none" };
  renderWith((d) => (
    <ClaimsProvider spec={d.spec}><p className="prose"><RichText text="{claim:q1.rank.West}" /></p></ClaimsProvider>
  ), data);
  await user.click(screen.getByRole("button", { name: "Evidence 1" }));
  const drawer = screen.getByRole("dialog", { name: "Evidence 1" });
  expect(within(drawer).getByText("✓ Checked against the data")).toBeTruthy();
  expect(within(drawer).getByText("The words around it: Not reviewed.")).toBeTruthy();
});
