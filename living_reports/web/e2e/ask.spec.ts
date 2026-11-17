import { expect, test } from "@playwright/test";

const longLabel = "A very long merchant group name that goes on and on past any normal width of a label";
const fact = (id: string, label: string, formatted: string) => ({ id, label, value: 1, formatted, query_hash: "abcd1234ef", metric: "fraud_loss", metric_version: 1, source_id: "duckdb_demo", data_time: "2025-12-31T10:00:00", state: "ok" });
const facts: Record<string, unknown> = {};
const points = Array.from({ length: 12 }, (_, i) => {
  const id = `q1.fraud_loss.${i}`;
  facts[id] = fact(id, `${longLabel} ${i}`, `$${i + 1}.0M`);
  return { label: `${longLabel} ${i}`, value: 12 - i, formatted: `$${12 - i}.0M`, fact_id: id };
});
facts["q1_total.fraud_loss"] = fact("q1_total.fraud_loss", "Fraud loss", "$1,234,567.8M");
const payload = {
  id: "mock", edition: null, state: "draft", tier: "team", view: "narrative", facts,
  queries: { q1: { columns: ["group", "loss"], rows: points.map((p) => [p.label, p.value]), query_hash: "abcd1234ef", points } },
  spec: { title: "A long report title that needs two lines to show all of its words on one laptop screen", kicker: "Fraud loss, all dates", objective: "why", assumptions: [{ id: "a1", text: "All dates in the data." }],
    sections: [{ id: "s1", heading: "Groups", intent: "Where?", decisions: [], blocks: [
      { type: "prose", text: "Fraud loss reached {fact:q1_total.fraud_loss}." },
      { type: "tile", fact_id: "q1_total.fraud_loss", label: longLabel },
      { type: "bars", query_ref: "q1", title: "Loss by group" },
      { type: "table", query_ref: "q1", title: "Rows" },
      { type: "action", text: "Review the largest group." },
    ] }] },
};

for (const width of [1280, 768]) {
  test.describe(`long data at ${width}px`, () => {
    test.use({ viewport: { width, height: 900 } });
    for (const view of ["narrative", "dashboard", "brief", "evidence"]) {
      test(`${view} has no overflow`, async ({ page }) => {
        await page.route("**/api/reports/mock", (r) => r.fulfill({ json: payload }));
        await page.route("**/api/reports/mock/threads", (r) => r.fulfill({ json: [] }));
        await page.goto(`/r/mock?view=${view}`);
        await expect(page.getByRole("tab", { name: "Evidence" })).toBeVisible();
        expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
        await page.screenshot({ path: `e2e/shots/long-${view}-${width}.png`, fullPage: true });
      });
    }
  });
}

test("home page has an ask box", async ({ page }) => {
  await page.route("**/api/reports", (r) => r.fulfill({ json: [] }));
  await page.goto("/");
  await expect(page.getByLabel("Ask a question")).toBeVisible();
  await page.screenshot({ path: "e2e/shots/home.png" });
});
