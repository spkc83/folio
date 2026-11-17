import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { citationOrder, ClaimsProvider } from "./claims";
import { CLAIMS_SPEC, claimsPayload } from "./claimsFixture";
import { renderWith } from "./claimsHarness";
import { RichText } from "./RichText";

const paragraph = (text: string) => renderWith((d) => (
  <ClaimsProvider spec={d.spec}><p className="prose"><RichText text={text} /></p><button type="button">Elsewhere</button></ClaimsProvider>
));

test("citation numbers follow reading order: sections, then their paragraphs and actions", () => {
  expect(citationOrder(CLAIMS_SPEC)).toEqual(["q1.rank.West", "q1.extreme.South", "q2.change.West", "q2.trend.West.half"]);
});

test("a claim token renders the claim's text with fact values and a citation number", () => {
  const { container } = paragraph("{claim:q1.rank.West}.");
  expect(container.querySelector("p")!.textContent).toBe(
    "West had the lowest fraud loss of the 4 regions ($9.29M, 19.03% of the total)1.",
  );
  expect(screen.getByRole("button", { name: "Evidence 1" })).toBeTruthy();
});

test("the second claim in reading order gets number 2 wherever it shows", () => {
  paragraph("{claim:q1.extreme.South}");
  expect(screen.getByRole("button", { name: "Evidence 2" })).toBeTruthy();
});

test("a word token renders the claim's word that code wrote, with the claim's citation", () => {
  const { container } = paragraph("South had the {word:q1.extreme.South} fraud loss at {fact:q1.fraud_loss.South}.");
  expect(container.querySelector("p")!.textContent).toBe("South had the highest2 fraud loss at $15.84M.");
  expect(screen.getByRole("button", { name: "Evidence 2" })).toBeTruthy();
});

test("a word token counts in the citation order", () => {
  const spec = { ...CLAIMS_SPEC, sections: [{ ...CLAIMS_SPEC.sections[0], blocks: [{ type: "prose" as const, text: "{word:q1.extreme.South}" }] }] };
  expect(citationOrder(spec)).toEqual(["q1.extreme.South"]);
});

test("an unknown claim shows No data, never invented words", () => {
  const { container } = paragraph("{claim:q9.nothing}");
  expect(container.querySelector("p")!.textContent).toBe("No data");
});

test("fact tokens and plain text keep working next to claims", () => {
  const { container } = paragraph("Total {fact:q1_total.fraud_loss}; {claim:q2.change.West}");
  expect(container.querySelector("p")!.textContent).toBe(
    "Total $48.83M; fraud loss for West rose from $2.11M in 2024 H1 to $2.63M in 2025 Jul–Nov3",
  );
});

test("a citation opens a labelled evidence drawer with the sentence and the verdict", async () => {
  const user = userEvent.setup();
  paragraph("{claim:q1.rank.West}");
  await user.click(screen.getByRole("button", { name: "Evidence 1" }));
  const drawer = screen.getByRole("dialog", { name: "Evidence 1" });
  expect(drawer.textContent).toContain("West had the lowest fraud loss of the 4 regions ($9.29M, 19.03% of the total)");
  expect(within(drawer).getByText("✓ Checked against the data")).toBeTruthy();
  expect(within(drawer).getByText("What was counted")).toBeTruthy();
  expect(within(drawer).getByText("Fraud loss · by Region · Jan 2024 – Nov 2025 · 1,130,716 orders")).toBeTruthy();
  expect(within(drawer).getByText("How it is defined")).toBeTruthy();
  expect(within(drawer).getByText("Fraud loss: the order amount of orders confirmed as fraud.")).toBeTruthy();
});

test("the comparison table shows value and share, with the claimed row highlighted", async () => {
  const user = userEvent.setup();
  paragraph("{claim:q1.rank.West}");
  await user.click(screen.getByRole("button", { name: "Evidence 1" }));
  const table = within(screen.getByRole("dialog")).getByRole("table", { name: "How it compares" });
  const header = within(table).getAllByRole("columnheader").map((h) => h.textContent);
  expect(header).toEqual(["Region", "Fraud loss", "Share"]);
  const rows = within(table).getAllByRole("row").slice(1);
  expect(rows.map((r) => r.textContent)).toEqual(["South$15.84M32.44%", "East$13.30M27.24%", "North$10.40M21.30%", "West$9.29M19.03%"]);
  expect(rows[3].getAttribute("aria-current")).toBe("true");
  expect(rows[0].getAttribute("aria-current")).toBeNull();
});

test("a change claim shows the series with the periods it names marked", async () => {
  const user = userEvent.setup();
  paragraph("{claim:q2.change.West}");
  await user.click(screen.getByRole("button", { name: "Evidence 3" }));
  const drawer = screen.getByRole("dialog");
  expect(within(drawer).getByText("How it changed")).toBeTruthy();
  const items = within(within(drawer).getByRole("list", { name: "Values over time" })).getAllByRole("listitem");
  expect(items.map((i) => i.textContent)).toEqual(["2024 H1$2.11M", "2024 H2$2.34M", "2025 H1$2.22M", "2025 Jul–Nov$2.63M"]);
  expect(items.map((i) => i.getAttribute("data-marked"))).toEqual(["true", "false", "false", "true"]);
  expect(drawer.querySelectorAll("svg circle.marked").length).toBe(2);
  expect(within(drawer).queryByText("How it compares")).toBeNull();
});

test("a rank claim has no series section", async () => {
  const user = userEvent.setup();
  paragraph("{claim:q1.rank.West}");
  await user.click(screen.getByRole("button", { name: "Evidence 1" }));
  expect(within(screen.getByRole("dialog")).queryByText("How it changed")).toBeNull();
});

test("Escape closes the drawer and focus returns to the citation", async () => {
  const user = userEvent.setup();
  paragraph("{claim:q1.rank.West}");
  const cite = screen.getByRole("button", { name: "Evidence 1" });
  await user.click(cite);
  expect(cite.getAttribute("aria-expanded")).toBe("true");
  await user.keyboard("{Escape}");
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(document.activeElement).toBe(cite);
});

test("the close button and a click outside close the drawer", async () => {
  const user = userEvent.setup();
  paragraph("{claim:q1.rank.West}");
  const cite = screen.getByRole("button", { name: "Evidence 1" });
  await user.click(cite);
  await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Close" }));
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(document.activeElement).toBe(cite);
  await user.click(cite);
  await user.click(screen.getByRole("button", { name: "Elsewhere" }));
  expect(screen.queryByRole("dialog")).toBeNull();
});

test("a second click on the same citation closes the drawer", async () => {
  const user = userEvent.setup();
  paragraph("{claim:q1.rank.West}");
  const cite = screen.getByRole("button", { name: "Evidence 1" });
  await user.click(cite);
  await user.click(cite);
  expect(screen.queryByRole("dialog")).toBeNull();
});

test("a fact chip inside a claim opens the same drawer", async () => {
  const user = userEvent.setup();
  paragraph("{claim:q1.rank.West}");
  await user.click(screen.getByRole("button", { name: "$9.29M" }));
  expect(screen.getByRole("dialog", { name: "Evidence 1" })).toBeTruthy();
  await user.keyboard("{Escape}");
  expect(document.activeElement).toBe(screen.getByRole("button", { name: "$9.29M" }));
});

test("focus moves into the drawer when it opens", async () => {
  const user = userEvent.setup();
  paragraph("{claim:q1.rank.West}");
  await user.click(screen.getByRole("button", { name: "Evidence 1" }));
  expect(screen.getByRole("dialog").contains(document.activeElement)).toBe(true);
});

test("Show this as a table re-aggregates the section's data for the claimed segment", async () => {
  const user = userEvent.setup();
  paragraph("{claim:q2.change.West}");
  await user.click(screen.getByRole("button", { name: "Evidence 3" }));
  const drawer = screen.getByRole("dialog");
  await user.click(within(drawer).getByRole("button", { name: "Show this as a table" }));
  const table = within(within(drawer).getByText("The data for West").closest("section")!).getByRole("table");
  const rows = within(table).getAllByRole("row").slice(1).map((r) => r.textContent);
  expect(rows).toEqual(["2024-01-01$2.1M", "2024-07-01$2.3M", "2025-01-01$2.2M", "2025-07-01$2.6M"]);
});

test("with no cube, the drawer offers no table link", async () => {
  const user = userEvent.setup();
  const data = { ...claimsPayload(), cube: null };
  renderWith((d) => <ClaimsProvider spec={d.spec}><RichText text="{claim:q1.rank.West}" /></ClaimsProvider>, data);
  await user.click(screen.getByRole("button", { name: "Evidence 1" }));
  expect(within(screen.getByRole("dialog")).queryByRole("button", { name: "Show this as a table" })).toBeNull();
});

test("the drawer shows no ids, hashes, or member names", async () => {
  const user = userEvent.setup();
  paragraph("{claim:q2.trend.West.half}");
  await user.click(screen.getByRole("button", { name: "Evidence 4" }));
  const text = screen.getByRole("dialog").textContent ?? "";
  expect(text).not.toMatch(/q2\.|dealers\.region|502a7904|9b1e0f33|derived/);
});

test("a stale claim is marked in the text and in the drawer", async () => {
  const user = userEvent.setup();
  const data = claimsPayload();
  data.spec.sections[0] = { ...data.spec.sections[0], stale_claims: ["q1.rank.West"] };
  renderWith((d) => <ClaimsProvider spec={d.spec}><p><RichText text="{claim:q1.rank.West}" /></p></ClaimsProvider>, data);
  expect(screen.getByText("No longer true with the new data")).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "Evidence 1" }));
  const drawer = screen.getByRole("dialog");
  expect(within(drawer).queryByText("✓ Checked against the data")).toBeNull();
  expect(within(drawer).getByText("No longer true with the new data")).toBeTruthy();
});

test("without a claims provider, a fact-only text renders as before", () => {
  const { container } = renderWith(() => <RichText text="Loss {fact:q1_total.fraud_loss}." />);
  expect(container.textContent).toBe("Loss $48.83M.");
});

// Review fixes

test("the drawer says Not checked when a cited value is missing", async () => {
  const user = userEvent.setup();
  const data = claimsPayload();
  delete data.facts["q1.fraud_loss.share.West"];
  renderWith((d) => <ClaimsProvider spec={d.spec}><RichText text="{claim:q1.rank.West}" /></ClaimsProvider>, data);
  await user.click(screen.getByRole("button", { name: "Evidence 1" }));
  const drawer = screen.getByRole("dialog");
  expect(within(drawer).queryByText("✓ Checked against the data")).toBeNull();
  expect(within(drawer).getByText("Not checked: a value is missing")).toBeTruthy();
});

test("the drawer says Not checked when a cited value has no data", async () => {
  const user = userEvent.setup();
  const data = claimsPayload();
  data.facts["q1.fraud_loss.West"] = { ...data.facts["q1.fraud_loss.West"], state: "empty", formatted: "No data", value: null };
  renderWith((d) => <ClaimsProvider spec={d.spec}><RichText text="{claim:q1.rank.West}" /></ClaimsProvider>, data);
  await user.click(screen.getByRole("button", { name: "Evidence 1" }));
  expect(within(screen.getByRole("dialog")).getByText("Not checked: a value is missing")).toBeTruthy();
});

test("the comparison header reads as a label even when the payload sends a member id", async () => {
  const user = userEvent.setup();
  const data = claimsPayload();
  data.spec.claims!["q1.rank.West"].evidence.by = "dealers.region";
  renderWith((d) => <ClaimsProvider spec={d.spec}><RichText text="{claim:q1.rank.West}" /></ClaimsProvider>, data);
  await user.click(screen.getByRole("button", { name: "Evidence 1" }));
  const table = within(screen.getByRole("dialog")).getByRole("table", { name: "How it compares" });
  expect(within(table).getAllByRole("columnheader")[0].textContent).toBe("Region");
});

test("an open citation controls the drawer", async () => {
  const user = userEvent.setup();
  paragraph("{claim:q1.rank.West}");
  const cite = screen.getByRole("button", { name: "Evidence 1" });
  await user.click(cite);
  expect(cite.getAttribute("aria-controls")).toBe(screen.getByRole("dialog").id);
});

test("a claim with no rows or facts in the payload still opens", async () => {
  const user = userEvent.setup();
  const data = claimsPayload();
  const claim = data.spec.claims!["q1.rank.West"] as unknown as Record<string, unknown>;
  delete claim.facts;
  delete (claim.evidence as Record<string, unknown>).rows;
  renderWith((d) => <ClaimsProvider spec={d.spec}><RichText text="{claim:q1.rank.West}" /></ClaimsProvider>, data);
  await user.click(screen.getByRole("button", { name: "Evidence 1" }));
  const drawer = screen.getByRole("dialog");
  expect(within(drawer).getByText("What was counted")).toBeTruthy();
  expect(within(drawer).queryByText("How it compares")).toBeNull();
});

test("a claim that cites no values is not checked", async () => {
  const user = userEvent.setup();
  const data = claimsPayload();
  data.spec.claims!["q1.rank.West"].facts = [];
  renderWith((d) => <ClaimsProvider spec={d.spec}><RichText text="{claim:q1.rank.West}" /></ClaimsProvider>, data);
  await user.click(screen.getByRole("button", { name: "Evidence 1" }));
  const drawer = screen.getByRole("dialog");
  expect(within(drawer).queryByText("✓ Checked against the data")).toBeNull();
  expect(within(drawer).getByText("Not checked: a value is missing")).toBeTruthy();
});

test("the segment table title shows segment values in plain words", async () => {
  const user = userEvent.setup();
  const data = claimsPayload();
  data.spec.claims!["q2.change.West"].segment = { "dealers.region": "north_east" };
  renderWith((d) => <ClaimsProvider spec={d.spec}><RichText text="{claim:q2.change.West}" /></ClaimsProvider>, data);
  await user.click(screen.getByRole("button", { name: "Evidence 3" }));
  await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Show this as a table" }));
  expect(within(screen.getByRole("dialog")).getByText("The data for North east")).toBeTruthy();
});

test("a claim after a lead-in starts in lower case; a member name keeps its capital", () => {
  const { container } = paragraph("Over time, {claim:q2.trend.West.half}. Across regions, {claim:q1.extreme.South}.");
  const text = container.querySelector("p")!.textContent!;
  expect(text).toMatch(/^Over time, fraud loss for West /);
  expect(text).toContain("Across regions, South had the largest");
});

test("a claim that starts a sentence keeps its capital", () => {
  const { container } = paragraph("In short. {claim:q2.trend.West.half}.");
  expect(container.querySelector("p")!.textContent).toMatch(/^In short\. Fraud loss for West /);
});
