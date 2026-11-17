import { expect, test } from "@playwright/test";

const changes = [
  { id: "1", kind: "definition", title: "Fraud loss: exclude friendly fraud", detail: "This changes the meaning of the metric, so it needs an owner's approval.", affected_reports: ["r1", "r2"], status: "open", proposal_id: "p1" },
  { id: "2", kind: "new_value", title: "New sales channel: Marketplace", detail: "First seen on 2025-12-25.", affected_reports: ["r1"], status: "open", proposal_id: null },
  { id: "3", kind: "new_column", title: "New column: contract_length_months", detail: "Approve it to make a new breakdown available.", affected_reports: [], status: "open", proposal_id: "p2" },
  { id: "4", kind: "new_data", title: "146,144 new orders arrived", detail: "Reports refresh on the new data, and each new edition waits for checks.", affected_reports: ["r1"], status: "open", proposal_id: null },
];
const diff = {
  changed_facts: [{ id: "a", label: "Fraud loss", before: "$52.6M", after: "$43.1M" }, { id: "b", label: "Fraud rate · Telesales", before: "3.51%", after: "3.52%" }],
  added_facts: [{ id: "c", label: "Fraud rate · Marketplace", before: "", after: "2.79%" }], removed_facts: [], added_sections: [], removed_sections: [],
  definition_changes: [{ metric: "fraud_loss", from_version: 1, to_version: 2, definition: "Total loss on orders confirmed as fraud, excluding friendly fraud." }],
};

for (const width of [1280, 768]) {
  test.describe(`change screens at ${width}px`, () => {
    test.use({ viewport: { width, height: 900 } });
    test("home shows the briefing", async ({ page }) => {
      await page.route("**/api/changes", (r) => r.fulfill({ json: changes }));
      await page.route("**/api/reports", (r) => r.fulfill({ json: [{ id: "r1", title: "Identity theft is the largest source of fraud loss", tier: "team", author: "ana" }] }));
      await page.route("**/api/sources", (r) => r.fulfill({ json: { sources: [], demo_moment: 2 } }));
      await page.goto("/");
      await expect(page.getByText("Fraud loss: exclude friendly fraud")).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
      await page.screenshot({ path: `e2e/shots/home-${width}.png`, fullPage: true });
    });
    test("the difference view shows the change", async ({ page }) => {
      await page.route("**/api/reports/r1/diff*", (r) => r.fulfill({ json: diff }));
      await page.goto("/r/r1/diff?from=1&to=2");
      await expect(page.getByText("$43.1M")).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
      await page.screenshot({ path: `e2e/shots/diff-${width}.png`, fullPage: true });
    });
  });
}
