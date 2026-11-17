import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import type { Payload, Spec } from "../../lib/api";
import { claimsPayload } from "../claimsFixture";
import { renderWith } from "../claimsHarness";
import { Brief } from "./Brief";
import { Evidence } from "./Evidence";
import { Narrative } from "./Narrative";

/** The claims payload with its claims and new fields removed: a report from before claims. */
function oldPayload(): Payload {
  const data = claimsPayload();
  const { claims: _c, ...spec } = data.spec;
  const sections = spec.sections.map(({ narration: _n, stale_claims: _s, ...s }) => ({
    ...s,
    blocks: s.blocks.map((b) => {
      if (b.type === "prose") return { ...b, text: "West lost {fact:q1.fraud_loss.West}." };
      if (b.type === "action") return { type: "action" as const, text: b.text };
      return b;
    }),
  }));
  return { ...data, spec: { ...spec, sections } as Spec };
}

// Brief

test("the brief leads with the recommendation, its section, and why cards for its basis", () => {
  renderWith((d) => <Brief spec={d.spec} onEvidence={() => undefined} />);
  const rec = screen.getByRole("region", { name: "Recommendation" });
  expect(within(rec).getByText("Review fraud controls in the South first.")).toBeTruthy();
  expect(within(rec).getByText("From: Regions")).toBeTruthy();
  const why = within(rec).getAllByRole("article");
  expect(why).toHaveLength(1);
  expect(why[0].textContent).toContain("South had the largest fraud loss of the 4 regions ($15.84M, 32.44% of the total)");
  const rows = within(within(why[0]).getByRole("table")).getAllByRole("row").slice(1);
  expect(rows.map((r) => r.textContent)).toEqual(["South$15.84M32.44%", "East$13.30M27.24%", "North$10.40M21.30%", "West$9.29M19.03%"]);
  expect(rows[0].getAttribute("aria-current")).toBe("true");
});

test("an action with no basis is not shown as a recommendation", () => {
  const data = claimsPayload();
  data.spec.sections[0].blocks[3] = { type: "action", text: "Do something.", basis: [] };
  renderWith((d) => <Brief spec={d.spec} onEvidence={() => undefined} />, data);
  expect(screen.queryByRole("region", { name: "Recommendation" })).toBeNull();
  expect(screen.getAllByRole("article")).toHaveLength(3);
});

test("with no recommendation, the brief shows the top 3 claims as key points", () => {
  const data = claimsPayload();
  data.spec.sections[0].blocks.splice(3, 1);
  renderWith((d) => <Brief spec={d.spec} onEvidence={() => undefined} />, data);
  expect(screen.getByText("Key points")).toBeTruthy();
  const cards = screen.getAllByRole("article");
  expect(cards).toHaveLength(3);
  expect(cards[0].textContent).toContain("West had the lowest fraud loss");
  expect(cards[2].textContent).toContain("West fraud loss rose from $2.11M");
});

test("the why card's mini table keeps the top 4 rows and the highlighted row", () => {
  const data = claimsPayload();
  const claim = data.spec.claims!["q1.extreme.South"];
  claim.evidence = {
    ...claim.evidence,
    rows: ["A", "B", "C", "D", "E", "West"].map((label, i) => ({
      label, fact: ["q1.fraud_loss.South", "q1.fraud_loss.East", "q1.fraud_loss.North", "q1.fraud_loss.West", "q1.fraud_loss.East", "q1.fraud_loss.West"][i],
      highlight: label === "West",
    })),
  };
  renderWith((d) => <Brief spec={d.spec} onEvidence={() => undefined} />, data);
  const rows = within(screen.getByRole("article")).getAllByRole("row").slice(1);
  expect(rows.map((r) => r.firstElementChild!.textContent)).toEqual(["A", "B", "C", "D", "West"]);
});

test("a citation in the brief opens the evidence drawer", async () => {
  const user = userEvent.setup();
  renderWith((d) => <Brief spec={d.spec} onEvidence={() => undefined} />);
  await user.click(screen.getByRole("button", { name: "Evidence 2" }));
  expect(screen.getByRole("dialog", { name: "Evidence 2" })).toBeTruthy();
});

test("an old brief with no claims renders as before", () => {
  const data = oldPayload();
  const { container } = renderWith((d) => <Brief spec={d.spec} onEvidence={() => undefined} />, data);
  expect(screen.getByText("Decision needed")).toBeTruthy();
  expect(container.querySelector("p.prose b")!.textContent).toBe("Review fraud controls in the South first.");
  expect(screen.queryByRole("region", { name: "Recommendation" })).toBeNull();
  expect(screen.queryByRole("article")).toBeNull();
});

// Evidence

test("Claims and proof lists each claim in reading order with its numbers", () => {
  renderWith((d) => <Evidence spec={d.spec} />);
  expect(screen.getByRole("heading", { name: "Claims and proof" })).toBeTruthy();
  const cards = screen.getAllByRole("article");
  expect(cards).toHaveLength(4);
  expect(cards[0].textContent).toContain("✓ Checked against the data");
  expect(cards[0].textContent).toContain("West had the lowest fraud loss of the 4 regions");
  const cited = within(within(cards[0]).getByRole("list", { name: "Numbers cited" })).getAllByRole("listitem");
  expect(cited.map((c) => c.textContent)).toEqual(["Fraud loss · West: $9.29M", "Share of fraud loss · West: 19.03%"]);
  expect(cards[3].textContent).toContain("West fraud loss rose in 2 of 3 half-years");
});

test("Open the evidence opens the drawer for that claim", async () => {
  const user = userEvent.setup();
  renderWith((d) => <Evidence spec={d.spec} />);
  const card = screen.getAllByRole("article")[2];
  const button = within(card).getByRole("button", { name: "Open the evidence" });
  await user.click(button);
  const drawer = screen.getByRole("dialog", { name: "Evidence 3" });
  expect(within(drawer).getByText("How it changed")).toBeTruthy();
  await user.keyboard("{Escape}");
  expect(document.activeElement).toBe(button);
});

test("For auditors is collapsed and holds the lineage in plain words", async () => {
  const user = userEvent.setup();
  renderWith((d) => <Evidence spec={d.spec} />);
  const details = screen.getByText("For auditors").closest("details")!;
  expect(details.open).toBe(false);
  await user.click(screen.getByText("For auditors"));
  expect(details.open).toBe(true);
  const row = within(details).getByRole("row", { name: /Fraud loss · West · 2024 H1/ });
  expect(row.textContent).toContain("Definition: Fraud loss v1 · Data: duckdb_demo, refreshed 30 Nov 2025 · Query 9b1e0f33 · Traced");
  expect(row.textContent).toContain("Sum of 6 monthly values");
  const plain = within(details).getByRole("row", { name: /^Fraud loss · West \$9\.29M/ });
  expect(plain.textContent).toContain("Query 502a7904");
});

test("a stale claim in Claims and proof says it no longer holds", () => {
  const data = claimsPayload();
  data.spec.sections[1] = { ...data.spec.sections[1], stale_claims: ["q2.trend.West.half"] };
  renderWith((d) => <Evidence spec={d.spec} />, data);
  const cards = screen.getAllByRole("article");
  expect(cards[3].textContent).toContain("No longer true with the new data");
  expect(cards[3].textContent).not.toContain("✓ Checked against the data");
});

test("an old report's evidence view is the fact table as before", () => {
  const data = oldPayload();
  renderWith((d) => <Evidence spec={d.spec} />, data);
  expect(screen.getByRole("heading", { name: "Where each number comes from" })).toBeTruthy();
  expect(screen.queryByText("For auditors")).toBeNull();
  const row = screen.getByRole("row", { name: /Fraud loss · West \$9\.29M/ });
  expect(within(row).getByText("fraud_loss v1")).toBeTruthy();
});

test("the evidence view without a spec is the fact table", () => {
  renderWith(() => <Evidence />);
  expect(screen.getByRole("heading", { name: "Where each number comes from" })).toBeTruthy();
});

// Narrative

test("the narrative renders claims with citations in reading order", () => {
  const { container } = renderWith((d) => <Narrative spec={d.spec} panelFor={() => null} />);
  const paragraphs = [...container.querySelectorAll("p.prose")].map((p) => p.textContent);
  expect(paragraphs[0]).toBe(
    "West had the lowest fraud loss of the 4 regions ($9.29M, 19.03% of the total)1. By contrast, South had the largest fraud loss of the 4 regions ($15.84M, 32.44% of the total)2.",
  );
  expect(paragraphs).toContain("West fraud loss rose from $2.11M in 2024 H1 to $2.63M in 2025 Jul–Nov3.");
});

test("a section written by code says so", () => {
  renderWith((d) => <Narrative spec={d.spec} panelFor={() => null} />);
  const notes = screen.getAllByText("Written by code: the model's draft did not pass the checks.");
  expect(notes).toHaveLength(1);
  expect(notes[0].closest("section")!.textContent).toContain("West over time");
});

test("a recommendation is labeled and cites its basis", async () => {
  const user = userEvent.setup();
  renderWith((d) => <Narrative spec={d.spec} panelFor={() => null} />);
  const rec = screen.getByRole("note", { name: "Recommendation" });
  expect(rec.textContent).toContain("Review fraud controls in the South first.");
  await user.click(within(rec).getByRole("button", { name: "Evidence 2" }));
  expect(screen.getByRole("dialog", { name: "Evidence 2" })).toBeTruthy();
});

test("stale claims are marked and the section offers a rewrite", async () => {
  const user = userEvent.setup();
  const onRewrite = vi.fn();
  const data = claimsPayload();
  data.spec.sections[1] = { ...data.spec.sections[1], stale_claims: ["q2.change.West"] };
  renderWith((d) => <Narrative spec={d.spec} panelFor={() => null} onRewrite={onRewrite} />, data);
  expect(screen.getAllByText("No longer true with the new data")).toHaveLength(1);
  await user.click(screen.getByRole("button", { name: "Rewrite this section" }));
  expect(onRewrite).toHaveBeenCalledWith("s2");
});

test("with no rewrite handler, stale claims are marked with no button", () => {
  const data = claimsPayload();
  data.spec.sections[1] = { ...data.spec.sections[1], stale_claims: ["q2.change.West"] };
  renderWith((d) => <Narrative spec={d.spec} panelFor={() => null} />, data);
  expect(screen.getByText("No longer true with the new data")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Rewrite this section" })).toBeNull();
});

test("an old narrative renders as before: no notes, no recommendation, no citations", () => {
  const data = oldPayload();
  const { container } = renderWith((d) => <Narrative spec={d.spec} panelFor={() => null} />, data);
  expect(container.querySelector("p.prose")!.textContent).toBe("West lost $9.29M.");
  expect(screen.queryByText(/Written by code/)).toBeNull();
  expect(screen.queryByRole("note", { name: "Recommendation" })).toBeNull();
  expect(screen.queryByRole("button", { name: /Evidence \d/ })).toBeNull();
});

// Review fixes

test("a Why card shows that its claim no longer holds, and the recommendation is flagged", () => {
  const data = claimsPayload();
  data.spec.sections[0] = { ...data.spec.sections[0], stale_claims: ["q1.extreme.South"] };
  renderWith((d) => <Brief spec={d.spec} onEvidence={() => undefined} />, data);
  const rec = screen.getByRole("region", { name: "Recommendation" });
  expect(within(rec).getByText("One of its reasons is no longer true with the new data")).toBeTruthy();
  const card = within(rec).getByRole("article");
  expect(within(card).getAllByText("No longer true with the new data").length).toBeGreaterThan(0);
  expect(card.textContent).not.toContain("✓ Checked against the data");
});

test("a recommendation whose reason has a missing value is flagged", () => {
  const data = claimsPayload();
  delete data.facts["q1.fraud_loss.share.South"];
  renderWith((d) => <Brief spec={d.spec} onEvidence={() => undefined} />, data);
  const rec = screen.getByRole("region", { name: "Recommendation" });
  expect(within(rec).getByText("One of its reasons could not be checked")).toBeTruthy();
  expect(within(within(rec).getByRole("article")).getByText("Not checked: a value is missing")).toBeTruthy();
});

test("a checked Why card shows its verdict and no flag", () => {
  renderWith((d) => <Brief spec={d.spec} onEvidence={() => undefined} />);
  const rec = screen.getByRole("region", { name: "Recommendation" });
  expect(within(within(rec).getByRole("article")).getByText("✓ Checked against the data")).toBeTruthy();
  expect(within(rec).queryByText(/One of its reasons/)).toBeNull();
});

test("an action with no basis in a report with claims still shows, unlabeled", () => {
  const data = claimsPayload();
  data.spec.sections[1].blocks.push({ type: "action", text: "Call the West dealers." });
  const { container } = renderWith((d) => <Brief spec={d.spec} onEvidence={() => undefined} />, data);
  expect(screen.getByText("Decision needed")).toBeTruthy();
  expect(container.querySelector("p.prose b")!.textContent).toBe("Call the West dealers.");
  expect(screen.getAllByRole("region", { name: "Recommendation" })).toHaveLength(1);
});

test("with only actions that have no basis, the brief says Decision needed", () => {
  const data = claimsPayload();
  data.spec.sections[0].blocks[3] = { type: "action", text: "Do something.", basis: [] };
  renderWith((d) => <Brief spec={d.spec} onEvidence={() => undefined} />, data);
  expect(screen.getByText("Decision needed")).toBeTruthy();
  expect(screen.getByText("Do something.")).toBeTruthy();
});

test("Claims and proof says Not checked when a cited value is missing", () => {
  const data = claimsPayload();
  delete data.facts["q1.fraud_loss.West"];
  renderWith((d) => <Evidence spec={d.spec} />, data);
  const card = screen.getAllByRole("article")[0];
  expect(card.textContent).toContain("Not checked: a value is missing");
  expect(card.textContent).not.toContain("✓ Checked against the data");
});

test("duplicate basis ids render one citation", () => {
  const data = claimsPayload();
  data.spec.sections[0].blocks[3] = { type: "action", text: "Review the South.", basis: ["q1.extreme.South", "q1.extreme.South"] };
  renderWith((d) => <Narrative spec={d.spec} panelFor={() => null} />, data);
  const rec = screen.getByRole("note", { name: "Recommendation" });
  expect(within(rec).getAllByRole("button", { name: "Evidence 2" })).toHaveLength(1);
});
