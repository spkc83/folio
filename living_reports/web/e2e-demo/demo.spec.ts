import { expect, test, type Page } from "@playwright/test";

async function asUser(page: Page, user: string) {
  await page.getByLabel("Demo user").selectOption(user);
}

test("the eight demo steps", async ({ page, context }) => {
  await page.setViewportSize({ width: 1280, height: 950 });

  // Step 1: a question opens a Narrative report in a new tab.
  await page.goto("/");
  const firstTab = context.waitForEvent("page");
  await page.getByLabel("Ask a question").fill("Which categories drive the most revenue?");
  await page.keyboard.press("Enter");
  const report = await firstTab;
  // A page error or a server error is the first thing to read when a later step fails.
  report.on("pageerror", (e) => console.log("PAGEERROR", e.message));
  report.on("response", (r) => { if (r.status() >= 500) console.log("SERVER", r.status(), r.request().method(), r.url()); });
  await expect(report.getByRole("heading", { name: /Revenue by category/ })).toBeVisible();
  await expect(report.getByRole("tab", { name: "Narrative" })).toHaveAttribute("aria-selected", "true");

  const firstTotal = (await report.locator(".fact-btn").first().textContent())!;

  // The routed question shows as chips, follow-ups come from the vocabulary, and a chart swap
  // needs no model call. Follow-up chips are not clicked here: each one is a narration call.
  const chips = report.getByRole("group", { name: "The routed question" }).first();
  await expect(chips.getByRole("button", { name: "Metric Revenue" })).toBeVisible();
  await expect(report.getByRole("group", { name: "Follow up" }).first()).toContainText("Follow up");
  await report.getByRole("button", { name: "Section commands" }).first().click();
  await report.getByRole("menuitem", { name: "Swap chart" }).click();
  await report.getByRole("menuitem", { name: "Table" }).click();
  await expect(report.locator(".narrow > section").first().locator("table.data")).toBeVisible();
  await report.screenshot({ path: "e2e-demo/shots/step1-asking.png", fullPage: true });
  for (const scheme of ["light", "dark"] as const) {
    await report.emulateMedia({ colorScheme: scheme });
    await chips.getByRole("button", { name: "By Category" }).click();
    await expect(report.getByRole("dialog", { name: "Edit By" })).toBeVisible();
    await report.screenshot({ path: `e2e-demo/shots/step1-chip-${scheme}.png` });
    await report.keyboard.press("Escape");
    await report.getByRole("button", { name: "Section commands" }).first().click();
    await expect(report.getByRole("menu")).toBeVisible();
    await report.screenshot({ path: `e2e-demo/shots/step1-menu-${scheme}.png` });
    await report.keyboard.press("Escape");
    await expect(report.getByRole("menu")).toHaveCount(0);
  }
  await report.emulateMedia({ colorScheme: "light" });

  // Step 2: a follow-up on a number stays on that number.
  await report.locator(".fact-btn").first().click();
  await report.getByRole("button", { name: "Ask about this number" }).click();
  // The ask bar names the selected number.
  await expect(report.getByLabel("Ask about this report")).toHaveAttribute("placeholder", /^Ask about Revenue/);
  const thread = report.getByLabel("Ask about this number");
  await thread.fill("Why this value?");
  await thread.press("Enter");
  await expect(report.getByText(/The selected value is/)).toBeVisible();
  await report.keyboard.press("Escape");
  await expect(report.getByLabel("Ask about this number")).toHaveCount(0);

  // Step 3: another question opens the Dashboard view.
  const secondTab = context.waitForEvent("page");
  await page.getByLabel("Ask a question").fill("How are we doing by channel?");
  await page.keyboard.press("Enter");
  const dashboard = await secondTab;
  await expect(dashboard.getByRole("tab", { name: "Dashboard" })).toHaveAttribute("aria-selected", "true");
  const online = dashboard.getByRole("button", { name: /^Online,/ }).first();
  await expect(online).toBeVisible();
  // A click on a bar filters the whole report, with no model call. Reset returns to the report.
  await online.click();
  await expect(dashboard.locator(".exploring")).toContainText("Channel is Online");
  await dashboard.screenshot({ path: "e2e-demo/shots/step3-filter.png", fullPage: true });
  await dashboard.getByRole("button", { name: "Reset" }).click();
  await expect(dashboard.locator(".exploring")).toHaveCount(0);
  await dashboard.close();

  // Step 4: publish edition 1 as a team report.
  const responses: string[] = [];
  report.on("response", (r) => { if (r.request().method() === "POST") responses.push(`${r.status()} ${r.url()}`); });
  await report.getByRole("button", { name: "Run checks" }).click();
  await expect(report.getByText("Numbers traced")).toBeVisible().catch(async (e) => {
    console.log("POSTS", responses, (await report.locator(".rail").innerText()));
    throw e;
  });
  await asUser(report, "omar");
  await report.getByRole("button", { name: "Sign" }).click();
  await expect(report.getByText("Signed by omar")).toBeVisible();
  await report.getByRole("button", { name: "Publish" }).click();
  await expect(report.getByRole("link", { name: "Edition 1 is published" })).toBeVisible();
  await report.screenshot({ path: "e2e-demo/shots/step4.png", fullPage: true });
  await expect(report.locator('.badge[data-kind="verified"]')).toBeVisible();

  // Publishing certified the question: Home shows it as a chip, and the chip opens the same report.
  await page.reload();
  const chip = page.locator("button.chip.certified", { hasText: "Which categories drive the most revenue?" });
  await expect(chip).toContainText("Verified");
  await expect(chip).toContainText("by omar on");
  const certifiedTab = context.waitForEvent("page");
  await chip.click();
  const reopened = await certifiedTab;
  // The chip opens the published edition 1 of the same report, with its Verified badge.
  await expect.poll(() => new URL(reopened.url()).pathname).toBe(`${new URL(report.url()).pathname}/e/1`);
  await expect(reopened.locator('.badge[data-kind="verified"]')).toBeVisible();
  await reopened.close();

  // Step 5: new data arrives, and the home page shows it.
  await page.reload();
  await page.getByText("Demo controls").click();
  await page.getByRole("button", { name: "Load the next data snapshot" }).click();
  await expect(page.getByRole("heading", { name: "New channel: Social commerce" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "New column: promotion_code" })).toBeVisible();
  const social = page.locator("article", { hasText: "New channel: Social commerce" });
  await expect(social.getByText("New", { exact: true })).toBeVisible();
  const week = page.getByRole("region", { name: "This week" });
  await expect(week.getByText(/Certified answer changed/)).toBeVisible();
  await page.getByRole("searchbox", { name: "Find a report" }).fill("revenue");
  await expect(page.locator(".search .results a").first()).toBeVisible();
  await page.screenshot({ path: "e2e-demo/shots/step5-home.png", fullPage: true });
  await page.emulateMedia({ colorScheme: "dark" });
  await page.screenshot({ path: "e2e-demo/shots/step5-home-dark.png", fullPage: true });
  await page.emulateMedia({ colorScheme: "light" });
  await page.screenshot({ path: "e2e-demo/shots/step5.png", fullPage: true });

  // Step 6: approve the changed definition, and see the differences.
  const card = page.locator("article", { hasText: "Revenue: exclude returned sales" });
  await card.getByRole("button", { name: "Review" }).click();
  await expect(card.getByText("$46.0M")).toBeVisible();
  await expect(card.getByText("$43.0M")).toBeVisible();
  await card.getByRole("button", { name: "Approve" }).click();
  await card.getByRole("button", { name: "Confirm approval" }).click();
  // A resolved item stays in the feed for seven days, without the review action.
  await expect(card.getByText("Resolved")).toBeVisible();
  await expect(card.getByRole("button", { name: "Review" })).toHaveCount(0);

  await report.reload();
  await report.getByRole("link", { name: "See what changed" }).click();
  await expect(report.getByText(/revenue changed from version 1 to version 2/)).toBeVisible();
  await report.screenshot({ path: "e2e-demo/shots/step6.png", fullPage: true });
  await report.goBack();

  // Edition 1 stays frozen with its old value.
  const frozen = await context.newPage();
  const id = new URL(report.url()).pathname.split("/")[2];
  await frozen.goto(`/r/${id}/e/1`);
  await expect(frozen.getByText(firstTotal).first()).toBeVisible();
  await expect(frozen.getByRole("link", { name: "A newer edition exists" })).toBeVisible();
  await frozen.close();

  // Step 7: the report becomes business critical, with two signatures.
  await report.getByLabel("Tier").selectOption("business_critical");
  await report.getByRole("button", { name: "Run checks" }).click({ timeout: 60_000 }).catch(async (e) => {
    console.log("POSTS", responses.slice(-4), "RAIL", await report.locator(".rail").innerText());
    throw e;
  });
  await expect(report.getByText("Compliance", { exact: true })).toBeVisible().catch(async (e) => {
    console.log("POSTS", responses.slice(-4), "RAIL", await report.locator(".rail").innerText());
    throw e;
  });
  await asUser(report, "omar");
  await report.getByRole("button", { name: "Sign" }).click();
  await expect(report.getByText("Signed by omar")).toBeVisible();
  await asUser(report, "chidi");
  await report.getByLabel("Review note").fill("The wording matches the values.");
  await report.getByLabel("Review note").press("Enter");
  await expect(report.getByLabel("Review note")).toHaveCount(0);
  await report.getByRole("button", { name: "Sign" }).click();
  await expect(report.getByText("Signed by chidi")).toBeVisible();
  await asUser(report, "omar");
  await report.getByRole("button", { name: "Publish" }).click();
  await expect(report.getByRole("link", { name: /is published/ })).toBeVisible();

  // Step 8: trace one number to its query.
  await report.getByRole("tab", { name: "Evidence" }).click();
  // Each claim shows its proof; the lineage of each number is under "For auditors".
  await report.getByText("For auditors").click();
  const row = report.getByRole("row", { name: /^Revenue \$/ }).first();
  await expect(row).toContainText("Revenue v2");
  await expect(row).toContainText("$43.0M");
  await expect(row).toContainText("Traced");
  await report.screenshot({ path: "e2e-demo/shots/step8.png", fullPage: true });
});
