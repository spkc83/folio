import { expect, test } from "@playwright/test";

for (const width of [1280, 768]) {
  test.describe(`fixture report at ${width}px`, () => {
    test.use({ viewport: { width, height: 1000 } });

    for (const view of ["narrative", "dashboard"]) {
      test(`${view} view shows real values with no overflow`, async ({ page }) => {
        await page.goto(`/r/fraud-overview?view=${view}`);
        await expect(page.locator(".fact").first()).toBeVisible();
        await expect(page.getByText("$52.6M").first()).toBeVisible();
        const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth);
        expect(overflow).toBe(false);
        await expect(page.locator("body")).not.toContainText(/NaN|undefined/);
        await page.screenshot({ path: `e2e/shots/${view}-${width}.png`, fullPage: true });
      });
    }
  });
}
