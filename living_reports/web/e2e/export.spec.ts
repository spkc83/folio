import { expect, test } from "@playwright/test";
import { execSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { CUBE } from "../src/report/cubeFixture";

const fact = { id: "a", label: "Fraud loss", value: 1, formatted: "$52.6M", query_hash: "abc", metric: "fraud_loss", metric_version: 1, source_id: "duckdb_demo", data_time: "2025-12-31T10:00:00", state: "ok" };
const payload = {
  id: "r1", edition: 3, state: "published", tier: "team", view: "narrative", hosted_url: "http://example.test/r/r1/e/3",
  facts: { a: fact }, queries: {},
  spec: { title: "A frozen report </script>", kicker: "Fraud loss", objective: "why", assumptions: [], sections: [{ id: "s1", heading: "h", intent: "i", decisions: [],
    blocks: [{ type: "prose", text: "Loss reached {fact:a}." }, { type: "tile", fact_id: "a", label: "Fraud loss" }, { type: "action", text: "Review it." }] }] },
};

const rate = { ...fact, id: "q1_total.fraud_rate", label: "Fraud rate", value: 0.0264, formatted: "2.64%", metric: "fraud_rate",
  binding: { ref: "q1", metric: "fraud_rate", keys: {}, share: false } };
const byChannel = {
  columns: ["orders.sales_channel", "fraud_rate"], query_hash: "abc",
  rows: [["Telesales", 0.0352], ["Web", 0.033], ["App", 0.02], ["Marketplace", 0.02]],
  points: [["Telesales", 0.0352, "3.52%"], ["Web", 0.033, "3.30%"], ["App", 0.02, "2.00%"], ["Marketplace", 0.02, "2.00%"]]
    .map(([label, value, formatted], i) => ({ label, value, formatted, fact_id: `q1.fraud_rate.${i}` })),
};
// The cube gains a region, so the export can drill by a dimension that the chart does not show.
const regional = {
  ...CUBE, dimensions: ["orders.sales_channel", "dealers.region", "month"], labels: { ...CUBE.labels, "dealers.region": "Region" },
  columns: ["orders.sales_channel", "dealers.region", ...CUBE.columns.slice(1)],
  rows: CUBE.rows.map(([channel, ...rest]) => [channel, "East", ...rest]),
};
const explorable = {
  ...payload, view: "dashboard", facts: { [rate.id]: rate }, queries: { q1: byChannel },
  cube: regional, query_defs: { q1: { metrics: ["fraud_rate"], dimensions: ["orders.sales_channel"] } },
  spec: { ...payload.spec, sections: [{ id: "s1", heading: "h", intent: "i", decisions: [],
    blocks: [{ type: "tile", fact_id: rate.id, label: "Fraud rate" }, { type: "bars", query_ref: "q1", title: "Fraud rate by sales channel" }] }] },
};

let template = "";
function exported(data: unknown): string {
  if (!template) {
    execSync("npx vite build --config vite.export.config.ts", { stdio: "pipe" });
    template = readFileSync("dist-export/export.html", "utf8");
  }
  const block = `<script type="application/json" id="lr-payload">${JSON.stringify(data).replaceAll("<", "\\u003c")}</script>`;
  const path = join(tmpdir(), `lr-export-${Date.now()}-${Math.random().toString(36).slice(2)}.html`);
  writeFileSync(path, template.replace("<!--LR_PAYLOAD-->", block));
  return path;
}

test("an exported file works with no server", async ({ page }) => {
  const path = exported(payload);

  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.route("**/*", (route) => (route.request().url().startsWith("file:") ? route.continue() : route.abort()));
  await page.goto(`file://${path}`);
  await expect(page.getByText("A frozen report </script>")).toBeVisible();
  for (const name of ["Dashboard", "Decision brief", "Evidence", "Narrative"]) {
    await page.getByRole("tab", { name }).click();
    await expect(page.getByText("$52.6M").first()).toBeVisible();
  }
  await expect(page.getByRole("link", { name: /Ask a question/ })).toHaveAttribute("href", payload.hosted_url);
  expect(errors).toEqual([]);
  expect(template.length).toBeLessThan(921_600);
});

test("an exported file filters and cross-filters over the cube it carries", async ({ page }) => {
  const path = exported(explorable);
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.route("**/*", (route) => (route.request().url().startsWith("file:") ? route.continue() : route.abort()));
  await page.goto(`file://${path}`);
  const tile = page.locator(".tile");
  await expect(tile).toContainText("2.64%");
  await page.getByRole("group", { name: "Report filters" }).getByLabel("Sales channel").selectOption("Telesales");
  await expect(tile).toContainText("3.52%");
  await expect(page.getByText("Exploring", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Pin as section" })).toHaveCount(0);
  await page.getByRole("button", { name: "Reset" }).click();
  await page.getByRole("button", { name: /^Web,/ }).click();
  await expect(page.locator(".exploring")).toContainText("Sales channel is Web");
  await page.getByLabel("Break down by…").selectOption("dealers.region");
  const panel = page.getByRole("region", { name: "Web · by Region" });
  await expect(panel).toBeVisible();
  await expect(panel.getByRole("button", { name: "East, 3.30%" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Pin" })).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("an exported file from an edition with no cube shows no filters", async ({ page }) => {
  const path = exported({ ...explorable, cube: null });
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.route("**/*", (route) => (route.request().url().startsWith("file:") ? route.continue() : route.abort()));
  await page.goto(`file://${path}`);
  await expect(page.getByText("Fraud rate by sales channel")).toBeVisible();
  await expect(page.getByRole("group", { name: "Report filters" })).toHaveCount(0);
  expect(errors).toEqual([]);
});
