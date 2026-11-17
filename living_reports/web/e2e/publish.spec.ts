import { expect, test } from "@playwright/test";

const fact = { id: "q1_total.fraud_loss", label: "Fraud loss", value: 1, formatted: "$52.6M", query_hash: "abcd1234ef", metric: "fraud_loss", metric_version: 1, source_id: "duckdb_demo", data_time: "2025-12-31T10:00:00", state: "ok" };
const payload = { id: "m", edition: null, state: "draft", tier: "business_critical", view: "narrative", cost: { usd: 0.0042 }, facts: { [fact.id]: fact }, queries: {},
  spec: { title: "Identity theft is the largest source of fraud loss", kicker: "Fraud loss, all dates", objective: "why", assumptions: [{ id: "a1", text: "All dates in the data." }],
    sections: [{ id: "s1", heading: "h", intent: "i", decisions: [], blocks: [{ type: "prose", text: "Fraud loss reached {fact:q1_total.fraud_loss}." }] }] } };
const edition = { number: 2, content_hash: "h", tier: "business_critical", status: "pending", author: "ana", newest: 2, can_publish: false, needs: ["compliance"],
  signatures: [{ role: "owner", signer: "omar", content_hash: "h", signed_at: "2026-11-01T10:00:00" }],
  checks: [{ step: "trace", status: "pass", findings: [] }, { step: "totals", status: "pass", findings: [] }, { step: "rules", status: "pass", findings: [] },
    { step: "wording", status: "open", findings: [{ where: "x", detail: "This sentence states a cause that the values cannot prove." }] }] };

for (const width of [1280, 1000, 768]) {
  test.describe(`rail at ${width}px`, () => {
    test.use({ viewport: { width, height: 900 } });
    test("shows the sign-off state with no overflow", async ({ page }) => {
      await page.route("**/api/reports/m", (r) => r.fulfill({ json: payload }));
      await page.route("**/api/reports/m/threads", (r) => r.fulfill({ json: [] }));
      await page.route("**/api/reports/m/publication", (r) => r.fulfill({ json: { edition, draft_changed: false } }));
      await page.goto("/r/m");
      await expect(page.getByText("Signed by omar")).toBeVisible();
      await expect(page.getByRole("button", { name: "Publish" })).toBeDisabled();
      expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
      await page.screenshot({ path: `e2e/shots/rail-${width}.png`, fullPage: true });
    });
  });
}
