import { test, expect } from '@playwright/test';

test.describe('Dashboard functionality', () => {
  test.beforeEach(async ({ page }) => {
    // baseURL carries the /signals-app basePath, but a leading-slash goto drops it.
    await page.goto('/signals-app/');
  });

  test('Recent Runs section mounts', async ({ page }) => {
    // Look for a section that contains "Recent" or "Runs"
    const recentRunsSection = page.locator(
      'text=/recent|runs/i',
    ).first();

    await expect(recentRunsSection).toBeVisible({ timeout: 5000 });
  });

  test('Watchlist section mounts', async ({ page }) => {
    // Look for watchlist-related content
    const watchlistSection = page.locator(
      'text=/watchlist|portfolio/i',
    ).first();

    await expect(watchlistSection).toBeVisible({ timeout: 5000 });
  });

  test('every landing showcase section mounts (empty-safe without Supabase)', async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', (e) => errors.push(e.message));
    // beforeEach already loaded the page; reload so hydration errors are captured
    await page.reload();

    for (const id of [
      'landing-hero',
      'landing-proof-line',
      'landing-example-chips',
      'landing-top-signals',
      'landing-heatmap',
      'landing-pipeline',
      'landing-track-record',
      'landing-featured',
      'landing-universe-cta',
      'landing-activity',
      'landing-footer',
    ]) {
      await expect(page.getByTestId(id)).toBeVisible({ timeout: 5000 });
    }
    expect(errors).toHaveLength(0);
  });

  test('landing page does not overflow horizontally at 375px', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 800 });
    await page.goto('/signals-app/');
    await expect(page.getByTestId('landing-footer')).toBeVisible({ timeout: 5000 });
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - window.innerWidth,
    );
    expect(overflow).toBeLessThanOrEqual(0);
  });

  test('ticker search accepts input', async ({ page }) => {
    // Find search input (look for input with placeholder or aria-label)
    const searchInput = page.locator('input[type="text"]').first();

    // Type a ticker symbol
    await searchInput.fill('AAPL');
    await expect(searchInput).toHaveValue('AAPL');

    // Clear it
    await searchInput.fill('');
    await expect(searchInput).toHaveValue('');
  });

  test('no console errors during interaction', async ({ page }) => {
    const errors: string[] = [];

    page.on('console', (msg) => {
      if (msg.type() === 'error') {
        errors.push(msg.text());
      }
    });

    // Try clicking on some elements (if they exist)
    const clickables = page.locator('button, a').first();
    if (await clickables.isVisible()) {
      await clickables.click().catch(() => {
        // Ignore click errors (element may not be clickable)
      });
    }

    expect(errors).toHaveLength(0);
  });
});
