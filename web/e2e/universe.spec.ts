import { test, expect } from "@playwright/test";

/**
 * Local-universe smoke: create a basket, add tickers via the paste box, and
 * confirm the membership chips render. Signal-fetching (run/backtest) needs
 * live Supabase data and is covered by unit tests with a mocked API layer.
 */
test.describe("Local universes", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/universe/");
    // Fresh local DB per test run.
    await page.evaluate(async () => {
      indexedDB.deleteDatabase("signals_app");
    });
    await page.reload();
  });

  test("index page mounts with a create field", async ({ page }) => {
    await expect(
      page.getByPlaceholder("New universe name…"),
    ).toBeVisible({ timeout: 5000 });
  });

  test("create a universe and add tickers from the paste box", async ({
    page,
  }) => {
    await page.getByPlaceholder("New universe name…").fill("E2E Basket");
    await page.getByRole("button", { name: "Create" }).click();

    await page.getByText("E2E Basket").click();
    await expect(page.getByText(/rev 1/)).toBeVisible();

    await page
      .getByPlaceholder(/Paste tickers/)
      .fill("AAPL, MSFT, $goog\nNVDA 100 shares");
    await page.getByRole("button", { name: "Add pasted" }).click();

    await expect(page.getByText(/added 4/)).toBeVisible();
    for (const t of ["AAPL", "MSFT", "GOOG", "NVDA"]) {
      await expect(
        page.locator("span", { hasText: new RegExp(`^${t}`) }).first(),
      ).toBeVisible();
    }
    await expect(page.getByText("Tickers (4)")).toBeVisible();
  });

  test("no console errors on the universe index", async ({ page }) => {
    const errors: string[] = [];
    page.on("console", (msg) => {
      if (msg.type() === "error") errors.push(msg.text());
    });
    await page.goto("/universe/");
    await page.waitForTimeout(500);
    expect(errors).toHaveLength(0);
  });
});
